"""Combine spatial, temporal and meteorological evidence into a process view."""

from __future__ import annotations

from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.schemas import (
    DataBundle,
    MeteoFindings,
    ProcessResult,
    SpatialFindings,
    StationFindings,
    TracingRequest,
)


def assess_process(
    request: TracingRequest,
    bundle: DataBundle,
    station: StationFindings,
    spatial: SpatialFindings,
    meteo: MeteoFindings,
    *,
    policy: AnalysisPolicy,
) -> ProcessResult:
    """Return a separate process assessment with support and counter-evidence."""
    limitations = list(
        dict.fromkeys(
            (
                *station.limitations,
                *spatial.limitations,
                *meteo.limitations,
                "process_association_not_causation",
            )
        )
    )
    if station.status != "anomaly":
        assessment = "insufficient" if station.status == "insufficient" else "mixed"
        return ProcessResult(
            analysis_id=request.analysis_id,
            revision=request.revision,
            assessment=assessment,  # type: ignore[arg-type]
            alternative_explanations=(
                ("no_target_anomaly",) if station.status == "no_anomaly" else ()
            ),
            evidence_ids=tuple(
                dict.fromkeys(
                    (*station.evidence_ids, *spatial.evidence_ids, *meteo.evidence_ids)
                )
            ),
            limitations=tuple(limitations),
        )
    if (
        meteo.wind is None
        or "meteo_data_unavailable" in meteo.limitations
        or "grid_data_unavailable" in spatial.limitations
    ):
        limitations.append("process_weather_or_spatial_unavailable")
        return ProcessResult(
            analysis_id=request.analysis_id,
            revision=request.revision,
            assessment="insufficient",
            counter_evidence=(
                "meteo_data_unavailable"
                if meteo.wind is None or "meteo_data_unavailable" in meteo.limitations
                else "grid_data_unavailable",
            ),
            alternative_explanations=("weather_or_spatial_evidence_missing",),
            evidence_ids=tuple(
                dict.fromkeys(
                    (*station.evidence_ids, *spatial.evidence_ids, *meteo.evidence_ids)
                )
            ),
            limitations=tuple(dict.fromkeys(limitations)),
        )
    components: list[str] = []
    sync_count = sum(1 for item in spatial.sync_neighbors if item.anomalous)
    if sync_count >= policy.spatial.min_sync_neighbors:
        components.append("spatial_sync")
    if (
        spatial.grid is not None
        and spatial.grid.contiguous_cell_count
        >= policy.spatial.grid_min_contiguous_cells
    ):
        if spatial.grid.source_relation not in {"station_interpolated", "unknown"}:
            components.append("independent_grid_contiguous")
        else:
            components.append("grid_contiguous_same_source")
    wind = meteo.wind
    if wind is not None and wind.direction_claim_allowed:
        components.append("wind_direction_stable")
    if len(meteo.upwind_neighbors) >= policy.wind.min_upwind_neighbors:
        components.append("upwind_geometry")
    if meteo.early_rise_neighbors:
        components.append("temporal_lead")
    if any(
        item.best_lag_hours >= 1 and abs(item.r) >= policy.wind.lag_min_abs_r
        for item in meteo.lag_associations
    ):
        components.append("lag_correlation")
    if (
        meteo.upwind_gradient is not None
        and meteo.upwind_gradient >= policy.wind.min_upwind_gradient
    ):
        components.append("upwind_gradient")
    counter: list[str] = []
    if wind is not None:
        if wind.calm_ratio > 0.5:
            counter.append("calm_dominant")
        if (
            wind.resultant_length is not None
            and wind.resultant_length < policy.wind.min_resultant_length
        ):
            counter.append("direction_unstable")
        if wind.sector_coverage < policy.wind.min_sector_coverage:
            counter.append("sector_coverage_insufficient")
    if spatial.grid is not None and spatial.grid.source_relation in {
        "station_interpolated",
        "unknown",
    }:
        counter.append("grid_not_independent")
    if meteo.pblh_available_ratio == 0:
        counter.append("pblh_unavailable")
    alternatives: list[str] = []
    transport_required = {
        "spatial_sync",
        "wind_direction_stable",
    }
    causal_component = {
        "temporal_lead",
        "lag_correlation",
        "upwind_gradient",
    }
    transport_ok = (
        len(set(components) & transport_required) == len(transport_required)
        and bool(set(components) & causal_component)
        and len(components) >= policy.process.min_transport_components
        and "calm_dominant" not in counter
        and "direction_unstable" not in counter
    )
    if sync_count == 0 and spatial.pattern in {"isolated", "mixed"}:
        assessment = "mixed"
        alternatives.append("instrument_missing_data_or_microenvironment")
    elif transport_ok:
        assessment = "transport_association"
        alternatives.extend(
            (
                "common_stagnation_can_also_produce_multistation_sync",
                "instrument_or_siting_effects_not_excluded",
            )
        )
    elif sync_count >= policy.spatial.min_sync_neighbors or (
        spatial.pattern in {"multi_station_sync", "regional_contiguous", "mixed"}
    ):
        if meteo.calm_ratio > 0.5 or meteo.low_pblh_ratio >= 0.5:
            assessment = "local_accumulation_consistent"
            alternatives.append("regional_stagnation_without_transport_sequence")
        elif spatial.pattern == "isolated":
            assessment = "mixed"
            alternatives.append("instrument_missing_data_or_microenvironment")
        else:
            assessment = "insufficient"
    elif spatial.pattern == "isolated":
        assessment = "mixed"
        alternatives.append("instrument_missing_data_or_microenvironment")
    else:
        assessment = "insufficient"
    if "temporal_lead" not in components:
        alternatives.append("no_upwind_temporal_sequence")
    if "pblh_unavailable" in counter:
        alternatives.append("boundary_layer_unavailable")
    source_hypotheses: list[str] = []
    if assessment == "transport_association" and policy.process.allow_source_hypothesis:
        source_hypotheses.append(
            "上风向可能存在未识别的排放活动，需结合排放清单与独立轨迹资料核查"
        )
    evidence_ids = tuple(
        dict.fromkeys(
            (*station.evidence_ids, *spatial.evidence_ids, *meteo.evidence_ids)
        )
    )
    return ProcessResult(
        analysis_id=request.analysis_id,
        revision=request.revision,
        assessment=assessment,  # type: ignore[arg-type]
        transport_components=tuple(dict.fromkeys(components)),
        counter_evidence=tuple(dict.fromkeys(counter)),
        alternative_explanations=tuple(dict.fromkeys(alternatives)),
        source_hypotheses=tuple(source_hypotheses),
        evidence_ids=evidence_ids,
        limitations=tuple(limitations),
    )
