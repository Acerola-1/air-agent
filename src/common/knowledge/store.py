"""Protected citation storage for knowledge references.

Only non-secret identifiers and short excerpts are stored. The full text is
never copied into ordinary events, logs or the model prompt: the source proxy
re-reads it from the knowledge service after a fresh delegation and permission
check. In PG mode the request role is the non-owner runtime role with FORCE RLS
and transaction-local ``air_agent.*`` GUCs.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol, cast

from common.config import config
from common.identity.runtime import RuntimeIdentity

_TABLE = "knowledge_citations"
_initialized = False
_initialized_key = ""
_init_lock = asyncio.Lock()


class CitationBackendUnavailable(RuntimeError):
    """Citation storage is missing or unreachable: fail closed."""


class CitationNotFound(LookupError):
    """Citation does not exist for this user/tenant (or was deleted)."""


class _Cursor(Protocol):
    async def fetchall(self) -> list[dict[str, Any]]: ...
    async def fetchone(self) -> dict[str, Any] | None: ...


class _Database(Protocol):
    async def execute(self, statement: str, args: tuple[Any, ...] = ()) -> _Cursor: ...
    async def commit(self) -> None: ...


def _is_pg_configured() -> bool:
    uri = os.getenv("DATABASE_URI", "")
    if not uri:
        return False
    if not uri.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URI 必须使用 postgresql://")
    return True


def _runtime_uri() -> str:
    uri = os.getenv("RUNTIME_DATABASE_URI", "")
    if not uri.startswith(("postgresql://", "postgres://")):
        raise CitationBackendUnavailable("RUNTIME_DATABASE_URI 未配置")
    return uri


def _sqlite_path() -> str:
    path = Path(config.SQLITE_PATH)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[3] / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


@asynccontextmanager
async def _connection(identity: RuntimeIdentity):
    if _is_pg_configured():
        import psycopg
        from psycopg.rows import dict_row

        async with await psycopg.AsyncConnection.connect(
            _runtime_uri(), row_factory=dict_row, connect_timeout=10, autocommit=False
        ) as db:
            await db.execute(
                "SELECT set_config('air_agent.tenant_id', %s, true)",
                (identity.tenant_id,),
            )
            await db.execute(
                "SELECT set_config('air_agent.user_id', %s, true)", (identity.user_id,)
            )
            yield cast(_Database, cast(object, db))
    else:
        import aiosqlite

        async with aiosqlite.connect(_sqlite_path()) as db:
            db.row_factory = aiosqlite.Row
            yield cast(_Database, cast(object, db))


def _sql(statement: str) -> str:
    return statement.replace("?", "%s") if _is_pg_configured() else statement


async def init_citation_storage() -> None:
    """SQLite dev fallback only; PG DDL truth lives in Alembic."""
    global _initialized, _initialized_key
    key = os.environ.get("DATABASE_URI", "") or _sqlite_path()
    async with _init_lock:
        if _initialized and _initialized_key == key:
            return
        if _is_pg_configured():
            import psycopg
            from psycopg.rows import dict_row

            async with await psycopg.AsyncConnection.connect(
                _runtime_uri(), row_factory=dict_row, connect_timeout=10
            ) as db:
                rows = await db.execute(
                    "SELECT to_regclass('public.knowledge_citations') AS t"
                )
                row = await rows.fetchone()
                if not row or not row.get("t"):
                    raise CitationBackendUnavailable(
                        "knowledge_citations 未初始化：请执行 admin API 迁移 0023"
                    )
            _initialized, _initialized_key = True, key
            return
        import aiosqlite

        async with aiosqlite.connect(_sqlite_path()) as db:
            await db.execute(
                f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
                    citation_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL DEFAULT '',
                    record_id TEXT NOT NULL,
                    evidence_index INTEGER NOT NULL,
                    workspace TEXT NOT NULL,
                    doc_id TEXT,
                    doc_name TEXT NOT NULL,
                    chunk_id TEXT,
                    kind TEXT,
                    source_version TEXT,
                    source_sha256 TEXT NOT NULL,
                    location_json TEXT,
                    excerpt TEXT NOT NULL,
                    query_sha256 TEXT,
                    outcome TEXT,
                    created_at REAL NOT NULL,
                    expires_at REAL
                )"""
            )
            await db.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_citation_source "
                f"ON {_TABLE}(tenant_id,user_id,thread_id,record_id,evidence_index,source_sha256)"
            )
            await db.commit()
        _initialized, _initialized_key = True, key


