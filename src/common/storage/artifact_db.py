"""带归属、保留期与计算配额的原始／派生归档；PG 故障不回退."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

from common.config import config

_TABLE_NAME = "tool_artifact_storage"
_initialized = False
_initialized_key = ""
_init_lock = asyncio.Lock()
_COLUMNS = {
    "user_id": "TEXT",
    "thread_id": "TEXT",
    "run_id": "TEXT",
    "parent_artifact_id": "TEXT",
    "kind": "TEXT DEFAULT 'raw'",
    "status": "TEXT DEFAULT 'stored'",
    "expires_at": "TEXT",
    "processing_metadata": "TEXT DEFAULT '{}'",
}


class _Cursor(Protocol):
    async def fetchall(self) -> Sequence[Mapping[str, Any]]: ...
    async def fetchone(self) -> Mapping[str, Any] | None: ...


class _Database(Protocol):
    async def execute(self, statement: str, args: tuple[Any, ...] = ()) -> _Cursor: ...
    async def commit(self) -> None: ...


class ArtifactExpired(ValueError):
    """归档已过期，聊天记录仍保留."""


class ComputationLimit(ValueError):
    """计算阶段配额已用尽."""


def _is_pg_configured() -> bool:
    uri = os.getenv("DATABASE_URI", "")
    if not uri:
        return False
    if not uri.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URI 必须使用 postgresql://；本地 SQLite 开发请留空")
    return True


def _get_sqlite_path() -> str:
    path = Path(config.SQLITE_PATH)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


@asynccontextmanager
async def _connection() -> AsyncIterator[_Database]:
    if _is_pg_configured():
        import psycopg
        from psycopg.rows import dict_row

        async with await psycopg.AsyncConnection.connect(
            os.environ["DATABASE_URI"], row_factory=dict_row, connect_timeout=10
        ) as db:
            # 两种驱动都已选择映射行工厂；统一其游标协议。
            yield cast(_Database, cast(object, db))
    else:
        import aiosqlite

        async with aiosqlite.connect(_get_sqlite_path()) as db:
            db.row_factory = aiosqlite.Row
            yield cast(_Database, cast(object, db))


def _sql(statement: str) -> str:
    return statement.replace("?", "%s") if _is_pg_configured() else statement


async def _query(
    statement: str, args: tuple[Any, ...] = (), *, fetch: bool = False
) -> list[dict[str, Any]]:
    async with _connection() as db:
        cur = await db.execute(_sql(statement), args)
        rows = [dict(row) for row in await cur.fetchall()] if fetch else []
        await db.commit()
        return rows


async def init_artifact_storage_table() -> None:
    """增量迁移旧表，初始化结果按实际数据库键区分."""
    global _initialized, _initialized_key
    key = (
        os.environ.get("DATABASE_URI", "")
        if _is_pg_configured()
        else _get_sqlite_path()
    )
    async with _init_lock:
        if _initialized and _initialized_key == key:
            return
        async with _connection() as db:
            await db.execute(
                f"CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (artifact_id TEXT PRIMARY KEY, tool_call_id TEXT NOT NULL, tool_name TEXT NOT NULL, char_length INTEGER NOT NULL, raw_payload TEXT NOT NULL, summary TEXT, created_at TEXT NOT NULL)"
            )
            if _is_pg_configured():
                for name, declaration in _COLUMNS.items():
                    await db.execute(
                        f"ALTER TABLE {_TABLE_NAME} ADD COLUMN IF NOT EXISTS {name} {declaration}"
                    )
            else:
                cur = await db.execute(f"PRAGMA table_info({_TABLE_NAME})")
                columns = {row["name"] for row in await cur.fetchall()}
                for name, declaration in _COLUMNS.items():
                    if name not in columns:
                        await db.execute(
                            f"ALTER TABLE {_TABLE_NAME} ADD COLUMN {name} {declaration}"
                        )
            await db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_tool_artifact_owner ON {_TABLE_NAME}(user_id, thread_id)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS artifact_computations (artifact_id TEXT NOT NULL, run_id TEXT NOT NULL, started_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (artifact_id,run_id))"
            )
            await db.commit()
        _initialized, _initialized_key = True, key


async def save_tool_artifact(
    *,
    tool_call_id: str,
    tool_name: str,
    raw_payload: str,
    user_id: str,
    thread_id: str,
    run_id: str,
    summary: str = "",
    kind: str = "raw",
    parent_artifact_id: str | None = None,
    processing_metadata: dict[str, Any] | None = None,
) -> str:
    """只有存储提交成功才返回引用；调用者必须提供可信归属."""
    if not user_id or not thread_id or not run_id:
        raise PermissionError("归档缺少已认证的用户、会话或运行归属")
    await init_artifact_storage_table()
    artifact_id = f"art_{uuid.uuid4().hex}"
    now = datetime.now(UTC)
    days = config.TOOL_ARTIFACT_RETENTION_DAYS
    if days < 0:
        raise ValueError("归档保留期不能为负数")
    expires = (now + timedelta(days=days)).isoformat() if days else None
    await _query(
        f"INSERT INTO {_TABLE_NAME} (artifact_id,tool_call_id,tool_name,char_length,raw_payload,summary,created_at,user_id,thread_id,run_id,parent_artifact_id,kind,status,expires_at,processing_metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            artifact_id,
            tool_call_id,
            tool_name,
            len(raw_payload),
            raw_payload,
            summary,
            now.isoformat(),
            user_id,
            thread_id,
            run_id,
            parent_artifact_id,
            kind,
            "stored",
            expires,
            json.dumps(processing_metadata or {}, ensure_ascii=False),
        ),
    )
    return artifact_id


async def get_tool_artifact(
    artifact_id: str, *, user_id: str, thread_id: str
) -> dict[str, Any] | None:
    """按归属读取，旧无归属与跨线程均不返回载荷."""
    await init_artifact_storage_table()
    rows = await _query(
        f"SELECT * FROM {_TABLE_NAME} WHERE artifact_id=? AND user_id=? AND thread_id=?",
        (artifact_id, user_id, thread_id),
        fetch=True,
    )
    if not rows:
        return None
    row = rows[0]
    if row["status"] == "expired" or (
        row["expires_at"]
        and datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC)
    ):
        await _query(
            f"UPDATE {_TABLE_NAME} SET raw_payload='', summary='', status='expired' WHERE artifact_id=? AND user_id=? AND thread_id=?",
            (artifact_id, user_id, thread_id),
        )
        raise ArtifactExpired("原始资料已过期，历史消息仍保留。请重新查询资料。")
    row["processing_metadata"] = json.loads(row["processing_metadata"] or "{}")
    return row


async def list_tool_artifacts(
    *, user_id: str, thread_id: str, offset: int = 0, limit: int = 50
) -> list[dict[str, Any]]:
    """分页列出元数据，不在列表响应中装载原文."""
    await init_artifact_storage_table()
    return await _query(
        f"SELECT artifact_id,tool_name,char_length,kind,status,expires_at,created_at,parent_artifact_id FROM {_TABLE_NAME} WHERE user_id=? AND thread_id=? ORDER BY created_at DESC,artifact_id LIMIT ? OFFSET ?",
        (user_id, thread_id, min(max(limit, 1), 100), max(offset, 0)),
        fetch=True,
    )


async def delete_thread_artifacts(*, user_id: str, thread_id: str) -> None:
    """删除本人线程的原始／派生归档和计算记录，幂等."""
    await init_artifact_storage_table()
    async with _connection() as db:
        await db.execute(
            _sql(
                f"DELETE FROM artifact_computations WHERE artifact_id IN (SELECT artifact_id FROM {_TABLE_NAME} WHERE user_id=? AND thread_id=?)"
            ),
            (user_id, thread_id),
        )
        await db.execute(
            _sql(f"DELETE FROM {_TABLE_NAME} WHERE user_id=? AND thread_id=?"),
            (user_id, thread_id),
        )
        await db.commit()


async def cleanup_expired_artifacts(limit: int = 500) -> None:
    """有界清理过期载荷，不删除聊天或诊断引用."""
    await init_artifact_storage_table()
    await _backfill_legacy_expiry()
    await _query(
        f"UPDATE {_TABLE_NAME} SET raw_payload='', summary='', status='expired' WHERE artifact_id IN (SELECT artifact_id FROM {_TABLE_NAME} WHERE expires_at<=? AND status!='expired' LIMIT ?)",
        (datetime.now(UTC).isoformat(), min(max(limit, 1), 1000)),
    )


async def claim_computation(
    artifact_id: str,
    run_id: str,
    *,
    attempt: bool = True,
    started_at: str | None = None,
) -> dict[str, Any]:
    """事务分配一次生成及有界修正，总阶段从首次归档就开始计时."""
    await init_artifact_storage_table()
    started = datetime.now(UTC)
    if started_at:
        try:
            candidate = datetime.fromisoformat(started_at)
            if candidate.tzinfo is not None:
                started = min(started, candidate)
        except ValueError:
            pass
    async with _connection() as db:
        if not _is_pg_configured():
            await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            _sql(
                "INSERT INTO artifact_computations (artifact_id,run_id,started_at,attempts) VALUES (?,?,?,0) ON CONFLICT (artifact_id,run_id) DO NOTHING"
            ),
            (artifact_id, run_id, started.isoformat()),
        )
        query = (
            "SELECT * FROM artifact_computations WHERE artifact_id=? AND run_id=?"
            + (" FOR UPDATE" if _is_pg_configured() else "")
        )
        cur = await db.execute(_sql(query), (artifact_id, run_id))
        stored = await cur.fetchone()
        if stored is None:
            raise ComputationLimit("计算配额记录不存在，尚未完成处理。")
        row = dict(stored)
        elapsed = (
            datetime.now(UTC) - datetime.fromisoformat(row["started_at"])
        ).total_seconds()
        if (
            elapsed >= config.DATA_PROCESSING_TIMEOUT_SECONDS
            or row["attempts"] >= 1 + config.QUICKJS_MAX_CORRECTIONS
        ):
            raise ComputationLimit("本阶段处理时限或修正次数已用尽，尚未完成计算。")
        if attempt:
            await db.execute(
                _sql(
                    "UPDATE artifact_computations SET attempts=attempts+1 WHERE artifact_id=? AND run_id=?"
                ),
                (artifact_id, run_id),
            )
        await db.commit()
        return {
            "attempt": row["attempts"] + (1 if attempt else 0),
            "remaining_seconds": config.DATA_PROCESSING_TIMEOUT_SECONDS - elapsed,
        }


async def update_artifact_summary(
    artifact_id: str,
    *,
    user_id: str,
    thread_id: str,
    summary: str,
    metadata: dict[str, Any],
) -> None:
    """原文保存成功后记录完整摘要和覆盖证据，不能更新他人归档."""
    await init_artifact_storage_table()
    rows = await _query(
        f"UPDATE {_TABLE_NAME} SET summary=?,processing_metadata=? WHERE artifact_id=? AND user_id=? AND thread_id=? AND status='stored' RETURNING artifact_id",
        (
            summary,
            json.dumps(metadata, ensure_ascii=False),
            artifact_id,
            user_id,
            thread_id,
        ),
        fetch=True,
    )
    if not rows:
        raise PermissionError("归档无法更新")


async def _backfill_legacy_expiry(limit: int = 500) -> None:
    days = config.TOOL_ARTIFACT_RETENTION_DAYS
    if days <= 0:
        return
    async with _connection() as db:
        cur = await db.execute(
            _sql(
                f"SELECT artifact_id,created_at FROM {_TABLE_NAME} WHERE user_id IS NULL AND expires_at IS NULL LIMIT ?"
            ),
            (limit,),
        )
        for row in await cur.fetchall():
            try:
                created = (
                    row["created_at"]
                    if isinstance(row["created_at"], datetime)
                    else datetime.fromisoformat(str(row["created_at"]))
                )
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
            except (ValueError, TypeError):
                created = datetime.now(UTC)
            await db.execute(
                _sql(f"UPDATE {_TABLE_NAME} SET expires_at=? WHERE artifact_id=?"),
                ((created + timedelta(days=days)).isoformat(), row["artifact_id"]),
            )
        await db.commit()
