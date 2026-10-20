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
    "tenant_id": "TEXT",
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
    """PG/SQLite 模式开关沿用 `DATABASE_URI`（平台部署标记）.

    R03：PG 模式下归档**绝不**连接该 owner URI——请求路径必须走运行角色
    `RUNTIME_DATABASE_URI`（非 owner、非 BYPASSRLS，RLS 真实生效），清理/
    回填走受限维护角色 `MAINTENANCE_DATABASE_URI`；缺失一律失败关闭。
    """
    uri = os.getenv("DATABASE_URI", "")
    if not uri:
        return False
    if not uri.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URI 必须使用 postgresql://；本地 SQLite 开发请留空")
    return True


class ArtifactBackendUnavailable(RuntimeError):
    """归档后端配置或连接不可用：失败关闭，绝不回退 owner/匿名连接."""


def _runtime_uri() -> str:
    uri = os.getenv("RUNTIME_DATABASE_URI", "")
    if not uri.startswith(("postgresql://", "postgres://")):
        raise ArtifactBackendUnavailable(
            "RUNTIME_DATABASE_URI 未配置：归档读写必须经非 owner 运行角色（RLS 生效）"
        )
    return uri


def _maintenance_uri() -> str:
    uri = os.getenv("MAINTENANCE_DATABASE_URI", "")
    if not uri.startswith(("postgresql://", "postgres://")):
        raise ArtifactBackendUnavailable(
            "MAINTENANCE_DATABASE_URI 未配置：过期清理/回填必须经受限维护角色"
        )
    return uri


def _scope_guc(tenant_id: str, user_id: str) -> dict[str, str]:
    if not tenant_id or not user_id:
        raise ArtifactBackendUnavailable("归档作用域缺失企业或用户上下文")
    return {"air_agent.tenant_id": tenant_id, "air_agent.user_id": user_id}


def _get_sqlite_path() -> str:
    path = Path(config.SQLITE_PATH)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


@asynccontextmanager
async def _pg_connection(
    uri: str, guc: dict[str, str] | None = None
) -> AsyncIterator[_Database]:
    """运行/维护角色连接：事务本地 GUC（SET LOCAL），连接不跨请求携带作用域."""
    import psycopg
    from psycopg.rows import dict_row

    async with await psycopg.AsyncConnection.connect(
        uri, row_factory=dict_row, connect_timeout=10, autocommit=False
    ) as db:
        if guc:
            for key, value in guc.items():
                await db.execute("SELECT set_config(%s, %s, true)", (key, value))
        yield cast(_Database, cast(object, db))


@asynccontextmanager
async def _connection(
    guc: dict[str, str] | None = None,
) -> AsyncIterator[_Database]:
    if _is_pg_configured():
        async with _pg_connection(_runtime_uri(), guc) as db:
            yield db
    else:
        import aiosqlite

        async with aiosqlite.connect(_get_sqlite_path()) as db:
            db.row_factory = aiosqlite.Row
            yield cast(_Database, cast(object, db))


@asynccontextmanager
async def _maintenance_connection() -> AsyncIterator[_Database]:
    """受限维护角色连接：只用于到期清理/回填（R03），带事务本地维护 GUC."""
    import psycopg
    from psycopg.rows import dict_row

    async with await psycopg.AsyncConnection.connect(
        _maintenance_uri(),
        row_factory=dict_row,
        connect_timeout=10,
        autocommit=False,
    ) as db:
        await db.execute(
            "SELECT set_config('air_agent.artifact_maintenance', 'on', true)"
        )
        yield cast(_Database, cast(object, db))


def _sql(statement: str) -> str:
    return statement.replace("?", "%s") if _is_pg_configured() else statement


