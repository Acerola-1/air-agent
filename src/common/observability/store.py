"""观测域存储：观测事实 + runtime.* 事件 + outbox 同事务持久化.

设计约束（change design §2 / spec agent-model-observability §观测故障有界降级）：

- 身份与关联只来自可信 ObservationContext；attempt/submission 关联由数据库
  复合外键强制同租户（0012_observability）。
- 写入走单一事务（观测行 + 事件 + outbox 同提交），故障注入缝 `_insert_outbox`
  与台账 store 同约定：事务内任何一步失败整笔回滚、零可见。
- 结束更新幂等且不回退强终态：succeeded/failed/cancelled/timed_out 一经写入，
  迟到的 started/重复结束不覆盖；仅 unknown 可被终态升级。
- 持久化只保存安全计数 / 时长 / 非秘密模型名 / 规范类别，不存自由 kwargs、
  错误正文、工具参数与结果。
- 事件经共享契约校验（runtime 五类型已启用），payload 遵守白名单。

失败语义：后端不可用抛 `ObservabilityBackendUnavailable`，采集层捕获后降级
（不阻断业务答案）；本模块不吞异常，由 collector 决定丢弃/重试。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import threading
import uuid
import weakref
from types import SimpleNamespace
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from common.event_ledger.contract import canonical_digest, validate_event
from common.observability.context import ObservationContext
from common.observability.pricing import compute_cost

_POOLS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_POOL_LOCK = threading.Lock()

# 强终态：一经写入不回退（spec：终态证据不因迟到 started 回退）
STRONG_TERMINAL = ("succeeded", "failed", "cancelled", "timed_out")


class ObservabilityBackendUnavailable(Exception):
    """观测后端不可用：调用方（collector）降级记录，业务不受影响."""


class ThreadDeleted(Exception):
    """目标会话已永久删除：拒绝写入新观测事实（删除优先）."""


def _uri() -> str:
    """观测写入连接：与台账运行写入共用 RUNTIME_DATABASE_URI（同库同角色）."""
    uri = os.environ.get("RUNTIME_DATABASE_URI", "")
    if not uri:
        raise ObservabilityBackendUnavailable("RUNTIME_DATABASE_URI not configured")
    return uri


async def get_pool() -> AsyncConnectionPool:
    """惰性共享连接池（与台账 store 独立，便于单独关闭/测试）."""
    loop = asyncio.get_running_loop()
    with _POOL_LOCK:
        if loop not in _POOLS:
            pool = AsyncConnectionPool(
                conninfo=_uri(),
                min_size=0,
                max_size=8,
                open=False,
                timeout=1.5,
                kwargs={"row_factory": dict_row, "autocommit": False},
            )
            _POOLS[loop] = (pool, asyncio.Lock())
        pool, lock = _POOLS[loop]
    async with lock:
        if pool.closed:
            await pool.open(wait=True, timeout=1.5)
    return pool


async def close_pool() -> None:
    """仅关闭当前事件循环的连接池，避免采集线程与图线程交叉使用."""
    with _POOL_LOCK:
        state = _POOLS.pop(asyncio.get_running_loop(), None)
    if state:
        await state[0].close()


def _enabled() -> bool:
    """观测采集总开关（默认关闭；迁移/配置就绪后由部署显式打开）."""
    return os.environ.get("AGENT_OBSERVABILITY_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def observability_enabled() -> bool:
    """供接线层查询的开关；关闭时所有采集函数直接返回 not_collected."""
    return _enabled()


async def record_capability_fact(
    ctx: ObservationContext, payload: dict[str, Any]
) -> dict[str, Any]:
    """Persist a capability phase through the existing event/outbox transaction."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            for key, value in ctx.as_guc().items():
                await conn.execute(
                    "SELECT set_config(%s, %s, true)", (key, value or "")
                )
            await _deleted_guard(conn, ctx)
            return await _write_event_in(
                conn,
                ctx,
                event_type="runtime.capability",
                payload=payload,
                occurred_at=dt.datetime.now(dt.UTC),
            )


