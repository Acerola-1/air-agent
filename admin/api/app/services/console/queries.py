"""运营后台明细查询：用户用量、运行列表/详情、反馈列表与历史.

所有查询：
- 在 `scope_session()` 已设置的企业/权限 GUC 上执行；
- 显式同租户关联与线程墓碑过滤（RLS 之外的双重边界）；
- 列表使用有界 limit（≤200）与稳定排序；游标绑定企业/上下文/过滤/as_of；
- 聚合不以"首屏前 200 项相加"冒充全集。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.console.scope import (
    ConsoleCursor,
    ConsoleError,
    ConsoleWindow,
    build_meta,
    cursor_window,
    decode_cursor,
    encode_cursor,
    ensure_identifiable_access,
    filter_fingerprint,
    parse_limit,
    parse_window,
    scope_session,
    tenant_id_of,
    window_key,
)
from app.services.identity import Identity
from app.services.ledger import NotFound

DELETED_GUARD = (
    "NOT EXISTS (SELECT 1 FROM agent_thread_deletions d"
    " WHERE d.tenant_id = {alias}.tenant_id AND d.thread_id = {alias}.thread_id)"
)


def _deleted(alias: str) -> str:
    return DELETED_GUARD.format(alias=alias)


def _graph_filter(alias: str, filters: dict[str, Any], params: dict[str, Any]) -> list[str]:
    clauses: list[str] = []
    if filters.get("graph_id"):
        clauses.append(f"{alias}.graph_id = :graph_id")
        params["graph_id"] = filters["graph_id"]
    if filters.get("business_code"):
        if alias == "o":
            clauses.append(
                "EXISTS (SELECT 1 FROM agent_run_attempts business_attempt "
                "JOIN agent_requests business_request ON "
                "business_request.id=business_attempt.request_id "
                "AND business_request.tenant_id=business_attempt.tenant_id "
                "WHERE business_attempt.id=o.attempt_id AND business_attempt.tenant_id=o.tenant_id "
                "AND "
                "COALESCE(business_request.business_code,business_request.graph_id)=:business_code)"
            )
        else:
            clauses.append(f"COALESCE({alias}.business_code, {alias}.graph_id) = :business_code")
        params["business_code"] = filters["business_code"]
    return clauses


def resolve_window(
    *,
    window: str | None,
    start: str | None,
    end: str | None,
    cursor: str | None,
    tenant_id: str,
    context_version: int,
    user_id: str | None = None,
    filter_key: str,
    sort: str,
) -> tuple[ConsoleWindow, ConsoleCursor | None]:
    """先解码游标（若有）再规范化窗口，保证翻页沿用原数据截止."""
    if cursor:
        decoded = decode_cursor(
            cursor,
            tenant_id=tenant_id,
            context_version=context_version,
            user_id=user_id,
            filter_key=filter_key,
            sort=sort,
        )
        # kind 只用于展示；实际边界来自签发游标
        return cursor_window(decoded, (window or "last_7_days")), decoded
    as_of = dt.datetime.now(dt.UTC)
    return (
        parse_window(window=window, start=start, end=end, as_of=as_of),
        None,
    )


# ------------------------------------------------------------------ 用户用量


async def list_users(
    session: AsyncSession,
    identity: Identity,
    *,
    window: str | None,
    start: str | None,
    end: str | None,
    business_code: str | None,
    graph_id: str | None,
    user_id: uuid.UUID | None,
    cursor: str | None,
    limit: int | None,
) -> dict[str, Any]:
    """用户用量/活跃/费用列表；必须 ops+metrics（可识别用户）."""
    await scope_session(session, identity, require_metrics=True, require_ops=True)
    if user_id is not None:
        ensure_identifiable_access(identity)
    tenant = tenant_id_of(identity)
    effective_limit = parse_limit(limit)
    filters = {
        "business_code": business_code,
        "graph_id": graph_id,
        "user_id": str(user_id) if user_id else None,
    }
    key = filter_fingerprint(filters, window_key=window_key(window, start, end))
    win, decoded = resolve_window(
        window=window,
        start=start,
        end=end,
        cursor=cursor,
        tenant_id=tenant,
        context_version=identity.context_version,
        user_id=str(identity.user.id),
        filter_key=key,
        sort="user_id_asc",
    )
    params: dict[str, Any] = {
        "tenant": tenant,
        "start": win.start,
        "end": win.end,
        "limit": effective_limit + 1,
    }
    request_clauses = [
        "r.tenant_id = :tenant",
        "r.admitted_at >= :start",
        "r.admitted_at < :end",
        "COALESCE(r.identity_revision ->> 'historical', 'false') <> 'true'",
        _deleted("r"),
    ]
    request_clauses.append(
        "EXISTS (SELECT 1 FROM agent_submissions confirmed_submission WHERE "
        "confirmed_submission.tenant_id=r.tenant_id AND "
        "confirmed_submission.request_id=r.id AND "
        "confirmed_submission.admission_status='confirmed')"
    )
    request_clauses += _graph_filter("r", filters, params)
    attempt_clauses = [
        "a.tenant_id = :tenant",
        "a.admitted_at >= :start",
        "a.admitted_at < :end",
        _deleted("r"),
    ]
    attempt_clauses += _graph_filter("r", filters, params)
    obs_clauses = [
        "o.tenant_id = :tenant",
        "o.started_at >= :start",
        "o.started_at < :end",
    ]
    obs_clauses += _graph_filter("o", filters, params)
    if user_id is not None:
        params["user_filter"] = str(user_id)
    user_clause = " AND r.user_id = :user_filter" if user_id is not None else ""
    obs_user_clause = " AND o.user_id = :user_filter" if user_id is not None else ""
    cursor_clause = ""
    if decoded:
        try:
            params["after_user"] = uuid.UUID(decoded.key)
        except ValueError as exc:
            raise ConsoleError("游标非法", code="bad_cursor") from exc
        cursor_clause = " AND x.user_id > :after_user"

    sql = text(
        f"""
        WITH req AS (
            SELECT r.user_id,
                   count(*) AS requests,
                   count(DISTINCT r.thread_id) AS threads,
                   max(r.admitted_at) AS last_request_at
            FROM agent_requests r
            WHERE {" AND ".join(request_clauses)}{user_clause}
            GROUP BY r.user_id
        ), att AS (
            SELECT a.user_id,
                   count(*) AS attempts,
                   count(*) FILTER (WHERE a.state = 'succeeded') AS succeeded,
                   count(*) FILTER (WHERE a.state IN ('failed','cancelled','timed_out'))
                       AS unsuccessful,
                   max(COALESCE(a.ended_at, a.admitted_at)) AS last_attempt_at
            FROM agent_run_attempts a
            JOIN agent_requests r
              ON r.id = a.request_id AND r.tenant_id = a.tenant_id
            WHERE {" AND ".join(attempt_clauses)}{user_clause.replace("r.user_id", "a.user_id")}
            GROUP BY a.user_id
        ), obs AS (
            SELECT o.user_id,
                   count(*) AS model_calls,
                   count(DISTINCT o.logical_call_id) AS logical_calls,
                   sum(o.total_tokens) FILTER (WHERE o.measurement_status = 'measured')
                       AS total_tokens,
                   count(*) FILTER (WHERE o.measurement_status <> 'measured') AS usage_missing
            FROM agent_model_observations o
            WHERE {" AND ".join(obs_clauses)}{obs_user_clause}
            GROUP BY o.user_id
        ), latest_cost AS (
            SELECT o.user_id, o.observation_id, o.measurement_status AS usage_status,
                   c.amount, c.currency, c.measurement_status AS cost_status
            FROM agent_model_observations o
            LEFT JOIN LATERAL (
                SELECT r.amount, r.currency, r.measurement_status
                FROM agent_cost_revisions r
                WHERE r.tenant_id = o.tenant_id AND r.observation_id = o.observation_id
                ORDER BY r.calculation_revision DESC LIMIT 1
            ) c ON true
            WHERE {" AND ".join(obs_clauses)}{obs_user_clause}
        ), cost AS (
            SELECT user_id,
                   COALESCE(jsonb_object_agg(currency, subtotal), '{{}}'::jsonb)
                       AS by_currency
            FROM (
                SELECT user_id, currency, sum(amount)::text AS subtotal
                FROM latest_cost
                WHERE amount IS NOT NULL AND cost_status = 'measured'
                GROUP BY user_id, currency
            ) priced
            GROUP BY user_id
        ), cost_missing AS (
            SELECT user_id, count(*) AS unpriced
            FROM latest_cost
            WHERE amount IS NULL OR cost_status <> 'measured' OR usage_status <> 'measured'
            GROUP BY user_id
        ), uids AS (
            SELECT user_id FROM req
            UNION SELECT user_id FROM att
            UNION SELECT user_id FROM obs
        )
        SELECT x.user_id::text AS user_id,
               COALESCE(i.display_name, x.user_id::text) AS display_name,
               COALESCE(req.requests, 0) AS requests,
               COALESCE(req.threads, 0) AS threads,
               COALESCE(att.attempts, 0) AS attempts,
               COALESCE(att.succeeded, 0) AS succeeded,
               COALESCE(att.unsuccessful, 0) AS unsuccessful,
               COALESCE(obs.model_calls, 0) AS model_calls,
               COALESCE(obs.logical_calls, 0) AS logical_calls,
               obs.total_tokens AS total_tokens,
               COALESCE(obs.usage_missing, 0) AS usage_missing,
               COALESCE(cost.by_currency, '{{}}'::jsonb) AS cost_by_currency,
               COALESCE(cost_missing.unpriced, 0) AS unpriced_cost_calls,
               GREATEST(req.last_request_at, att.last_attempt_at) AS last_active_at
        FROM uids x
        LEFT JOIN req ON req.user_id = x.user_id
        LEFT JOIN att ON att.user_id = x.user_id
        LEFT JOIN obs ON obs.user_id = x.user_id
        LEFT JOIN cost ON cost.user_id = x.user_id
        LEFT JOIN cost_missing ON cost_missing.user_id = x.user_id
        LEFT JOIN iam_reference_users i ON i.id = x.user_id
        WHERE true{cursor_clause}
        ORDER BY x.user_id ASC
        LIMIT :limit
        """
    )
    rows = (await session.execute(sql, params)).mappings().all()
    has_more = len(rows) > effective_limit
    rows = rows[:effective_limit]
    items = [
        {
            "user_id": row["user_id"],
            "display_name": row["display_name"],
            "requests": int(row["requests"]),
            "threads": int(row["threads"]),
            "attempts": int(row["attempts"]),
            "succeeded": int(row["succeeded"]),
            "unsuccessful": int(row["unsuccessful"]),
            "model_calls": int(row["model_calls"]),
            "logical_calls": int(row["logical_calls"]),
            "total_tokens": int(row["total_tokens"]) if row["total_tokens"] is not None else None,
            "usage_missing_calls": int(row["usage_missing"]),
            "cost_by_currency": row["cost_by_currency"] or {},
            "unpriced_cost_calls": int(row["unpriced_cost_calls"] or 0),
            "last_active_at": row["last_active_at"].isoformat() if row["last_active_at"] else None,
            "measurement_source": "direct_sql",
        }
        for row in rows
    ]
    next_cursor = (
        encode_cursor(
            tenant_id=tenant,
            context_version=identity.context_version,
            user_id=str(identity.user.id),
            filter_key=key,
            sort="user_id_asc",
            window=win,
            key=rows[-1]["user_id"],
        )
        if has_more and rows
        else None
    )
    return {
        "meta": build_meta(identity=identity, window=win, filters=filters),
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "limit": effective_limit,
    }


# ------------------------------------------------------------------ 运行列表


async def list_runs(
    session: AsyncSession,
    identity: Identity,
    *,
    window: str | None,
    start: str | None,
    end: str | None,
    business_code: str | None,
    graph_id: str | None,
    user_id: uuid.UUID | None,
    state: str | None,
    thread_id: str | None,
    cursor: str | None,
    limit: int | None,
) -> dict[str, Any]:
    await scope_session(session, identity, require_ops=True)
    tenant = tenant_id_of(identity)
    effective_limit = parse_limit(limit)
    filters = {
        "business_code": business_code,
        "graph_id": graph_id,
        "user_id": str(user_id) if user_id else None,
        "state": state,
        "thread_id": thread_id,
    }
    key = filter_fingerprint(filters, window_key=window_key(window, start, end))
    win, decoded = resolve_window(
        window=window,
        start=start,
        end=end,
        cursor=cursor,
        tenant_id=tenant,
        context_version=identity.context_version,
        user_id=str(identity.user.id),
        filter_key=key,
        sort="admitted_at_desc",
    )
    params: dict[str, Any] = {
        "tenant": tenant,
        "start": win.start,
        "end": win.end,
        "limit": effective_limit + 1,
    }
    clauses = [
        "a.tenant_id = :tenant",
        "a.admitted_at >= :start",
        "a.admitted_at < :end",
        _deleted("r"),
    ]
    clauses += _graph_filter("r", filters, params)
    if user_id is not None:
        clauses.append("a.user_id = :user_id")
        params["user_id"] = str(user_id)
    if state:
        clauses.append("a.state = :state")
        params["state"] = state
    if thread_id:
        clauses.append("r.thread_id = :thread_id")
        params["thread_id"] = thread_id
    if decoded:
        try:
            ts_text, id_text = decoded.key.split("|", 1)
            params["after_ts"] = dt.datetime.fromisoformat(ts_text)
            params["after_id"] = uuid.UUID(id_text)
        except ValueError as exc:
            raise ConsoleError("游标非法", code="bad_cursor") from exc
        clauses.append("(a.admitted_at, a.id) < (:after_ts, :after_id)")
    sql = text(
        f"""
        SELECT a.id::text AS attempt_id, a.request_id::text AS request_id,
               r.thread_id, a.graph_id, COALESCE(a.business_code, a.graph_id) AS business_code,
               a.state, a.state_revision, a.evidence_source, a.platform_run_id,
               a.cancel_requested, a.cancel_origin, a.cancel_confirmed,
               a.admitted_at, a.started_at, a.ended_at,
               a.user_id::text AS user_id,
               COALESCE(i.display_name, a.user_id::text) AS display_name,
               ans.answer_id, ans.content_version, ans.confirmed, ans.confirmed_at,
               ans.content_status, ans.truncated,
               (SELECT count(*) FROM agent_run_resumes rs
                WHERE rs.tenant_id = a.tenant_id AND rs.attempt_id = a.id) AS resume_count
        FROM agent_run_attempts a
        JOIN agent_requests r
          ON r.id = a.request_id AND r.tenant_id = a.tenant_id
        LEFT JOIN LATERAL (
            SELECT an.id::text AS answer_id, an.content_version, an.confirmed,
                   an.confirmed_at,
                   CASE
                     WHEN ac.id IS NULL THEN 'not_collected'
                     WHEN ac.capture_status = 'capture_failed' THEN 'capture_failed'
                     WHEN ac.retention_until IS NOT NULL AND ac.retention_until <= now()
                       THEN 'expired'
                     WHEN ac.truncated THEN 'truncated'
                     ELSE 'available'
                   END AS content_status,
                   COALESCE(ac.truncated, false) AS truncated
            FROM agent_answers an
            LEFT JOIN agent_answer_content_metadata ac
              ON ac.tenant_id = an.tenant_id AND ac.answer_id = an.id
             AND ac.content_version = an.content_version
            WHERE an.tenant_id = a.tenant_id AND an.attempt_id = a.id
              AND an.confirmed = true AND an.partial = false
            ORDER BY an.confirmed_at DESC NULLS LAST, an.id DESC
            LIMIT 1
        ) ans ON true
        LEFT JOIN iam_reference_users i ON i.id = a.user_id
        WHERE {" AND ".join(clauses)}
        ORDER BY a.admitted_at DESC, a.id DESC
        LIMIT :limit
        """
    )
    rows = (await session.execute(sql, params)).mappings().all()
    has_more = len(rows) > effective_limit
    rows = rows[:effective_limit]
    items = [
        {
            "attempt_id": row["attempt_id"],
            "request_id": row["request_id"],
            "thread_id": row["thread_id"],
            "graph_id": row["graph_id"],
            "business_code": row["business_code"],
            "user_id": row["user_id"],
            "display_name": row["display_name"],
            "state": row["state"],
            "state_revision": row["state_revision"],
            "evidence_source": row["evidence_source"],
            "platform_run_id": row["platform_run_id"],
            "cancel_requested": row["cancel_requested"],
            "cancel_origin": row["cancel_origin"],
            "cancel_confirmed": row["cancel_confirmed"],
            "admitted_at": row["admitted_at"].isoformat(),
            "started_at": row["started_at"].isoformat() if row["started_at"] else None,
            "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
            "resume_count": int(row["resume_count"] or 0),
            "answer": (
                {
                    "answer_id": row["answer_id"],
                    "content_version": row["content_version"],
                    "confirmed": row["confirmed"],
                    "confirmed_at": row["confirmed_at"].isoformat()
                    if row["confirmed_at"]
                    else None,
                    "content_status": row["content_status"],
                    "truncated": row["truncated"],
                }
                if row["answer_id"]
                else None
            ),
            "content_included": False,
        }
        for row in rows
    ]
    next_cursor = (
        encode_cursor(
            tenant_id=tenant,
            context_version=identity.context_version,
            user_id=str(identity.user.id),
            filter_key=key,
            sort="admitted_at_desc",
            window=win,
            key=f"{rows[-1]['admitted_at'].isoformat()}|{rows[-1]['attempt_id']}",
        )
        if has_more and rows
        else None
    )
    return {
        "meta": build_meta(identity=identity, window=win, filters=filters),
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "limit": effective_limit,
    }


async def get_run(
    session: AsyncSession, identity: Identity, attempt_id: uuid.UUID
) -> dict[str, Any]:
    """运行详情：元数据/恢复/回答版本/选择/观测引用；无正文."""
    await scope_session(session, identity, require_ops=True)
    tenant = tenant_id_of(identity)
    params = {"tenant": tenant, "attempt": str(attempt_id)}
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT a.id::text AS attempt_id, a.request_id::text AS request_id,
                       r.thread_id, r.user_message_id, a.graph_id,
                       COALESCE(a.business_code, a.graph_id) AS business_code,
                       a.operation_kind, a.state, a.state_revision, a.evidence_source,
                       a.platform_status, a.platform_run_id, a.cancel_requested,
                       a.cancel_origin, a.cancel_phase, a.cancel_confirmed,
                       a.transport_connected, a.waiting_kind, a.content_digest,
                       a.admitted_at, a.queued_at, a.started_at, a.ended_at,
                       a.last_progress_at, a.updated_at, a.user_id::text AS user_id,
                       COALESCE(i.display_name, a.user_id::text) AS display_name,
                       r.department_snapshot, r.role_snapshot,
                       r.admitted_at AS request_admitted_at
                FROM agent_run_attempts a
                JOIN agent_requests r
                  ON r.id = a.request_id AND r.tenant_id = a.tenant_id
                LEFT JOIN iam_reference_users i ON i.id = a.user_id
                WHERE a.tenant_id = :tenant AND a.id = :attempt
                  AND NOT EXISTS (SELECT 1 FROM agent_thread_deletions d
                                  WHERE d.tenant_id = r.tenant_id AND d.thread_id = r.thread_id)
                """
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("运行不存在或不属于当前企业", code="attempt_not_found")
    answers = (
        (
            await session.execute(
                text(
                    """
                SELECT an.id::text AS answer_id, an.message_id, an.content_version,
                       an.partial, an.confirmed, an.confirmed_at, an.created_at,
                       ac.id::text AS snapshot_id, ac.content_sha256,
                       ac.total_chars, ac.truncated, ac.capture_status, ac.captured_at,
                       ac.retention_until,
                       CASE
                         WHEN ac.id IS NULL THEN 'not_collected'
                         WHEN ac.capture_status = 'capture_failed' THEN 'capture_failed'
                         WHEN ac.retention_until IS NOT NULL AND ac.retention_until <= now()
                           THEN 'expired'
                         WHEN ac.truncated THEN 'truncated'
                         ELSE 'available'
                       END AS availability
                FROM agent_answers an
                LEFT JOIN agent_answer_content_metadata ac
                  ON ac.tenant_id = an.tenant_id AND ac.answer_id = an.id
                 AND ac.content_version = an.content_version
                WHERE an.tenant_id = :tenant AND an.attempt_id = :attempt
                ORDER BY an.created_at, an.content_version
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    resumes = (
        (
            await session.execute(
                text(
                    """
                SELECT rs.resume_index, rs.platform_run_id, rs.original_run_id,
                       rs.created_at, rs.user_id::text AS user_id
                FROM agent_run_resumes rs
                WHERE rs.tenant_id = :tenant AND rs.attempt_id = :attempt
                ORDER BY rs.resume_index
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    traces = (
        (
            await session.execute(
                text(
                    "SELECT provider, project_scope, trace_id, is_primary, link_source"
                    " FROM agent_trace_links WHERE tenant_id = :tenant AND attempt_id = :attempt"
                    " ORDER BY is_primary DESC, id"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    selection = (
        (
            await session.execute(
                text(
                    """
                SELECT s.id::text AS selection_id, s.mcp_mode, s.tool_ids, s.catalog_version,
                       s.selection_digest, s.digest_version,
                       s.validation_time
                FROM agent_capability_selections s
                WHERE s.tenant_id = :tenant AND s.attempt_id = :attempt
                ORDER BY s.created_at DESC LIMIT 1
                """
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    observation = (
        (
            await session.execute(
                text(
                    """
                SELECT count(*) AS observations,
                       count(DISTINCT logical_call_id) AS logical_calls,
                       count(*) FILTER (WHERE measurement_status = 'measured') AS measured,
                       count(*) FILTER (WHERE measurement_status = 'usage_missing')
                           AS usage_missing,
                       sum(total_tokens) FILTER (WHERE measurement_status = 'measured')
                           AS total_tokens,
                       count(*) FILTER (WHERE status = 'failed') AS failed,
                       count(*) FILTER (WHERE ttft_ms IS NOT NULL) AS ttft_samples
                FROM agent_model_observations
                WHERE tenant_id = :tenant AND attempt_id = :attempt
                """
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    return {
        "meta": build_meta(
            identity=identity,
            window=_point_window(),
            filters={"attempt_id": str(attempt_id)},
        ),
        "attempt": {
            key: (value.isoformat() if isinstance(value, (dt.datetime,)) else value)
            for key, value in dict(row).items()
        },
        "answers": [
            {
                **{
                    key: (value.isoformat() if isinstance(value, dt.datetime) else value)
                    for key, value in dict(item).items()
                },
                "content_included": False,
            }
            for item in answers
        ],
        "resumes": [
            {
                **{
                    key: (value.isoformat() if isinstance(value, dt.datetime) else value)
                    for key, value in dict(item).items()
                }
            }
            for item in resumes
        ],
        "traces": [dict(item) for item in traces],
        "capability_selection": (
            {
                "selection_status": "collected",
                "selection_id": selection["selection_id"],
                "schema_version": "agent.capability-selection.v2",
                "mcp": {
                    "mode": selection["mcp_mode"],
                    "tool_ids": selection["tool_ids"],
                },
                "catalog_version": selection["catalog_version"],
                "selection_digest": selection["selection_digest"],
                "digest_version": selection["digest_version"],
                "selection_source": "user_explicit"
                if selection["mcp_mode"] == "allowlist"
                else "automatic",
                "validation_time": selection["validation_time"].isoformat()
                if isinstance(selection["validation_time"], dt.datetime)
                else selection["validation_time"],
            }
            if selection
            else {"selection_status": "not_collected"}
        ),
        "observations": {
            "observations": int(observation["observations"]),
            "logical_calls": int(observation["logical_calls"]),
            "measured": int(observation["measured"]),
            "usage_missing": int(observation["usage_missing"]),
            "total_tokens": int(observation["total_tokens"])
            if observation["total_tokens"] is not None
            else None,
            "failed": int(observation["failed"]),
            "ttft_samples": int(observation["ttft_samples"]),
            "details_url": f"/agent/observability/runs/{attempt_id}",
            "content_included": False,
        },
    }


def _point_window() -> ConsoleWindow:
    now = dt.datetime.now(dt.UTC)
    return ConsoleWindow(kind="custom", start=now, end=now, as_of=now)


# ------------------------------------------------------------------ 反馈列表


async def list_feedback(
    session: AsyncSession,
    identity: Identity,
    *,
    window: str | None,
    start: str | None,
    end: str | None,
    business_code: str | None,
    graph_id: str | None,
    rating: str | None,
    reason: str | None,
    case_state: str | None,
    cursor: str | None,
    limit: int | None,
) -> dict[str, Any]:
    """当前反馈列表（按回答确认时间窗口）；不含意见正文."""
    await scope_session(session, identity, require_ops=True)
    tenant = tenant_id_of(identity)
    effective_limit = parse_limit(limit)
    filters = {
        "business_code": business_code,
        "graph_id": graph_id,
        "rating": rating,
        "reason": reason,
        "case_state": case_state,
    }
    key = filter_fingerprint(filters, window_key=window_key(window, start, end))
    win, decoded = resolve_window(
        window=window,
        start=start,
        end=end,
        cursor=cursor,
        tenant_id=tenant,
        context_version=identity.context_version,
        user_id=str(identity.user.id),
        filter_key=key,
        sort="confirmed_at_desc",
    )
    params: dict[str, Any] = {
        "tenant": tenant,
        "start": win.start,
        "end": win.end,
        "limit": effective_limit + 1,
    }
    clauses = [
        "f.tenant_id = :tenant",
        "a.confirmed_at >= :start",
        "a.confirmed_at < :end",
        _deleted("r"),
    ]
    clauses += _graph_filter("r", filters, params)
    if rating:
        clauses.append("f.rating = :rating")
        params["rating"] = rating
    if reason:
        clauses.append("f.reason_code = :reason")
        params["reason"] = reason
    if case_state:
        clauses.append("COALESCE(c.state, 'pending') = :case_state")
        params["case_state"] = case_state
    if decoded:
        try:
            ts_text, id_text = decoded.key.split("|", 1)
            params["after_ts"] = dt.datetime.fromisoformat(ts_text)
            params["after_id"] = uuid.UUID(id_text)
        except ValueError as exc:
            raise ConsoleError("游标非法", code="bad_cursor") from exc
        clauses.append("(a.confirmed_at, f.answer_id) < (:after_ts, :after_id)")
    rows = (
        (
            await session.execute(
                text(
                    f"""
                SELECT f.answer_id::text AS answer_id, f.user_id::text AS user_id,
                       f.rating, f.reason_code, f.revision AS feedback_revision,
                       f.updated_at AS feedback_updated_at,
                       (f.comment_id IS NOT NULL) AS has_comment,
                       a.message_id, a.content_version, a.confirmed_at,
                       r.id::text AS request_id, r.thread_id,
                       COALESCE(r.business_code, r.graph_id) AS business_code,
                       c.id::text AS case_id, c.state AS case_state,
                       c.revision AS case_revision, c.reviewed_feedback_revision,
                       c.handled_at,
                       CASE WHEN f.rating = 'negative'
                                  AND f.revision > COALESCE(c.reviewed_feedback_revision, 0)
                            THEN true ELSE false END AS needs_review
                FROM agent_feedback_current f
                JOIN agent_answers a
                  ON a.id = f.answer_id AND a.tenant_id = f.tenant_id
                JOIN agent_run_attempts t
                  ON t.id = a.attempt_id AND t.tenant_id = a.tenant_id
                JOIN agent_requests r
                  ON r.id = t.request_id AND r.tenant_id = t.tenant_id
                LEFT JOIN agent_feedback_cases c
                  ON c.tenant_id = f.tenant_id AND c.answer_id = f.answer_id
                WHERE {" AND ".join(clauses)}
                ORDER BY a.confirmed_at DESC, f.answer_id DESC
                LIMIT :limit
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    has_more = len(rows) > effective_limit
    rows = rows[:effective_limit]
    items = [
        {
            "answer_id": row["answer_id"],
            "request_id": row["request_id"],
            "thread_id": row["thread_id"],
            "user_id": row["user_id"],
            "business_code": row["business_code"],
            "rating": row["rating"],
            "reason_code": row["reason_code"],
            "feedback_revision": row["feedback_revision"],
            "feedback_updated_at": row["feedback_updated_at"].isoformat()
            if row["feedback_updated_at"]
            else None,
            "has_comment": row["has_comment"],
            "comment_included": False,
            "message_id": row["message_id"],
            "content_version": row["content_version"],
            "confirmed_at": row["confirmed_at"].isoformat(),
            "case_id": row["case_id"],
            "case_state": row["case_state"] or "pending",
            "case_revision": int(row["case_revision"] or 0),
            "reviewed_feedback_revision": int(row["reviewed_feedback_revision"] or 0),
            "needs_review": bool(row["needs_review"]),
            "handled_at": row["handled_at"].isoformat() if row["handled_at"] else None,
        }
        for row in rows
    ]
    next_cursor = (
        encode_cursor(
            tenant_id=tenant,
            context_version=identity.context_version,
            user_id=str(identity.user.id),
            filter_key=key,
            sort="confirmed_at_desc",
            window=win,
            key=f"{rows[-1]['confirmed_at'].isoformat()}|{rows[-1]['answer_id']}",
        )
        if has_more and rows
        else None
    )
    return {
        "meta": build_meta(identity=identity, window=win, filters=filters),
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "limit": effective_limit,
    }


async def feedback_history(
    session: AsyncSession,
    identity: Identity,
    *,
    answer_id: uuid.UUID,
    kind: str,
    limit: int | None,
    cursor: str | None,
) -> dict[str, Any]:
    """评价/处理修订历史（无正文）；`kind=rating|case` 独立分页."""
    await scope_session(session, identity, require_ops=True)
    tenant = tenant_id_of(identity)
    effective_limit = parse_limit(limit)
    if kind not in ("rating", "case"):
        raise ConsoleError("kind 必须是 rating 或 case", code="invalid_kind")
    params: dict[str, Any] = {
        "tenant": tenant,
        "answer": str(answer_id),
        "limit": effective_limit + 1,
    }
    answer_exists = (
        await session.execute(
            text(
                "SELECT 1 FROM agent_answers a JOIN agent_run_attempts t"
                " ON t.id=a.attempt_id AND t.tenant_id=a.tenant_id"
                " JOIN agent_requests r ON r.id=t.request_id AND r.tenant_id=t.tenant_id"
                " WHERE a.tenant_id=:tenant AND a.id=:answer AND " + _deleted("r")
            ),
            params,
        )
    ).first()
    if answer_exists is None:
        raise NotFound("回答不存在或不属于当前企业", code="not_found")
    filters = {"answer_id": str(answer_id), "kind": kind}
    fingerprint = filter_fingerprint(filters, window_key="feedback_history")
    win = parse_window(window="today", start=None, end=None, as_of=dt.datetime.now(dt.UTC))
    after_id = None
    if cursor:
        decoded = decode_cursor(
            cursor,
            tenant_id=tenant,
            context_version=identity.context_version,
            user_id=str(identity.user.id),
            filter_key=fingerprint,
            sort="history_id_asc",
        )
        win = cursor_window(decoded, "today")
        try:
            if kind == "rating":
                after_id = int(decoded.key)
            else:
                timestamp, identifier = decoded.key.split("|", 1)
                params["after_time"] = dt.datetime.fromisoformat(timestamp)
                after_id = str(uuid.UUID(identifier))
        except ValueError as exc:
            raise ConsoleError("游标非法", code="bad_cursor") from exc
        params["after"] = after_id
    params["as_of"] = win.as_of
    if kind == "rating":
        after = " AND id > :after" if after_id is not None else ""
        rows = (
            (
                await session.execute(
                    text(
                        f"""
                    SELECT id, operation_id, rating, reason_code, accepted_via,
                           expected_revision, result_revision, received_at,
                           (comment_id IS NOT NULL) AS has_comment
                    FROM agent_feedback_operations
                    WHERE tenant_id = :tenant AND answer_id = :answer{after}
                      AND received_at <= :as_of
                    ORDER BY id ASC LIMIT :limit
                    """
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        next_cursor = str(rows[effective_limit - 1]["id"]) if len(rows) > effective_limit else None
    else:
        after_case = (
            " AND (a.created_at,a.id) > (:after_time,CAST(:after AS uuid))"
            if after_id is not None
            else ""
        )
        rows = (
            (
                await session.execute(
                    text(
                        f"""
                    SELECT a.id, a.case_id::text AS case_id, a.operation_id, a.action,
                           a.from_state, a.to_state, a.expected_revision,
                           a.result_revision, a.actor_user_id::text AS actor_user_id,
                           a.actor_account, (a.note_id IS NOT NULL) AS has_note,
                           a.created_at
                    FROM agent_feedback_case_actions a
                    JOIN agent_feedback_cases c
                      ON c.id = a.case_id AND c.tenant_id = a.tenant_id
                    WHERE a.tenant_id = :tenant AND a.answer_id = :answer{after_case}
                      AND a.created_at <= :as_of
                    ORDER BY a.created_at ASC, a.id ASC LIMIT :limit
                    """
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        last = rows[effective_limit - 1] if len(rows) > effective_limit else None
        next_cursor = f"{last['created_at'].isoformat()}|{last['id']}" if last else None
    has_more = len(rows) > effective_limit
    rows = rows[:effective_limit]
    items = []
    for row in rows:
        item = dict(row)
        for key, value in list(item.items()):
            if isinstance(value, dt.datetime):
                item[key] = value.isoformat()
        items.append(item)
    return {
        "meta": build_meta(
            identity=identity,
            window=win,
            filters={"answer_id": str(answer_id), "kind": kind},
        ),
        "items": items,
        "next_cursor": encode_cursor(
            tenant_id=tenant,
            context_version=identity.context_version,
            user_id=str(identity.user.id),
            filter_key=fingerprint,
            sort="history_id_asc",
            window=win,
            key=next_cursor,
        )
        if has_more
        else None,
        "has_more": has_more,
        "limit": effective_limit,
        "content_included": False,
    }