async def _query(
    statement: str,
    args: tuple[Any, ...] = (),
    *,
    fetch: bool = False,
    guc: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    async with _connection(guc) as db:
        cur = await db.execute(_sql(statement), args)
        rows = [dict(row) for row in await cur.fetchall()] if fetch else []
        await db.commit()
        return rows


async def init_artifact_storage_table() -> None:
    """SQLite 开发路径自动建表；PG 生产路径 DDL 真相源收敛到 Alembic（6.2/R03）.

    PG 模式下运行角色无 DDL 权限，只核对表存在；缺失即失败关闭并提示执行
    admin API 迁移——绝不以 owner 连接兜底建表。
    """
    global _initialized, _initialized_key
    key = (
        os.environ.get("DATABASE_URI", "")
        if _is_pg_configured()
        else _get_sqlite_path()
    )
    async with _init_lock:
        if _initialized and _initialized_key == key:
            return
        if _is_pg_configured():
            rows = await _query(
                "SELECT to_regclass('public.tool_artifact_storage') AS t,"
                " to_regclass('public.artifact_computations') AS c",
                (),
                fetch=True,
            )
            if not rows or not rows[0].get("t") or not rows[0].get("c"):
                raise ArtifactBackendUnavailable(
                    "归档表未初始化：请在 admin API 执行 alembic upgrade head（0004/0005）"
                )
            _initialized, _initialized_key = True, key
            return
        async with _connection() as db:
            await db.execute(
                f"CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (artifact_id TEXT PRIMARY KEY, tool_call_id TEXT NOT NULL, tool_name TEXT NOT NULL, char_length INTEGER NOT NULL, raw_payload TEXT NOT NULL, summary TEXT, created_at TEXT NOT NULL)"
            )
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
                f"CREATE INDEX IF NOT EXISTS idx_tool_artifact_tenant ON {_TABLE_NAME}(tenant_id, user_id, thread_id)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS artifact_computations (artifact_id TEXT NOT NULL, run_id TEXT NOT NULL, started_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, tenant_id TEXT, user_id TEXT, PRIMARY KEY (artifact_id,run_id))"
            )
            cur2 = await db.execute("PRAGMA table_info(artifact_computations)")
            comp_cols = {row["name"] for row in await cur2.fetchall()}
            for name in ("tenant_id", "user_id"):
                if name not in comp_cols:
                    await db.execute(
                        f"ALTER TABLE artifact_computations ADD COLUMN {name} TEXT"
                    )
            await db.commit()
        _initialized, _initialized_key = True, key


async def save_tool_artifact(
    *,
    tool_call_id: str,
    tool_name: str,
    raw_payload: str,
    tenant_id: str,
    user_id: str,
    thread_id: str,
    run_id: str,
    summary: str = "",
    kind: str = "raw",
    parent_artifact_id: str | None = None,
    processing_metadata: dict[str, Any] | None = None,
) -> str:
    """只有存储提交成功才返回引用；调用者必须提供可信企业/用户/线程/运行归属."""
    if not tenant_id or not user_id or not thread_id or not run_id:
        raise PermissionError("归档缺少已认证的企业、用户、会话或运行归属")
    await init_artifact_storage_table()
    artifact_id = f"art_{uuid.uuid4().hex}"
    now = datetime.now(UTC)
    days = config.TOOL_ARTIFACT_RETENTION_DAYS
    if days < 0:
        raise ValueError("归档保留期不能为负数")
    expires = (now + timedelta(days=days)).isoformat() if days else None
    await _query(
        f"INSERT INTO {_TABLE_NAME} (artifact_id,tool_call_id,tool_name,char_length,raw_payload,summary,created_at,tenant_id,user_id,thread_id,run_id,parent_artifact_id,kind,status,expires_at,processing_metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            artifact_id,
            tool_call_id,
            tool_name,
            len(raw_payload),
            raw_payload,
            summary,
            now.isoformat(),
            tenant_id,
            user_id,
            thread_id,
            run_id,
            parent_artifact_id,
            kind,
            "stored",
            expires,
            json.dumps(processing_metadata or {}, ensure_ascii=False),
        ),
        guc=_scope_guc(tenant_id, user_id),
    )
    return artifact_id


