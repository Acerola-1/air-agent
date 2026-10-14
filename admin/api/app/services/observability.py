"""观测查询与价格服务（admin API 侧；change complete-agent-observability）.

授权分级（spec agent-observability-access）：
- 本人：只回本人 observation（user GUC + RLS，agent_operations 不开）；
- 运营明细：`agent:operations:read`（显式开 agent_operations GUC，仍限本企业）；
- 统计聚合：`agent:metrics:read`（同上，只返回聚合）；
- 价格管理：`agent:pricing:manage`（版本化价格写入 + 显式幂等重算）。
平台账号不隐式拥有企业原文或 Trace 权限。

数据路径：非 owner 运行角色 + RLS（0012_observability FORCE RLS）；所有查询
带 tenant 绑定游标（cursor 编码 filter 指纹，跨租户/改过滤不可重放）。

Trace 查询：本地归属核验先行——伪造/跨企业 Trace ID 在调用任何外部查询**之前**
拒绝，响应不暴露其他记录存在性。共享 Langfuse 项目（events_only 部署）不提供
控制台旁路：返回只读映射与受控跳转信息，不含密钥。
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import TenantContext, apply_tenant_context
from app.services.identity import Identity
from app.services.ledger import LedgerError
from app.services.observability_pricing import compute_cost

MAX_PAGE = 200


class ObservabilityError(LedgerError):
    """观测查询/价格配置错误（复用 ledger 的错误映射语义）."""


# ------------------------------------------------------------------ 授权上下文


async def _scope_session(session: AsyncSession, identity: Identity, *, operations: bool) -> None:
    """本人作用域；运营读需显式开启 agent_operations（不隐式放大）."""
    await apply_tenant_context(
        session,
        TenantContext(
            user_id=str(identity.user.id),
            tenant_id=str(identity.tenant.id) if identity.tenant else None,
            session_id=str(identity.session.id),
            platform=identity.platform_admin,
        ),
    )
    if operations:
        await session.execute(text("SELECT set_config('air_agent.agent_operations', 'on', true)"))


def _cursor_fingerprint(filters: dict[str, Any]) -> str:
    """游标与过滤条件绑定：过滤变化/跨租户重放一律拒绝."""
    canonical = json.dumps(filters, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def encode_cursor(*, last_created_at: dt.datetime, last_id: str, filters: dict[str, Any]) -> str:
    """不透明游标：时间 + id + 过滤指纹（base64url）."""
    raw = json.dumps(
        {
            "t": last_created_at.astimezone(dt.UTC).isoformat(),
            "id": last_id,
            "f": _cursor_fingerprint(filters),
        },
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str, filters: dict[str, Any]) -> tuple[dt.datetime, str]:
    """解码游标；非法/指纹不匹配 → 400 语义错误."""
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ObservabilityError("游标非法", code="bad_cursor") from exc
    if raw.get("f") != _cursor_fingerprint(filters):
        raise ObservabilityError("游标与查询条件不匹配", code="cursor_filter_mismatch")
    try:
        return (
            dt.datetime.fromisoformat(str(raw["t"])),
            str(raw["id"]),
        )
    except (KeyError, ValueError) as exc:
        raise ObservabilityError("游标非法", code="bad_cursor") from exc


# ------------------------------------------------------------------ 明细查询


async def list_observations(
    session: AsyncSession,
    identity: Identity,
    *,
    limit: int = 50,
    cursor: str | None = None,
    graph_id: str | None = None,
    model: str | None = None,
    role: str | None = None,
    status: str | None = None,
    user_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID | None = None,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> dict[str, Any]:
    """逐调用模型观测分页（本人/运营；limit<=200，游标绑定过滤+租户）."""
    if limit > MAX_PAGE or limit < 1:
        raise ObservabilityError(f"limit 必须在 1..{MAX_PAGE}", code="bad_limit")
    filters = {
        "graph_id": graph_id,
        "model": model,
        "role": role,
        "status": status,
        "user_id": str(user_id) if user_id else None,
        "attempt_id": str(attempt_id) if attempt_id else None,
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "tenant": str(identity.tenant.id) if identity.tenant else None,
    }
    operations = "agent:operations:read" in identity.permissions
    await _scope_session(session, identity, operations=operations)
    params: dict[str, Any] = {"limit": limit}
    where = ["1=1"]
    if attempt_id:
        where.append("o.attempt_id = :attempt_id")
        params["attempt_id"] = attempt_id
    if graph_id:
        where.append("o.graph_id = :graph_id")
        params["graph_id"] = graph_id
    if model:
        where.append("o.model = :model")
        params["model"] = model
    if role:
        where.append("o.role = :role")
        params["role"] = role
    if status:
        where.append("o.status = :status")
        params["status"] = status
    if user_id:
        where.append("o.user_id = :obs_user")
        params["obs_user"] = str(user_id)
    if since:
        where.append("o.started_at >= :since")
        params["since"] = since
    if until:
        where.append("o.started_at < :until")
        params["until"] = until
    if cursor:
        after_ts, after_id = decode_cursor(cursor, filters)
        where.append("(o.updated_at, o.observation_id::text) < (:after_ts, :after_id)")
        params["after_ts"] = after_ts
        params["after_id"] = after_id
    sql = text(
        "SELECT o.observation_id::text AS observation_id, o.attempt_id::text AS attempt_id,"
        " o.request_id::text AS request_id, o.submission_id::text AS submission_id,"
        " o.platform_run_id, o.logical_call_id::text AS logical_call_id,"
        " o.parent_observation_id::text AS parent_observation_id, o.graph_id, o"
        ".role, o.model, o.provider,"
        " o.status, o.started_at, o.ended_at, o.duration_ms, o.ttft_ms, o.first_piece_kind,"
        " o.input_tokens, o.output_tokens, o.total_tokens, o.cache_read_tokens,"
        " o.cache_write_tokens, o.reasoning_tokens, o.measurement_status, o.usage_source,"
        " o.retry_index, o.updated_at"
        " FROM agent_model_observations o"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY o.updated_at DESC, o.observation_id DESC LIMIT :limit"
    )
    rows = (await session.execute(sql, params)).mappings().all()
    items = []
    last_ts = last_id = None
    for row in rows:
        items.append(_observation_out(row))
        last_ts, last_id = row["updated_at"], row["observation_id"]
    next_cursor = None
    if len(items) == limit and last_ts is not None:
        next_cursor = encode_cursor(last_created_at=last_ts, last_id=last_id, filters=filters)
    return {"items": items, "next_cursor": next_cursor, "limit": limit}


def _observation_out(row: Any) -> dict[str, Any]:
    """观测明细输出形状（无原文、无参数、无结果正文）."""
    return {
        "observation_id": row["observation_id"],
        "attempt_id": row["attempt_id"],
        "request_id": row["request_id"],
        "submission_id": row["submission_id"],
        "platform_run_id": row["platform_run_id"],
        "logical_call_id": row["logical_call_id"],
        "parent_observation_id": row["parent_observation_id"],
        "graph_id": row["graph_id"],
        "role": row["role"],
        "model": row["model"],
        "provider": row["provider"],
        "status": row["status"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
        "duration_ms": row["duration_ms"],
        "ttft_ms": row["ttft_ms"],
        "first_piece_kind": row["first_piece_kind"],
        "usage": {
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "total_tokens": row["total_tokens"],
            "cache_read_tokens": row["cache_read_tokens"],
            "cache_write_tokens": row["cache_write_tokens"],
            "reasoning_tokens": row["reasoning_tokens"],
        },
        "measurement_status": row["measurement_status"],
        "usage_source": row["usage_source"],
        "retry_index": row["retry_index"],
    }


async def get_run_observations(
    session: AsyncSession, identity: Identity, *, attempt_id: str
) -> dict[str, Any]:
    """单次运行的观测聚合视图（本人/运营）：明细 + Trace 映射 + 采集完整性."""
    await _scope_session(
        session, identity, operations="agent:operations:read" in identity.permissions
    )
    available = await session.scalar(
        text(
            "SELECT 1 FROM agent_run_attempts a JOIN agent_requests r "
            "ON r.id=a.request_id AND r.tenant_id=a.tenant_id WHERE a.id=:id "
            "AND NOT EXISTS (SELECT 1 FROM agent_thread_deletions d "
            "WHERE d.tenant_id=r.tenant_id AND d.thread_id=r.thread_id)"
        ),
        {"id": attempt_id},
    )
    if not available:
        from app.services.ledger import NotFound

        raise NotFound("对象不存在", code="not_found")
    params = {"attempt_id": attempt_id}
    obs_rows = (
        (
            await session.execute(
                text(
                    "SELECT observation_id::text AS observation_id, attempt_id::text AS "
                    "attempt_id, request_id::text AS request_id, submission_id:"
                    ":text AS submission_id, "
                    "platform_run_id,parent_observation_id::text AS parent_obse"
                    "rvation_id,graph_id, started_at, "
                    "ended_at, role, model, provider,"
                    " status, duration_ms, ttft_ms, first_piece_kind, input_tokens,"
                    " output_tokens, total_tokens, cache_read_tokens, cache_write_tokens,"
                    " reasoning_tokens, measurement_status, usage_source, retry_index,"
                    " logical_call_id::text AS logical_call_id"
                    " FROM agent_model_observations WHERE attempt_id = :attempt_id"
                    " ORDER BY started_at,observation_id LIMIT 201"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    trace_rows = (
        (
            await session.execute(
                text(
                    "SELECT provider, project_scope, trace_id, is_primary, link_source"
                    " FROM agent_trace_links WHERE attempt_id = :attempt_id ORDER BY id LIMIT 201"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    span_rows = (
        (
            await session.execute(
                text(
                    "SELECT span_kind, span_name, stage, status, duration_ms, skill_id,"
                    " skill_version, selection_source FROM agent_capability_spans"
                    " WHERE attempt_id = :attempt_id ORDER BY started_at,observation_id LIMIT 201"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    tool_rows = (
        (
            await session.execute(
                text(
                    "SELECT c.logical_call_id::text AS logical_call_id, c.tool_name,"
                    " c.service_key, a.retry_index, a.status, a.duration_ms,"
                    " a.fallback_kind, a.error_category, a.network_attempted"
                    " FROM agent_tool_calls c LEFT JOIN agent_tool_call_attempts a"
                    " ON a.tenant_id = c.tenant_id AND a.logical_call_id = c.logical_call_id"
                    " WHERE c.attempt_id = :attempt_id ORDER BY c.started_at, a"
                    ".retry_index LIMIT 201"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    truncated = any(len(rows) > 200 for rows in (obs_rows, trace_rows, span_rows, tool_rows))
    obs_rows, trace_rows, span_rows, tool_rows = (
        list(rows[:200]) for rows in (obs_rows, trace_rows, span_rows, tool_rows)
    )
    total = len(obs_rows)
    usage_missing = sum(1 for r in obs_rows if r["measurement_status"] == "usage_missing")
    invalid = sum(1 for r in obs_rows if r["measurement_status"] == "invalid_usage")
    return {
        "attempt_id": attempt_id,
        "truncated": truncated,
        "model_details_url": f"/agent/observability/observations?attempt_id={attempt_id}",
        "coverage_scope": "returned_rows",
        "model_observations": [_observation_out(r) for r in obs_rows],
        "skill_spans": [dict(r) for r in span_rows],
        "tool_calls": [dict(r) for r in tool_rows],
        "trace_links": [dict(r) for r in trace_rows],
        "coverage": {
            "total_observations": total,
            "usage_missing": usage_missing,
            "not_collected": sum(r["measurement_status"] == "not_collected" for r in obs_rows),
            "invalid_usage": invalid,
            "usage_coverage": (
                sum(r["measurement_status"] == "measured" for r in obs_rows) / total
                if total
                else None
            ),
        },
    }


# ------------------------------------------------------------------ 统计聚合


async def metrics_summary(
    session: AsyncSession,
    identity: Identity,
    *,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> dict[str, Any]:
    """统计聚合：Token、耗时分位、覆盖与缺失（统计权限；无明细正文）."""
    await _scope_session(session, identity, operations=True)
    params: dict[str, Any] = {"since": since, "until": until}
    where = ["1=1"]
    if since:
        where.append("started_at >= :since")
    if until:
        where.append("started_at < :until")
    where_sql = " AND ".join(where)
    totals = (
        (
            await session.execute(
                text(
                    "SELECT count(*) AS n, count(DISTINCT logical_call_id) AS logical_calls,"
                    " count(*) FILTER (WHERE measurement_status='not_collected') AS not_collected,"
                    " count(*) FILTER (WHERE status='unknown') AS unknown,"
                    " count(*) FILTER (WHERE status='started') AS in_progress,"
                    " count(input_tokens) FILTER (WHERE measurement_status='measured') AS "
                    "input_count,"
                    " count(output_tokens) FILTER (WHERE measurement_status='measured') AS "
                    "output_count,"
                    " count(total_tokens) FILTER (WHERE measurement_status='measured') AS "
                    "total_count,"
                    " count(*) FILTER (WHERE measurement_status = 'measured') AS measured,"
                    " count(*) FILTER (WHERE measurement_status = 'usage_missing')"
                    " AS usage_missing,"
                    " count(*) FILTER (WHERE measurement_status = 'invalid_usage')"
                    " AS invalid_usage,"
                    " count(*) FILTER (WHERE status = 'failed') AS failed,"
                    " count(*) FILTER (WHERE status = 'cancelled') AS cancelled,"
                    " sum(input_tokens) FILTER (WHERE measurement_status='measured') "
                    "AS input_tokens, sum(output_tokens) FILTER (WHERE "
                    "measurement_status='measured') AS output_tokens,"
                    " sum(total_tokens) FILTER (WHERE measurement_status='measured') "
                    "AS total_tokens, sum(cache_read_tokens) FILTER (WHERE "
                    "measurement_status='measured') AS cache_read,"
                    " sum(reasoning_tokens) FILTER (WHERE "
                    "measurement_status='measured') AS reasoning,"
                    " percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50,"
                    " percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95,"
                    " percentile_cont(0.5) WITHIN GROUP (ORDER BY ttft_ms) FILTER"
                    "   (WHERE ttft_ms IS NOT NULL) AS ttft_p50"
                    f" FROM agent_model_observations WHERE {where_sql}"
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    by_model = (
        (
            await session.execute(
                text(
                    "SELECT model, count(*) AS n, sum(total_tokens) FILTER (WHERE "
                    "measurement_status='measured') AS tokens"
                    f" FROM agent_model_observations WHERE {where_sql}"
                    " GROUP BY model ORDER BY n DESC LIMIT 20"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    skill_rows = (
        (
            await session.execute(
                text(
                    "SELECT skill_id, stage, count(*) AS n FROM agent_capability_spans"
                    f" WHERE {' AND '.join(where) if where_sql != '1=1' else '1=1'}"
                    " GROUP BY skill_id, stage ORDER BY skill_id, stage"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    n = int(totals["n"] or 0)
    measured = int(totals["measured"] or 0)
    tool_where = ["1=1"]
    if since:
        tool_where.append("c.started_at>=:since")
    if until:
        tool_where.append("c.started_at<:until")
    tool_groups = (
        (
            await session.execute(
                text(
                    "SELECT c.service_key,c.tool_name,count(DISTINCT c.logical_call_id) AS "
                    "logical_calls,"
                    "count(a.observation_id) AS attempt_records,"
                    "count(*) FILTER(WHERE a.network_attempted IS TRUE) AS network_attempts,"
                    "count(*) FILTER(WHERE a.network_attempted IS NULL) AS network_status_unknown,"
                    "count(*) FILTER(WHERE a.fallback_kind IS NOT NULL) AS recovery_records,"
                    "count(*) FILTER(WHERE a.status='permission_denied') AS permission_denied,"
                    "count(*) FILTER(WHERE a.status='validation_rejected') AS validation_rejected,"
                    "count(*) FILTER(WHERE a.status='technical_error') AS technical_error,"
                    "count(*) FILTER(WHERE a.status='empty_result') AS empty_result,"
                    "count(*) FILTER(WHERE a.status='cancelled') AS cancelled,"
                    "count(*) FILTER(WHERE a.status='timed_out') AS timed_out,"
                    "count(*) FILTER(WHERE a.status='unknown') AS unknown,"
                    "percentile_cont(.95) WITHIN GROUP(ORDER BY a.duration_ms) FILTER(WHERE "
                    "a.fallback_kind IS NULL) AS duration_p95_ms "
                    "FROM agent_tool_calls c LEFT JOIN agent_tool_call_attempts a "
                    "ON a.tenant_id=c.tenant_id AND a.logical_call_id=c.logical_call_id "
                    "WHERE " + " AND ".join(tool_where) + " GROUP BY c.service_key,c.tool_name"
                    " ORDER BY c.service_key,c.tool_name LIMIT 201"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return {
        "source": "observations_live",
        "as_of": dt.datetime.now(dt.UTC).isoformat(),
        "tool_groups": [dict(row) for row in tool_groups[:200]],
        "tool_groups_truncated": len(tool_groups) > 200,
        "window": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        "totals": {
            "observations": n,
            "logical_calls": int(totals["logical_calls"]),
            "not_collected": int(totals["not_collected"]),
            "unknown": int(totals["unknown"]),
            "in_progress": int(totals["in_progress"]),
            "measured": measured,
            "usage_missing": int(totals["usage_missing"] or 0),
            "invalid_usage": int(totals["invalid_usage"] or 0),
            "failed": int(totals["failed"] or 0),
            "cancelled": int(totals["cancelled"] or 0),
            "usage_coverage": (measured / n) if n else None,
        },
        "tokens": {
            "input": int(totals["input_tokens"]) if totals["input_tokens"] is not None else None,
            "output": int(totals["output_tokens"]) if totals["output_tokens"] is not None else None,
            "total": int(totals["total_tokens"]) if n and totals["total_count"] == n else None,
            "reported_total_subtotal": int(totals["total_tokens"])
            if totals["total_tokens"] is not None
            else None,
            "known_token_subtotal": int(totals["input_tokens"] or 0)
            + int(totals["output_tokens"] or 0)
            if totals["input_count"] or totals["output_count"]
            else None,
            "component_coverage": {
                k: int(totals[k + "_count"]) / n if n else None
                for k in ("input", "output", "total")
            },
            "input_output_are_known_subtotals": True,
            "cache_read": int(totals["cache_read"]) if totals["cache_read"] is not None else None,
            "reasoning": int(totals["reasoning"]) if totals["reasoning"] is not None else None,
        },
        "latency_ms": {
            "duration_p50": float(totals["p50"]) if totals["p50"] is not None else None,
            "duration_p95": float(totals["p95"]) if totals["p95"] is not None else None,
            "ttft_p50": float(totals["ttft_p50"]) if totals["ttft_p50"] is not None else None,
        },
        "by_model": [dict(r) for r in by_model],
        "skill_stages": [dict(r) for r in skill_rows],
    }


# ------------------------------------------------------------------ 价格管理


async def create_price_version(
    session: AsyncSession,
    identity: Identity,
    *,
    provider: str,
    model: str,
    currency: str,
    input_per_unit: Decimal,
    output_per_unit: Decimal,
    cache_read_per_unit: Decimal | None,
    cache_write_per_unit: Decimal | None,
    reasoning_mode: str,
    reasoning_per_unit: Decimal | None,
    effective_from: dt.datetime,
    effective_to: dt.datetime | None,
    supersedes_price_ref: str | None = None,
) -> dict[str, Any]:
    """发布不可变价格版本（重叠由排他约束拒绝；审计记录操作者）."""
    await _scope_session(session, identity, operations=False)
    await session.execute(text("SELECT set_config('air_agent.pricing_manage','on',true)"))
    if supersedes_price_ref:
        previous = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM agent_model_prices WHERE retired_at IS NULL AND"
                        " tenant_id=:tenant AND price_ref=:ref FOR UPDATE"
                    ),
                    {"tenant": str(identity.tenant.id), "ref": supersedes_price_ref},
                )
            )
            .mappings()
            .first()
        )
        if (
            previous is None
            or previous["retired_at"] is not None
            or (
                previous["provider"],
                previous["model"],
                previous["currency"],
                previous["effective_from"],
                previous["effective_to"],
            )
            != (provider, model, currency, effective_from, effective_to)
        ):
            raise ObservabilityError(
                "被替代价格不存在、已退役或适用范围不一致", code="price_overlap"
            )
        await session.execute(
            text(
                "UPDATE agent_model_prices SET retired_at=now() WHERE "
                "tenant_id=:tenant AND price_ref=:ref"
            ),
            {"tenant": str(identity.tenant.id), "ref": supersedes_price_ref},
        )
    price_ref = f"pv-{uuid.uuid4().hex[:12]}"
    try:
        row = (
            (
                await session.execute(
                    text(
                        "INSERT INTO agent_model_prices"
                        " (id, tenant_id, price_ref, provider, model, currency, unit,"
                        "  input_per_unit, output_per_unit, cache_read_per_unit,"
                        "  cache_write_per_unit, reasoning_mode, reasoning_per_unit,"
                        "  effective_from, effective_to, created_by, created_at, "
                        "supersedes_price_ref)"
                        " VALUES (gen_random_uuid(), :tenant, :ref, :provider, :model,"
                        "  :currency, '1m_tokens', :inp, :outp, :cr, :cw, :rmode, :rp,"
                        "  :from, :to, :by, now(), :supersedes) RETURNING price_ref, created_at"
                    ),
                    {
                        "tenant": str(identity.tenant.id) if identity.tenant else None,
                        "ref": price_ref,
                        "provider": provider,
                        "model": model,
                        "currency": currency,
                        "inp": input_per_unit,
                        "outp": output_per_unit,
                        "cr": cache_read_per_unit,
                        "cw": cache_write_per_unit,
                        "rmode": reasoning_mode,
                        "rp": reasoning_per_unit,
                        "from": effective_from,
                        "to": effective_to,
                        "by": str(identity.user.id),
                        "supersedes": supersedes_price_ref,
                    },
                )
            )
            .mappings()
            .one()
        )
        from app.services.audit import record_audit

        record_audit(
            session,
            identity=identity,
            actor_id=identity.user.id,
            actor_account=identity.user.account,
            action="observability.price.create",
            target_type="agent_model_prices",
            target_id=price_ref,
            detail={
                "provider": provider,
                "model": model,
                "effective_from": effective_from.isoformat(),
                "supersedes_price_ref": supersedes_price_ref,
            },
        )
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if "excl" in str(exc).lower() or "overlap" in str(exc).lower():
            raise ObservabilityError("价格区间与既有版本重叠", code="price_overlap") from exc
        raise ObservabilityError(f"价格写入失败: {name}", code="price_write_failed") from exc
    return {"price_ref": row["price_ref"], "created_at": row["created_at"].isoformat()}


def _select_price(
    prices: list[dict[str, Any]],
    *,
    provider: str,
    model: str,
    called_at: dt.datetime,
) -> dict[str, Any] | None:
    """价格版本选择（半开区间；与 src/common/observability/pricing.py 同规则）."""
    called = called_at if called_at.tzinfo else called_at.replace(tzinfo=dt.UTC)
    matches = []
    for price in prices:
        if str(price["provider"]) != provider or str(price["model"]) != model:
            continue
        start = price["effective_from"]
        if start.tzinfo is None:
            start = start.replace(tzinfo=dt.UTC)
        if called < start:
            continue
        end = price["effective_to"]
        if end is not None:
            if end.tzinfo is None:
                end = end.replace(tzinfo=dt.UTC)
            if called >= end:
                continue
        matches.append(price)
    return matches[0] if len(matches) == 1 else None


def _compute_cost(
    price: dict[str, Any] | None,
    *,
    usage: dict[str, Any],
) -> tuple[str, Decimal | None, str | None, str | None]:
    """分项计费（Decimal；缓存/推理子集不重复收费；缺失/非法不伪造）."""

    adapted = SimpleNamespace(**price) if price else None
    result = compute_cost(
        adapted,
        observation_id="adapter",
        usage=usage,
        measurement_status=usage.get("measurement_status", "measured"),
    )
    return result.measurement_status, result.amount, result.currency, result.price_ref


async def recalculate_costs(session, identity, *, attempt_id, since, until):
    """有权限的显式重算：分批读取全部窗口，原始usage不可变，依据相同幂等."""
    await _scope_session(session, identity, operations=True)
    tenant = str(identity.tenant.id) if identity.tenant else None
    if since and until and since >= until:
        raise ObservabilityError("窗口无效", code="bad_window")
    where = ["tenant_id=:tenant"]
    params = {"tenant": tenant}
    for clause, key, value in [
        ("attempt_id=:attempt", "attempt", attempt_id),
        ("started_at>=:since", "since", since),
        ("started_at<:until", "until", until),
    ]:
        if value:
            where.append(clause)
            params[key] = value
    prices = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    "SELECT * FROM agent_model_prices WHERE retired_at IS NULL AND "
                    "tenant_id=:tenant"
                ),
                params,
            )
        ).mappings()
    ]
    scanned = written = unchanged = 0
    after = None
    while True:
        clauses = where + (["observation_id>:after"] if after else [])
        if after:
            params["after"] = after
        batch = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM agent_model_observations WHERE "
                        + " AND ".join(clauses)
                        + " ORDER BY observation_id LIMIT 500 FOR UPDATE"
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        if not batch:
            break
        for obs in batch:
            scanned += 1
            usage = {
                k: obs[k]
                for k in (
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                    "reasoning_tokens",
                )
                if obs[k] is not None
            }
            price = _select_price(
                prices,
                provider=obs["provider"] or "",
                model=obs["model"],
                called_at=obs["started_at"],
            )
            cost = compute_cost(
                SimpleNamespace(**price) if price else None,
                observation_id=str(obs["observation_id"]),
                usage=usage,
                measurement_status=obs["measurement_status"],
            )
            basis = {
                "usage": usage,
                "measurement_status": obs["measurement_status"],
                "price_ref": cost.price_ref,
            }
            old = (
                (
                    await session.execute(
                        text(
                            "SELECT calculation_revision,basis FROM "
                            "agent_cost_revisions WHERE tenant_id=:tenant AND "
                            "observation_id=:obs ORDER BY calculation_revision DESC "
                            "LIMIT 1 FOR UPDATE"
                        ),
                        {"tenant": tenant, "obs": obs["observation_id"]},
                    )
                )
                .mappings()
                .first()
            )
            if old and old["basis"] == basis:
                unchanged += 1
                continue
            revision = int(old["calculation_revision"]) + 1 if old else 1
            await session.execute(
                text(
                    "INSERT INTO agent_cost_revisions(id,tenant_id,user_id,observation"
                    "_id,attempt_id,calculation_revision,price_ref,amount,currency,mea"
                    "surement_status,basis,created_by,created_at) VALUES (gen_random_u"
                    "uid(),:tenant,:user,:obs,:attempt,:rev,:ref,:amount,:currency,:st"
                    "atus,CAST(:basis AS jsonb),:actor,now())"
                ),
                {
                    "tenant": tenant,
                    "user": obs["user_id"],
                    "obs": obs["observation_id"],
                    "attempt": obs["attempt_id"],
                    "rev": revision,
                    "ref": cost.price_ref,
                    "amount": cost.amount,
                    "currency": cost.currency,
                    "status": cost.measurement_status,
                    "basis": json.dumps(basis),
                    "actor": identity.user.id,
                },
            )
            from app.services.ledger import emit_fact

            await emit_fact(
                session,
                tenant_id=identity.tenant.id,
                user_id=obs["user_id"],
                membership_id=(
                    await session.execute(
                        text(
                            "SELECT membership_id FROM agent_run_attempts WHERE "
                            "tenant_id=:tenant AND id=:attempt"
                        ),
                        {"tenant": tenant, "attempt": obs["attempt_id"]},
                    )
                ).scalar_one(),
                event_type="runtime.cost",
                source="runtime",
                payload={
                    "attempt_id": str(obs["attempt_id"]),
                    "observation_id": str(obs["observation_id"]),
                    "calculation_revision": revision,
                    **cost.as_event_payload(),
                },
                thread_id=obs["thread_id"],
                request_id=obs["request_id"],
                attempt_id=obs["attempt_id"],
                consumers=("observability_projection",),
            )
            written += 1
        after = batch[-1]["observation_id"]
    from app.services.audit import record_audit

    record_audit(
        session,
        identity=identity,
        actor_id=identity.user.id,
        actor_account=identity.user.account,
        action="observability.cost.recalculate",
        target_type="agent_cost_revisions",
        target_id=attempt_id or "window",
        detail={"scanned": scanned, "written": written, "unchanged": unchanged},
    )
    return {"scanned": scanned, "cost_revisions_written": written, "unchanged": unchanged}


async def cost_summary(session, identity, *, since=None, until=None):
    """最新计费修订只计一次，按币种/缺失状态分列，未归因桶保持守恒."""
    await _scope_session(session, identity, operations=True)
    params = {"tenant": str(identity.tenant.id) if identity.tenant else None}
    where = ["o.tenant_id=:tenant"]
    if since:
        where.append("o.started_at>=:since")
        params["since"] = since
    if until:
        where.append("o.started_at<:until")
        params["until"] = until
    # 单个 SQL 快照计算最新修订，避免把全部明细载入进程或混读并发重算结果。
    # 先转文本再打包 JSON，保留 NUMERIC 的十进制精度。
    row = (
        (
            await session.execute(
                text(
                    """
                WITH latest AS MATERIALIZED (
                    SELECT o.user_id, o.graph_id, o.input_tokens, o.output_tokens,
                           c.currency, c.amount,
                           COALESCE(c.measurement_status, o.measurement_status)
                               AS measurement_status
                    FROM agent_model_observations o
                    LEFT JOIN LATERAL (
                        SELECT r.amount, r.currency, r.measurement_status
                        FROM agent_cost_revisions r
                        WHERE r.tenant_id=o.tenant_id AND r.observation_id=o.observation_id
                        ORDER BY calculation_revision DESC LIMIT 1
                    ) c ON true WHERE """
                    + " AND ".join(where)
                    + """
                ), priced AS (
                    SELECT * FROM latest
                    WHERE measurement_status='measured' AND amount IS NOT NULL
                ), currency_groups AS (
                    SELECT currency, SUM(amount)::text AS subtotal, COUNT(*) AS priced
                    FROM priced GROUP BY currency ORDER BY currency LIMIT 201
                ), user_groups AS (
                    SELECT user_id::text, currency, SUM(amount)::text AS subtotal
                    FROM priced GROUP BY user_id, currency ORDER BY user_id, currency LIMIT 201
                ), graph_groups AS (
                    SELECT graph_id, currency, SUM(amount)::text AS subtotal
                    FROM priced GROUP BY graph_id, currency ORDER BY graph_id, currency LIMIT 201
                ), coverage AS (
                    SELECT measurement_status, COUNT(*) AS n FROM latest
                    GROUP BY measurement_status
                )
                SELECT
                    COALESCE((SELECT jsonb_agg(to_jsonb(g)) FROM currency_groups g),
                             '[]'::jsonb) AS currencies,
                    COALESCE((SELECT jsonb_agg(to_jsonb(g)) FROM user_groups g),
                             '[]'::jsonb) AS users,
                    COALESCE((SELECT jsonb_agg(to_jsonb(g)) FROM graph_groups g),
                             '[]'::jsonb) AS graphs,
                    COALESCE((SELECT jsonb_object_agg(measurement_status,n) FROM coverage),
                             '{}'::jsonb) AS coverage,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE measurement_status!='measured' OR amount IS NULL)
                        AS unpriced,
                    COUNT(*) FILTER (WHERE input_tokens IS NULL OR output_tokens IS NULL)
                        AS partial
                FROM latest
                """
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    currencies, users, graphs = row["currencies"], row["users"], row["graphs"]
    return {
        "by_currency": currencies[:MAX_PAGE],
        "by_currency_truncated": len(currencies) > MAX_PAGE,
        "total_observations": row["total"],
        "coverage": row["coverage"],
        "unpriced_observations": row["unpriced"],
        "partial_usage_observations": row["partial"],
        "complete_cost_known": bool(row["total"]) and not row["unpriced"] and not row["partial"],
        "by_user": users[:MAX_PAGE],
        "by_user_truncated": len(users) > MAX_PAGE,
        "by_graph": graphs[:MAX_PAGE],
        "by_graph_truncated": len(graphs) > MAX_PAGE,
        "capability_attribution": {
            "mode": "shared_or_unattributed",
            "by_currency": [
                {"currency": value["currency"], "subtotal": value["subtotal"]}
                for value in currencies[:MAX_PAGE]
            ],
            "truncated": len(currencies) > MAX_PAGE,
        },
        "external_tool_cost": {"measurement_status": "not_collected", "amount": None},
        "as_of": dt.datetime.now(dt.UTC).isoformat(),
        "source": "observations_latest_cost_revision",
        "metric_version": "obs-cost-v2",
    }


# ------------------------------------------------------------------ Trace 映射


async def get_trace_mapping(
    session: AsyncSession, identity: Identity, *, trace_id: str
) -> dict[str, Any] | None:
    """Trace 归属核验先行：不归属本企业的 trace 在外部查询前拒绝（None）.

    共享 Langfuse 项目（events_only）：返回本地映射与元信息，不暴露密钥，
    不提供共享控制台直链；浏览器没有共享项目凭据。
    """
    await _scope_session(session, identity, operations=True)
    row = (
        (
            await session.execute(
                text(
                    "SELECT l.provider, l.project_scope, l.trace_id, l.is_primary,"
                    " l.link_source, l.attempt_id::text AS attempt_id"
                    " FROM agent_trace_links l WHERE l.trace_id = :trace"
                    " AND l.tenant_id = :tenant LIMIT 1"
                ),
                {"trace": trace_id, "tenant": str(identity.tenant.id) if identity.tenant else None},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    links = (
        (
            await session.execute(
                text(
                    "SELECT remote_observation_id FROM agent_observation_trace_links "
                    "WHERE tenant_id=:tenant AND project_scope=:project AND trace_id=:trace "
                    "AND remote_observation_id IS NOT NULL"
                ),
                {
                    "tenant": str(identity.tenant.id),
                    "project": row["project_scope"],
                    "trace": trace_id,
                },
            )
        )
        .scalars()
        .all()
    )
    import os

    from app.services.langfuse_proxy import read_observations

    remote = {"sync_state": "pending", "observations": [], "reason": "project_unconfigured"}
    if row["provider"] == "langfuse" and row["project_scope"] == os.environ.get(
        "LANGFUSE_PROJECT_SCOPE"
    ):
        remote = await read_observations(trace_id, set(links))
        if remote["sync_state"] == "confirmed":
            await session.execute(
                text(
                    "UPDATE agent_observation_trace_links SET sync_state='confi"
                    "rmed', synced_at=now() "
                    "WHERE tenant_id=:tenant AND project_scope=:project AND trace_id=:trace "
                    "AND remote_observation_id=ANY(:ids)"
                ),
                {
                    "tenant": str(identity.tenant.id),
                    "project": row["project_scope"],
                    "trace": trace_id,
                    "ids": list(links),
                },
            )
    return {
        **dict(row),
        "console_access": "not_available_shared_project",
        "readback_mode": "server_scoped_v2",
        **remote,
    }