async def register_citations(
    identity: RuntimeIdentity,
    *,
    thread_id: str,
    run_id: str,
    record_id: str,
    query_sha256: str,
    outcome: str,
    items: list[dict[str, Any]],
) -> list[str]:
    """Register up to 8 references and return their opaque citation ids.

    Raises :class:`CitationBackendUnavailable` when the store cannot persist:
    the caller must then publish no citation at all (no untraceable source).
    """
    await init_citation_storage()
    ids: list[str] = []
    async with _connection(identity) as db:
        for index, item in enumerate(items):
            citation_id = str(uuid.uuid4())
            location = item.get("location")
            import json

            created_expr = "to_timestamp(?)" if _is_pg_configured() else "?"
            await db.execute(
                _sql(
                    f"""INSERT INTO {_TABLE} (
                        citation_id, tenant_id, user_id, thread_id, run_id,
                        record_id, evidence_index, workspace, doc_id, doc_name,
                        chunk_id, kind, source_version, source_sha256, location_json,
                        excerpt, query_sha256, outcome, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,{created_expr})
                    ON CONFLICT (tenant_id,user_id,thread_id,record_id,evidence_index,source_sha256)
                    DO UPDATE SET excerpt=excluded.excerpt, outcome=excluded.outcome
                    RETURNING citation_id"""
                ),
                (
                    citation_id,
                    identity.tenant_id,
                    identity.user_id,
                    thread_id,
                    run_id,
                    record_id,
                    int(item["evidence_index"]),
                    str(item.get("workspace") or ""),
                    item.get("doc_id"),
                    str(item.get("doc_name") or ""),
                    item.get("chunk_id"),
                    item.get("kind"),
                    item.get("source_version"),
                    str(item["source_sha256"]),
                    json.dumps(location, ensure_ascii=False) if location else None,
                    str(item.get("excerpt") or "")[:500],
                    query_sha256,
                    outcome,
                    __import__("time").time(),
                ),
            )
            cursor = await db.execute(
                _sql(
                    f"SELECT citation_id FROM {_TABLE} WHERE tenant_id=? AND user_id=? "
                    "AND thread_id=? AND record_id=? AND evidence_index=? AND source_sha256=?"
                ),
                (
                    identity.tenant_id,
                    identity.user_id,
                    thread_id,
                    record_id,
                    int(item["evidence_index"]),
                    str(item["source_sha256"]),
                ),
            )
            row = await cursor.fetchone()
            if row:
                ids.append(str(row["citation_id"]))
        await db.commit()
    return ids


async def get_citation(identity: RuntimeIdentity, citation_id: str) -> dict[str, Any]:
    """Read one citation owned by the current tenant/user (RLS plus explicit match)."""
    await init_citation_storage()
    async with _connection(identity) as db:
        cursor = await db.execute(
            _sql(
                f"SELECT * FROM {_TABLE} WHERE citation_id=? AND tenant_id=? AND user_id=?"
            ),
            (citation_id, identity.tenant_id, identity.user_id),
        )
        row = await cursor.fetchone()
    if row is None:
        raise CitationNotFound(citation_id)
    result = dict(row)
    import json

    raw = result.pop("location_json", None)
    result["location"] = json.loads(raw) if raw else None
    return result


async def list_thread_citations(
    identity: RuntimeIdentity, thread_id: str
) -> list[dict[str, Any]]:
    """Read the complete server-side dependency set, never only its first page."""
    await init_citation_storage()
    async with _connection(identity) as db:
        cursor = await db.execute(
            _sql(
                f"SELECT * FROM {_TABLE} WHERE tenant_id=? AND user_id=? AND thread_id=?"
            ),
            (identity.tenant_id, identity.user_id, thread_id),
        )
        return [dict(row) for row in await cursor.fetchall()]