async def _write_event_in(
    conn: Any,  # noqa: ANN401 - 事务内连接
    ctx: ObservationContext,
    *,
    event_type: str,
    payload: dict[str, Any],
    occurred_at: dt.datetime,
) -> dict[str, Any]:
    """在调用方事务内写 runtime.* 事件 + outbox（复用共享契约校验）.

    消费者：observability_projection（独立 consumer_version）；投递不改变事件
    语义，失败可见由 outbox attempt_count 记录。
    """
    eid = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{ctx.tenant_id}:{event_type}:{canonical_digest(payload)}",
        )
    )
    event: dict[str, Any] = {
        "schema_version": "1.0",
        "event_id": eid,
        "event_type": event_type,
        "occurred_at": occurred_at.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[
            :-3
        ]
        + "Z",
        "source": "runtime",
        "tenant_id": ctx.tenant_id,
        "user_id": ctx.user_id,
        "membership_id": ctx.membership_id,
        "payload": payload,
    }
    for key, value in (
        ("thread_id", ctx.thread_id),
        ("request_id", ctx.request_id),
        ("attempt_id", ctx.attempt_id),
        ("run_id", ctx.platform_run_id),
    ):
        if value:
            event[key] = value
    result = validate_event(event)
    if not result.valid:
        raise ValueError(f"observation event contract rejected: {result.reason}")
    cur = await conn.execute(
        "INSERT INTO agent_events (event_id, tenant_id, user_id, membership_id, schema_version,"
        " event_type, source, occurred_at, received_at, thread_id, request_id, attempt_id, run_id,"
        " payload, payload_digest, created_at)"
        " VALUES (%(event_id)s, %(tenant_id)s, %(user_id)s, %(membership_id)s, '1.0',"
        " %(event_type)s, 'runtime', %(occurred_at)s, now(), %(thread_id)s, %(request_id)s,"
        " %(attempt_id)s, %(run_id)s, %(payload)s::jsonb, %(digest)s, now())"
        " ON CONFLICT (tenant_id, event_id) DO NOTHING RETURNING event_id",
        {
            "event_id": eid,
            "tenant_id": ctx.tenant_id,
            "user_id": ctx.user_id,
            "membership_id": ctx.membership_id,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "thread_id": ctx.thread_id,
            "request_id": ctx.request_id,
            "attempt_id": ctx.attempt_id,
            "run_id": ctx.platform_run_id,
            "payload": json.dumps(payload, ensure_ascii=False),
            "digest": canonical_digest(payload),
        },
    )
    row = await cur.fetchone()
    if row is None:
        return {"written": False, "reason": "duplicate", "event_id": eid}
    await conn.execute(
        "INSERT INTO agent_outbox (tenant_id, event_id, consumer, available_at, status,"
        " attempt_count, thread_id, created_at, updated_at)"
        " VALUES (%(tenant_id)s, %(event_id)s, 'observability_projection', now(), 'pending',"
        " 0, %(thread_id)s, now(), now())"
        " ON CONFLICT (tenant_id, event_id, consumer) DO NOTHING",
        {
            "tenant_id": ctx.tenant_id,
            "event_id": eid,
            "thread_id": ctx.thread_id,
        },
    )
    return {"written": True, "reason": "ok", "event_id": eid}


async def _deleted_guard(conn: Any, ctx: ObservationContext) -> None:  # noqa: ANN401
    """删除优先：已永久删除的会话拒绝新观测事实."""
    if ctx.thread_id:
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"thread:{ctx.tenant_id}:{ctx.thread_id}",),
        )
    fact = await (
        await conn.execute(
            "SELECT a.user_id::text, a.request_id::text, a.graph_id, r.thread_i"
            "d FROM agent_run_attempts a JOIN agent_requests r ON r.id=a.reques"
            "t_id AND r.tenant_id=a.tenant_id WHERE a.id=%s AND a.tenant_id=%s",
            (ctx.attempt_id, ctx.tenant_id),
        )
    ).fetchone()
    if (
        fact is None
        or fact["user_id"] != ctx.user_id
        or fact["request_id"] != ctx.request_id
        or fact["graph_id"] != ctx.graph_id
        or (ctx.thread_id and fact["thread_id"] != ctx.thread_id)
    ):
        raise ValueError("invalid observation association")
    if ctx.submission_id:
        submission = await (
            await conn.execute(
                "SELECT 1 FROM agent_submissions WHERE id=%s AND tenant_id=%s A"
                "ND user_id=%s AND request_id=%s AND attempt_id=%s",
                (
                    ctx.submission_id,
                    ctx.tenant_id,
                    ctx.user_id,
                    ctx.request_id,
                    ctx.attempt_id,
                ),
            )
        ).fetchone()
        if submission is None:
            raise ValueError("invalid observation submission")
    if not ctx.thread_id:
        return
    row = await (
        await conn.execute(
            "SELECT 1 FROM agent_thread_deletions"
            " WHERE tenant_id = %(t)s AND thread_id = %(th)s",
            {"t": ctx.tenant_id, "th": ctx.thread_id},
        )
    ).fetchone()
    if row is not None:
        raise ThreadDeleted(f"thread {ctx.thread_id} already deleted")