async def get_tool_artifact(
    artifact_id: str, *, tenant_id: str, user_id: str, thread_id: str
) -> dict[str, Any] | None:
    """按企业+用户+线程归属读取；历史无租户行（tenant_id NULL）保持不可读."""
    await init_artifact_storage_table()
    rows = await _query(
        f"SELECT * FROM {_TABLE_NAME} WHERE artifact_id=? AND tenant_id=? AND user_id=? AND thread_id=?",
        (artifact_id, tenant_id, user_id, thread_id),
        fetch=True,
        guc=_scope_guc(tenant_id, user_id),
    )
    if not rows:
        return None
    row = rows[0]
    if row["status"] == "expired" or (
        row["expires_at"]
        and datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC)
    ):
        await _query(
            f"UPDATE {_TABLE_NAME} SET raw_payload='', summary='', status='expired' WHERE artifact_id=? AND tenant_id=? AND user_id=? AND thread_id=?",
            (artifact_id, tenant_id, user_id, thread_id),
            guc=_scope_guc(tenant_id, user_id),
        )
        raise ArtifactExpired("原始资料已过期，历史消息仍保留。请重新查询资料。")
    row["processing_metadata"] = json.loads(row["processing_metadata"] or "{}")
    return row


async def get_tool_artifact_by_call(
    tool_call_id: str, *, tenant_id: str, user_id: str, thread_id: str
) -> dict[str, Any] | None:
    """按工具调用 ID 读取同一线程内最新归档；归属与过期规则同 get_tool_artifact."""
    await init_artifact_storage_table()
    rows = await _query(
        f"SELECT * FROM {_TABLE_NAME} WHERE tool_call_id=? AND tenant_id=? AND user_id=? AND thread_id=? ORDER BY created_at DESC LIMIT 1",
        (tool_call_id, tenant_id, user_id, thread_id),
        fetch=True,
        guc=_scope_guc(tenant_id, user_id),
    )
    if not rows:
        return None
    row = rows[0]
    if row["status"] == "expired" or (
        row["expires_at"]
        and datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC)
    ):
        raise ArtifactExpired("原始资料已过期，历史消息仍保留。请重新查询资料。")
    row["processing_metadata"] = json.loads(row["processing_metadata"] or "{}")
    return row


async def list_tool_artifacts(
    *, tenant_id: str, user_id: str, thread_id: str, offset: int = 0, limit: int = 50
) -> list[dict[str, Any]]:
    """分页列出元数据，不在列表响应中装载原文；企业维度强制."""
    await init_artifact_storage_table()
    return await _query(
        f"SELECT artifact_id,tool_name,char_length,kind,status,expires_at,created_at,parent_artifact_id FROM {_TABLE_NAME} WHERE tenant_id=? AND user_id=? AND thread_id=? ORDER BY created_at DESC,artifact_id LIMIT ? OFFSET ?",
        (tenant_id, user_id, thread_id, min(max(limit, 1), 100), max(offset, 0)),
        fetch=True,
        guc=_scope_guc(tenant_id, user_id),
    )


async def delete_thread_artifacts(
    *, tenant_id: str, user_id: str, thread_id: str
) -> None:
    """删除本人线程的原始／派生归档和计算记录，幂等；只影响该企业该线程."""
    await init_artifact_storage_table()
    async with _connection(_scope_guc(tenant_id, user_id)) as db:
        await db.execute(
            _sql(
                f"DELETE FROM artifact_computations WHERE artifact_id IN (SELECT artifact_id FROM {_TABLE_NAME} WHERE tenant_id=? AND user_id=? AND thread_id=?)"
            ),
            (tenant_id, user_id, thread_id),
        )
        await db.execute(
            _sql(
                f"DELETE FROM {_TABLE_NAME} WHERE tenant_id=? AND user_id=? AND thread_id=?"
            ),
            (tenant_id, user_id, thread_id),
        )
        await db.commit()


