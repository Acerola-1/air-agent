"""运营后台聚合指标：总览、日趋势、能力、性能与费用.

口径（design D3/D7）：

- 各事实先按权威键聚合，再做一对一关联；禁止宽连接导致计数膨胀。
- request/attempt/resume/answer/model-call 是不同 cohort，各自窗口与分母。
- 分位数从窗口明细计算；不平均每日分位数。
- 缺 usage/价格不补零；多币种分开；最新计费修订只计一次。
- 首屏/run 前 200 项截断不作为全量汇总；聚合 SQL 直接覆盖窗口全集。
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.console.browser_metrics import browser_metrics
from app.services.console.scope import (
    ConsoleError,
    ConsoleWindow,
    build_meta,
    business_day_start,
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
from app.services.interaction_policy import enabled as interactions_enabled
from app.services.interaction_policy import enabled_at

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


def _resolve(
    *,
    window: str | None,
    start: str | None,
    end: str | None,
    cursor: str | None,
    tenant: str,
    identity: Identity,
    filters: dict[str, Any],
    sort: str,
) -> tuple[ConsoleWindow, Any]:
    key = filter_fingerprint(filters, window_key=window_key(window, start, end))
    if cursor:
        decoded = decode_cursor(
            cursor,
            tenant_id=tenant,
            context_version=identity.context_version,
            user_id=str(identity.user.id),
            filter_key=key,
            sort=sort,
        )
        return cursor_window(decoded, window or "last_7_days"), decoded
    return (
        parse_window(
            window=window,
            start=start,
            end=end,
            as_of=dt.datetime.now(dt.UTC),
        ),
        None,
    )


def _metric(
    value: Any,
    *,
    cohort: str,
    measurement_source: str = "direct_sql",
    numerator: Any = None,
    denominator: Any = None,
    sample_count: int | None = None,
    missing_count: int = 0,
    status: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    if status is None:
        if value is None:
            # 空分母是 unknown；没有可采集来源是 not_collected。真实 0 仍是
            # measured（由 value=0 表达），不能把"窗口内确实没有请求"写成缺失。
            status = "unknown" if denominator == 0 else "not_collected"
        elif missing_count:
            status = "partial"
        else:
            status = "measured"
    payload = {
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "sample_count": sample_count,
        "missing_count": missing_count,
        "measurement_source": measurement_source,
        "cohort": cohort,
        "status": status,
    }
    payload.update(extra)
    return payload


def _ms(value: Any) -> float | None:
    return round(float(value), 1) if value is not None else None


# ------------------------------------------------------------------ 总览


async def overview(
    session: AsyncSession,
    identity: Identity,
    *,
    window: str | None,
    start: str | None,
    end: str | None,
    business_code: str | None,
    graph_id: str | None,
) -> dict[str, Any]:
    # v1 /overview 不接受 user_id/model/skill/tool：路由层显式 422。
    await scope_session(session, identity, require_metrics=True)
    tenant = tenant_id_of(identity)
    filters = {"business_code": business_code, "graph_id": graph_id}
    win = parse_window(window=window, start=start, end=end, as_of=dt.datetime.now(dt.UTC))
    params: dict[str, Any] = {"tenant": tenant, "start": win.start, "end": win.end}

    request_clauses = [
        "r.tenant_id = :tenant",
        "r.admitted_at >= :start",
        "r.admitted_at < :end",
        _deleted("r"),
        "COALESCE(r.identity_revision ->> 'historical', 'false') <> 'true'",
    ]
    request_clauses.append(
        "EXISTS (SELECT 1 FROM agent_submissions confirmed_submission WHERE "
        "confirmed_submission.tenant_id=r.tenant_id AND "
        "confirmed_submission.request_id=r.id AND "
        "confirmed_submission.admission_status='confirmed')"
    )
    request_clauses += _graph_filter("r", filters, params)
    requests_row = (
        (
            await session.execute(
                text(
                    "SELECT count(*) AS questions, count(DISTINCT r.user_id) AS active_users"
                    " FROM agent_requests r WHERE " + " AND ".join(request_clauses)
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    historical = (
        await session.execute(
            text(
                "SELECT count(*) FROM agent_requests r WHERE "
                + " AND ".join(
                    [
                        "r.tenant_id = :tenant",
                        "r.admitted_at >= :start",
                        "r.admitted_at < :end",
                        _deleted("r"),
                        "COALESCE(r.identity_revision ->> 'historical', 'false') = 'true'",
                    ]
                    + _graph_filter("r", filters, params)
                )
            ),
            params,
        )
    ).scalar() or 0

    attempt_clauses = [
        "a.tenant_id = :tenant",
        "a.admitted_at >= :start",
        "a.admitted_at < :end",
        _deleted("r"),
    ]
    attempt_clauses += _graph_filter("r", filters, params)
    attempt_row = (
        (
            await session.execute(
                text(
                    """
                SELECT count(*) AS attempts,
                       count(*) FILTER (WHERE a.state = 'succeeded') AS succeeded,
                       count(*) FILTER (WHERE a.state = 'failed') AS failed,
                       count(*) FILTER (WHERE a.state = 'cancelled') AS cancelled,
                       count(*) FILTER (WHERE a.state = 'timed_out') AS timed_out,
                       count(*) FILTER (WHERE a.state NOT IN
                       ('succeeded','failed','cancelled','timed_out'))
                           AS not_ended,
                       count(*) FILTER (WHERE a.cancel_requested) AS stop_intent,
                       count(*) FILTER (WHERE a.cancel_confirmed) AS cancel_confirmed
                FROM agent_run_attempts a
                JOIN agent_requests r
                  ON r.id = a.request_id AND r.tenant_id = a.tenant_id
                WHERE """
                    + " AND ".join(attempt_clauses)
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    resumes = (
        await session.execute(
            text(
                "SELECT count(*) FROM agent_run_resumes rs"
                " JOIN agent_run_attempts a ON a.id=rs.attempt_id AND a.tenant_id=rs.tenant_id"
                " JOIN agent_requests r ON r.id=a.request_id AND r.tenant_id=a.tenant_id"
                " WHERE rs.tenant_id=:tenant AND rs.created_at>=:start AND rs.created_at<:end"
                " AND "
                + _deleted("r")
                + " AND "
                + " AND ".join(["true"] + _graph_filter("r", filters, params))
            ),
            params,
        )
    ).scalar() or 0

    params["collection_start"] = (
        enabled_at() if interactions_enabled() else dt.datetime.max.replace(tzinfo=dt.UTC)
    )
    feedback_clauses = [
        "f.tenant_id = :tenant",
        "a.confirmed AND NOT a.partial",
        "a.confirmed_at >= :collection_start",
        "a.confirmed_at >= :start",
        "a.confirmed_at < :end",
        _deleted("r"),
    ]
    feedback_clauses += _graph_filter("r", filters, params)
    feedback_row = (
        (
            await session.execute(
                text(
                    """
                SELECT count(*) FILTER (WHERE f.rating = 'positive') AS positive,
                       count(*) FILTER (WHERE f.rating = 'negative') AS negative,
                       count(*) FILTER (WHERE f.rating = 'none') AS withdrawn,
                       count(*) AS rated
                FROM agent_feedback_current f
                JOIN agent_answers a
                  ON a.id=f.answer_id AND a.tenant_id=f.tenant_id
                JOIN agent_run_attempts t
                  ON t.id=a.attempt_id AND t.tenant_id=a.tenant_id
                JOIN agent_requests r
                  ON r.id=t.request_id AND r.tenant_id=t.tenant_id
                WHERE """
                    + " AND ".join(feedback_clauses)
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    answer_clauses = [
        "a.tenant_id=:tenant",
        "a.confirmed=true",
        "a.partial=false",
        "a.confirmed_at>=:start",
        "a.confirmed_at<:end",
        _deleted("r"),
    ]
    answer_clauses += _graph_filter("r", filters, params)
    answer_row = (
        (
            await session.execute(
                text(
                    """
                SELECT count(*) AS confirmed,
                       count(ac.id) FILTER (WHERE ac.capture_status = 'captured') AS captured,
                       count(ac.id) FILTER (WHERE ac.capture_status = 'capture_failed')
                           AS capture_failed,
                       count(ac.id) FILTER (WHERE ac.truncated) AS truncated,
                       count(*) FILTER (WHERE ac.id IS NULL) AS not_collected
                FROM agent_answers a
                JOIN agent_run_attempts t
                  ON t.id=a.attempt_id AND t.tenant_id=a.tenant_id
                JOIN agent_requests r
                  ON r.id=t.request_id AND r.tenant_id=t.tenant_id
                LEFT JOIN agent_answer_content_metadata ac
                  ON ac.tenant_id=a.tenant_id AND ac.answer_id=a.id
                 AND ac.content_version=a.content_version
                WHERE """
                    + " AND ".join(answer_clauses)
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    copy_row, timing_row = await browser_metrics(
        session, tenant=tenant, start=win.start, end=win.end, as_of=win.as_of, filters=filters
    )

    observation_clauses = [
        "o.tenant_id = :tenant",
        "o.started_at >= :start",
        "o.started_at < :end",
    ]
    observation_clauses += _graph_filter("o", filters, params)
    observation_row = (
        (
            await session.execute(
                text(
                    """
                SELECT count(*) AS calls,
                       count(DISTINCT o.logical_call_id) AS logical_calls,
                       count(*) FILTER (WHERE o.measurement_status='measured') AS measured,
                       count(*) FILTER (WHERE o.measurement_status='usage_missing')
                           AS usage_missing,
                       count(*) FILTER (WHERE o.measurement_status='invalid_usage')
                           AS invalid_usage,
                       count(*) FILTER (WHERE o.status='failed') AS failed,
                       sum(o.input_tokens) FILTER (WHERE o.measurement_status='measured')
                           AS input_tokens,
                       sum(o.output_tokens) FILTER (WHERE o.measurement_status='measured')
                           AS output_tokens,
                       sum(o.total_tokens) FILTER (WHERE o.measurement_status='measured')
                           AS total_tokens,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY o.duration_ms)
                           FILTER (WHERE o.duration_ms IS NOT NULL) AS p50,
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY o.duration_ms)
                           FILTER (WHERE o.duration_ms IS NOT NULL) AS p95,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY o.ttft_ms)
                           FILTER (WHERE o.ttft_ms IS NOT NULL) AS ttft_p50,
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY o.ttft_ms)
                           FILTER (WHERE o.ttft_ms IS NOT NULL) AS ttft_p95,
                       count(o.ttft_ms) AS ttft_samples,
                       count(o.duration_ms) AS duration_samples
                FROM agent_model_observations o
                WHERE """
                    + " AND ".join(observation_clauses)
                ),
                params,
            )
        )
        .mappings()
        .one()
    )

    cost_row = (
        (
            await session.execute(
                text(
                    """
                WITH latest AS (
                    SELECT o.observation_id, o.measurement_status AS usage_status,
                           c.amount, c.currency,
                           c.measurement_status AS cost_status
                    FROM agent_model_observations o
                    LEFT JOIN (
                        SELECT DISTINCT ON (r.tenant_id,r.observation_id)
                            r.tenant_id,r.observation_id,r.amount,r.currency,r.measurement_status
                        FROM agent_cost_revisions r WHERE r.tenant_id=:tenant
                        ORDER BY r.tenant_id,r.observation_id,r.calculation_revision DESC
                    ) c ON c.tenant_id=o.tenant_id AND c.observation_id=o.observation_id
                    WHERE """
                    + " AND ".join(observation_clauses)
                    + """
                ), currency_totals AS (
                    SELECT currency,
                           sum(amount) FILTER (WHERE cost_status='measured')::text AS subtotal,
                           count(*) AS total,
                           count(*) FILTER (WHERE amount IS NULL OR cost_status <> 'measured')
                               AS unpriced,
                           count(*) FILTER (WHERE usage_status <> 'measured') AS usage_incomplete
                    FROM latest GROUP BY currency
                )
                SELECT COALESCE(jsonb_object_agg(currency,subtotal)
                           FILTER(WHERE subtotal IS NOT NULL AND currency IS NOT NULL),'{}'::jsonb)
                           AS by_currency,
                       COALESCE(sum(unpriced),0) AS unpriced,
                       COALESCE(sum(usage_incomplete),0) AS usage_incomplete,
                       COALESCE(sum(total),0) AS total
                FROM currency_totals
                """
                ),
                params,
            )
        )
        .mappings()
        .one()
    )

    trace_clauses = [
        "l.tenant_id=:tenant",
        "a.admitted_at>=:start",
        "a.admitted_at<:end",
        _deleted("r"),
    ]
    trace_clauses += _graph_filter("r", filters, params)
    trace_row = (
        (
            await session.execute(
                text(
                    """
                SELECT count(*) AS links, count(DISTINCT l.trace_id) AS traces,
                       count(DISTINCT l.attempt_id) AS attempts
                FROM agent_trace_links l
                JOIN agent_run_attempts a ON a.id=l.attempt_id AND a.tenant_id=l.tenant_id
                JOIN agent_requests r ON r.id=a.request_id AND r.tenant_id=a.tenant_id
                WHERE """
                    + " AND ".join(trace_clauses)
                ),
                params,
            )
        )
        .mappings()
        .one()
    )

    questions = int(requests_row["questions"] or 0)
    active_users = int(requests_row["active_users"] or 0)
    attempts = int(attempt_row["attempts"] or 0)
    succeeded = int(attempt_row["succeeded"] or 0)
    ended = (
        succeeded
        + int(attempt_row["failed"] or 0)
        + int(attempt_row["cancelled"] or 0)
        + int(attempt_row["timed_out"] or 0)
    )
    positive = int(feedback_row["positive"] or 0)
    negative = int(feedback_row["negative"] or 0)
    rated = positive + negative
    confirmed = int(answer_row["confirmed"] or 0)
    eligible = int(copy_row["eligible"])
    captured = int(answer_row["captured"] or 0)
    calls = int(observation_row["calls"] or 0)
    measured = int(observation_row["measured"] or 0)
    unpriced = int(cost_row.get("unpriced") or 0)
    limitations: list[str] = []
    if unpriced or int(cost_row.get("usage_incomplete") or 0):
        limitations.append("部分模型调用缺少 usage 或价格，费用不是零")
    if int(answer_row["not_collected"] or 0):
        limitations.append("部分确认回答没有正文快照（历史未采集）")
    if win.partial_day:
        limitations.append("窗口包含当前未完成业务日")

    def _cost_currency() -> dict[str, str]:
        raw = cost_row.get("by_currency") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = {}
        return {str(k): str(v) for k, v in raw.items()}

    result = {
        "meta": build_meta(
            identity=identity,
            window=win,
            filters=filters,
            limitations=limitations,
            status="partial" if limitations else "ok",
        ),
        "requests": {
            "questions": _metric(
                questions,
                numerator=questions,
                cohort="requests_admitted_in_window",
                sample_count=questions,
            ),
            "active_users": _metric(
                active_users,
                numerator=active_users,
                cohort="requests_admitted_in_window",
                sample_count=questions,
            ),
            "historical_requests": _metric(
                int(historical),
                cohort="historical_excluded_from_default",
                sample_count=int(historical),
                excluded_from_default_cohort=True,
            ),
        },
        "attempts": {
            "attempts": _metric(
                attempts,
                numerator=attempts,
                cohort="attempts_admitted_in_window",
                sample_count=attempts,
            ),
            "succeeded": _metric(
                succeeded, cohort="attempts_admitted_in_window", sample_count=attempts
            ),
            "failed": _metric(
                int(attempt_row["failed"] or 0),
                cohort="attempts_admitted_in_window",
                sample_count=attempts,
            ),
            "cancelled": _metric(
                int(attempt_row["cancelled"] or 0),
                cohort="attempts_admitted_in_window",
                sample_count=attempts,
            ),
            "timed_out": _metric(
                int(attempt_row["timed_out"] or 0),
                cohort="attempts_admitted_in_window",
                sample_count=attempts,
            ),
            "not_ended": _metric(
                int(attempt_row["not_ended"] or 0),
                cohort="attempts_admitted_in_window",
                sample_count=attempts,
            ),
            "resumes": _metric(
                int(resumes),
                cohort="resumes_created_in_window",
                sample_count=int(resumes),
            ),
            "stop_intent": _metric(
                int(attempt_row["stop_intent"] or 0),
                cohort="attempts_admitted_in_window",
                sample_count=attempts,
            ),
            "cancel_confirmed": _metric(
                int(attempt_row["cancel_confirmed"] or 0),
                cohort="attempts_admitted_in_window",
                sample_count=attempts,
            ),
            "technical_completion_rate": _metric(
                round(succeeded / ended, 4) if ended else None,
                numerator=succeeded,
                denominator=ended,
                cohort="attempts_admitted_in_window",
                sample_count=ended,
                missing_count=max(0, attempts - ended),
            ),
        },
        "feedback": {
            "positive": _metric(
                positive, cohort="answers_confirmed_in_window", sample_count=confirmed
            ),
            "negative": _metric(
                negative, cohort="answers_confirmed_in_window", sample_count=confirmed
            ),
            "withdrawn": _metric(
                int(feedback_row["withdrawn"] or 0),
                cohort="answers_confirmed_in_window",
                sample_count=confirmed,
            ),
            "satisfaction_rate": _metric(
                round(positive / rated, 4) if rated else None,
                numerator=positive,
                denominator=rated,
                cohort="current_rated_answers",
                sample_count=rated,
                missing_count=max(0, confirmed - rated),
                note="未评价与已撤销不计入满意率分母",
            ),
            "rating_coverage": _metric(
                round(rated / eligible, 4) if eligible else None,
                numerator=rated,
                denominator=eligible,
                cohort="collection_eligible_answers_confirmed_in_window",
                sample_count=eligible,
                missing_count=max(0, eligible - rated),
            ),
            "copy_events": _metric(
                int(copy_row["copies"] or 0),
                cohort="collection_eligible_answers_confirmed_in_window",
                sample_count=int(copy_row["copies"] or 0),
            ),
            "copy_rate": _metric(
                int(copy_row["answers"]) / int(copy_row["eligible"])
                if copy_row["eligible"]
                else None,
                numerator=int(copy_row["answers"]),
                denominator=int(copy_row["eligible"]),
                cohort="answers_confirmed_in_window",
            ),
            "copied_answers": _metric(
                int(copy_row["answers"] or 0),
                cohort="answers_confirmed_in_window",
                sample_count=int(copy_row["answers"] or 0),
            ),
        },
        "content": {
            "confirmed_answers": _metric(
                confirmed, cohort="answers_confirmed_in_window", sample_count=confirmed
            ),
            "captured": _metric(
                captured, cohort="answers_confirmed_in_window", sample_count=confirmed
            ),
            "truncated": _metric(
                int(answer_row["truncated"] or 0),
                cohort="answers_confirmed_in_window",
                sample_count=confirmed,
            ),
            "capture_failed": _metric(
                int(answer_row["capture_failed"] or 0),
                cohort="answers_confirmed_in_window",
                sample_count=confirmed,
            ),
            "not_collected": _metric(
                int(answer_row["not_collected"] or 0),
                cohort="answers_confirmed_in_window",
                sample_count=confirmed,
                note="历史回答不从事后 Trace/checkpoint 回填",
            ),
            "capture_coverage": _metric(
                round(captured / confirmed, 4) if confirmed else None,
                numerator=captured,
                denominator=confirmed,
                cohort="answers_confirmed_in_window",
                sample_count=confirmed,
                missing_count=max(0, confirmed - captured),
            ),
        },
        "usage": {
            "model_calls": _metric(
                calls, cohort="model_calls_started_in_window", sample_count=calls
            ),
            "logical_calls": _metric(
                int(observation_row["logical_calls"] or 0),
                cohort="model_calls_started_in_window",
                sample_count=calls,
            ),
            "retry_attempts": _metric(
                max(0, calls - int(observation_row["logical_calls"] or 0)),
                cohort="model_calls_started_in_window",
                sample_count=calls,
            ),
            "measured_calls": _metric(
                measured, cohort="model_calls_started_in_window", sample_count=calls
            ),
            "usage_missing": _metric(
                int(observation_row["usage_missing"] or 0),
                cohort="model_calls_started_in_window",
                sample_count=calls,
            ),
            "invalid_usage": _metric(
                int(observation_row["invalid_usage"] or 0),
                cohort="model_calls_started_in_window",
                sample_count=calls,
            ),
            "failed_calls": _metric(
                int(observation_row["failed"] or 0),
                cohort="model_calls_started_in_window",
                sample_count=calls,
            ),
            "input_tokens": _metric(
                int(observation_row["input_tokens"])
                if observation_row["input_tokens"] is not None
                else None,
                cohort="model_calls_started_in_window",
                sample_count=measured,
                missing_count=max(0, calls - measured),
                note="已知分量小计，不是全量 Token",
            ),
            "output_tokens": _metric(
                int(observation_row["output_tokens"])
                if observation_row["output_tokens"] is not None
                else None,
                cohort="model_calls_started_in_window",
                sample_count=measured,
                missing_count=max(0, calls - measured),
                note="已知分量小计，不是全量 Token",
            ),
            "total_tokens": _metric(
                int(observation_row["total_tokens"])
                if observation_row["total_tokens"] is not None
                else None,
                cohort="model_calls_started_in_window",
                sample_count=measured,
                missing_count=max(0, calls - measured),
            ),
        },
        "cost": {
            "by_currency": _cost_currency(),
            "unpriced_calls": _metric(
                unpriced, cohort="model_calls_started_in_window", sample_count=calls
            ),
            "usage_incomplete_calls": _metric(
                int(cost_row.get("usage_incomplete") or 0),
                cohort="model_calls_started_in_window",
                sample_count=calls,
            ),
            "complete_cost_known": bool(calls)
            and not unpriced
            and not int(cost_row.get("usage_incomplete") or 0),
            "measurement_source": "latest_cost_revision",
        },
        "performance": {
            "model_duration_ms": _metric(
                _ms(observation_row["p50"]),
                cohort="model_calls_started_in_window",
                sample_count=int(observation_row["duration_samples"] or 0),
                missing_count=max(0, calls - int(observation_row["duration_samples"] or 0)),
                p50_ms=_ms(observation_row["p50"]),
                p95_ms=_ms(observation_row["p95"]),
                quantization="percentile_cont_on_window_detail",
            ),
            "model_ttft_ms": _metric(
                _ms(observation_row["ttft_p50"]),
                cohort="model_calls_started_in_window",
                sample_count=int(observation_row["ttft_samples"] or 0),
                missing_count=max(0, calls - int(observation_row["ttft_samples"] or 0)),
                p50_ms=_ms(observation_row["ttft_p50"]),
                p95_ms=_ms(observation_row["ttft_p95"]),
                quantization="percentile_cont_on_window_detail",
            ),
            "first_visible_answer_ms": _metric(
                _ms(timing_row["p50"]),
                cohort="browser_timing_in_window",
                sample_count=int(timing_row["samples"] or 0),
                missing_count=int(timing_row["missing"] or 0),
                p50_ms=_ms(timing_row["p50"]),
                p95_ms=_ms(timing_row["p95"]),
                quantization="percentile_cont_on_window_detail",
                measurement_source="browser_monotonic_clock",
            ),
        },
        "trace": {
            "links": _metric(
                int(trace_row["links"] or 0),
                cohort="trace_links_for_attempts_in_window",
                sample_count=int(trace_row["links"] or 0),
            ),
            "traces": _metric(
                int(trace_row["traces"] or 0),
                cohort="trace_links_for_attempts_in_window",
                sample_count=int(trace_row["traces"] or 0),
            ),
            "attempts_with_trace": _metric(
                int(trace_row["attempts"] or 0),
                cohort="trace_links_for_attempts_in_window",
                sample_count=attempts,
                missing_count=max(0, attempts - int(trace_row["attempts"] or 0)),
            ),
        },
    }

    for metric in result["feedback"].values():
        metric["not_collected_count"] = max(0, confirmed - eligible)
        if not interactions_enabled() or (confirmed > 0 and eligible == 0):
            metric["value"] = None
            metric["numerator"] = None
            metric["status"] = "not_collected"
    browser_metric = result["performance"]["first_visible_answer_ms"]
    browser_metric["invalid_count"] = int(timing_row["invalid"])
    browser_metric["not_collected_count"] = int(timing_row["not_collected"])
    return result


async def _scalar(
    session: AsyncSession,
    params: dict[str, Any],
    clauses: list[str],
    expression: str,
) -> int:
    """保留给轻量标量查询（当前总览不依赖它，避免重复扫描）."""
    value = (
        await session.execute(
            text(
                f"SELECT {expression} FROM agent_model_observations o WHERE "
                + " AND ".join(clauses)
            ),
            params,
        )
    ).scalar()
    return int(value or 0)


# ------------------------------------------------------------------ 日趋势


async def trends(
    session: AsyncSession,
    identity: Identity,
    *,
    window: str | None,
    start: str | None,
    end: str | None,
    business_code: str | None,
    graph_id: str | None,
    metric: str | None,
) -> dict[str, Any]:
    await scope_session(session, identity, require_metrics=True)
    tenant = tenant_id_of(identity)
    filters = {"business_code": business_code, "graph_id": graph_id}
    win = parse_window(window=window, start=start, end=end, as_of=dt.datetime.now(dt.UTC))
    if metric and metric not in (
        "all",
        "requests",
        "attempts",
        "answers",
        "feedback",
        "usage",
        "cost",
    ):
        raise ConsoleError("不支持的 metric", code="invalid_metric")
    params: dict[str, Any] = {"tenant": tenant, "start": win.start, "end": win.end}
    day = "(ts AT TIME ZONE 'Asia/Shanghai')::date"
    request_clauses = [
        "r.tenant_id=:tenant",
        "r.admitted_at>=:start",
        "r.admitted_at<:end",
        _deleted("r"),
        "COALESCE(r.identity_revision ->> 'historical','false') <> 'true'",
    ]
    request_clauses.append(
        "EXISTS (SELECT 1 FROM agent_submissions confirmed_submission WHERE "
        "confirmed_submission.tenant_id=r.tenant_id AND "
        "confirmed_submission.request_id=r.id AND "
        "confirmed_submission.admission_status='confirmed')"
    )
    request_clauses += _graph_filter("r", filters, params)
    request_rows = (
        (
            await session.execute(
                text(
                    "SELECT "
                    + day.replace("ts", "r.admitted_at")
                    + " AS day, count(*) AS n, count(DISTINCT r.user_id) AS users"
                    " FROM agent_requests r WHERE "
                    + " AND ".join(request_clauses)
                    + " GROUP BY 1 ORDER BY 1"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    attempt_clauses = [
        "a.tenant_id=:tenant",
        "a.admitted_at>=:start",
        "a.admitted_at<:end",
        _deleted("r"),
    ]
    attempt_clauses += _graph_filter("r", filters, params)
    attempt_rows = (
        (
            await session.execute(
                text(
                    "SELECT "
                    + day.replace("ts", "a.admitted_at")
                    + " AS day, count(*) AS attempts,"
                    " count(*) FILTER (WHERE a.state='succeeded') AS succeeded,"
                    " count(*) FILTER (WHERE a.state='failed') AS failed,"
                    " count(*) FILTER (WHERE a.state='cancelled') AS cancelled"
                    " FROM agent_run_attempts a JOIN agent_requests r"
                    " ON r.id=a.request_id AND r.tenant_id=a.tenant_id"
                    " WHERE " + " AND ".join(attempt_clauses) + " GROUP BY 1 ORDER BY 1"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    answer_clauses = [
        "a.tenant_id=:tenant",
        "a.confirmed_at>=:start",
        "a.confirmed_at<:end",
        "a.confirmed=true",
        "a.partial=false",
        _deleted("r"),
    ]
    answer_clauses += _graph_filter("r", filters, params)
    answer_rows = (
        (
            await session.execute(
                text(
                    "SELECT "
                    + day.replace("ts", "a.confirmed_at")
                    + " AS day, count(*) AS confirmed,"
                    " count(ac.id) FILTER (WHERE ac.capture_status='captured') AS captured"
                    " FROM agent_answers a"
                    " JOIN agent_run_attempts t ON t.id=a.attempt_id AND t.tenant_id=a.tenant_id"
                    " JOIN agent_requests r ON r.id=t.request_id AND r.tenant_id=t.tenant_id"
                    " LEFT JOIN agent_answer_content_metadata ac ON ac.tenant_id=a.tenant_id"
                    " AND ac.answer_id=a.id AND ac.content_version=a.content_version"
                    " WHERE " + " AND ".join(answer_clauses) + " GROUP BY 1 ORDER BY 1"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    feedback_clauses = [
        "f.tenant_id=:tenant",
        "a.confirmed_at>=:start",
        "a.confirmed_at<:end",
        _deleted("r"),
    ]
    feedback_clauses += _graph_filter("r", filters, params)
    feedback_rows = (
        (
            await session.execute(
                text(
                    "SELECT " + day.replace("ts", "a.confirmed_at") + " AS day,"
                    " count(*) FILTER (WHERE f.rating='positive') AS positive,"
                    " count(*) FILTER (WHERE f.rating='negative') AS negative,"
                    " count(*) FILTER (WHERE f.rating='none') AS withdrawn"
                    " FROM agent_feedback_current f"
                    " JOIN agent_answers a ON a.id=f.answer_id AND a.tenant_id=f.tenant_id"
                    " JOIN agent_run_attempts t ON t.id=a.attempt_id AND t.tenant_id=a.tenant_id"
                    " JOIN agent_requests r ON r.id=t.request_id AND r.tenant_id=t.tenant_id"
                    " WHERE " + " AND ".join(feedback_clauses) + " GROUP BY 1 ORDER BY 1"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    obs_clauses = [
        "o.tenant_id=:tenant",
        "o.started_at>=:start",
        "o.started_at<:end",
    ]
    obs_clauses += _graph_filter("o", filters, params)
    obs_rows = (
        (
            await session.execute(
                text(
                    "SELECT " + day.replace("ts", "o.started_at") + " AS day, count(*) AS calls,"
                    " count(*) FILTER (WHERE o.measurement_status='measured') AS measured,"
                    " sum(o.total_tokens) FILTER (WHERE o.measurement_status='measured')"
                    " AS tokens"
                    " FROM agent_model_observations o WHERE "
                    + " AND ".join(obs_clauses)
                    + " GROUP BY 1 ORDER BY 1"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    cost_rows = (
        (
            await session.execute(
                text(
                    """
                WITH latest AS (
                    SELECT o.observation_id, o.started_at, c.amount, c.currency,
                           c.measurement_status AS cost_status
                    FROM agent_model_observations o
                    LEFT JOIN (
                        SELECT DISTINCT ON (r.tenant_id,r.observation_id)
                            r.tenant_id,r.observation_id,r.amount,r.currency,r.measurement_status
                        FROM agent_cost_revisions r WHERE r.tenant_id=:tenant
                        ORDER BY r.tenant_id,r.observation_id,r.calculation_revision DESC
                    ) c ON c.tenant_id=o.tenant_id AND c.observation_id=o.observation_id
                    WHERE """
                    + " AND ".join(obs_clauses)
                    + """
                ), daily AS (
                    SELECT (started_at AT TIME ZONE 'Asia/Shanghai')::date AS day,
                           currency,
                           (sum(amount) FILTER (
                               WHERE amount IS NOT NULL AND cost_status='measured'))::text
                               AS subtotal,
                           count(*) FILTER (
                               WHERE amount IS NULL OR cost_status <> 'measured')
                               AS missing
                    FROM latest
                    GROUP BY 1, 2
                )
                SELECT day,
                       COALESCE(jsonb_object_agg(currency, subtotal)
                           FILTER (WHERE subtotal IS NOT NULL), '{}'::jsonb)
                           AS by_currency,
                       sum(missing) AS unpriced
                FROM daily GROUP BY day ORDER BY day
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    by_day: dict[str, dict[str, Any]] = {}
    current = business_day_start(win.start)
    end_day = (win.end - dt.timedelta(microseconds=1)).astimezone(dt.UTC)
    while current <= end_day:
        local_day = (current + dt.timedelta(hours=8)).date()
        by_day[local_day.isoformat()] = {
            "day": local_day.isoformat(),
            "requests": 0,
            "active_users": 0,
            "attempts": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
            "confirmed_answers": 0,
            "captured_answers": 0,
            "positive": 0,
            "negative": 0,
            "withdrawn": 0,
            "model_calls": 0,
            "measured_calls": 0,
            "tokens": None,
            "cost_by_currency": {},
            "unpriced_calls": 0,
        }
        current += dt.timedelta(days=1)
    for row in request_rows:
        bucket = by_day.setdefault(str(row["day"]), {})
        bucket["requests"] = int(row["n"])
        bucket["active_users"] = int(row["users"])
    for row in attempt_rows:
        bucket = by_day.setdefault(str(row["day"]), {})
        bucket["attempts"] = int(row["attempts"])
        bucket["succeeded"] = int(row["succeeded"])
        bucket["failed"] = int(row["failed"])
        bucket["cancelled"] = int(row["cancelled"])
    for row in answer_rows:
        bucket = by_day.setdefault(str(row["day"]), {})
        bucket["confirmed_answers"] = int(row["confirmed"])
        bucket["captured_answers"] = int(row["captured"])
    for row in feedback_rows:
        bucket = by_day.setdefault(str(row["day"]), {})
        bucket["positive"] = int(row["positive"])
        bucket["negative"] = int(row["negative"])
        bucket["withdrawn"] = int(row["withdrawn"])
    for row in obs_rows:
        bucket = by_day.setdefault(str(row["day"]), {})
        bucket["model_calls"] = int(row["calls"])
        bucket["measured_calls"] = int(row["measured"])
        bucket["tokens"] = int(row["tokens"]) if row["tokens"] is not None else None
    for row in cost_rows:
        bucket = by_day.setdefault(str(row["day"]), {})
        raw = row["by_currency"] or {}
        if isinstance(raw, str):
            raw = json.loads(raw)
        bucket["cost_by_currency"] = {str(k): str(v) for k, v in raw.items()}
        bucket["unpriced_calls"] = int(row["unpriced"] or 0)
    for bucket in by_day.values():
        for key in (
            "day",
            "requests",
            "active_users",
            "attempts",
            "succeeded",
            "failed",
            "cancelled",
            "confirmed_answers",
            "captured_answers",
            "positive",
            "negative",
            "withdrawn",
            "model_calls",
            "measured_calls",
            "tokens",
            "cost_by_currency",
            "unpriced_calls",
        ):
            bucket.setdefault(key, 0 if key != "cost_by_currency" and key != "tokens" else None)
        if bucket["cost_by_currency"] is None:
            bucket["cost_by_currency"] = {}
    return {
        "meta": build_meta(
            identity=identity,
            window=win,
            filters=filters,
            limitations=(["窗口包含当前未完成业务日"] if win.partial_day else []),
            status="partial" if win.partial_day else "ok",
        ),
        "metric": metric or "all",
        "bucket_timezone": "Asia/Shanghai",
        "items": [by_day[key] for key in sorted(by_day)],
        "cohorts": {
            "requests": "requests_admitted_in_window",
            "attempts": "attempts_admitted_in_window",
            "answers": "answers_confirmed_in_window",
            "feedback": "current_rating_by_answer_confirmed_in_window",
            "usage": "model_calls_started_in_window",
            "cost": "latest_cost_revision",
        },
    }


