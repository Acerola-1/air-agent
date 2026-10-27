"""Deterministic meteorology, wind-sector and lag association analysis."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.quality import clean_weather
from intelligent_tracing.schemas import (
    DataBundle,
    HourlyObservation,
    HourlyWeather,
    LagAssociation,
    MeteoFindings,
    RuntimeScope,
    StationFindings,
    TracingRequest,
    WindSector,
    WindStats,
)


def circular_mean(directions: list[float]) -> tuple[float | None, float]:
    """Return circular mean (degrees from) and resultant length."""
    if not directions:
        return None, 0.0
    sin_sum = sum(math.sin(math.radians(value)) for value in directions)
    cos_sum = sum(math.cos(math.radians(value)) for value in directions)
    count = len(directions)
    mean = math.degrees(math.atan2(sin_sum / count, cos_sum / count)) % 360.0
    resultant = math.hypot(sin_sum / count, cos_sum / count)
    return mean, min(1.0, resultant)


def angular_difference(a: float, b: float) -> float:
    """Smallest absolute angular difference in degrees."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def bearing_degrees(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the initial bearing from point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    x = math.sin(d_lambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(
        d_lambda
    )
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _paired(
    weather: tuple[HourlyWeather, ...],
    observations: tuple[HourlyObservation, ...],
) -> list[tuple[datetime, HourlyWeather, HourlyObservation]]:
    by_time = {row.timestamp: row for row in observations if row.value is not None}
    paired: list[tuple[datetime, HourlyWeather, HourlyObservation]] = []
    for row in weather:
        observation = by_time.get(row.timestamp)
        if observation is not None:
            paired.append((row.timestamp, row, observation))
    return paired


def _sector_for(direction: float, width: float) -> tuple[float, float]:
    start = math.floor(direction / width) * width
    return start, (start + width) % 360.0


def compute_wind_stats(
    weather: tuple[HourlyWeather, ...],
    observations: tuple[HourlyObservation, ...],
    policy: AnalysisPolicy,
) -> tuple[WindStats, list[tuple[datetime, HourlyWeather, HourlyObservation]]]:
    """Compute calm ratio, circular statistics and concentration sectors."""
    paired = _paired(weather, observations)
    valid = [
        (timestamp, row, observation)
        for timestamp, row, observation in paired
        if row.wind_direction is not None
    ]
    speeds = [row.wind_speed for _, row, _ in paired if row.wind_speed is not None]
    mean_speed = sum(speeds) / len(speeds) if speeds else None
    calm = sum(1 for speed in speeds if speed < policy.wind.calm_speed_threshold)
    calm_ratio = calm / len(speeds) if speeds else 1.0
    directions = [
        row.wind_direction for _, row, _ in valid if row.wind_direction is not None
    ]
    mean_direction, resultant = circular_mean(directions)
    buckets: dict[tuple[float, float], list[float]] = {}
    for _, row, observation in valid:
        if row.wind_direction is None:
            continue
        key = _sector_for(row.wind_direction, policy.wind.sector_width_degrees)
        buckets.setdefault(key, []).append(observation.value)  # type: ignore[arg-type]
    sectors = tuple(
        WindSector(
            sector_start=key[0],
            sector_end=key[1],
            mean_concentration=sum(values) / len(values),
            sample_count=len(values),
            fraction=len(values) / len(valid) if valid else 0.0,
        )
        for key, values in sorted(buckets.items())
        if len(values) >= policy.wind.min_paired_samples
    )
    sector_coverage = (
        sum(sector.sample_count for sector in sectors) / len(valid) if valid else 0.0
    )
    dominant = (
        max(sectors, key=lambda item: item.mean_concentration) if sectors else None
    )
    direction_allowed = (
        len(valid) >= policy.wind.min_paired_samples
        and calm_ratio <= 0.5
        and resultant >= policy.wind.min_resultant_length
        and sector_coverage >= policy.wind.min_sector_coverage
        and dominant is not None
    )
    limitations: list[str] = []
    if len(valid) < policy.wind.min_paired_samples:
        limitations.append("wind_pairs_insufficient")
    if calm_ratio > 0.5:
        limitations.append("calm_dominant")
    if resultant < policy.wind.min_resultant_length:
        limitations.append("direction_unstable")
    if sector_coverage < policy.wind.min_sector_coverage:
        limitations.append("sector_coverage_insufficient")
    return (
        WindStats(
            paired_samples=len(valid),
            calm_ratio=calm_ratio,
            mean_speed=mean_speed,
            resultant_direction=mean_direction,
            resultant_length=resultant,
            sector_coverage=sector_coverage,
            dominant_sector=dominant,
            sectors=sectors,
            direction_claim_allowed=direction_allowed,
            limitations=tuple(limitations),
        ),
        valid,
    )


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 3:
        return None
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
    )
    if denominator == 0:
        return None
    return numerator / denominator