async def cleanup_expired_artifacts(limit: int = 500) -> None:
    """有界清理过期载荷，不删除聊天或诊断引用.

    R03：走受限维护角色连接（列权限+GUC 策略双重限定，见 0005）；PG 模式
    缺失 MAINTENANCE_DATABASE_URI 一律失败关闭，绝不回退 owner 连接。
    """
    await init_artifact_storage_table()
    await _backfill_legacy_expiry()
    if _is_pg_configured():
        async with _maintenance_connection() as db:
            await db.execute(
                _sql(
                    f"UPDATE {_TABLE_NAME} SET raw_payload='', summary='', status='expired'"
                    " WHERE artifact_id IN (SELECT artifact_id FROM"
                    f" {_TABLE_NAME} WHERE expires_at<=? AND status!='expired' LIMIT ?)"
                ),
                (datetime.now(UTC).isoformat(), min(max(limit, 1), 1000)),
            )
            await db.commit()
        return
    await _query(
        f"UPDATE {_TABLE_NAME} SET raw_payload='', summary='', status='expired' WHERE artifact_id IN (SELECT artifact_id FROM {_TABLE_NAME} WHERE expires_at<=? AND status!='expired' LIMIT ?)",
        (datetime.now(UTC).isoformat(), min(max(limit, 1), 1000)),
    )


async def claim_computation(
    artifact_id: str,
    run_id: str,
    *,
    tenant_id: str,
    user_id: str,
    attempt: bool = True,
    started_at: str | None = None,
) -> dict[str, Any]:
    """事务分配一次生成及有界修正，总阶段从首次归档就开始计时.

    R03：计算记录携带租户/用户维度，PG 下经运行角色事务本地 GUC 隔离；
    缺失作用域失败关闭，跨企业引用被"父归档同租户"RLS 策略拒绝。
    """
    if not tenant_id or not user_id:
        raise PermissionError("计算配额认领缺少已认证的企业与用户归属")
    await init_artifact_storage_table()
    started = datetime.now(UTC)
    if started_at:
        try:
            candidate = datetime.fromisoformat(started_at)
            if candidate.tzinfo is not None:
                started = min(started, candidate)
        except ValueError:
            pass
    async with _connection(_scope_guc(tenant_id, user_id)) as db:
        if not _is_pg_configured():
            await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute(
                _sql(
                    "INSERT INTO artifact_computations (artifact_id,run_id,started_at,attempts,tenant_id,user_id)"
                    " VALUES (?,?,?,?,?,?) ON CONFLICT (artifact_id,run_id) DO NOTHING"
                ),
                (artifact_id, run_id, started.isoformat(), 0, tenant_id, user_id),
            )
        except Exception as exc:
            # R03：跨企业父归档引用在 RLS WITH CHECK 处被数据库拒绝——
            # 映射为可读的计算失败，不回退 owner 连接。
            raise ComputationLimit("计算引用不属于当前授权作用域，已拒绝。") from exc
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
    tenant_id: str,
    user_id: str,
    thread_id: str,
    summary: str,
    metadata: dict[str, Any],
) -> None:
    """原文保存成功后记录完整摘要和覆盖证据，不能更新其他归属的归档."""
    await init_artifact_storage_table()
    rows = await _query(
        f"UPDATE {_TABLE_NAME} SET summary=?,processing_metadata=? WHERE artifact_id=? AND tenant_id=? AND user_id=? AND thread_id=? AND status='stored' RETURNING artifact_id",
        (
            summary,
            json.dumps(metadata, ensure_ascii=False),
            artifact_id,
            tenant_id,
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
    if _is_pg_configured():
        # 历史无归属行只补到期列（维护角色列权限不含载荷读取，见 0005）。
        async with _maintenance_connection() as db:
            cur = await db.execute(
                _sql(
                    f"SELECT artifact_id,created_at FROM {_TABLE_NAME}"
                    " WHERE user_id IS NULL AND expires_at IS NULL LIMIT ?"
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
