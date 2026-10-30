"""Agent 侧台账存储：业务 PG 直连最小异步读写（运行角色 + 事务本地 GUC）.

设计 §1：基本事实与 outbox 同事务，运行可直接写 PG，避免每个节点依赖后台 HTTP。
本模块**不导入** `admin/api/app`，只按迁移 0007/0008 的表契约写 SQL。

**原子性（R02 修复）**：所有"事实 + 事件 + outbox"写入都走同一个 `_tx` 事务
（同一连接、同一提交点）。事务内任何一步失败 → 整笔回滚、**零可见**；
不再出现"事件已提交、outbox 缺失"的半提交事实。故障注入缝为 `_insert_outbox`。

失败关闭：缺配置/连接失败抛 `LedgerBackendUnavailable`，调用方不得据"写入失败"
推断业务成功；也不得把台账故障改写为已有答案的业务失败。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from common.event_ledger.contract import canonical_digest, validate_event

TERMINAL_STATES = ("succeeded", "failed", "cancelled", "timed_out")
ACTIVE_STATES = ("admitting", "admission_unknown", "queued", "running", "waiting_input")
EVIDENCE_RANK = {"platform_read": 3, "runtime_callback": 2, "browser_observed": 1}
DEFAULT_CONSUMERS = ("run_projection",)
# 参与"同 ID 冲突判定"的信封字段（R02：不能只比 payload 摘要）
_ENVELOPE_IDENTITY = (
    "event_type",
    "source",
    "thread_id",
    "request_id",
    "attempt_id",
    "run_id",
    "answer_id",
    "message_id",
)

_POOL: AsyncConnectionPool | None = None


class ThreadDeleted(Exception):
    """目标会话已删除（永久语义）：**拒绝写入新事实/事件**.

    F04：删除后仍可能有**晚到回调**（运行收尾、状态校准、补投递重试）到达。它们不得
    复活已删会话的事实与派生内容，也不得写入新的运行事件；调用方按失败开放处理
    （记录而非回滚业务答案），绝不因删除状态把业务失败反灌给用户。
    """


class LedgerBackendUnavailable(Exception):
    """台账后端不可用：调用方失败关闭（不接收新运行 / 不冒充已持久化）."""


def _uri() -> str:
    uri = os.environ.get("RUNTIME_DATABASE_URI", "")
    if not uri:
        raise LedgerBackendUnavailable("RUNTIME_DATABASE_URI not configured")
    return uri


async def get_pool() -> AsyncConnectionPool:
    """首次使用时惰性打开共享连接池."""
    global _POOL
    if _POOL is None:
        _POOL = AsyncConnectionPool(
            conninfo=_uri(),
            min_size=1,
            max_size=8,
            open=False,
            timeout=10,
            kwargs={"row_factory": dict_row, "autocommit": False},
        )
        await _POOL.open(wait=True, timeout=10)
    return _POOL


async def close_pool() -> None:
    """关闭并释放共享连接池（停机/测试辅助）."""
    global _POOL
    if _POOL is not None:
        await _POOL.close()
        _POOL = None


@dataclass(frozen=True)
class LedgerScope:
    """可信台账作用域（来自 configurable.langgraph_auth_user）."""

    tenant_id: str
    user_id: str
    membership_id: str

    def as_guc(self) -> dict[str, str]:
        """返回台账作用域对应的 GUC 键值."""
        return {
            "air_agent.tenant_id": self.tenant_id,
            "air_agent.user_id": self.user_id,
        }


# ---------------------------------------------------------------- 事务与底层查询


@asynccontextmanager
async def _tx(
    scope: LedgerScope, extra_guc: dict[str, str] | None = None
) -> AsyncIterator[Any]:
    """单一事务：设置 GUC 后交出连接，退出时统一提交/回滚.

    写入路径必须用本上下文；事务内任何异常都会回滚全部改动（不留半提交事实）。
    """
    try:
        pool = await get_pool()
    except LedgerBackendUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LedgerBackendUnavailable(
            f"pool unavailable: {type(exc).__name__}"
        ) from exc
    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                guc = {**scope.as_guc(), **(extra_guc or {})}
                for key, value in guc.items():
                    await conn.execute(
                        "SELECT set_config(%s, %s, true)", (key, value or "")
                    )
                yield conn
    except psycopg.Error as exc:
        from loguru import logger

        logger.bind(event_ledger=True).exception(
            "ledger query failed: {}", type(exc).__name__
        )
        raise LedgerBackendUnavailable("ledger query rejected or failed") from exc


async def _run(
    sql: str, params: dict[str, Any], guc: dict[str, str], *, fetch: str = "none"
) -> Any:
    """只读/单语句辅助（自带事务）；写入路径请用 `_tx` 保证与事实同事务."""
    try:
        pool = await get_pool()
    except LedgerBackendUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LedgerBackendUnavailable(
            f"pool unavailable: {type(exc).__name__}"
        ) from exc
    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                for key, value in guc.items():
                    await conn.execute(
                        "SELECT set_config(%s, %s, true)", (key, value or "")
                    )
                cur = await conn.execute(sql, params)
                if fetch == "one":
                    return await cur.fetchone()
                if fetch == "all":
                    return await cur.fetchall()
                return None
    except psycopg.Error as exc:
        from loguru import logger

        logger.bind(event_ledger=True).exception(
            "ledger query failed: {}", type(exc).__name__
        )
        raise LedgerBackendUnavailable("ledger query rejected or failed") from exc


async def _insert_outbox(
    conn: Any,  # noqa: ANN401 - 调用方事务内的连接
    tenant_id: str,
    event_id: str,
    consumer: str,
    thread_id: str | None = None,
) -> None:
    """Outbox 写入缝（单测在此注入故障以验证整笔回滚）.

    同时记录 thread_id：受限清理 worker 需要在不读事件正文、不越权的前提下定位
    线程归属（R05）。
    """
    await conn.execute(
        "INSERT INTO agent_outbox (tenant_id, event_id, consumer, available_at, status,"
        " attempt_count, thread_id, created_at, updated_at)"
        " VALUES (%(tenant_id)s, %(event_id)s, %(consumer)s, now(), 'pending', 0,"
        " %(thread_id)s, now(), now())"
        " ON CONFLICT (tenant_id, event_id, consumer) DO UPDATE SET"
        " thread_id = COALESCE(agent_outbox.thread_id, EXCLUDED.thread_id)",
        {
            "tenant_id": tenant_id,
            "event_id": event_id,
            "consumer": consumer,
            "thread_id": thread_id,
        },
    )


# ------------------------------------------------------------------ 事件与 outbox


async def _write_event_in(
    conn: Any,  # noqa: ANN401 - 调用方事务内的连接
    scope: LedgerScope,
    *,
    event_type: str,
    source: str,
    payload: dict[str, Any],
    occurred_at: dt.datetime | None = None,
    thread_id: str | None = None,
    request_id: str | None = None,
    attempt_id: str | None = None,
    run_id: str | None = None,
    answer_id: str | None = None,
    message_id: str | None = None,
    state_revision: int | None = None,
    event_id: str | None = None,
    consumers: tuple[str, ...] = DEFAULT_CONSUMERS,
    allow_deleted: bool = False,
) -> dict[str, Any]:
    """在**调用方事务**内写入事件 + outbox（不自行提交）.

    幂等/冲突判定覆盖**信封身份字段**（类型、来源、线程/请求/尝试/运行/回答/消息），
    不只比较 payload 摘要（R02）：同 ID 但类型或关键关联变化 → 冲突。

    F04：带线程作用域的写入先检查**永久删除标记**——已删会话不允许再产生新事实/事件
    （晚到回调、状态校准、重试补写都走这里），避免"删完又被回调写回来"。
    只有**登记删除本身**（`record_thread_deletion`）以 `allow_deleted=True` 放行。
    """
    if not allow_deleted:
        # 线程作用域可能来自事件字段，也可能只能由 attempt/request 反查（例如"回答"
        # 事件只带 attempt_id）——两条路都要拦住，否则晚到回调会从缺字段的一侧漏进来。
        target_thread = thread_id or ""
        if not target_thread and (attempt_id or request_id):
            row = await (
                await conn.execute(
                    "SELECT r.thread_id FROM agent_requests r"
                    " WHERE r.tenant_id = %(t)s AND (r.id = CAST(%(rid)s AS uuid)"
                    "   OR r.id = (SELECT s.request_id FROM agent_run_attempts s"
                    "             WHERE s.id = CAST(%(aid)s AS uuid) AND s.tenant_id = %(t)s))"
                    " LIMIT 1",
                    {
                        "t": scope.tenant_id,
                        "rid": request_id or None,
                        "aid": attempt_id if (not request_id and attempt_id) else None,
                    },
                )
            ).fetchone()
            target_thread = str(row["thread_id"]) if row else ""
        if target_thread:
            deleted = await (
                await conn.execute(
                    "SELECT 1 FROM agent_thread_deletions"
                    " WHERE tenant_id = %(t)s AND thread_id = %(th)s",
                    {"t": scope.tenant_id, "th": target_thread},
                )
            ).fetchone()
            if deleted is not None:
                raise ThreadDeleted(f"thread {target_thread} already deleted")
    now = dt.datetime.now(dt.UTC)
    occurred = occurred_at or now
    eid = event_id or str(uuid.uuid4())
    event: dict[str, Any] = {
        "schema_version": "1.0",
        "event_id": eid,
        "event_type": event_type,
        "occurred_at": occurred.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        + "Z",
        "source": source,
        "tenant_id": scope.tenant_id,
        "user_id": scope.user_id,
        "membership_id": scope.membership_id,
        "payload": payload,
    }
    for key, value in (
        ("thread_id", thread_id),
        ("request_id", request_id),
        ("attempt_id", attempt_id),
        ("run_id", run_id),
        ("answer_id", answer_id),
        ("message_id", message_id),
    ):
        if value is not None:
            event[key] = value
    result = validate_event(event)
    if not result.valid:
        return {
            "written": False,
            "reason": result.reason,
            "detail": result.detail,
            "event_id": eid,
        }

    digest = canonical_digest(payload)
    cur = await conn.execute(
        "INSERT INTO agent_events (event_id, tenant_id, user_id, membership_id, schema_version,"
        " event_type, source, occurred_at, received_at, thread_id, request_id, attempt_id, run_id,"
        " answer_id, message_id, payload, payload_digest, state_revision, created_at)"
        " VALUES (%(event_id)s, %(tenant_id)s, %(user_id)s, %(membership_id)s, '1.0',"
        " %(event_type)s, %(source)s, %(occurred_at)s, now(), %(thread_id)s, %(request_id)s,"
        " %(attempt_id)s, %(run_id)s, %(answer_id)s, %(message_id)s, %(payload)s::jsonb,"
        " %(digest)s, %(state_revision)s, now())"
        " ON CONFLICT (tenant_id, event_id) DO NOTHING"
        " RETURNING event_id",
        {
            "event_id": eid,
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "membership_id": scope.membership_id,
            "event_type": event_type,
            "source": source,
            "occurred_at": occurred,
            "thread_id": thread_id,
            "request_id": request_id,
            "attempt_id": attempt_id,
            "run_id": run_id,
            "answer_id": answer_id,
            "message_id": message_id,
            "payload": json.dumps(payload, ensure_ascii=False),
            "digest": digest,
            "state_revision": state_revision,
        },
    )
    row = await cur.fetchone()
    if row is None:
        existing = await (
            await conn.execute(
                "SELECT payload_digest, "
                + ", ".join(_ENVELOPE_IDENTITY)
                + " FROM agent_events WHERE tenant_id = %(tenant_id)s"
                " AND event_id = %(event_id)s",
                {"tenant_id": scope.tenant_id, "event_id": eid},
            )
        ).fetchone()
        if existing is None:  # 事务内不可见（异常路径）：按冲突处理，不冒充成功
            return {"written": False, "reason": "conflict_unknown", "event_id": eid}
        incoming: dict[str, Any] = {
            "payload_digest": digest,
            "event_type": event_type,
            "source": source,
            "thread_id": thread_id,
            "request_id": request_id,
            "attempt_id": attempt_id,
            "run_id": run_id,
            "answer_id": answer_id,
            "message_id": message_id,
        }
        same = all(
            _norm(existing.get(key)) == _norm(incoming.get(key))
            for key in ("payload_digest", *_ENVELOPE_IDENTITY)
        )
        return {
            "written": False,
            "reason": "duplicate" if same else "payload_conflict",
            "event_id": eid,
        }

    for consumer in consumers:
        await _insert_outbox(conn, scope.tenant_id, eid, consumer, thread_id)
    return {"written": True, "reason": "ok", "event_id": eid}


def _norm(value: Any) -> Any:  # noqa: ANN401
    """冲突比较用的归一化：None 与空串都视为"未提供"."""
    if value is None or value == "":
        return None
    return str(value)


async def write_event(
    scope: LedgerScope,
    *,
    event_type: str,
    source: str,
    payload: dict[str, Any],
    occurred_at: dt.datetime | None = None,
    thread_id: str | None = None,
    request_id: str | None = None,
    attempt_id: str | None = None,
    run_id: str | None = None,
    answer_id: str | None = None,
    message_id: str | None = None,
    state_revision: int | None = None,
    event_id: str | None = None,
    consumers: tuple[str, ...] = DEFAULT_CONSUMERS,
) -> dict[str, Any]:
    """仅事件 + outbox 的原子写入（自带事务）；事实类写入走各自 helper."""
    async with _tx(scope) as conn:
        return await _write_event_in(
            conn,
            scope,
            event_type=event_type,
            source=source,
            payload=payload,
            occurred_at=occurred_at,
            thread_id=thread_id,
            request_id=request_id,
            attempt_id=attempt_id,
            run_id=run_id,
            answer_id=answer_id,
            message_id=message_id,
            state_revision=state_revision,
            event_id=event_id,
            consumers=consumers,
        )


async def orphan_repair(
    scope: LedgerScope,
    *,
    limit: int = 200,
    consumers: tuple[str, ...] = DEFAULT_CONSUMERS,
) -> dict[str, Any]:
    """有界修复：为"有事件但缺 outbox 行"的历史孤立记录补投递（R02）.

    - 只看本租户、有限条数；
    - 跳过有开放删除标记的线程（删除优先）；
    - 幂等（ON CONFLICT DO NOTHING），可重复执行。
    """
    async with _tx(scope) as conn:
        cur = await conn.execute(
            "SELECT e.event_id::text AS event_id, e.thread_id"
            " FROM agent_events e"
            " WHERE e.tenant_id = %(t)s"
            "   AND NOT EXISTS ("
            "     SELECT 1 FROM agent_outbox o WHERE o.tenant_id = e.tenant_id"
            "       AND o.event_id = e.event_id AND o.consumer = ANY(%(consumers)s))"
            # F04：删除是永久语义——只要存在删除记录（含清理已完成），
            # 该会话的事件就不再补投递，避免重放把已删会话的事实复活。
            "   AND NOT EXISTS ("
            "     SELECT 1 FROM agent_thread_deletions d WHERE d.tenant_id = e.tenant_id"
            "       AND d.thread_id = e.thread_id)"
            " ORDER BY e.received_at ASC LIMIT %(limit)s",
            {"t": scope.tenant_id, "consumers": list(consumers), "limit": limit},
        )
        rows = await cur.fetchall()
        for row in rows:
            for consumer in consumers:
                await _insert_outbox(
                    conn,
                    scope.tenant_id,
                    str(row["event_id"]),
                    consumer,
                    row["thread_id"],
                )
    return {"repaired_events": len(rows), "scanned": len(rows)}


# ------------------------------------------------------------------ 状态转换


async def observe_state(
    scope: LedgerScope,
    *,
    attempt_id: str,
    state: str,
    evidence_source: str,
    platform_status: str | None = None,
    cancel_confirmed: bool | None = None,
    waiting_kind: str | None = None,
) -> dict[str, Any]:
    """带行锁的状态转换：低秩证据不得覆盖高秩终态；终态不回退（事实+事件同事务）."""
    if evidence_source not in EVIDENCE_RANK:
        raise LedgerBackendUnavailable(f"unknown evidence source: {evidence_source}")
    now = dt.datetime.now(dt.UTC)
    async with _tx(scope) as conn:
        cur = await conn.execute(
            "SELECT state, state_revision, evidence_source, ended_at FROM agent_run_attempts"
            " WHERE id = %(id)s AND tenant_id = %(tenant_id)s FOR UPDATE",
            {"id": attempt_id, "tenant_id": scope.tenant_id},
        )
        row = await cur.fetchone()
        if row is None:
            return {"applied": False, "reason": "attempt_not_found"}
        current_rank = EVIDENCE_RANK.get(str(row["evidence_source"] or ""), 0)
        incoming_rank = EVIDENCE_RANK[evidence_source]
        applied = True
        note = None
        if str(row["state"]) in TERMINAL_STATES:
            if incoming_rank < current_rank:
                applied, note = False, "lower_rank_after_terminal"
            elif state == str(row["state"]):
                applied, note = False, "duplicate_terminal"
            elif incoming_rank <= current_rank:
                applied, note = False, "terminal_conflict_kept"
            else:
                note = "terminal_conflict_repaired"
        if applied:
            await conn.execute(
                "UPDATE agent_run_attempts SET state = %(state)s,"
                " evidence_source = %(ev)s,"
                " platform_status = COALESCE(%(ps)s, platform_status),"
                " state_revision = state_revision + 1,"
                " started_at = COALESCE(started_at, CASE WHEN %(state)s = 'running'"
                "   THEN now() ELSE NULL END),"
                " ended_at = CASE WHEN %(state)s IN"
                "   ('succeeded','failed','cancelled','timed_out')"
                "   THEN COALESCE(ended_at, now()) ELSE ended_at END,"
                " cancel_confirmed = COALESCE(%(cc)s, cancel_confirmed),"
                " waiting_kind = COALESCE(%(wk)s, waiting_kind),"
                " last_progress_at = now(), updated_at = now()"
                " WHERE id = %(id)s AND tenant_id = %(tenant_id)s",
                {
                    "state": state,
                    "ev": evidence_source,
                    "ps": platform_status,
                    "cc": cancel_confirmed,
                    "wk": waiting_kind,
                    "id": attempt_id,
                    "tenant_id": scope.tenant_id,
                },
            )
        fresh = await (
            await conn.execute(
                "SELECT state, state_revision, platform_run_id FROM agent_run_attempts"
                " WHERE id = %(id)s",
                {"id": attempt_id},
            )
        ).fetchone()
        revision = int(fresh["state_revision"]) if fresh else 0
        await _write_event_in(
            conn,
            scope,
            event_type="run.state_observed",
            source="runtime",
            payload={
                "state": state,
                "evidence_source": evidence_source,
                "platform_status": platform_status or "",
                "state_revision": revision,
                "cancel_requested": bool(cancel_confirmed),
            },
            attempt_id=attempt_id,
            run_id=(
                str(fresh["platform_run_id"])
                if fresh and fresh["platform_run_id"]
                else None
            ),
            state_revision=revision,
            occurred_at=now,
        )
    return {
        "applied": applied,
        "note": note,
        "state": str(fresh["state"]) if fresh else state,
        "state_revision": revision,
    }


async def record_answer(
    scope: LedgerScope,
    *,
    attempt_id: str,
    message_id: str | None,
    content_version: int,
    confirmed: bool,
) -> dict[str, Any]:
    """可见回答版本：幂等（同尝试/消息/版本）；草稿与推理由调用方排除（事实+事件同事务）."""
    now = dt.datetime.now(dt.UTC)
    answer_id = str(uuid.uuid4())
    async with _tx(scope) as conn:
        cur = await conn.execute(
            "INSERT INTO agent_answers (id, tenant_id, user_id, attempt_id, message_id,"
            " content_version, partial, confirmed, confirmed_at, created_at)"
            " VALUES (%(id)s, %(tenant_id)s, %(user_id)s, %(attempt_id)s, %(message_id)s,"
            " %(cv)s, %(partial)s, %(confirmed)s, %(ca)s, now())"
            " ON CONFLICT (tenant_id, attempt_id, message_id, content_version)"
            "   WHERE message_id IS NOT NULL DO NOTHING"
            " RETURNING id, confirmed, partial",
            {
                "id": answer_id,
                "tenant_id": scope.tenant_id,
                "user_id": scope.user_id,
                "attempt_id": attempt_id,
                "message_id": message_id,
                "cv": content_version,
                "partial": not confirmed,
                "confirmed": confirmed,
                "ca": now if confirmed else None,
            },
        )
        row = await cur.fetchone()
        if row is None and message_id:
            row = await (
                await conn.execute(
                    "SELECT id, confirmed, partial FROM agent_answers WHERE tenant_id = %(t)s"
                    " AND attempt_id = %(a)s AND message_id = %(m)s AND content_version = %(cv)s",
                    {
                        "t": scope.tenant_id,
                        "a": attempt_id,
                        "m": message_id,
                        "cv": content_version,
                    },
                )
            ).fetchone()
            if row is not None and confirmed and not row["confirmed"]:
                await conn.execute(
                    "UPDATE agent_answers SET confirmed = true, partial = false,"
                    " confirmed_at = COALESCE(confirmed_at, now()) WHERE id = %(id)s",
                    {"id": row["id"]},
                )
                row = {"id": row["id"], "confirmed": True, "partial": False}
        if row is None:
            row = {"id": answer_id, "confirmed": confirmed, "partial": not confirmed}
        attempt = await (
            await conn.execute(
                "SELECT platform_run_id FROM agent_run_attempts WHERE id = %(a)s"
                " AND tenant_id = %(t)s",
                {"a": attempt_id, "t": scope.tenant_id},
            )
        ).fetchone()
        await _write_event_in(
            conn,
            scope,
            event_type="answer.confirmed" if confirmed else "answer.partial",
            source="runtime",
            payload={"answer_id": str(row["id"]), "content_version": content_version},
            attempt_id=attempt_id,
            answer_id=str(row["id"]),
            message_id=message_id,
            run_id=(
                str(attempt["platform_run_id"])
                if attempt and attempt["platform_run_id"]
                else None
            ),
            occurred_at=now,
        )
    return {
        "answer_id": str(row["id"]),
        "confirmed": bool(row["confirmed"]),
        "partial": bool(row["partial"]),
        "content_version": content_version,
    }


async def link_trace(
    scope: LedgerScope,
    *,
    attempt_id: str,
    provider: str,
    trace_id: str,
    project_scope: str = "default",
    primary: bool = False,
    link_source: str = "runtime_callback",
) -> dict[str, Any]:
    """Trace 多重映射：幂等；已有主 Trace 不被无规则替换（事实+事件同事务）."""
    now = dt.datetime.now(dt.UTC)
    link_id = str(uuid.uuid4())
    async with _tx(scope) as conn:
        count_cur = await conn.execute(
            "SELECT count(*) AS n FROM agent_trace_links WHERE tenant_id = %(t)s"
            " AND attempt_id = %(a)s AND is_primary",
            {"t": scope.tenant_id, "a": attempt_id},
        )
        has_primary = int((await count_cur.fetchone())["n"]) > 0
        is_primary = primary and not has_primary
        cur = await conn.execute(
            "INSERT INTO agent_trace_links (id, tenant_id, user_id, attempt_id, provider,"
            " project_scope, trace_id, is_primary, link_source, created_at)"
            " VALUES (%(id)s, %(t)s, %(u)s, %(a)s, %(p)s, %(ps)s, %(tr)s, %(prim)s, %(ls)s, now())"
            " ON CONFLICT (tenant_id, provider, project_scope, trace_id, attempt_id)"
            "   DO NOTHING RETURNING id, is_primary",
            {
                "id": link_id,
                "t": scope.tenant_id,
                "u": scope.user_id,
                "a": attempt_id,
                "p": provider,
                "ps": project_scope,
                "tr": trace_id,
                "prim": is_primary,
                "ls": link_source,
            },
        )
        row = await cur.fetchone()
        created = row is not None
        if row is None:
            row = await (
                await conn.execute(
                    "SELECT id, is_primary FROM agent_trace_links WHERE tenant_id = %(t)s"
                    " AND provider = %(p)s AND project_scope = %(ps)s AND trace_id = %(tr)s"
                    " AND attempt_id = %(a)s",
                    {
                        "t": scope.tenant_id,
                        "p": provider,
                        "ps": project_scope,
                        "tr": trace_id,
                        "a": attempt_id,
                    },
                )
            ).fetchone()
        if created:
            attempt = await (
                await conn.execute(
                    "SELECT platform_run_id FROM agent_run_attempts WHERE id = %(a)s"
                    " AND tenant_id = %(t)s",
                    {"a": attempt_id, "t": scope.tenant_id},
                )
            ).fetchone()
            await _write_event_in(
                conn,
                scope,
                event_type="trace.linked",
                source="runtime",
                payload={
                    "provider": provider,
                    "project_scope": project_scope,
                    "trace_id": trace_id,
                    "primary": bool(row["is_primary"]),
                    "link_source": link_source,
                },
                attempt_id=attempt_id,
                run_id=(
                    str(attempt["platform_run_id"])
                    if attempt and attempt["platform_run_id"]
                    else None
                ),
                occurred_at=now,
            )
    return {
        "trace_link_id": str(row["id"]) if row else None,
        "created": created,
        "is_primary": bool(row["is_primary"]) if row else False,
    }


async def _confirm_submission_in(
    conn: Any,
    scope: LedgerScope,
    *,
    attempt_id: str,
    run_id: str,
    submission_id: str | None,
) -> dict[str, Any] | None:
    """同事务确认单个操作的不可漂移映射，历史歧义不猜最新提交."""
    cur = await conn.execute(
        "SELECT id::text AS id, operation_kind, platform_run_id FROM agent_submissions"
        " WHERE tenant_id = %(t)s AND attempt_id = %(a)s"
        " AND ((%(sid)s::uuid IS NOT NULL AND id = %(sid)s::uuid)"
        " OR (%(sid)s::uuid IS NULL AND start_claimed_at IS NOT NULL"
        " AND admission_status <> 'confirmed')) FOR UPDATE",
        {"t": scope.tenant_id, "a": attempt_id, "sid": submission_id},
    )
    rows = await cur.fetchall()
    if len(rows) != 1:
        if submission_id is not None:
            raise LedgerBackendUnavailable("submission association invalid")
        return None
    row = dict(rows[0])
    if row["platform_run_id"] and str(row["platform_run_id"]) != run_id:
        raise LedgerBackendUnavailable("submission run mapping conflict")
    deleted = await (
        await conn.execute(
            "SELECT 1 FROM agent_thread_deletions d JOIN agent_submissions s"
            " ON s.tenant_id=d.tenant_id AND s.thread_id=d.thread_id"
            " WHERE s.id=%(sid)s AND s.tenant_id=%(t)s",
            {"sid": row["id"], "t": scope.tenant_id},
        )
    ).fetchone()
    if deleted is not None:
        raise ThreadDeleted("submission thread already deleted")
    await conn.execute(
        "UPDATE agent_submissions SET platform_run_id = %(r)s, admission_status = 'confirmed',"
        " updated_at = now() WHERE id = %(sid)s AND tenant_id = %(t)s",
        {"r": run_id, "sid": row["id"], "t": scope.tenant_id},
    )
    return row


async def record_resume(
    scope: LedgerScope,
    *,
    attempt_id: str,
    platform_run_id: str,
    submission_id: str | None = None,
) -> dict[str, Any]:
    """人工恢复：追加平台运行映射与恢复记录，不新建提问/尝试（事实+事件同事务）."""
    now = dt.datetime.now(dt.UTC)
    async with _tx(scope) as conn:
        row = await (
            await conn.execute(
                "SELECT id, request_id::text AS request_id, platform_run_id"
                " FROM agent_run_attempts WHERE id = %(id)s AND tenant_id = %(t)s FOR UPDATE",
                {"id": attempt_id, "t": scope.tenant_id},
            )
        ).fetchone()
        if row is None:
            return {"recorded": False, "reason": "attempt_not_found"}
        submission = await _confirm_submission_in(
            conn,
            scope,
            attempt_id=attempt_id,
            run_id=platform_run_id,
            submission_id=submission_id,
        )
        if submission is not None and submission["operation_kind"] != "resume":
            raise LedgerBackendUnavailable(
                "non-resume submission cannot replace mapping"
            )
        prior = await (
            await conn.execute(
                "SELECT id, resume_index FROM agent_run_resumes"
                " WHERE tenant_id = %(t)s AND attempt_id = %(a)s AND platform_run_id = %(r)s",
                {"t": scope.tenant_id, "a": attempt_id, "r": platform_run_id},
            )
        ).fetchone()
        if prior is not None:
            # 幂等：同一恢复运行重复上报只保留一条映射
            return {
                "recorded": True,
                "reason": "duplicate",
                "resume_index": int(prior["resume_index"]),
                "state": "running",
            }
        idx_cur = await conn.execute(
            "SELECT COALESCE(max(resume_index), 0) AS n FROM agent_run_resumes"
            " WHERE tenant_id = %(t)s AND attempt_id = %(a)s",
            {"t": scope.tenant_id, "a": attempt_id},
        )
        resume_index = int((await idx_cur.fetchone())["n"]) + 1
        await conn.execute(
            "INSERT INTO agent_run_resumes (id, tenant_id, user_id, attempt_id, resume_index,"
            " original_run_id, platform_run_id, created_at)"
            " VALUES (gen_random_uuid(), %(t)s, %(u)s, %(a)s, %(idx)s, %(orig)s, %(run)s, now())",
            {
                "t": scope.tenant_id,
                "u": scope.user_id,
                "a": attempt_id,
                "idx": resume_index,
                "orig": row["platform_run_id"],
                "run": platform_run_id,
            },
        )
        await conn.execute(
            "UPDATE agent_run_attempts SET platform_run_id = %(run)s, state = 'running',"
            " evidence_source = 'platform_read', state_revision = state_revision + 1,"
            " last_progress_at = now(), updated_at = now()"
            " WHERE id = %(id)s AND tenant_id = %(t)s",
            {"run": platform_run_id, "id": attempt_id, "t": scope.tenant_id},
        )
        await _write_event_in(
            conn,
            scope,
            event_type="run.resumed",
            source="runtime",
            payload={"resume_index": resume_index, "platform_run_id": platform_run_id},
            request_id=str(row["request_id"]),
            attempt_id=attempt_id,
            run_id=platform_run_id,
            occurred_at=now,
        )
    return {"recorded": True, "resume_index": resume_index, "state": "running"}


async def record_stop_intent(
    scope: LedgerScope,
    *,
    attempt_id: str,
    origin: str,
    phase: str,
    platform_run_id: str | None = None,
) -> dict[str, Any]:
    """停止意图独立保存（不改主状态；事实+事件同事务）."""
    now = dt.datetime.now(dt.UTC)
    async with _tx(scope) as conn:
        row = await (
            await conn.execute(
                "UPDATE agent_run_attempts SET cancel_requested = true, cancel_origin = %(o)s,"
                " cancel_phase = %(p)s, state_revision = state_revision + 1,"
                " platform_run_id = COALESCE(platform_run_id, %(run)s),"
                " last_progress_at = now(), updated_at = now()"
                " WHERE id = %(id)s AND tenant_id = %(t)s"
                " RETURNING id, state, request_id::text AS request_id, platform_run_id",
                {
                    "o": origin,
                    "p": phase,
                    "run": platform_run_id,
                    "id": attempt_id,
                    "t": scope.tenant_id,
                },
            )
        ).fetchone()
        if row is None:
            return {"recorded": False, "reason": "attempt_not_found"}
        await _write_event_in(
            conn,
            scope,
            event_type="run.cancel_requested",
            source="runtime",
            payload={
                "origin": origin,
                "phase": phase,
                **({"platform_run_id": platform_run_id} if platform_run_id else {}),
            },
            request_id=str(row["request_id"]),
            attempt_id=attempt_id,
            run_id=(
                str(row["platform_run_id"])
                if row["platform_run_id"]
                else platform_run_id
            ),
            occurred_at=now,
        )
    return {
        "recorded": True,
        "state": str(row["state"]),
        "origin": origin,
        "phase": phase,
    }


async def attach_platform_run(
    scope: LedgerScope,
    *,
    attempt_id: str,
    platform_run_id: str,
    evidence: str = "metadata_match",
    submission_id: str | None = None,
) -> dict[str, Any]:
    """把**由可信 metadata 定位到的**平台运行写回台账（补偿收敛，避免一律 no_platform_run_id）.

    只补"受理成功但 run_id 未确认"的映射：
    - 已有映射且一致 → 幂等返回；
    - 已有映射但不同 → 冲突（不覆盖）；
    - 状态推进到 running（若仍在 admitting/admission_unknown）。
    """
    now = dt.datetime.now(dt.UTC)
    async with _tx(scope) as conn:
        row = await (
            await conn.execute(
                "SELECT id, request_id::text AS request_id, platform_run_id, state,"
                " state_revision FROM agent_run_attempts"
                " WHERE id = %(aid)s AND tenant_id = %(t)s FOR UPDATE",
                {"aid": attempt_id, "t": scope.tenant_id},
            )
        ).fetchone()
        if row is None:
            return {"attached": False, "reason": "attempt_not_found"}
        submission = await _confirm_submission_in(
            conn,
            scope,
            attempt_id=attempt_id,
            run_id=platform_run_id,
            submission_id=submission_id,
        )
        existing = row["platform_run_id"]
        if existing is not None:
            if str(existing) == str(platform_run_id):
                return {"attached": False, "reason": "already_attached"}
            return {
                "attached": False,
                "reason": "mapping_conflict"
                if submission is None or submission["operation_kind"] == "resume"
                else "stale_submission",
            }
        try:
            await conn.execute(
                "UPDATE agent_run_attempts SET platform_run_id = %(run)s,"
                " state = CASE WHEN state IN ('admitting','admission_unknown')"
                "   THEN 'running' ELSE state END,"
                " evidence_source = 'platform_read', state_revision = state_revision + 1,"
                " started_at = COALESCE(started_at, now()),"
                " last_progress_at = now(), updated_at = now()"
                " WHERE id = %(aid)s AND tenant_id = %(t)s",
                {"run": platform_run_id, "aid": attempt_id, "t": scope.tenant_id},
            )
        except psycopg.errors.UniqueViolation:
            # 该平台运行已挂到别的尝试（唯一映射）：不覆盖，按冲突返回
            return {"attached": False, "reason": "run_already_linked"}
        fresh = await (
            await conn.execute(
                "SELECT state, state_revision FROM agent_run_attempts WHERE id = %(aid)s",
                {"aid": attempt_id},
            )
        ).fetchone()
        await _write_event_in(
            conn,
            scope,
            event_type="run.admission_confirmed",
            source="system",
            payload={
                "platform_run_id": platform_run_id,
                "admission_outcome": "confirmed",
                "admission_unknown_reason": evidence,
                "admitted_at": now.isoformat(),
            },
            request_id=str(row["request_id"]),
            attempt_id=attempt_id,
            run_id=platform_run_id,
            state_revision=int(fresh["state_revision"]) if fresh else None,
            occurred_at=now,
        )
    return {
        "attached": True,
        "state": str(fresh["state"]) if fresh else None,
        "platform_run_id": platform_run_id,
    }


def canonical_content_digest_v1(
    *, graph_id: str, operation_kind: str, content: Any
) -> str:  # noqa: ANN401
    """与 admin 侧一致的 v1 提交内容摘要（规范化 json）."""
    normalized = content.strip() if isinstance(content, str) else content
    canonical = json.dumps(
        {"graph_id": graph_id, "operation_kind": operation_kind, "content": normalized},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_content_digest_v2(
    *,
    graph_id: str,
    operation_kind: str,
    content: Any,  # noqa: ANN401
    capability_selection_digest: str,
) -> str:
    """与 admin 侧一致的 v2 提交内容摘要（包含选择快照摘要）."""
    normalized = content.strip() if isinstance(content, str) else content
    canonical = json.dumps(
        {
            "capability_selection_digest": capability_selection_digest,
            "content": normalized,
            "graph_id": graph_id,
            "operation_kind": operation_kind,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ 启动权裁决


async def claim_start(
    scope: LedgerScope,
    *,
    submission_id: str,
    attempt_id: str,
    thread_id: str,
    graph_id: str,
    operation_kind: str,
    content: Any,  # noqa: ANN401
    source: str,
) -> dict[str, Any]:
    """原子裁决"启动权"（R01）：**一次业务提交只能启动一次**，且必须与真实输入一致.

    裁决单元是 **submission**（不是 attempt）：initial/retry/regenerate 的 submission 与
    attempt 一一对应；人工 resume 复用同一 attempt 但每次是新 submission，因此各有独立
    槽位，resume 不会被 attempt 级的一次性消费误拒。

    返回 `status`：
    - `claimed`           本次调用赢得启动权
    - `already_started`   该提交已被消费（含并发）；调用方**不得**再创建运行
    - `already_confirmed` 已有平台运行映射（同提交或同尝试）：直接复用关联
    - `mismatch`          graph/操作/规范内容与预留不一致（防换图换正文）
    - `not_found`/`terminal`  预留不可用

    原子性由单条带条件的 UPDATE 保证（并发只有一个胜出）。
    """
    token = str(uuid.uuid4())
    async with _tx(scope) as conn:
        # 所有提交先锁同一个attempt，再锁submission，与确认/恢复写入保持锁顺序。
        await conn.execute(
            "SELECT id FROM agent_run_attempts WHERE id=%(a)s"
            " AND tenant_id=%(t)s FOR UPDATE",
            {"a": attempt_id, "t": scope.tenant_id},
        )
        base = await (
            await conn.execute(
                "SELECT s.id, s.request_id::text AS request_id, s.attempt_id,"
                " s.operation_kind AS submission_kind, s.start_claimed_at,"
                " s.digest_version, cs.selection_digest,"
                " COALESCE(s.platform_run_id, CASE WHEN s.operation_kind <> 'resume' THEN"
                " CASE WHEN EXISTS (SELECT 1 FROM agent_run_resumes x WHERE x.attempt_id = a.id"
                " AND x.tenant_id = a.tenant_id) THEN (SELECT x.original_run_id FROM agent_run_resumes x"
                " WHERE x.attempt_id = a.id AND x.tenant_id = a.tenant_id ORDER BY x.resume_index LIMIT 1)"
                " ELSE a.platform_run_id END END) AS submission_run_id,"
                " a.graph_id, a.operation_kind AS attempt_kind, a.state,"
                # F02：校验**本次提交**的规范摘要（恢复提交有自己的 resume 摘要），
                # 而不是初次 attempt 的摘要——否则恢复会被误判为 content mismatch。
                " CASE WHEN s.operation_kind = 'initial' THEN a.content_digest"
                " ELSE s.content_digest END AS content_digest,"
                " a.platform_run_id, r.thread_id"
                " FROM agent_submissions s"
                " JOIN agent_run_attempts a ON a.id = s.attempt_id"
                "   AND a.tenant_id = s.tenant_id"
                " JOIN agent_requests r ON r.id = s.request_id AND r.tenant_id = s.tenant_id"
                " LEFT JOIN agent_capability_selections cs ON cs.id = COALESCE(s.capability_selection_id, a.capability_selection_id)"
                "   AND cs.tenant_id = s.tenant_id"
                " WHERE s.id = %(sid)s AND s.tenant_id = %(t)s AND s.user_id = %(u)s FOR UPDATE OF a, s",
                {"sid": submission_id, "t": scope.tenant_id, "u": scope.user_id},
            )
        ).fetchone()
        if base is None:
            return {"status": "not_found"}
        if (
            await (
                await conn.execute(
                    "SELECT 1 FROM agent_thread_deletions WHERE tenant_id=%(t)s AND thread_id=%(th)s",
                    {"t": scope.tenant_id, "th": thread_id},
                )
            ).fetchone()
            is not None
        ):
            return {"status": "not_found"}
        if str(base["thread_id"]) != thread_id or str(base["attempt_id"]) != attempt_id:
            return {"status": "not_found"}
        if str(base["graph_id"]) != graph_id:
            return {
                "status": "mismatch",
                "detail": "graph",
                "reserved_graph_id": str(base["graph_id"]),
            }
        if str(base["submission_kind"]) != operation_kind:
            return {
                "status": "mismatch",
                "detail": "operation_kind",
                "reserved_operation_kind": str(base["submission_kind"]),
            }
        if base["content_digest"]:
            dv = base.get("digest_version")
            sel_digest = base.get("selection_digest")
            if dv == 2 and sel_digest:
                v2_digest = canonical_content_digest_v2(
                    graph_id=graph_id,
                    operation_kind=operation_kind,
                    content=content,
                    capability_selection_digest=str(sel_digest),
                )
                if str(base["content_digest"]) != v2_digest:
                    v1_digest = canonical_content_digest_v1(
                        graph_id=graph_id,
                        operation_kind=operation_kind,
                        content=content,
                    )
                    if str(base["content_digest"]) != v1_digest:
                        return {"status": "mismatch", "detail": "content"}
            else:
                v1_digest = canonical_content_digest_v1(
                    graph_id=graph_id, operation_kind=operation_kind, content=content
                )
                if str(base["content_digest"]) != v1_digest:
                    return {"status": "mismatch", "detail": "content"}
        if base["submission_run_id"]:
            return {
                "status": "already_confirmed",
                "platform_run_id": str(base["submission_run_id"]),
                "state": str(base["state"]),
            }
        if str(base["state"]) in TERMINAL_STATES:
            return {"status": "terminal", "state": str(base["state"])}
        if (
            str(base["submission_kind"]) == "resume"
            and base["start_claimed_at"] is None
            and str(base["state"]) != "waiting_input"
        ):
            return {"status": "mismatch", "detail": "resume_not_waiting"}
        cur = await conn.execute(
            "UPDATE agent_submissions SET start_claimed_at = now(),"
            " start_claim_token = %(token)s, start_claim_source = %(src)s, updated_at = now()"
            " WHERE id = %(sid)s AND tenant_id = %(t)s AND start_claimed_at IS NULL"
            " RETURNING id",
            {"token": token, "src": source, "sid": submission_id, "t": scope.tenant_id},
        )
        won = await cur.fetchone()
        if won is None:
            again = await (
                await conn.execute(
                    "SELECT start_claim_token, start_claim_source FROM agent_submissions"
                    " WHERE id = %(sid)s AND tenant_id = %(t)s",
                    {"sid": submission_id, "t": scope.tenant_id},
                )
            ).fetchone()
            return {
                "status": "already_started",
                "start_claim_token": (
                    str(again["start_claim_token"])
                    if again and again["start_claim_token"]
                    else None
                ),
                "start_claim_source": (
                    str(again["start_claim_source"])
                    if again and again["start_claim_source"]
                    else None
                ),
            }
        if str(base["submission_kind"]) == "resume":
            await conn.execute(
                "UPDATE agent_run_attempts SET state = CASE WHEN state IN"
                " ('admitting','admission_unknown','waiting_input') THEN 'queued' ELSE state END,"
                " state_revision = state_revision + 1, last_progress_at = now(), updated_at = now()"
                " WHERE id = %(aid)s AND tenant_id = %(t)s",
                {"aid": attempt_id, "t": scope.tenant_id},
            )
    return {"status": "claimed", "start_claim_token": token}


async def record_thread_deletion(
    scope: LedgerScope, *, thread_id: str, source: str = "native_delete"
) -> dict[str, Any]:
    """原生删除入口的**持久删除标记**（幂等）：立即禁读/禁新写，并排入清理任务.

    F04：BFF 删除此前已接台账，但直连原生 `DELETE /threads/{id}` 只删归属登记，
    删完仍可回读/新写。现在两条删除路径都先落**永久**标记（存在即视为已删除），
    清理失败可重试，且标记不因清理完成而失效。
    """
    now = dt.datetime.now(dt.UTC)
    async with _tx(scope) as conn:
        existing = await (
            await conn.execute(
                "SELECT id::text AS id FROM agent_thread_deletions"
                " WHERE tenant_id = %(t)s AND thread_id = %(th)s",
                {"t": scope.tenant_id, "th": thread_id},
            )
        ).fetchone()
        if existing is not None:
            return {
                "thread_id": thread_id,
                "deletion_state": "recorded",
                "created": False,
            }
        await conn.execute(
            "INSERT INTO agent_thread_deletions"
            " (id, tenant_id, thread_id, user_id, requested_by, state, attempts,"
            "  requested_at)"
            " VALUES (gen_random_uuid(), %(t)s, %(th)s, %(u)s, %(u)s, 'requested', 0, now())"
            " ON CONFLICT (tenant_id, thread_id) DO NOTHING",
            {"t": scope.tenant_id, "th": thread_id, "u": scope.user_id},
        )
        written = await _write_event_in(
            conn,
            scope,
            event_type="resource.deleted",
            source="server",
            consumers=("deletion_cleanup",),
            payload={
                "resource_type": "thread",
                "resource_id": thread_id,
                "deletion_state": "requested",
                "deleted_at": now.isoformat(),
            },
            thread_id=thread_id,
            occurred_at=now,
            allow_deleted=True,  # 删除登记本身必须能写入
        )
        if not written.get("written") and written.get("reason") not in (
            "duplicate",
            "payload_conflict",
        ):
            # 不要把"事件被契约拒绝"静默丢掉：那样删除标记在、但清理任务永远不会排队。
            raise LedgerBackendUnavailable(
                f"deletion event rejected: {written.get('reason')}:{written.get('detail')}"
            )
    return {"thread_id": thread_id, "deletion_state": "requested", "created": True}


async def thread_deletion_exists(scope: LedgerScope, *, thread_id: str) -> bool:
    """该会话是否已有（永久）删除标记：新受理/新运行/新事实一律拒绝."""
    row = await _run(
        "SELECT 1 FROM agent_thread_deletions"
        " WHERE tenant_id = %(t)s AND thread_id = %(th)s",
        {"t": scope.tenant_id, "th": thread_id},
        scope.as_guc(),
        fetch="one",
    )
    return row is not None


async def record_reconcile_run(
    scope: LedgerScope,
    *,
    actor: str,
    batches: int,
    candidates: int,
    calibrated: int,
    kept_unknown: int,
    released: int,
    status: str,
    cursor: dt.datetime | None = None,
    cursor_after: dt.datetime | None = None,
    scope_value: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """记录一次**有界**校准运行（续跑游标 + 计数），供运维审计与继续执行.

    F05：服务级校准必须有可执行、可续跑的落点：单次运行受 `max_batches` 限制，
    游标确保下一次从上次位置继续；本函数只写自身租户的运维台账，不含正文。
    """
    async with _tx(scope) as conn:
        row = await (
            await conn.execute(
                "INSERT INTO agent_ledger_reconcile_runs"
                " (tenant_id, actor, auth_kind, scope_kind, scope_value, cursor,"
                "  cursor_after, batches, candidates, calibrated, kept_unknown, released,"
                "  status, detail, finished_at)"
                " VALUES (%(t)s, %(actor)s, 'service', %(kind)s, %(value)s, %(cur)s,"
                "  %(cur_after)s, %(batches)s, %(cand)s, %(cal)s, %(unknown)s, %(rel)s,"
                "  %(status)s, %(detail)s, now())"
                " RETURNING id::text AS id",
                {
                    "t": scope.tenant_id,
                    "actor": actor,
                    "kind": "tenant" if not scope_value else "thread",
                    "value": scope_value,
                    "cur": cursor,
                    "cur_after": cursor_after,
                    "batches": batches,
                    "cand": candidates,
                    "cal": calibrated,
                    "unknown": kept_unknown,
                    "rel": released,
                    "status": status,
                    "detail": detail,
                },
            )
        ).fetchone()
    return {
        "id": str(row["id"]) if row else "",
        "status": status,
        "cursor_after": cursor_after.isoformat() if cursor_after else None,
    }


async def release_unstarted_claim(
    scope: LedgerScope, *, attempt_id: str, reason: str
) -> dict[str, Any]:
    """兼容旧运维调用，但拒绝依据空列表或时间重新开放启动权.

    当前平台没有可证明本次创建未提交的 fencing 凭证；保留裁决，避免重复业务。
    """
    async with _tx(scope) as conn:
        row = await (
            await conn.execute(
                "SELECT platform_run_id FROM agent_run_attempts WHERE id = %(aid)s"
                " AND tenant_id = %(t)s FOR UPDATE",
                {"aid": attempt_id, "t": scope.tenant_id},
            )
        ).fetchone()
        if row is None:
            return {"released": False, "reason": "attempt_not_found"}
        if row["platform_run_id"] is not None:
            return {"released": False, "reason": "mapping_present"}
    return {"released": False, "reason": "creation_outcome_unknown"}


# ------------------------------------------------------------------ 校准查询


async def attempts_needing_reconcile(
    scope: LedgerScope,
    *,
    stalled_seconds: int = 60,
    limit: int = 100,
    after_progress_at: dt.datetime | None = None,
    after_attempt_id: str | None = None,
    thread_id: str | None = None,
) -> list[dict[str, Any]]:
    """超过间隔无进展的活跃尝试，以及**终态但缺平台映射**的尝试（校准候选）.

    F05：只筛"活跃"会把"平台已 success、台账也 succeeded、但 platform_run_id 未持久化"
    的记录永久排除在补偿之外——那正是最需要回填映射的一类。因此在活跃候选之外，
    额外纳入"无 platform_run_id 的终态尝试"（同样有界、同样只做只读核对）。
    """
    rows = await _run(
        "SELECT a.id::text AS attempt_id, a.request_id::text AS request_id,"
        " a.platform_run_id, a.state, a.state_revision, r.thread_id, r.graph_id,"
        " (a.platform_run_id IS NULL AND a.state = ANY(%(terminal)s)) AS missing_mapping,"
        " CASE WHEN pending.admission_status <> 'confirmed' OR"
        " (pending.operation_kind = 'resume' AND pending.platform_run_id IS DISTINCT FROM a.platform_run_id)"
        " THEN pending.id::text ELSE NULL END AS pending_submission_id,"
        " pending.operation_kind AS pending_operation_kind,"
        " a.last_progress_at,"
        " EXTRACT(EPOCH FROM (now() - a.last_progress_at))::int AS stalled_seconds"
        " FROM agent_run_attempts a"
        " JOIN agent_requests r ON r.id = a.request_id AND r.tenant_id = a.tenant_id"
        " LEFT JOIN LATERAL (SELECT s.id, s.operation_kind, s.admission_status, s.platform_run_id"
        " FROM agent_submissions s WHERE s.attempt_id = a.id AND s.tenant_id = a.tenant_id"
        " AND s.start_claimed_at IS NOT NULL ORDER BY s.start_claimed_at DESC, s.id DESC LIMIT 1) pending ON true"
        " WHERE a.tenant_id = %(t)s"
        "   AND a.last_progress_at < now() - make_interval(secs => %(stalled)s)"
        "   AND (a.state = ANY(%(states)s)"
        "        OR (a.platform_run_id IS NULL AND a.state = ANY(%(terminal)s)))"
        # 有界续跑游标：只取"上次批次处理位置之后"的候选（避免重复处理同一批）
        "   AND (%(after)s::timestamptz IS NULL OR (a.last_progress_at, a.id) >"
        " (%(after)s::timestamptz, COALESCE(%(after_id)s::uuid,"
        " '00000000-0000-0000-0000-000000000000'::uuid)))"
        # 按线程收窄（与 rs-v1 票据的线程清单一致）：只核对被授权的那一个会话，
        # 避免被其它历史积压占满批次而漏掉目标
        "   AND (%(thread)s::text IS NULL OR r.thread_id = %(thread)s)"
        " ORDER BY a.last_progress_at ASC, a.id ASC LIMIT %(limit)s",
        {
            "t": scope.tenant_id,
            "states": list(ACTIVE_STATES),
            "terminal": list(TERMINAL_STATES),
            "stalled": stalled_seconds,
            "after": after_progress_at,
            "after_id": after_attempt_id,
            "thread": thread_id,
            "limit": limit,
        },
        scope.as_guc(),
        fetch="all",
    )
    return [dict(r) for r in rows or []]


async def attempt_reservation(
    scope: LedgerScope, *, attempt_id: str, thread_id: str, allow_terminal: bool = False
) -> dict[str, Any] | None:
    """校验"台账受理预留"是否存在且属于本人本线程（原生入口的运行准入依据）.

    返回可信尝试行；默认拒绝终态。准入可读取终态以返回原提交的幂等结果，
    是否允许新建仍由 claim_start 裁决，终态绝不会获得新的启动权。
    `attempt_id` 是**非秘密**标识，但伪造无意义：必须真实存在于台账且归属可信身份。
    """
    row = await _run(
        "SELECT a.id::text AS attempt_id, a.state, a.request_id::text AS request_id,"
        " a.platform_run_id, a.business_code, a.business_policy_version, r.thread_id"
        " FROM agent_run_attempts a"
        " JOIN agent_requests r ON r.id = a.request_id AND r.tenant_id = a.tenant_id"
        " WHERE a.id = %(aid)s AND a.tenant_id = %(t)s AND a.user_id = %(u)s"
        "   AND r.thread_id = %(th)s",
        {
            "aid": attempt_id,
            "t": scope.tenant_id,
            "u": scope.user_id,
            "th": thread_id,
        },
        scope.as_guc(),
        fetch="one",
    )
    if row is None:
        return None
    if str(row["state"]) in TERMINAL_STATES and not allow_terminal:
        return None
    return dict(row)


async def fetch_attempt_lifecycle(
    scope: LedgerScope, *, attempt_id: str, thread_id: str
) -> dict[str, Any] | None:
    """只读尝试生命周期事实：状态、修订、取消意图/确认与结束时间.

    发布前使用：不新增事件或表，复用既有 `agent_run_attempts` 列；归属不一致
    返回 None，调用方必须失败关闭。
    """
    row = await _run(
        "SELECT a.id::text AS attempt_id, a.state, a.state_revision,"
        " a.cancel_requested, a.cancel_confirmed, a.ended_at,"
        " a.business_code, a.business_policy_version, a.platform_run_id,"
        " a.request_id::text AS request_id, r.thread_id"
        " FROM agent_run_attempts a"
        " JOIN agent_requests r ON r.id = a.request_id AND r.tenant_id = a.tenant_id"
        " WHERE a.id = %(aid)s AND a.tenant_id = %(t)s AND a.user_id = %(u)s"
        "   AND r.thread_id = %(th)s",
        {
            "aid": attempt_id,
            "t": scope.tenant_id,
            "u": scope.user_id,
            "th": thread_id,
        },
        scope.as_guc(),
        fetch="one",
    )
    return dict(row) if row else None


async def content_digest(*, graph_id: str, operation_kind: str, content: Any) -> str:  # noqa: ANN401
    """与 admin 侧一致的提交内容摘要."""
    normalized = content.strip() if isinstance(content, str) else content
    canonical = json.dumps(
        {"graph_id": graph_id, "operation_kind": operation_kind, "content": normalized},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def fetch_capability_selection_snapshot(
    scope: LedgerScope, *, attempt_id: str, submission_id: str | None = None
) -> dict[str, Any] | None:
    """从业务数据库读取已受理的不可变能力选择快照（任务 4.1）."""
    sql = (
        "SELECT s.id::text AS id, s.request_id::text AS request_id,"
        " s.membership_id::text AS membership_id, r.thread_id::text AS thread_id,"
        " s.attempt_id::text AS attempt_id, s.submission_id::text AS submission_id,"
        " s.graph_id, s.skill_mode, s.skill_id, s.mcp_mode, s.tool_ids, s.catalog_version,"
        " s.material_version, s.selection_digest, s.digest_version, s.selection_source,"
        " s.validation_time, s.created_at"
        " FROM agent_capability_selections s JOIN agent_requests r"
        " ON r.id = s.request_id AND r.tenant_id = s.tenant_id"
        " WHERE s.attempt_id = %(aid)s AND s.tenant_id = %(t)s AND s.user_id = %(u)s"
        " AND s.membership_id = %(m)s"
    )
    params = {
        "aid": attempt_id,
        "t": scope.tenant_id,
        "u": scope.user_id,
        "m": scope.membership_id,
    }
    if submission_id:
        sql += " AND s.submission_id = %(sid)s"
        params["sid"] = submission_id
    else:
        sql += " ORDER BY s.created_at DESC LIMIT 1"
    row = await _run(
        sql,
        params,
        scope.as_guc(),
        fetch="one",
    )
    if row is None:
        return None
    return dict(row)
