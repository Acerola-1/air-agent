"""Pollutant-specific evaluation metrics with explicit validity rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.schemas import (
    EvaluationSummary,
    HourlyObservation,
    RuntimeScope,
    TimeWindow,
    TracingRequest,
)


def _valid_values_by_hour(
    observations: tuple[HourlyObservation, ...] | list[HourlyObservation],
) -> dict[datetime, float]:
    return {row.timestamp: row.value for row in observations if row.value is not None}


def daily_max_8h(
    observations: tuple[HourlyObservation, ...] | list[HourlyObservation],
    *,
    timezone: str,
    min_valid_hours: int = 6,
    evaluation_window: TimeWindow | None = None,
) -> tuple[EvaluationSummary, ...]:
    """Compute daily max 8h O3 with within-day and cross-midnight windows.

    Windows end at every hour of the local day, so the first seven windows may
    include the previous day's late hours.  A window is valid when at least
    ``min_valid_hours`` of its eight hours are present; the mean is over the
    valid hours and the partial-window limitation is retained.
    """
    values = _valid_values_by_hour(observations)
    if not values:
        return ()
    zone = ZoneInfo(timezone)
    days = sorted({timestamp.astimezone(zone).date() for timestamp in values})
    summaries: list[EvaluationSummary] = []
    for day in days:
        day_start = datetime(day.year, day.month, day.day, tzinfo=zone)
        best: EvaluationSummary | None = None
        for end_offset in range(1, 25):
            end = day_start + timedelta(hours=end_offset)
            if (
                evaluation_window
                and not evaluation_window.start < end <= evaluation_window.end
            ):
                continue
            window = [end - timedelta(hours=8 - index) for index in range(8)]
            present = [values[key] for key in window if key in values]
            if len(present) < min_valid_hours:
                continue
            limitations: list[str] = []
            if len(present) < 8:
                limitations.append("partial_8h_window")
            if window[0].astimezone(zone).date() != day:
                limitations.append("cross_day_8h_window")
            summary = EvaluationSummary(
                metric="daily_max_8h",
                period_start=end - timedelta(hours=8),
                period_end=end,
                value=sum(present) / len(present),
                unit="ug/m3",
                valid_hours=len(present),
                required_hours=8,
                limitations=tuple(limitations),
            )
            if best is None or summary.value > best.value:
                best = summary
        if best is not None:
            summaries.append(best)
    return tuple(summaries)


def pm25_24h_sliding(
    observations: tuple[HourlyObservation, ...] | list[HourlyObservation],
    *,
    timezone: str,
    min_valid_hours: int = 18,
    evaluation_window: TimeWindow | None = None,
) -> tuple[EvaluationSummary, ...]:
    """Compute daily maximum 24h sliding PM2.5 as a supplementary statistic."""
    values = _valid_values_by_hour(observations)
    if not values:
        return ()
    zone = ZoneInfo(timezone)
    days = sorted({timestamp.astimezone(zone).date() for timestamp in values})
    summaries: list[EvaluationSummary] = []
    for day in days:
        day_start = datetime(day.year, day.month, day.day, tzinfo=zone)
        best: EvaluationSummary | None = None
        for end_offset in range(1, 25):
            end = day_start + timedelta(hours=end_offset)
            if (
                evaluation_window
                and not evaluation_window.start < end <= evaluation_window.end
            ):
                continue
            window = [end - timedelta(hours=24 - index) for index in range(24)]
            present = [values[key] for key in window if key in values]
            if len(present) < min_valid_hours:
                continue
            limitations = ["pm25_24h_sliding_supplementary"]
            if len(present) < 24:
                limitations.append("partial_24h_window")
            summary = EvaluationSummary(
                metric="24h_sliding",
                period_start=end - timedelta(hours=24),
                period_end=end,
                value=sum(present) / len(present),
                unit="ug/m3",
                valid_hours=len(present),
                required_hours=24,
                limitations=tuple(limitations),
            )
            if best is None or summary.value > best.value:
                best = summary
        if best is not None:
            summaries.append(best)
    return tuple(summaries)


def evaluate_request(
    request: TracingRequest,
    observations: tuple[HourlyObservation, ...] | list[HourlyObservation],
    *,
    registry: EvidenceRegistry,
    scope: RuntimeScope,
    policy: AnalysisPolicy,
    fetched_at: datetime | None = None,
    derived_from: tuple[str, ...] = (),
) -> tuple[tuple[EvaluationSummary, ...], tuple[str, ...]]:
    """Return evaluation summaries and extra limitations for the pollutant."""
    pollutant = request.pollutant.lower()
    fetched = fetched_at or datetime.now(UTC)
    if pollutant == "o3":
        summaries = daily_max_8h(
            observations,
            timezone=request.time_window.timezone,
            evaluation_window=request.time_window,
        )
    elif pollutant == "pm2.5":
        summaries = pm25_24h_sliding(
            observations,
            timezone=request.time_window.timezone,
            evaluation_window=request.time_window,
        )
    else:
        return (), ("evaluation_profile_not_defined",)
    evidence_ids: list[str] = []
    for summary in summaries:
        record = registry.register(
            scope=scope,
            analysis_id=request.analysis_id,
            revision=request.revision,
            tool_id=f"evaluation.{summary.metric}",
            parameters={
                "metric": summary.metric,
                "period_start": summary.period_start.isoformat(),
                "period_end": summary.period_end.isoformat(),
                "valid_hours": summary.valid_hours,
                "required_hours": summary.required_hours,
            },
            source_type="derived",
            product="evaluation_metric",
            fetched_at=fetched,
            valid_from=summary.period_start,
            valid_to=summary.period_end,
            unit=summary.unit,
            algorithm_version="evaluation-v1",
            analysis_policy_version=policy.version,
            raw_payload=summary.model_dump(mode="json"),
            quality_flags=summary.limitations,
            sample_count=summary.valid_hours,
            derived_from=derived_from,
            metadata={"metric": summary.metric},
        )
        evidence_ids.append(record.evidence_id)
    summaries = tuple(
        summary.model_copy(update={"evidence_id": evidence_id})
        for summary, evidence_id in zip(summaries, evidence_ids, strict=True)
    )
    return summaries, ()
