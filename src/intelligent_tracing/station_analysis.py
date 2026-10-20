"""Deterministic station anomaly analysis."""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta

from intelligent_tracing.evaluation import evaluate_request
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.policy import AnalysisPolicy, PollutantProfile
from intelligent_tracing.quality import clean_concentration_series
from intelligent_tracing.schemas import (
    AnomalySegment,
    Coverage,
    DataBundle,
    RuntimeScope,
    StationFindings,
    TracingRequest,
)


def _median_and_mad(values: list[float]) -> tuple[float, float]:
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations)
    return median, mad


def baseline_statistics(
    request: TracingRequest,
    bundle: DataBundle,
    policy: AnalysisPolicy,
    profile: PollutantProfile,
    *,
    registry: EvidenceRegistry,
    scope: RuntimeScope,
    fetched_at: datetime,
) -> tuple[list[float], float | None, float | None, Coverage, str]:
    """Return baseline values, median, MAD, coverage and an evidence id."""
    start = request.baseline_window.start if request.baseline_window else None
    end = request.baseline_window.end if request.baseline_window else None
    if start is None or end is None:
        end = request.time_window.start
        start = end - timedelta(days=policy.baseline.lookback_days)
    cleaned = clean_concentration_series(
        bundle.observations,
        start=start,
        end=end,
        timezone=request.time_window.timezone,
        policy=policy,
        profile=profile,
    )
    values = [row.value for row in cleaned.observations if row.value is not None]
    if not values:
        return [], None, None, cleaned.coverage, ""
    median, mad = _median_and_mad(values)
    evidence = register_station_evidence(
        registry,
        scope=scope,
        bundle=bundle,
        request=request,
        policy=policy,
        fetched_at=fetched_at,
        start=start,
        end=end,
        values=values,
        product="baseline_series",
        tool_id="station.clean_baseline",
        raw_payload=[row.model_dump(mode="json") for row in cleaned.observations],
        quality_flags=cleaned.flags,
        sample_count=len(values),
    )
    return values, median, mad, cleaned.coverage, evidence


def register_station_evidence(
    registry: EvidenceRegistry,
    *,
    scope: RuntimeScope,
    bundle: DataBundle,
    request: TracingRequest,
    policy: AnalysisPolicy,
    fetched_at: datetime,
    start: datetime,
    end: datetime,
    values: list[float],
    product: str,
    tool_id: str,
    raw_payload: object,
    quality_flags: tuple[str, ...],
    sample_count: int,
    derived_from: tuple[str, ...] = (),
    source_relation: str = "not_applicable",
    source_type: str | None = None,
) -> str:
    """Register a station product through the execution-layer registry."""
    resolved_source = source_type or _bundle_source_type(bundle)
    record = registry.register(
        scope=scope,
        analysis_id=request.analysis_id,
        revision=request.revision,
        tool_id=tool_id,
        parameters={
            "station": request.target.station_code,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "product": product,
        },
        source_type=resolved_source,  # type: ignore[arg-type]
        product=product,
        fetched_at=fetched_at,
        valid_from=start,
        valid_to=end,
        unit=policy.require_supported(request.pollutant).unit,
        algorithm_version="station-analysis-v1",
        analysis_policy_version=policy.version,
        raw_payload=raw_payload,
        quality_flags=quality_flags,
        sample_count=sample_count,
        derived_from=derived_from,
        source_relation=source_relation,  # type: ignore[arg-type]
    )
    return record.evidence_id


def detect_anomalies(
    values: list[tuple[datetime, float]],
    *,
    baseline_median: float,
    baseline_mad: float,
    baseline_std: float,
    policy: AnalysisPolicy,
    profile: PollutantProfile,
) -> tuple[AnomalySegment, ...]:
    """Detect contiguous anomalies using robust z and an absolute floor."""
    anomalous: list[tuple[datetime, float, float, str]] = []
    for timestamp, value in values:
        delta = value - baseline_median
        if baseline_mad > 0:
            z = 0.6745 * delta / baseline_mad
        elif baseline_std > 0:
            z = delta / baseline_std
        else:
            z = 0.0
        threshold_met = abs(delta) >= profile.anomaly_abs_delta and (
            abs(z) >= policy.baseline.robust_z_threshold
            or (baseline_mad == 0 and baseline_std == 0)
        )
        if threshold_met:
            direction = "above" if delta >= 0 else "below"
            anomalous.append((timestamp, value, z, direction))
    if not anomalous:
        return ()
    segments: list[AnomalySegment] = []
    current: list[tuple[datetime, float, float, str]] = [anomalous[0]]
    for item in anomalous[1:]:
        previous = current[-1]
        contiguous = (item[0] - previous[0]) == timedelta(hours=1)
        if contiguous and item[3] == previous[3]:
            current.append(item)
        else:
            _append_segment(segments, current, policy)
            current = [item]
    _append_segment(segments, current, policy)
    return tuple(segments)


def _append_segment(
    segments: list[AnomalySegment],
    rows: list[tuple[datetime, float, float, str]],
    policy: AnalysisPolicy,
) -> None:
    if len(rows) < policy.baseline.min_duration_hours:
        return
    values = [row[1] for row in rows]
    peak_index = max(range(len(rows)), key=lambda index: abs(rows[index][2]))
    segments.append(
        AnomalySegment(
            start=rows[0][0],
            end=rows[-1][0] + timedelta(hours=1),
            duration_hours=len(rows),
            peak_value=rows[peak_index][1],
            peak_at=rows[peak_index][0],
            mean_value=sum(values) / len(values),
            max_z=rows[peak_index][2],
            direction=rows[0][3],  # type: ignore[arg-type]
        )
    )