# ------------------------------------------------------------------ 能力


async def capabilities(
    session: AsyncSession,
    identity: Identity,
    *,
    window: str | None,
    start: str | None,
    end: str | None,
    business_code: str | None,
    graph_id: str | None,
    group_by: str | None,
    cursor: str | None,
    limit: int | None,
) -> dict[str, Any]:
    await scope_session(session, identity, require_metrics=True)
    tenant = tenant_id_of(identity)
    dimension = group_by or "business"
    if dimension not in ("business", "skill", "tool"):
        raise ConsoleError("group_by 必须是 business/skill/tool", code="invalid_group_by")
    effective_limit = parse_limit(limit)
    filters = {
        "business_code": business_code,
        "graph_id": graph_id,
        "group_by": dimension,
    }
    win, decoded = _resolve(
        window=window,
        start=start,
        end=end,
        cursor=cursor,
        tenant=tenant,
        identity=identity,
        filters=filters,
        sort=f"capabilities_{dimension}_asc",
    )
    params: dict[str, Any] = {
        "tenant": tenant,
        "start": win.start,
        "end": win.end,
        "limit": effective_limit + 1,
    }
    after = ""
    if decoded:
        params["after"] = decoded.key
    if dimension == "business":
        if decoded:
            after = " AND COALESCE(a.business_code, a.graph_id) > :after"
        clauses = [
            "a.tenant_id=:tenant",
            "a.admitted_at>=:start",
            "a.admitted_at<:end",
            _deleted("r"),
        ]
        clauses += _graph_filter("r", filters, params)
        if after:
            clauses.append(after[5:])
        sql = text(
            f"""
            SELECT COALESCE(a.business_code, a.graph_id) AS grp,
                   count(*) AS attempts,
                   count(*) FILTER (WHERE a.state='succeeded') AS succeeded,
                   count(*) FILTER (WHERE a.state='failed') AS failed,
                   count(*) FILTER (WHERE a.state='cancelled') AS cancelled,
                   count(*) FILTER (WHERE a.state='timed_out') AS timed_out,
                   count(DISTINCT a.user_id) AS distinct_users
            FROM agent_run_attempts a
            JOIN agent_requests r ON r.id=a.request_id AND r.tenant_id=a.tenant_id
            WHERE {" AND ".join(clauses)}
            GROUP BY 1
            ORDER BY grp ASC LIMIT :limit
            """
        )
    elif dimension == "skill":
        if decoded:
            after = " AND COALESCE(s.skill_id, s.span_name) > :after"
        clauses = [
            "s.tenant_id=:tenant",
            "s.started_at>=:start",
            "s.started_at<:end",
            _deleted("r"),
        ]
        clauses += _graph_filter("r", filters, params)
        if after:
            clauses.append(after[5:])
        sql = text(
            f"""
            SELECT COALESCE(s.skill_id, s.span_name) AS grp,
                   count(DISTINCT s.attempt_id) AS attempts,
                   count(*) FILTER (WHERE s.stage='matched') AS matched_spans,
                   count(*) FILTER (WHERE s.stage='selected') AS selected_spans,
                   count(*) FILTER (WHERE s.stage='loaded') AS loaded_spans,
                   count(*) FILTER (WHERE s.stage='loaded' AND s.status='succeeded')
                       AS loaded_succeeded,
                   count(*) FILTER (WHERE s.stage='loaded' AND s.status='failed')
                       AS loaded_failed,
                   count(*) FILTER (WHERE s.stage='executed') AS executed_spans,
                   count(*) FILTER (WHERE s.stage='executed' AND s.status='succeeded')
                       AS executed_succeeded,
                   count(*) FILTER (WHERE s.stage='executed' AND s.status='not_executed')
                       AS not_executed,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY s.duration_ms)
                       FILTER (WHERE s.duration_ms IS NOT NULL) AS duration_p95_ms
            FROM agent_capability_spans s
            JOIN agent_run_attempts a ON a.id=s.attempt_id AND a.tenant_id=s.tenant_id
            JOIN agent_requests r ON r.id=a.request_id AND r.tenant_id=a.tenant_id
            WHERE {" AND ".join(clauses)}
            GROUP BY 1
            ORDER BY grp ASC LIMIT :limit
            """
        )
    else:
        if decoded:
            after = " AND COALESCE(c.service_key || ':' || c.tool_name, c.tool_name) > :after"
        clauses = [
            "c.tenant_id=:tenant",
            "c.started_at>=:start",
            "c.started_at<:end",
            _deleted("r"),
        ]
        clauses += _graph_filter("r", filters, params)
        if after:
            clauses.append(after[5:])
        sql = text(
            f"""
            SELECT COALESCE(c.service_key || ':' || c.tool_name, c.tool_name) AS grp,
                   count(DISTINCT c.logical_call_id) AS logical_calls,
                   count(a.observation_id) AS attempt_records,
                   count(*) FILTER (WHERE a.network_attempted IS TRUE) AS network_attempts,
                   count(*) FILTER (WHERE a.network_attempted IS NULL) AS network_unknown,
                   count(*) FILTER (WHERE a.fallback_kind IS NOT NULL) AS recovery_records,
                   count(*) FILTER (WHERE a.status='permission_denied') AS permission_denied,
                   count(*) FILTER (WHERE a.status='validation_rejected') AS validation_rejected,
                   count(*) FILTER (WHERE a.status='technical_error') AS technical_error,
                   count(*) FILTER (WHERE a.status='empty_result') AS business_empty,
                   count(*) FILTER (WHERE a.status='cancelled') AS cancelled,
                   count(*) FILTER (WHERE a.status='timed_out') AS timed_out,
                   count(*) FILTER (WHERE a.status='unknown') AS unknown,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY a.duration_ms)
                       FILTER (WHERE a.duration_ms IS NOT NULL) AS duration_p95_ms
            FROM agent_tool_calls c
            JOIN agent_run_attempts a2 ON a2.id=c.attempt_id AND a2.tenant_id=c.tenant_id
            JOIN agent_requests r ON r.id=a2.request_id AND r.tenant_id=a2.tenant_id
            LEFT JOIN agent_tool_call_attempts a
              ON a.tenant_id=c.tenant_id AND a.logical_call_id=c.logical_call_id
            WHERE {" AND ".join(clauses)}
            GROUP BY 1
            ORDER BY grp ASC LIMIT :limit
            """
        )
    rows = (await session.execute(sql, params)).mappings().all()
    has_more = len(rows) > effective_limit
    rows = rows[:effective_limit]
    items = []
    for row in rows:
        item = {key: value for key, value in dict(row).items() if key != "grp"}
        item["group"] = row["grp"]
        if isinstance(item.get("duration_p95_ms"), Decimal):
            item["duration_p95_ms"] = float(item["duration_p95_ms"])
        item["group_by"] = dimension
        item["measurement_source"] = "direct_sql"
        items.append(item)
    next_cursor = (
        encode_cursor(
            tenant_id=tenant,
            context_version=identity.context_version,
            user_id=str(identity.user.id),
            filter_key=filter_fingerprint(filters, window_key=window_key(window, start, end)),
            sort=f"capabilities_{dimension}_asc",
            window=win,
            key=rows[-1]["grp"],
        )
        if has_more and rows
        else None
    )
    return {
        "meta": build_meta(
            identity=identity,
            window=win,
            filters=filters,
            limitations=(
                ["Skill 阶段是内部观测维度，不增加提问量"] if dimension == "skill" else []
            ),
        ),
        "group_by": dimension,
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "limit": effective_limit,
    }