def lag_correlation(
    target: tuple[HourlyObservation, ...],
    neighbor: tuple[HourlyObservation, ...],
    *,
    max_lag: int,
    min_pairs: int,
) -> tuple[int, float, int] | None:
    """Return (best lag, r, pairs) for neighbor(t) vs target(t+lag)."""
    target_by_time = {
        row.timestamp: row.value for row in target if row.value is not None
    }
    best: tuple[int, float, int] | None = None
    for lag in range(max_lag + 1):
        pairs: list[tuple[float, float]] = []
        for row in neighbor:
            if row.value is None:
                continue
            later = target_by_time.get(row.timestamp + timedelta(hours=lag))
            if later is not None:
                pairs.append((row.value, later))
        if len(pairs) < min_pairs:
            continue
        r = _pearson(pairs)
        if r is None:
            continue
        candidate = (lag, r, len(pairs))
        if best is None or abs(r) > abs(best[1]):
            best = candidate
    return best


def _early_rise_hours(
    target: StationFindings,
    neighbor: tuple[HourlyObservation, ...],
    threshold: float,
    policy: AnalysisPolicy,
) -> int | None:
    if not target.anomaly_segments:
        return None
    target_start = target.anomaly_segments[0].start
    for row in sorted(neighbor, key=lambda item: item.timestamp):
        if row.value is not None and row.value >= threshold:
            gap = int((target_start - row.timestamp).total_seconds() // 3600)
            if (
                policy.wind.early_rise_min_hours
                <= gap
                <= policy.wind.early_rise_max_hours
            ):
                return gap
            break
    return None


def analyze_meteo(
    request: TracingRequest,
    bundle: DataBundle,
    station_findings: StationFindings,
    *,
    registry: EvidenceRegistry,
    scope: RuntimeScope,
    policy: AnalysisPolicy,
    fetched_at: datetime | None = None,
) -> MeteoFindings:
    """Compute meteorology and wind associations, preserving limitations."""
    fetched = fetched_at or datetime.now(UTC)
    profile = policy.require_supported(request.pollutant)
    # Prefer station/observed weather for wind pairing; use NWP only when no
    # observed weather exists.  Combining both would let a coarse NWP series
    # silently dilute a calm observed period.
    primary_rows = tuple(bundle.weather) if bundle.weather else tuple(bundle.nwp)
    quality = clean_weather(
        primary_rows,
        policy=policy,
        start=request.time_window.start,
        end=request.time_window.end,
        timezone=request.time_window.timezone,
    )
    observations = tuple(row for row in bundle.observations if row.value is not None)
    source_types = tuple(sorted({row.source_type for row in quality.rows}))
    nwp_rows = [row for row in bundle.nwp if row.source_type == "nwp"]
    pblh_values = [row.pblh for row in quality.rows if row.pblh is not None]
    pblh_ratio = len(pblh_values) / len(quality.rows) if quality.rows else 0.0
    low_pblh = sum(
        1 for value in pblh_values if value < policy.process.low_pblh_threshold
    )
    low_pblh_ratio = low_pblh / len(pblh_values) if pblh_values else 0.0
    wind_stats, _valid_pairs = compute_wind_stats(quality.rows, observations, policy)
    weather_speeds = [
        row.wind_speed for row in quality.rows if row.wind_speed is not None
    ]
    weather_evidence = registry.register(
        scope=scope,
        analysis_id=request.analysis_id,
        revision=request.revision,
        tool_id="meteo.weather_series",
        parameters={
            "start": request.time_window.start.isoformat(),
            "end": request.time_window.end.isoformat(),
            "source_types": list(source_types),
        },
        source_type=("nwp" if "nwp" in source_types else _bundle_source_type(bundle)),  # type: ignore[arg-type]
        product="weather_series",
        fetched_at=fetched,
        valid_from=request.time_window.start,
        valid_to=request.time_window.end,
        unit="mixed",
        algorithm_version="meteo-analysis-v1",
        analysis_policy_version=policy.version,
        raw_payload=[row.model_dump(mode="json") for row in quality.rows],
        quality_flags=quality.flags,
        sample_count=len(quality.rows),
    )
    nwp_evidence = None
    if nwp_rows:
        nwp_evidence = registry.register(
            scope=scope,
            analysis_id=request.analysis_id,
            revision=request.revision,
            tool_id="meteo.nwp_series",
            parameters={
                "start": request.time_window.start.isoformat(),
                "end": request.time_window.end.isoformat(),
                "run_time": min(
                    (row.run_time for row in nwp_rows if row.run_time is not None),
                    default=None,
                ),
            },
            source_type="nwp",
            product="nwp_series",
            fetched_at=fetched,
            valid_from=request.time_window.start,
            valid_to=request.time_window.end,
            unit="mixed",
            algorithm_version="meteo-analysis-v1",
            analysis_policy_version=policy.version,
            raw_payload=[row.model_dump(mode="json") for row in nwp_rows],
            quality_flags=quality.flags,
            sample_count=len(nwp_rows),
            nwp_run_time=min(
                (row.run_time for row in nwp_rows if row.run_time is not None),
                default=None,
            ),
            nwp_lead_hours=min(
                (row.lead_hours for row in nwp_rows if row.lead_hours is not None),
                default=None,
            ),
        )
    wind_evidence = registry.register(
        scope=scope,
        analysis_id=request.analysis_id,
        revision=request.revision,
        tool_id="meteo.wind_stats",
        parameters={
            "sector_width": policy.wind.sector_width_degrees,
            "calm_threshold": policy.wind.calm_speed_threshold,
        },
        source_type="derived",
        product="wind_pairing",
        fetched_at=fetched,
        valid_from=request.time_window.start,
        valid_to=request.time_window.end,
        unit=profile.unit,
        algorithm_version="meteo-analysis-v1",
        analysis_policy_version=policy.version,
        raw_payload=wind_stats.model_dump(mode="json"),
        quality_flags=quality.flags,
        sample_count=wind_stats.paired_samples,
        derived_from=(weather_evidence.evidence_id,),
    )
    # pblh_sentinel 是内部质量标志；对用户只保留可解释的“边界层不可用”。
    limitations = [flag for flag in quality.flags if flag != "pblh_sentinel"]
    limitations.extend(wind_stats.limitations)
    if quality.pblh_unavailable:
        limitations.append("pblh_unavailable")
    if "nwp" in source_types:
        limitations.append("nwp_source")
    elif bundle.nwp:
        limitations.append("nwp_available_not_used_for_wind")
    else:
        limitations.append("nwp_unavailable")
    station_by_code = {station.station_code: station for station in bundle.stations}
    upwind: list[str] = []
    early_rise: list[str] = []
    temporal_evidence_ids: list[str] = []
    lag_associations: list[LagAssociation] = []
    mean_direction = wind_stats.resultant_direction
    threshold = (station_findings.baseline_median or 0.0) + policy.require_supported(
        request.pollutant
    ).anomaly_abs_delta
    if mean_direction is not None and station_findings.status == "anomaly":
        for code, neighbor in bundle.neighbors.items():
            meta = station_by_code.get(code)
            if meta is None or code == request.target.station_code:
                continue
            bearing = bearing_degrees(
                request.target.latitude,
                request.target.longitude,
                meta.latitude,
                meta.longitude,
            )
            if (
                angular_difference(bearing, mean_direction)
                <= policy.wind.upwind_angle_tolerance_degrees
            ):
                upwind.append(code)
                lead_hours = _early_rise_hours(
                    station_findings, tuple(neighbor), threshold, policy
                )
                if lead_hours is not None:
                    early_rise.append(code)
                    lead_record = registry.register(
                        scope=scope,
                        analysis_id=request.analysis_id,
                        revision=request.revision,
                        tool_id="meteo.early_rise",
                        parameters={"neighbor": code, "lead_hours": lead_hours},
                        source_type="derived",
                        product="temporal_lead",
                        fetched_at=fetched,
                        valid_from=request.time_window.start,
                        valid_to=request.time_window.end,
                        unit=profile.unit,
                        algorithm_version="meteo-analysis-v1",
                        analysis_policy_version=policy.version,
                        raw_payload={"neighbor": code, "lead_hours": lead_hours},
                        sample_count=1,
                        derived_from=(weather_evidence.evidence_id,),
                    )
                    evidence_extra = lead_record.evidence_id
                    temporal_evidence_ids.append(evidence_extra)
                else:
                    evidence_extra = ""
                best = lag_correlation(
                    tuple(observations),
                    tuple(neighbor),
                    max_lag=policy.wind.lag_search_hours,
                    min_pairs=policy.wind.lag_min_pairs,
                )
                if best is not None:
                    lag, r, pairs = best
                    lag_record = registry.register(
                        scope=scope,
                        analysis_id=request.analysis_id,
                        revision=request.revision,
                        tool_id="meteo.lag_correlation",
                        parameters={"neighbor": code, "lag": lag},
                        source_type="derived",
                        product="lag_analysis",
                        fetched_at=fetched,
                        valid_from=request.time_window.start,
                        valid_to=request.time_window.end,
                        unit=profile.unit,
                        algorithm_version="meteo-analysis-v1",
                        analysis_policy_version=policy.version,
                        raw_payload={"lag": lag, "r": r, "pairs": pairs},
                        sample_count=pairs,
                        derived_from=tuple(
                            item
                            for item in (wind_evidence.evidence_id, evidence_extra)
                            if item
                        ),
                    )
                    lag_associations.append(
                        LagAssociation(
                            neighbor_code=code,
                            best_lag_hours=lag,
                            r=r,
                            pairs=pairs,
                            evidence_id=lag_record.evidence_id,
                            limitations=(
                                ("lag_correlation_not_causal",)
                                if abs(r) >= policy.wind.lag_min_abs_r
                                else ()
                            ),
                        )
                    )
    upwind_gradient = None
    if upwind and station_findings.peak_value is not None:
        neighbor_peaks: list[float] = []
        for code in upwind:
            values = [
                row.value
                for row in bundle.neighbors.get(code, ())
                if row.value is not None
            ]
            if values:
                neighbor_peaks.append(max(values))
        if neighbor_peaks:
            upwind_gradient = station_findings.peak_value - (
                sum(neighbor_peaks) / len(neighbor_peaks)
            )
    if wind_stats.direction_claim_allowed:
        limitations.append("wind_direction_association_not_source")
    evidence_ids = (
        weather_evidence.evidence_id,
        *((nwp_evidence.evidence_id,) if nwp_evidence else ()),
        wind_evidence.evidence_id,
        *temporal_evidence_ids,
        *(item.evidence_id for item in lag_associations if item.evidence_id),
    )
    return MeteoFindings(
        analysis_id=request.analysis_id,
        revision=request.revision,
        pblh_available_ratio=pblh_ratio,
        low_pblh_ratio=low_pblh_ratio,
        mean_pblh=(sum(pblh_values) / len(pblh_values) if pblh_values else None),
        mean_wind_speed=wind_stats.mean_speed,
        background_mean_wind_speed=sum(weather_speeds) / len(weather_speeds)
        if weather_speeds
        else None,
        weather_wind_samples=len(weather_speeds),
        calm_ratio=wind_stats.calm_ratio,
        source_types=source_types,
        nwp_run_times=tuple(
            sorted({row.run_time for row in nwp_rows if row.run_time is not None})
        ),
        nwp_lead_hours=tuple(
            sorted({row.lead_hours for row in nwp_rows if row.lead_hours is not None})
        ),
        wind=wind_stats.model_copy(update={"evidence_id": wind_evidence.evidence_id}),
        lag_associations=tuple(lag_associations),
        upwind_neighbors=tuple(upwind),
        early_rise_neighbors=tuple(early_rise),
        upwind_gradient=upwind_gradient,
        evidence_ids=evidence_ids,
        limitations=tuple(dict.fromkeys(limitations)),
    )


def _bundle_source_type(bundle: DataBundle) -> str:
    """Map a snapshot's provenance to an evidence source label."""
    if bundle.source_type == "mcp":
        return "mcp"
    if bundle.source_type == "controlled":
        return "controlled"
    return "fixture"