# ------------------------------------------------------------------ 模型观测


async def record_model_observation(
    ctx: ObservationContext,
    *,
    observation_id: str,
    logical_call_id: str,
    role: str,
    model: str,
    provider: str | None,
    status: str,
    started_at: dt.datetime | None = None,
    ended_at: dt.datetime | None = None,
    duration_ms: int | None = None,
    ttft_ms: int | None = None,
    first_piece_kind: str | None = None,
    usage: dict[str, int | None] | None = None,
    measurement_status: str = "not_collected",
    usage_source: str | None = None,
    retry_index: int = 0,
    parent_observation_id: str | None = None,
) -> dict[str, Any]:
    """写入/更新一次模型尝试观测（同事务：观测行 + runtime.model_usage 事件）.

    幂等与终态保护：
    - 新观测（INSERT）成功返回 written=True；
    - 已存在的观测按状态秩更新：强终态不被迟到证据回退；unknown 可升级；
    - usage 以"最终修订优先"合并：非空字段覆盖空字段；同一物理观测的流块/
      结束回调重复提供一致 usage 不重复累计（直接覆盖同值）。
    """
    try:
        pool = await get_pool()
    except Exception as exc:  # noqa: BLE001
        raise ObservabilityBackendUnavailable(
            f"pool unavailable: {type(exc).__name__}"
        ) from exc
    now = dt.datetime.now(dt.UTC)
    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                for key, value in ctx.as_guc().items():
                    await conn.execute(
                        "SELECT set_config(%s, %s, true)", (key, value or "")
                    )
                await _deleted_guard(conn, ctx)
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"observation:{ctx.tenant_id}:{observation_id}",),
                )
                existing = await (
                    await conn.execute(
                        "SELECT status, state_revision, measurement_status,"
                        " input_tokens, output_tokens, total_tokens, cache_read_tokens,"
                        " cache_write_tokens, reasoning_tokens, ttft_ms, duration_ms, ended_at"
                        " FROM agent_model_observations"
                        " WHERE tenant_id = %(t)s AND observation_id = %(o)s FOR UPDATE",
                        {"t": ctx.tenant_id, "o": observation_id},
                    )
                ).fetchone()
                if existing is None:
                    await conn.execute(
                        "INSERT INTO agent_model_observations"
                        " (id, tenant_id, user_id, attempt_id, submission_id, request_id,"
                        "  thread_id, platform_run_id, graph_id, observation_id,"
                        "  logical_call_id, parent_observation_id, role, model, provider,"
                        "  status, started_at, ended_at, duration_ms, ttft_ms,"
                        "  first_piece_kind, input_tokens, output_tokens, total_tokens,"
                        "  cache_read_tokens, cache_write_tokens, reasoning_tokens,"
                        "  measurement_status, usage_source, retry_index, state"
                        "_revision, created_at, updated_at)"
                        " VALUES (gen_random_uuid(), %(tenant)s, %(user)s, %(attempt)s,"
                        "  %(submission)s, %(request)s, %(thread)s, %(run)s, %(graph)s,"
                        "  %(observation)s, %(logical)s, %(parent)s, %(role)s, %(model)s,"
                        "  %(provider)s, %(status)s, %(started)s, %(ended)s, %(duration)s,"
                        "  %(ttft)s, %(fpk)s, %(in)s, %(out)s, %(total)s, %(cr)s, %(cw)s,"
                        "  %(rt)s, %(ms)s, %(usrc)s, %(retry)s, 1, now(), now())",
                        {
                            "tenant": ctx.tenant_id,
                            "user": ctx.user_id,
                            "attempt": ctx.attempt_id,
                            "submission": ctx.submission_id,
                            "request": ctx.request_id,
                            "thread": ctx.thread_id,
                            "run": ctx.platform_run_id,
                            "graph": ctx.graph_id or "unknown",
                            "observation": observation_id,
                            "logical": logical_call_id,
                            "parent": parent_observation_id,
                            "role": role,
                            "model": model,
                            "provider": provider,
                            "status": status,
                            "started": started_at or now,
                            "ended": ended_at,
                            "duration": duration_ms,
                            "ttft": ttft_ms,
                            "fpk": first_piece_kind,
                            "in": (usage or {}).get("input_tokens"),
                            "out": (usage or {}).get("output_tokens"),
                            "total": (usage or {}).get("total_tokens"),
                            "cr": (usage or {}).get("cache_read_tokens"),
                            "cw": (usage or {}).get("cache_write_tokens"),
                            "rt": (usage or {}).get("reasoning_tokens"),
                            "ms": measurement_status,
                            "usrc": usage_source,
                            "retry": retry_index,
                        },
                    )
                    revision = 1
                else:
                    # 终态保护：强终态不被非终态/重复结束回退
                    current_status = str(existing["status"])
                    if current_status in STRONG_TERMINAL and status != current_status:
                        return {
                            "written": False,
                            "reason": "terminal_kept",
                            "observation_id": observation_id,
                        }
                    incoming = usage or {}
                    if any(
                        v is not None
                        and existing.get(k) is not None
                        and existing[k] != v
                        for k, v in incoming.items()
                    ):
                        # 逐块累积在适配器完成；存储层不接受两个不同的最终usage。
                        measurement_status = "invalid_usage"
                        incoming = {
                            k: v for k, v in incoming.items() if existing.get(k) is None
                        }
                    if (
                        current_status == status
                        and all(existing.get(k) == v for k, v in incoming.items())
                        and existing["measurement_status"] == measurement_status
                        and (ttft_ms is None or existing["ttft_ms"] == ttft_ms)
                        and (
                            duration_ms is None
                            or existing["duration_ms"] == duration_ms
                        )
                        and (ended_at is None or existing["ended_at"] == ended_at)
                    ):
                        return {
                            "written": False,
                            "reason": "duplicate",
                            "observation_id": observation_id,
                        }
                    if existing[
                        "measurement_status"
                    ] == "measured" and measurement_status in (
                        "usage_missing",
                        "not_collected",
                    ):
                        measurement_status = "measured"
                    merged_usage = {
                        "input_tokens": existing["input_tokens"],
                        "output_tokens": existing["output_tokens"],
                        "total_tokens": existing["total_tokens"],
                        "cache_read_tokens": existing["cache_read_tokens"],
                        "cache_write_tokens": existing["cache_write_tokens"],
                        "reasoning_tokens": existing["reasoning_tokens"],
                    }
                    for key, value in incoming.items():
                        if value is not None:
                            merged_usage[key] = value  # 最终修订优先，不累计
                    await conn.execute(
                        "UPDATE agent_model_observations SET"
                        " model = %(model)s, provider = COALESCE(%(provider)s, "
                        "provider), status = %(status)s, ended_at = COALESCE(%("
                        "ended)s, ended_at),"
                        " duration_ms = COALESCE(%(duration)s, duration_ms),"
                        " ttft_ms = COALESCE(%(ttft)s, ttft_ms),"
                        " first_piece_kind = COALESCE(%(fpk)s, first_piece_kind),"
                        " input_tokens = %(in)s, output_tokens = %(out)s,"
                        " total_tokens = %(total)s, cache_read_tokens = %(cr)s,"
                        " cache_write_tokens = %(cw)s, reasoning_tokens = %(rt)s,"
                        " measurement_status = %(ms)s, usage_source = COALESCE("
                        "%(usrc)s, usage_source),"
                        " state_revision = state_revision + 1, updated_at = now()"
                        " WHERE tenant_id = %(tenant)s AND observation_id = %(observation)s",
                        {
                            "status": status,
                            "model": model,
                            "provider": provider,
                            "ended": ended_at,
                            "duration": duration_ms,
                            "ttft": ttft_ms,
                            "fpk": first_piece_kind,
                            "in": merged_usage.get("input_tokens"),
                            "out": merged_usage.get("output_tokens"),
                            "total": merged_usage.get("total_tokens"),
                            "cr": merged_usage.get("cache_read_tokens"),
                            "cw": merged_usage.get("cache_write_tokens"),
                            "rt": merged_usage.get("reasoning_tokens"),
                            "ms": measurement_status,
                            "usrc": usage_source,
                            "tenant": ctx.tenant_id,
                            "observation": observation_id,
                        },
                    )
                    revision = int(existing["state_revision"]) + 1
                event_payload: dict[str, Any] = {
                    "attempt_id": ctx.attempt_id,
                    "observation_id": observation_id,
                    "model": model,
                    "measurement_status": measurement_status,
                }
                if provider:
                    event_payload["provider"] = provider
                if role:
                    event_payload["role"] = role
                if logical_call_id:
                    event_payload["logical_call_id"] = logical_call_id
                if ctx.submission_id:
                    event_payload["submission_id"] = ctx.submission_id
                if usage:
                    event_usage: dict[str, Any] = dict(usage)
                elif existing is not None:
                    event_usage = {k: v for k, v in incoming.items() if v is not None}
                else:
                    event_usage = {}
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                    "reasoning_tokens",
                ):
                    value = event_usage.get(key)
                    if value is not None:
                        event_payload[key] = value
                if usage_source:
                    event_payload["usage_source"] = usage_source
                event_payload["state_revision"] = revision
                written = await _write_event_in(
                    conn,
                    ctx,
                    event_type="runtime.model_usage",
                    payload=event_payload,
                    occurred_at=now,
                )
                if ttft_ms is not None:
                    await _write_event_in(
                        conn,
                        ctx,
                        event_type="runtime.ttft",
                        payload={
                            "attempt_id": ctx.attempt_id,
                            "observation_id": observation_id,
                            "ttft_ms": ttft_ms,
                            "clock": "monotonic",
                            "first_piece_kind": first_piece_kind or "unknown",
                            "measurement_status": "measured",
                        },
                        occurred_at=now,
                    )
                if status in STRONG_TERMINAL:
                    await _price_model_in(
                        conn,
                        ctx,
                        observation_id,
                        model=model,
                        provider=provider,
                        called_at=started_at or now,
                        usage=usage or {},
                        measurement_status=measurement_status,
                    )

    except ThreadDeleted:
        raise
    except psycopg.Error as exc:
        raise ObservabilityBackendUnavailable(
            f"observation rejected: {type(exc).__name__}"
        ) from exc
    return {
        "written": True,
        "reason": "ok",
        "observation_id": observation_id,
        "event": written,
    }