def analyze_station(
    request: TracingRequest,
    bundle: DataBundle,
    *,
    registry: EvidenceRegistry,
    scope: RuntimeScope,
    policy: AnalysisPolicy,
    fetched_at: datetime | None = None,
) -> StationFindings:
    """Run deterministic target-station analysis and register all evidence."""
    profile = policy.require_supported(request.pollutant)
    fetched = fetched_at or datetime.now(UTC)
    target_series = clean_concentration_series(
        bundle.observations,
        start=request.time_window.start,
        end=request.time_window.end,
        timezone=request.time_window.timezone,
        policy=policy,
        profile=profile,
    )
    limitations: list[str] = list(policy_fixed_limitations())
    if not target_series.coverage.sufficient:
        limitations.append("coverage_insufficient")
    baseline_values, median, mad, baseline_coverage, baseline_evidence = (
        baseline_statistics(
            request,
            bundle,
            policy,
            profile,
            registry=registry,
            scope=scope,
            fetched_at=fetched,
        )
    )
    if (
        baseline_coverage.ratio < policy.baseline.min_coverage_ratio
        or len(baseline_values) < policy.baseline.min_samples
    ):
        limitations.append("baseline_insufficient")
    values = [
        (row.timestamp, row.value)
        for row in target_series.observations
        if row.value is not None
    ]
    target_evidence = register_station_evidence(
        registry,
        scope=scope,
        bundle=bundle,
        request=request,
        policy=policy,
        fetched_at=fetched,
        start=request.time_window.start,
        end=request.time_window.end,
        values=[value for _, value in values],
        product="station_series",
        tool_id="station.clean_target",
        raw_payload=[row.model_dump(mode="json") for row in target_series.observations],
        quality_flags=target_series.flags,
        sample_count=len(values),
    )
    evidence_ids = (
        (baseline_evidence, target_evidence)
        if baseline_evidence
        else (target_evidence,)
    )
    evaluations, evaluation_limitations = evaluate_request(
        request,
        target_series.observations,
        registry=registry,
        scope=scope,
        policy=policy,
        fetched_at=fetched,
        derived_from=(target_evidence,),
    )
    limitations.extend(evaluation_limitations)
    evidence_ids = (*evidence_ids, *(item.evidence_id for item in evaluations))
    if not target_series.coverage.sufficient:
        return StationFindings(
            analysis_id=request.analysis_id,
            revision=request.revision,
            station_code=request.target.station_code,
            status="insufficient",
            coverage=target_series.coverage,
            baseline_coverage=baseline_coverage,
            baseline_median=median,
            baseline_mad=mad,
            evidence_ids=evidence_ids,
            evaluations=evaluations,
            limitations=tuple(dict.fromkeys(limitations)),
        )
    if not values:
        return StationFindings(
            analysis_id=request.analysis_id,
            revision=request.revision,
            station_code=request.target.station_code,
            status="insufficient",
            coverage=target_series.coverage,
            baseline_coverage=baseline_coverage,
            baseline_median=median,
            baseline_mad=mad,
            evidence_ids=evidence_ids,
            evaluations=evaluations,
            limitations=tuple(dict.fromkeys(limitations)),
        )
    baseline_std = statistics.pstdev(baseline_values) if baseline_values else 0.0
    segments = (
        detect_anomalies(
            values,
            baseline_median=median or 0.0,
            baseline_mad=mad or 0.0,
            baseline_std=baseline_std,
            policy=policy,
            profile=profile,
        )
        if median is not None
        else ()
    )
    peak_at, peak_value = max(values, key=lambda item: item[1])
    first_value = values[0][1]
    last_value = values[-1][1]
    change_rate = (last_value - first_value) / first_value if first_value > 0 else None
    if "baseline_insufficient" in limitations:
        status = "insufficient"
    elif segments:
        status = "anomaly"
    else:
        status = "no_anomaly"
    anomaly_evidence = ""
    if segments:
        anomaly_evidence = register_station_evidence(
            registry,
            scope=scope,
            bundle=bundle,
            request=request,
            policy=policy,
            fetched_at=fetched,
            start=request.time_window.start,
            end=request.time_window.end,
            values=[segment.peak_value for segment in segments],
            product="station_anomaly",
            tool_id="station.detect_anomaly",
            raw_payload=[segment.model_dump(mode="json") for segment in segments],
            quality_flags=target_series.flags,
            sample_count=len(segments),
            derived_from=(target_evidence,),
        )
        evidence_ids = (*evidence_ids, anomaly_evidence)
    return StationFindings(
        analysis_id=request.analysis_id,
        revision=request.revision,
        station_code=request.target.station_code,
        status=status,
        coverage=target_series.coverage,
        baseline_coverage=baseline_coverage,
        baseline_median=median,
        baseline_mad=mad,
        anomaly_segments=segments,
        peak_value=peak_value,
        peak_at=peak_at,
        min_value=min(value for _, value in values),
        mean_value=sum(value for _, value in values) / len(values),
        change_rate=change_rate,
        evaluations=evaluations,
        evidence_ids=evidence_ids,
        limitations=tuple(dict.fromkeys(limitations)),
    )


def policy_fixed_limitations() -> tuple[str, ...]:
    """Import fixed limitations without a module-level cycle."""
    from intelligent_tracing.policy import FIXED_LIMITATIONS

    return FIXED_LIMITATIONS


def _bundle_source_type(bundle: DataBundle) -> str:
    """Map a snapshot's provenance to an evidence source label."""
    if bundle.source_type == "mcp":
        return "mcp"
    if bundle.source_type == "controlled":
        return "controlled"
    return "fixture"