async def capability_details(
    session: AsyncSession,
    identity: Identity,
    *,
    kind: str,
    window: str | None,
    start: str | None,
    end: str | None,
    business_code: str | None,
    graph_id: str | None,
    skill_id: str | None,
    service_key: str | None,
    tool_id: str | None,
    cursor: str | None,
    limit: int | None,
) -> dict[str, Any]:
    """能力明细：skill_span / tool_call（attempt 下钻走运行列表）."""
    await scope_session(session, identity, require_ops=True)
    tenant = tenant_id_of(identity)
    if kind not in ("skill_span", "tool_call"):
        raise ConsoleError("kind 必须是 skill_span 或 tool_call", code="invalid_kind")
    effective_limit = parse_limit(limit)
    filters = {
        "kind": kind,
        "business_code": business_code,
        "graph_id": graph_id,
        "skill_id": skill_id,
        "service_key": service_key,
        "tool_id": tool_id,
    }
    win, decoded = _resolve(
        window=window,
        start=start,
        end=end,
        cursor=cursor,
        tenant=tenant,
        identity=identity,
        filters=filters,
        sort="started_at_desc",
    )
    params: dict[str, Any] = {
        "tenant": tenant,
        "start": win.start,
        "end": win.end,
        "limit": effective_limit + 1,
    }
    after = ""
    if decoded:
        try:
            ts_text, id_text = decoded.key.split("|", 1)
            params["after_ts"] = dt.datetime.fromisoformat(ts_text)
            params["after_id"] = str(uuid.UUID(id_text))
        except (ValueError, IndexError) as exc:
            raise ConsoleError("游标非法", code="bad_cursor") from exc
        if kind == "skill_span":
            after = " AND (s.started_at, s.id) < (:after_ts, CAST(:after_id AS uuid))"
        else:
            after = " AND (c.started_at,COALESCE(a.id,c.id)) < (:after_ts,CAST(:after_id AS uuid))"
    if kind == "skill_span":
        clauses = [
            "s.tenant_id=:tenant",
            "s.started_at>=:start",
            "s.started_at<:end",
            _deleted("r"),
        ]
        clauses += _graph_filter("r", filters, params)
        if skill_id:
            clauses.append("s.skill_id = :skill_id")
            params["skill_id"] = skill_id
        rows = (
            (
                await session.execute(
                    text(
                        f"""
                    SELECT s.id::text AS id, s.started_at AS started_at,
                           s.attempt_id::text AS attempt_id, s.span_kind, s.span_name,
                           s.stage, s.status, s.duration_ms, s.skill_id, s.skill_version,
                           s.selection_source, s.user_id::text AS user_id
                    FROM agent_capability_spans s
                    JOIN agent_run_attempts a ON a.id=s.attempt_id AND a.tenant_id=s.tenant_id
                    JOIN agent_requests r ON r.id=a.request_id AND r.tenant_id=a.tenant_id
                    WHERE {" AND ".join(clauses)}{after}
                    ORDER BY s.started_at DESC, s.id DESC LIMIT :limit
                    """
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
    else:
        clauses = [
            "c.tenant_id=:tenant",
            "c.started_at>=:start",
            "c.started_at<:end",
            _deleted("r"),
        ]
        clauses += _graph_filter("r", filters, params)
        if service_key:
            clauses.append("c.service_key = :service_key")
            params["service_key"] = service_key
        if tool_id:
            clauses.append("c.tool_name = :tool_id")
            params["tool_id"] = tool_id
        rows = (
            (
                await session.execute(
                    text(
                        f"""
                    SELECT COALESCE(a.id,c.id)::text AS id, c.started_at AS started_at,
                           c.logical_call_id::text AS logical_call_id,
                           c.attempt_id::text AS attempt_id, c.tool_name, c.service_key,
                           c.skill_id, c.user_id::text AS user_id,
                           a.retry_index, a.status, a.duration_ms, a.fallback_kind,
                           a.error_category, a.network_attempted
                    FROM agent_tool_calls c
                    JOIN agent_run_attempts a2 ON a2.id=c.attempt_id AND a2.tenant_id=c.tenant_id
                    JOIN agent_requests r ON r.id=a2.request_id AND r.tenant_id=a2.tenant_id
                    LEFT JOIN agent_tool_call_attempts a
                      ON a.tenant_id=c.tenant_id AND a.logical_call_id=c.logical_call_id
                    WHERE {" AND ".join(clauses)}{after}
                    ORDER BY c.started_at DESC, COALESCE(a.id,c.id) DESC
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
    items = []
    for row in rows:
        item = dict(row)
        for key, value in list(item.items()):
            if isinstance(value, dt.datetime):
                item[key] = value.isoformat()
        items.append(item)
    next_cursor = (
        encode_cursor(
            tenant_id=tenant,
            context_version=identity.context_version,
            user_id=str(identity.user.id),
            filter_key=filter_fingerprint(filters, window_key=window_key(window, start, end)),
            sort="started_at_desc",
            window=win,
            key=f"{rows[-1]['started_at'].isoformat()}|{rows[-1]['id']}",
        )
        if has_more and rows
        else None
    )
    return {
        "meta": build_meta(identity=identity, window=win, filters=filters),
        "kind": kind,
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "limit": effective_limit,
    }


# ------------------------------------------------------------------ 性能


async def performance(
    session: AsyncSession,
    identity: Identity,
    *,
    window: str | None,
    start: str | None,
    end: str | None,
    business_code: str | None,
    graph_id: str | None,
    model: str | None,
    provider: str | None,
    source: str | None,
) -> dict[str, Any]:
    await scope_session(session, identity, require_metrics=True)
    tenant = tenant_id_of(identity)
    filters = {
        "business_code": business_code,
        "graph_id": graph_id,
        "model": model,
        "provider": provider,
        "source": source or "all",
    }
    win = parse_window(window=window, start=start, end=end, as_of=dt.datetime.now(dt.UTC))
    params: dict[str, Any] = {"tenant": tenant, "start": win.start, "end": win.end}
    selected = source or "all"
    if selected not in ("all", "service", "browser", "model"):
        raise ConsoleError("source 必须是 all/service/browser/model", code="invalid_source")

    service_clauses = [
        "a.tenant_id=:tenant",
        "a.admitted_at>=:start",
        "a.admitted_at<:end",
        _deleted("r"),
    ]
    service_clauses += _graph_filter("r", filters, params)
    service = (
        (
            await session.execute(
                text(
                    """
                SELECT percentile_cont(0.5) WITHIN GROUP (
                           ORDER BY EXTRACT(EPOCH FROM (a.ended_at-a.admitted_at))*1000)
                           FILTER (WHERE a.ended_at IS NOT NULL) AS p50,
                       percentile_cont(0.95) WITHIN GROUP (
                           ORDER BY EXTRACT(EPOCH FROM (a.ended_at-a.admitted_at))*1000)
                           FILTER (WHERE a.ended_at IS NOT NULL) AS p95,
                       percentile_cont(0.99) WITHIN GROUP (
                           ORDER BY EXTRACT(EPOCH FROM (a.ended_at-a.admitted_at))*1000)
                           FILTER (WHERE a.ended_at IS NOT NULL) AS p99,
                       count(*) FILTER (WHERE a.ended_at IS NOT NULL) AS samples,
                       count(*) FILTER (WHERE a.queued_at IS NOT NULL AND a.started_at IS NOT NULL)
                           AS queue_samples,
                       percentile_cont(0.5) WITHIN GROUP (
                           ORDER BY EXTRACT(EPOCH FROM (a.started_at-a.queued_at))*1000)
                           FILTER (WHERE a.queued_at IS NOT NULL AND a.started_at IS NOT NULL)
                           AS queue_p50,
                       percentile_cont(0.95) WITHIN GROUP (
                           ORDER BY EXTRACT(EPOCH FROM (a.started_at-a.queued_at))*1000)
                           FILTER (WHERE a.queued_at IS NOT NULL AND a.started_at IS NOT NULL)
                           AS queue_p95,
                       count(*) FILTER (WHERE a.ended_at IS NULL) AS missing
                FROM agent_run_attempts a
                JOIN agent_requests r ON r.id=a.request_id AND r.tenant_id=a.tenant_id
                WHERE """
                    + " AND ".join(service_clauses)
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    _, browser = await browser_metrics(
        session, tenant=tenant, start=win.start, end=win.end, as_of=win.as_of, filters=filters
    )
    obs_clauses = [
        "o.tenant_id=:tenant",
        "o.started_at>=:start",
        "o.started_at<:end",
    ]
    obs_clauses += _graph_filter("o", filters, params)
    if model:
        obs_clauses.append("o.model = :model")
    if provider:
        obs_clauses.append("o.provider = :provider")
    model_row = (
        (
            await session.execute(
                text(
                    """
                SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY o.ttft_ms)
                           FILTER (WHERE o.ttft_ms IS NOT NULL) AS p50,
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY o.ttft_ms)
                           FILTER (WHERE o.ttft_ms IS NOT NULL) AS p95,
                       percentile_cont(0.99) WITHIN GROUP (ORDER BY o.ttft_ms)
                           FILTER (WHERE o.ttft_ms IS NOT NULL) AS p99,
                       count(*) FILTER (WHERE o.ttft_ms IS NOT NULL) AS samples,
                       count(*) FILTER (WHERE o.ttft_ms IS NULL) AS missing
                FROM agent_model_observations o WHERE """
                    + " AND ".join(obs_clauses)
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
                    """
                SELECT o.model, o.provider, count(*) AS calls,
                       count(o.ttft_ms) AS ttft_samples,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY o.ttft_ms) AS p50,
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY o.ttft_ms) AS p95
                FROM agent_model_observations o WHERE """
                    + " AND ".join(obs_clauses)
                    + " GROUP BY o.model, o.provider ORDER BY calls DESC LIMIT 21"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    not_applicable = {
        "status": "not_applicable",
        "value": None,
        "sample_count": 0,
        "missing_count": 0,
        "measurement_source": "not_applicable",
        "cohort": "not_applicable",
        "reason": "model/provider 过滤只适用于模型分区",
    }

    def partition(row: dict[str, Any], cohort: str, source_name: str) -> dict[str, Any]:
        samples = int(row["samples"] or 0)
        return {
            **_metric(
                _ms(row["p50"]),
                cohort=cohort,
                sample_count=samples,
                missing_count=int(row["missing"] or 0),
                measurement_source=source_name,
                p50_ms=_ms(row["p50"]),
                p95_ms=_ms(row["p95"]),
                p99_ms=_ms(row["p99"]),
                quantization="percentile_cont_on_window_detail",
            )
        }

    partitions: dict[str, Any] = {}
    model_filtered = bool(model or provider)
    if selected in ("all", "service") and not model_filtered:
        partitions["service"] = partition(
            service, "attempts_admitted_in_window", "server_clock_admitted_to_ended"
        )
        partitions["service"]["queue_wait_ms"] = _metric(
            _ms(service["queue_p50"]),
            cohort="attempts_with_queue_timestamps",
            sample_count=int(service["queue_samples"] or 0),
            missing_count=max(0, int(service["samples"] or 0) - int(service["queue_samples"] or 0)),
            p50_ms=_ms(service["queue_p50"]),
            p95_ms=_ms(service["queue_p95"]),
            measurement_source="server_clock_queued_to_started",
        )
    else:
        partitions["service"] = dict(
            not_applicable,
            reason=(
                "model/provider 过滤只适用于模型分区"
                if model_filtered
                else "source 过滤未选择服务分区"
            ),
        )
    if selected in ("all", "browser") and not model_filtered:
        partitions["browser"] = partition(
            browser, "attempts_admitted_in_window", "browser_monotonic_clock"
        )
        partitions["browser"]["invalid_count"] = int(browser["invalid"])
        partitions["browser"]["not_collected_count"] = int(browser["not_collected"])
        partitions["browser"]["display_complete"] = {
            "sample_count": int(browser["display_samples"]),
            "p50_ms": _ms(browser["display_p50"]),
            "p95_ms": _ms(browser["display_p95"]),
            "p99_ms": _ms(browser["display_p99"]),
            "measurement_source": "browser_monotonic_clock",
        }
    else:
        partitions["browser"] = dict(
            not_applicable,
            reason=(
                "model/provider 过滤只适用于模型分区"
                if model_filtered
                else "source 过滤未选择浏览器分区"
            ),
        )
    if selected in ("all", "model"):
        partitions["model"] = partition(model_row, "model_calls_started_in_window", "runtime_ttft")
        partitions["model"]["by_model"] = [
            {
                "model": row["model"],
                "provider": row["provider"],
                "calls": int(row["calls"]),
                "ttft_samples": int(row["ttft_samples"]),
                "p50_ms": _ms(row["p50"]),
                "p95_ms": _ms(row["p95"]),
            }
            for row in by_model[:20]
        ]
        partitions["model"]["by_model_truncated"] = len(by_model) > 20
    else:
        partitions["model"] = dict(not_applicable, reason="source 过滤未选择模型分区")
    return {
        "meta": build_meta(identity=identity, window=win, filters=filters),
        "partitions": partitions,
        "not_applicable": not_applicable,
    }


# ------------------------------------------------------------------ 费用


async def costs(
    session: AsyncSession,
    identity: Identity,
    *,
    window: str | None,
    start: str | None,
    end: str | None,
    business_code: str | None,
    graph_id: str | None,
    model: str | None,
    provider: str | None,
    group_by: str | None,
    cursor: str | None,
    limit: int | None,
) -> dict[str, Any]:
    await scope_session(session, identity, require_metrics=True)
    tenant = tenant_id_of(identity)
    dimension = group_by or "model"
    if dimension not in ("model", "user", "business", "currency"):
        raise ConsoleError("group_by 必须是 model/user/business/currency", code="invalid_group_by")
    if dimension == "user":
        # 费用按用户分组返回可识别标识：除 metrics 外必须另有 ops。
        ensure_identifiable_access(identity)
    effective_limit = parse_limit(limit)
    filters = {
        "business_code": business_code,
        "graph_id": graph_id,
        "model": model,
        "provider": provider,
        "group_by": dimension,
    }
    win, decoded = _resolve(
        window=window,
        start=start,
        end=end,
        cursor=cursor,
        tenant=tenant,
        identity=identity,
        filters=filters,
        sort=f"costs_{dimension}_asc",
    )
    params: dict[str, Any] = {
        "tenant": tenant,
        "start": win.start,
        "end": win.end,
        "limit": effective_limit + 1,
    }
    obs_clauses = [
        "o.tenant_id=:tenant",
        "o.started_at>=:start",
        "o.started_at<:end",
    ]
    obs_clauses += _graph_filter("o", filters, params)
    if model:
        obs_clauses.append("o.model = :model")
    if provider:
        obs_clauses.append("o.provider = :provider")
    group_expr = {
        "model": "c.model",
        "user": "c.user_id::text",
        "business": "COALESCE(c.graph_id, 'unknown')",
        "currency": "COALESCE(c.currency, 'unknown')",
    }[dimension]
    secondary = "COALESCE(c.currency, 'unknown')"
    after = ""
    if decoded:
        try:
            grp, currency = decoded.key.split("|", 1)
        except ValueError as exc:
            raise ConsoleError("游标非法", code="bad_cursor") from exc
        params["after_grp"] = grp
        params["after_currency"] = currency
        after = " AND (r.grp > :after_grp OR (r.grp = :after_grp AND r.currency > :after_currency))"
    rows = (
        (
            await session.execute(
                text(
                    f"""
                WITH latest AS (
                    SELECT o.user_id, o.graph_id, o.model, o.provider, o.started_at,
                           o.measurement_status AS usage_status,
                           c.amount, c.currency,
                           c.measurement_status AS cost_status
                    FROM agent_model_observations o
                    LEFT JOIN (
                        SELECT DISTINCT ON (r.tenant_id,r.observation_id)
                            r.tenant_id,r.observation_id,r.amount,r.currency,r.measurement_status
                        FROM agent_cost_revisions r WHERE r.tenant_id=:tenant
                        ORDER BY r.tenant_id,r.observation_id,r.calculation_revision DESC
                    ) c ON c.tenant_id=o.tenant_id AND c.observation_id=o.observation_id
                    WHERE {" AND ".join(obs_clauses)}
                ), priced AS (
                    SELECT {group_expr} AS grp, {secondary} AS currency,
                           sum(amount)::text AS subtotal, count(*) AS priced_calls
                    FROM latest c
                    WHERE amount IS NOT NULL AND cost_status='measured'
                    GROUP BY 1, 2
                ), unknown AS (
                    SELECT {group_expr} AS grp, {secondary} AS currency,
                           count(*) AS unknown_calls
                    FROM latest c
                    WHERE amount IS NULL OR cost_status <> 'measured'
                       OR usage_status <> 'measured'
                    GROUP BY 1, 2
                )
                SELECT r.grp, r.currency, r.subtotal, r.priced_calls,
                       COALESCE(u.unknown_calls, 0) AS unknown_calls
                FROM (
                    SELECT COALESCE(p.grp, u.grp) AS grp,
                           COALESCE(p.currency, u.currency) AS currency,
                           p.subtotal, COALESCE(p.priced_calls, 0) AS priced_calls,
                           u.unknown_calls
                    FROM priced p
                    FULL OUTER JOIN unknown u
                      ON u.grp = p.grp AND u.currency = p.currency
                ) r
                LEFT JOIN unknown u
                  ON u.grp = r.grp AND u.currency = r.currency
                WHERE true{after}
                ORDER BY r.grp ASC, r.currency ASC
                LIMIT :limit
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    totals = (
        (
            await session.execute(
                text(
                    """
                WITH latest AS (
                    SELECT o.measurement_status AS usage_status,
                           c.amount, c.currency, c.measurement_status AS cost_status
                    FROM agent_model_observations o
                    LEFT JOIN (
                        SELECT DISTINCT ON (r.tenant_id,r.observation_id)
                            r.tenant_id,r.observation_id,r.amount,r.currency,r.measurement_status
                        FROM agent_cost_revisions r WHERE r.tenant_id=:tenant
                        ORDER BY r.tenant_id,r.observation_id,r.calculation_revision DESC
                    ) c ON c.tenant_id=o.tenant_id AND c.observation_id=o.observation_id
                    WHERE """
                    + " AND ".join(obs_clauses)
                    + """
                )
                SELECT count(*) AS total,
                       count(*) FILTER (WHERE amount IS NULL OR cost_status <> 'measured')
                           AS unpriced,
                       count(*) FILTER (WHERE usage_status <> 'measured')
                           AS usage_incomplete
                FROM latest
                """
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    has_more = len(rows) > effective_limit
    rows = rows[:effective_limit]
    items = [
        {
            "group_by": dimension,
            "group": row["grp"],
            "currency": row["currency"],
            "subtotal": str(row["subtotal"]) if row["subtotal"] is not None else None,
            "priced_calls": int(row["priced_calls"] or 0),
            "unknown_calls": int(row["unknown_calls"] or 0),
            "measurement_status": ("measured" if row["subtotal"] is not None else "not_collected"),
            "measurement_source": "latest_cost_revision",
        }
        for row in rows
    ]
    next_cursor = (
        encode_cursor(
            tenant_id=tenant,
            context_version=identity.context_version,
            user_id=str(identity.user.id),
            filter_key=filter_fingerprint(filters, window_key=window_key(window, start, end)),
            sort=f"costs_{dimension}_asc",
            window=win,
            key=f"{rows[-1]['group']}|{rows[-1]['currency']}",
        )
        if has_more and rows
        else None
    )
    unpriced = int(totals["unpriced"] or 0)
    usage_incomplete = int(totals["usage_incomplete"] or 0)
    return {
        "meta": build_meta(
            identity=identity,
            window=win,
            filters=filters,
            limitations=(
                ["存在缺少 usage 或价格的调用，费用不是零"] if unpriced or usage_incomplete else []
            ),
            status="partial" if unpriced or usage_incomplete else "ok",
        ),
        "totals": {
            "observations": int(totals["total"] or 0),
            "unpriced_calls": unpriced,
            "usage_incomplete_calls": usage_incomplete,
            "complete_cost_known": bool(totals["total"]) and not unpriced and not usage_incomplete,
        },
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "limit": effective_limit,
    }