# ------------------------------------------------------------------ Span / 工具观测


async def record_span(
    ctx: ObservationContext,
    *,
    observation_id: str,
    parent_observation_id: str | None,
    span_kind: str,
    span_name: str,
    stage: str | None = None,
    status: str | None = None,
    duration_ms: int | None = None,
    skill_id: str | None = None,
    skill_version: str | None = None,
    selection_source: str | None = None,
    model_observation_id: str | None = None,
    selection_id: str | None = None,
    selection_digest: str | None = None,
    submission_id: str | None = None,
) -> dict[str, Any]:
    """写入一条能力 span（Skill 阶段 matched/selected/loaded/executed 各一行）."""
    try:
        pool = await get_pool()
    except Exception as exc:  # noqa: BLE001
        raise ObservabilityBackendUnavailable(
            f"pool unavailable: {type(exc).__name__}"
        ) from exc
    now = dt.datetime.now(dt.UTC)
    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                for key, value in ctx.as_guc().items():
                    await conn.execute(
                        "SELECT set_config(%s, %s, true)", (key, value or "")
                    )
                await _deleted_guard(conn, ctx)
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"observation:{ctx.tenant_id}:{observation_id}",),
                )
                old = await (
                    await conn.execute(
                        "SELECT 1 FROM agent_capability_spans WHERE tenant_id=%"
                        "s AND observation_id=%s",
                        (ctx.tenant_id, observation_id),
                    )
                ).fetchone()
                if old:
                    return {"written": False, "reason": "duplicate"}

                await conn.execute(
                    "INSERT INTO agent_capability_spans"
                    " (id, tenant_id, user_id, attempt_id, observation_id,"
                    "  parent_observation_id, span_kind, span_name, stage, status,"
                    "  duration_ms, skill_id, skill_version, selection_source,"
                    "  model_observation_id, started_at, created_at)"
                    " VALUES (gen_random_uuid(), %(tenant)s, %(user)s, %(attempt)s,"
                    "  %(observation)s, %(parent)s, %(kind)s, %(name)s, %(stage)s,"
                    "  %(status)s, %(duration)s, %(skill)s, %(sver)s, %(sel)s,"
                    "  %(mobs)s, %(started)s, now())"
                    " ON CONFLICT (tenant_id, observation_id) DO UPDATE SET"
                    "  status = COALESCE(EXCLUDED.status, agent_capability_spans.status),"
                    "  duration_ms = COALESCE(EXCLUDED.duration_ms, agent_capab"
                    "ility_spans.duration_ms),"
                    "  stage = COALESCE(EXCLUDED.stage, agent_capability_spans.stage)",
                    {
                        "tenant": ctx.tenant_id,
                        "user": ctx.user_id,
                        "attempt": ctx.attempt_id,
                        "observation": observation_id,
                        "parent": parent_observation_id,
                        "kind": span_kind,
                        "name": span_name,
                        "stage": stage,
                        "status": status,
                        "duration": duration_ms,
                        "skill": skill_id,
                        "sver": skill_version,
                        "sel": selection_source,
                        "mobs": model_observation_id,
                        "started": now,
                    },
                )
                payload: dict[str, Any] = {
                    "attempt_id": ctx.attempt_id,
                    "observation_id": observation_id,
                    "span_kind": span_kind,
                }
                if span_name:
                    payload["span_name"] = span_name
                if stage:
                    payload["stage"] = stage
                if status:
                    payload["status"] = status
                if duration_ms is not None:
                    payload["duration_ms"] = duration_ms
                if skill_id:
                    payload["skill_id"] = skill_id
                if skill_version:
                    payload["skill_version"] = skill_version
                if selection_source:
                    payload["selection_source"] = selection_source
                if selection_id:
                    payload["selection_id"] = selection_id
                if selection_digest:
                    payload["selection_digest"] = selection_digest
                if submission_id:
                    payload["submission_id"] = submission_id
                written = await _write_event_in(
                    conn,
                    ctx,
                    event_type="runtime.span",
                    payload=payload,
                    occurred_at=now,
                )
    except ThreadDeleted:
        raise
    except psycopg.Error as exc:
        raise ObservabilityBackendUnavailable(
            f"span rejected: {type(exc).__name__}"
        ) from exc
    return {"written": True, "reason": "ok", "event": written}


