"""反馈/复制/浏览器延迟统计（change add-chat-feedback-interactions §5）。

口径（agent-feedback-metrics spec）：
- 默认队列：确认时间（confirmed_at）进入半开窗口 [start, end) 的可评价回答
  （本人已确认、非 partial、线程未删除）。
- 满意率 = 当前有效正面 / 当前有效评价（positive+negative）；撤销（none）退出分子，
  未评价不计入评价分母；无分母 → null。
- 覆盖率 = 有当前有效评价的回答 / 可评价回答。
- 复制：browser.copy 事件次数与 distinct 回答数；复制率 = distinct_copied / 可复制回答。
- 浏览器延迟：仅同页面单调计时有效明细的 P50/P95/P99（不平均分组分位数）。
- 启用时刻之前的历史数据另列 not_collected，不伪零。
- UTC 存储；业务日窗口由调用方换算成 UTC 边界传入（Asia/Shanghai = UTC+8，无夏令时）。
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_feedback import AgentFeedbackCurrent
from app.models_ledger import (
    AgentAnswer,
    AgentEvent,
    AgentRequest,
    AgentRunAttempt,
    AgentThreadDeletion,
)
from app.services.identity import Identity
from app.services.interaction_policy import enabled_at
from app.services.ledger import LedgerError, NotFound, _enable_operations_read

METRIC_VERSION = "fb-v1"
# 采集启用时刻（本 change 上线；启用前的回答没有任何采集证据）

BUCKET_MS = (50, 95, 99)


class MetricsError(LedgerError):
    status_code = 400


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * pct
    low = int(rank // 1)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


def _distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "p50_ms": _percentile(ordered, 0.50),
        "p95_ms": _percentile(ordered, 0.95),
        "p99_ms": _percentile(ordered, 0.99),
    }


async def feedback_metrics(
    session: AsyncSession,
    identity: Identity,
    *,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> dict[str, Any]:
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFound("对象不存在", code="not_found")
    await _enable_operations_read(session, identity)
    window_end = end or dt.datetime.now(dt.UTC)
    window_start = start or (window_end - dt.timedelta(days=7))
    if window_start >= window_end:
        raise MetricsError("统计窗口起点必须早于终点", code="invalid_window")

    collection_enabled_at = enabled_at()
    # ---------------- 反馈队列：窗口内确认的可评价回答 ----------------
    eligible_rows = (
        await session.execute(
            select(AgentAnswer.id, AgentAnswer.confirmed_at, AgentRequest.admitted_at)
            .join(
                AgentRunAttempt,
                and_(
                    AgentRunAttempt.id == AgentAnswer.attempt_id,
                    AgentRunAttempt.tenant_id == AgentAnswer.tenant_id,
                ),
            )
            .join(
                AgentRequest,
                and_(
                    AgentRequest.id == AgentRunAttempt.request_id,
                    AgentRequest.tenant_id == AgentRunAttempt.tenant_id,
                ),
            )
            .where(
                AgentAnswer.tenant_id == tenant_id,
                AgentAnswer.confirmed.is_(True),
                AgentAnswer.partial.is_(False),
                AgentAnswer.confirmed_at >= window_start,
                AgentAnswer.confirmed_at < window_end,
                ~select(1)
                .select_from(AgentThreadDeletion)
                .where(
                    AgentThreadDeletion.tenant_id == AgentAnswer.tenant_id,
                    AgentThreadDeletion.thread_id == AgentRequest.thread_id,
                )
                .exists(),
            )
        )
    ).all()
    eligible_ids = [r.id for r in eligible_rows]
    pre_collected = [
        r
        for r in eligible_rows
        if r.confirmed_at is not None
        and (collection_enabled_at is None or r.confirmed_at < collection_enabled_at)
    ]
    not_collected_ids = {r.id for r in pre_collected}
    collectible_ids = [i for i in eligible_ids if i not in not_collected_ids]

    feedback_by_answer: dict[uuid.UUID, AgentFeedbackCurrent] = {}
    if collectible_ids:
        fb_rows = (
            (
                await session.execute(
                    select(AgentFeedbackCurrent).where(
                        AgentFeedbackCurrent.tenant_id == tenant_id,
                        AgentFeedbackCurrent.answer_id.in_(collectible_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        feedback_by_answer = {f.answer_id: f for f in fb_rows}

    ratings = [feedback_by_answer[i].rating for i in collectible_ids if i in feedback_by_answer]
    positive = sum(1 for r in ratings if r == "positive")
    negative = sum(1 for r in ratings if r == "negative")
    rated = positive + negative
    eligible = len(collectible_ids)

    # ---------------- 复制：事件次数 / distinct 回答 ----------------
    copy_events = (
        await session.execute(
            select(AgentEvent.answer_id, AgentEvent.event_id, AgentEvent.occurred_at).where(
                AgentEvent.tenant_id == tenant_id,
                AgentEvent.event_type == "browser.copy",
                AgentEvent.answer_id.in_(collectible_ids),
            )
        )
    ).all()
    copy_total = len(copy_events)
    copied_answers = {r.answer_id for r in copy_events if r.answer_id is not None}

    # ---------------- 浏览器计时：有效明细（payload 白名单在受理层保证） ----------------
    # 时间队列以运行受理时间归属窗口，包括没有产生回答的失败/取消运行。
    cohort = (
        await session.execute(
            select(AgentRunAttempt.id, AgentRequest.admitted_at)
            .join(
                AgentRequest,
                and_(
                    AgentRequest.id == AgentRunAttempt.request_id,
                    AgentRequest.tenant_id == AgentRunAttempt.tenant_id,
                ),
            )
            .where(
                AgentRunAttempt.tenant_id == tenant_id,
                AgentRequest.admitted_at >= window_start,
                AgentRequest.admitted_at < window_end,
                ~select(1)
                .select_from(AgentThreadDeletion)
                .where(
                    AgentThreadDeletion.tenant_id == AgentRequest.tenant_id,
                    AgentThreadDeletion.thread_id == AgentRequest.thread_id,
                )
                .exists(),
            )
        )
    ).all()
    timing_ids = {
        r.id
        for r in cohort
        if collection_enabled_at is not None and r.admitted_at >= collection_enabled_at
    }
    timing_rows = (
        await session.execute(
            select(AgentEvent.payload, AgentEvent.occurred_at, AgentEvent.attempt_id)
            .where(
                AgentEvent.tenant_id == tenant_id,
                AgentEvent.event_type == "browser.timing",
                AgentEvent.attempt_id.in_(timing_ids),
            )
            .order_by(AgentEvent.received_at, AgentEvent.event_id)
        )
    ).all()
    first_visible: list[float] = []
    display_complete: list[float] = []
    invalid_timing = 0
    measured_attempts = set()
    seen_measurements = set()
    for row in timing_rows:
        payload = row.payload or {}
        measurement = payload.get("measurement_id")
        # 旧事件没有measurement_id时仍可审计，但不能据此假定同页面有效测量。
        if not isinstance(measurement, str) or not measurement:
            invalid_timing += 1
            continue
        key = (row.attempt_id, measurement)
        if key in seen_measurements:
            continue
        seen_measurements.add(key)
        fva = payload.get("first_visible_answer_ms")
        dcm = payload.get("display_complete_ms")

        def valid(value):
            return (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value >= 0
            )

        if (
            payload.get("clock") != "monotonic"
            or any(v is not None and not valid(v) for v in (fva, dcm))
            or (valid(fva) and valid(dcm) and dcm < fva)
        ):
            invalid_timing += 1
            continue
        if valid(fva):
            first_visible.append(float(fva))
            measured_attempts.add(row.attempt_id)
        if valid(dcm) and payload.get("completion_state") == "complete":
            display_complete.append(float(dcm))
        if (
            fva is None
            and dcm is None
            and payload.get("completion_state") not in {"incomplete", "abandoned"}
        ):
            invalid_timing += 1

    def _ratio(num: int, den: int) -> float | None:
        return None if den == 0 else round(num / den, 4)

    return {
        "metric_version": METRIC_VERSION,
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "half_open": "[start, end)",
        },
        "as_of": dt.datetime.now(dt.UTC).isoformat(),
        "data_lag_seconds": None,
        "collection_enabled_at": collection_enabled_at.isoformat()
        if collection_enabled_at
        else None,
        "feedback": {
            "eligible_answers": eligible,
            "rated_answers": rated,
            "positive": positive,
            "negative": negative,
            "satisfaction_rate": _ratio(positive, rated),
            "coverage_rate": _ratio(rated, eligible),
            "not_collected_answers": len(not_collected_ids),
        },
        "copy": {
            "eligible_copyable_answers": eligible,
            "copy_events": copy_total,
            "distinct_copied_answers": len(copied_answers),
            "copy_rate": _ratio(len(copied_answers), eligible),
            "not_collected_answers": len(not_collected_ids),
        },
        "browser_timing": {
            "first_visible_answer": _distribution(first_visible),
            "display_complete": _distribution(display_complete),
            "invalid_measurements": invalid_timing,
            "eligible_attempts": len(timing_ids),
            "not_collected_attempts": len(cohort) - len(timing_ids),
            "measured_attempts": len(measured_attempts),
            "missing_measurements": len(timing_ids - measured_attempts),
            "cohort": "request_admitted_at",
            "measurement_source": "browser_monotonic",
        },
    }