async def record_tool_attempt(
    ctx: ObservationContext,
    *,
    observation_id: str,
    logical_call_id: str,
    tool_name: str,
    service_key: str | None,
    retry_index: int,
    status: str,
    duration_ms: int | None = None,
    tool_call_id: str | None = None,
    skill_id: str | None = None,
    fallback_kind: str | None = None,
    error_category: str | None = None,
    network_attempted: bool | None = None,
    started_at: dt.datetime | None = None,
) -> dict[str, Any]:
    """记录一次物理工具尝试（逻辑调用行 + 尝试行 + runtime.tool_call 事件）.

    双层不重复约定：物理尝试的所有者是本函数（tool_wrappers 层调用）；
    LangChain 回调层只写 parent span，不写 attempt。
    """
    try:
        pool = await get_pool()
    except Exception as exc:  # noqa: BLE001
        raise ObservabilityBackendUnavailable(
            f"pool unavailable: {type(exc).__name__}"
        ) from exc
    now = dt.datetime.now(dt.UTC)
    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                for key, value in ctx.as_guc().items():
                    await conn.execute(
                        "SELECT set_config(%s, %s, true)", (key, value or "")
                    )
                await _deleted_guard(conn, ctx)
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"observation:{ctx.tenant_id}:{observation_id}",),
                )
                old = await (
                    await conn.execute(
                        "SELECT 1 FROM agent_tool_call_attempts WHERE tenant_id"
                        "=%s AND observation_id=%s",
                        (ctx.tenant_id, observation_id),
                    )
                ).fetchone()
                if old:
                    return {"written": False, "reason": "duplicate"}

                await conn.execute(
                    "INSERT INTO agent_tool_calls"
                    " (id, tenant_id, user_id, attempt_id, logical_call_id, tool_name,"
                    "  service_key, skill_id, started_at, created_at)"
                    " VALUES (gen_random_uuid(), %(tenant)s, %(user)s, %(attempt)s,"
                    "  %(logical)s, %(tool)s, %(service)s, %(skill)s, %(started)s, now())"
                    " ON CONFLICT (tenant_id, logical_call_id) DO UPDATE SET started_at=LEAST(agent_tool_calls.started_at,EXCLUDED.started_at)",
                    {
                        "tenant": ctx.tenant_id,
                        "user": ctx.user_id,
                        "attempt": ctx.attempt_id,
                        "logical": logical_call_id,
                        "tool": tool_name,
                        "service": service_key,
                        "skill": skill_id,
                        "started": started_at or now,
                    },
                )
                await conn.execute(
                    "INSERT INTO agent_tool_call_attempts"
                    " (id, tenant_id, user_id, attempt_id, observation_id, logical_call_id,"
                    "  retry_index, status, duration_ms, fallback_kind, error_c"
                    "ategory, network_attempted, created_at)"
                    " VALUES (gen_random_uuid(), %(tenant)s, %(user)s, %(attempt)s,"
                    "  %(observation)s, %(logical)s, %(retry)s, %(status)s, %(duration)s,"
                    "  %(fallback)s, %(error)s, %(network)s, now())"
                    " ON CONFLICT (tenant_id, observation_id) DO NOTHING",
                    {
                        "tenant": ctx.tenant_id,
                        "user": ctx.user_id,
                        "attempt": ctx.attempt_id,
                        "observation": observation_id,
                        "logical": logical_call_id,
                        "retry": retry_index,
                        "status": status,
                        "duration": duration_ms,
                        "fallback": fallback_kind,
                        "error": error_category,
                        "network": network_attempted,
                    },
                )
                payload: dict[str, Any] = {
                    "attempt_id": ctx.attempt_id,
                    "observation_id": observation_id,
                    "tool_name": tool_name,
                    "status": status,
                }
                if logical_call_id:
                    payload["logical_call_id"] = logical_call_id
                if service_key:
                    payload["service_key"] = service_key
                if tool_call_id:
                    payload["tool_call_id"] = tool_call_id
                if retry_index:
                    payload["retry_index"] = retry_index
                if duration_ms is not None:
                    payload["duration_ms"] = duration_ms
                if skill_id:
                    payload["skill_id"] = skill_id
                if fallback_kind:
                    payload["fallback_kind"] = fallback_kind
                if error_category:
                    payload["error_category"] = error_category
                written = await _write_event_in(
                    conn,
                    ctx,
                    event_type="runtime.tool_call",
                    payload=payload,
                    occurred_at=now,
                )
    except ThreadDeleted:
        raise
    except psycopg.Error as exc:
        raise ObservabilityBackendUnavailable(
            f"tool attempt rejected: {type(exc).__name__}"
        ) from exc
    return {"written": True, "reason": "ok", "event": written}


async def record_cost(
    ctx: ObservationContext,
    *,
    observation_id: str,
    attempt_id: str,
    calculation_revision: int,
    amount: str | None,
    currency: str | None,
    price_ref: str | None,
    measurement_status: str,
    basis: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """写入计费修订（幂等：同 observation+revision 只有一行；审计保留 basis）."""
    try:
        pool = await get_pool()
    except Exception as exc:  # noqa: BLE001
        raise ObservabilityBackendUnavailable(
            f"pool unavailable: {type(exc).__name__}"
        ) from exc
    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                for key, value in ctx.as_guc().items():
                    await conn.execute(
                        "SELECT set_config(%s, %s, true)", (key, value or "")
                    )
                await conn.execute(
                    "INSERT INTO agent_cost_revisions"
                    " (id, tenant_id, user_id, observation_id, attempt_id,"
                    "  calculation_revision, price_ref, amount, currency,"
                    "  measurement_status, basis, created_by, created_at)"
                    " VALUES (gen_random_uuid(), %(tenant)s, %(user)s, %(observation)s,"
                    "  %(attempt)s, %(revision)s, %(price)s, %(amount)s, %(currency)s,"
                    "  %(status)s, %(basis)s::jsonb, %(by)s, now())"
                    " ON CONFLICT (tenant_id, observation_id, calculation_revision)"
                    "  DO NOTHING",
                    {
                        "tenant": ctx.tenant_id,
                        "user": ctx.user_id,
                        "observation": observation_id,
                        "attempt": attempt_id,
                        "revision": calculation_revision,
                        "price": price_ref,
                        "amount": amount,
                        "currency": currency,
                        "status": measurement_status,
                        "basis": json.dumps(basis or {}, ensure_ascii=False),
                        "by": created_by,
                    },
                )
    except psycopg.Error as exc:
        raise ObservabilityBackendUnavailable(
            f"cost revision rejected: {type(exc).__name__}"
        ) from exc
    return {
        "written": True,
        "reason": "ok",
        "calculation_revision": calculation_revision,
    }


async def record_trace_link(
    ctx: ObservationContext,
    *,
    trace_id: str,
    provider: str = "langfuse",
    project_scope: str | None = None,
    observation_id: str | None = None,
    primary: bool = False,
    link_source: str = "runtime_callback",
    remote_observation_id: str | None = None,
) -> dict[str, Any]:
    """持久化 Langfuse Trace 映射（幂等；同事务写 trace.linked 事件）."""
    try:
        pool = await get_pool()
    except Exception as exc:  # noqa: BLE001
        raise ObservabilityBackendUnavailable(
            f"pool unavailable: {type(exc).__name__}"
        ) from exc
    now = dt.datetime.now(dt.UTC)
    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                for key, value in ctx.as_guc().items():
                    await conn.execute(
                        "SELECT set_config(%s, %s, true)", (key, value or "")
                    )
                await _deleted_guard(conn, ctx)
                if not project_scope:
                    project_scope = (
                        os.environ.get("LANGFUSE_PROJECT_SCOPE")
                        or "project-"
                        + __import__("hashlib")
                        .sha256(os.environ.get("LANGFUSE_PUBLIC_KEY", "").encode())
                        .hexdigest()[:16]
                    )
                if observation_id:
                    await conn.execute(
                        "INSERT INTO agent_observation_trace_links "
                        "(tenant_id,user_id,attempt_id,observation_id,provider,"
                        "project_scope,trace_id,remote_observation_id) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT "
                        "(tenant_id,observation_id,provider,project_scope,trace_id) DO UPDATE SET "
                        "remote_observation_id=COALESCE(agent_observation_trace"
                        "_links.remote_observation_id,EXCLUDED.remote_observati"
                        "on_id)",
                        (
                            ctx.tenant_id,
                            ctx.user_id,
                            ctx.attempt_id,
                            observation_id,
                            provider,
                            project_scope,
                            trace_id,
                            remote_observation_id,
                        ),
                    )
                cur = await conn.execute(
                    "INSERT INTO agent_trace_links (id, tenant_id, user_id, attempt_id,"
                    " provider, project_scope, trace_id, is_primary, link_sourc"
                    "e, observation_id, created_at)"
                    " VALUES (gen_random_uuid(), %(t)s, %(u)s, %(a)s, %(p)s, %(ps)s,"
                    " %(tr)s, %(prim)s, %(ls)s, %(oid)s, now())"
                    " ON CONFLICT (tenant_id, provider, project_scope, trace_id, attempt_id)"
                    " DO NOTHING RETURNING id",
                    {
                        "t": ctx.tenant_id,
                        "u": ctx.user_id,
                        "a": ctx.attempt_id,
                        "p": provider,
                        "ps": project_scope,
                        "tr": trace_id,
                        "prim": primary,
                        "ls": link_source,
                        "oid": observation_id,
                    },
                )
                row = await cur.fetchone()
                if row is None:
                    return {"written": False, "reason": "duplicate"}
                written = await _write_event_in(
                    conn,
                    ctx,
                    event_type="trace.linked",
                    payload={
                        "provider": provider,
                        "project_scope": project_scope,
                        "trace_id": trace_id,
                        "primary": primary,
                        "link_source": link_source,
                    },
                    occurred_at=now,
                )
    except ThreadDeleted:
        raise
    except psycopg.Error as exc:
        raise ObservabilityBackendUnavailable(
            f"trace link rejected: {type(exc).__name__}"
        ) from exc
    return {"written": True, "reason": "ok", "event": written}


async def _price_model_in(
    conn, ctx, observation_id, *, model, provider, called_at, usage, measurement_status
):
    prices = await (
        await conn.execute(
            "SELECT * FROM agent_model_prices WHERE retired_at IS NULL AND tena"
            "nt_id=%s AND provider=%s AND model=%s AND effective_from<=%s AND ("
            "effective_to IS NULL OR effective_to>%s)",
            (ctx.tenant_id, provider or "", model, called_at, called_at),
        )
    ).fetchall()
    price = SimpleNamespace(**prices[0]) if len(prices) == 1 else None
    result = compute_cost(
        price,
        observation_id=observation_id,
        usage=usage,
        measurement_status=measurement_status,
    )
    basis = {
        "usage": usage,
        "measurement_status": measurement_status,
        "price_ref": result.price_ref,
    }
    old = await (
        await conn.execute(
            "SELECT calculation_revision,basis FROM agent_cost_revisions WHERE "
            "tenant_id=%s AND observation_id=%s ORDER BY calculation_revision D"
            "ESC LIMIT 1 FOR UPDATE",
            (ctx.tenant_id, observation_id),
        )
    ).fetchone()
    if old and old["basis"] == basis:
        return
    revision = int(old["calculation_revision"]) + 1 if old else 1
    await conn.execute(
        "INSERT INTO agent_cost_revisions (id,tenant_id,user_id,observation_id,"
        "attempt_id,calculation_revision,price_ref,amount,currency,measurement_"
        "status,basis,created_at) VALUES (gen_random_uuid(),%s,%s,%s,%s,%s,%s,%"
        "s,%s,%s,%s::jsonb,now())",
        (
            ctx.tenant_id,
            ctx.user_id,
            observation_id,
            ctx.attempt_id,
            revision,
            result.price_ref,
            result.amount,
            result.currency,
            result.measurement_status,
            json.dumps(basis),
        ),
    )
    await _write_event_in(
        conn,
        ctx,
        event_type="runtime.cost",
        payload={
            "attempt_id": ctx.attempt_id,
            "observation_id": observation_id,
            "calculation_revision": revision,
            **result.as_event_payload(),
        },
        occurred_at=dt.datetime.now(dt.UTC),
    )
