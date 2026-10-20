"""Deterministic PM2.5 analysis pipeline shared by all orchestration modes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from intelligent_tracing.claims import ClaimAllowlist, build_claim_allowlist
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.meteo_analysis import analyze_meteo
from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.process_assessment import assess_process
from intelligent_tracing.schemas import (
    DataBundle,
    MeteoFindings,
    ProcessResult,
    RuntimeScope,
    SpatialFindings,
    StationFindings,
    TracingRequest,
)
from intelligent_tracing.spatial_analysis import analyze_spatial
from intelligent_tracing.station_analysis import analyze_station


@dataclass(frozen=True)
class TracingAnalysis:
    """Complete deterministic analysis result for one request revision."""

    request: TracingRequest
    station: StationFindings
    spatial: SpatialFindings
    meteo: MeteoFindings
    process: ProcessResult
    allowlist: ClaimAllowlist


def analyze_bundle(
    request: TracingRequest,
    bundle: DataBundle,
    *,
    registry: EvidenceRegistry,
    scope: RuntimeScope,
    policy: AnalysisPolicy,
    fetched_at: datetime | None = None,
) -> TracingAnalysis:
    """Run station, spatial, meteo and process analysis in a fixed order."""
    fetched = fetched_at or datetime.now(UTC)
    station = analyze_station(
        request,
        bundle,
        registry=registry,
        scope=scope,
        policy=policy,
        fetched_at=fetched,
    )
    spatial = analyze_spatial(
        request,
        bundle,
        station,
        registry=registry,
        scope=scope,
        policy=policy,
        fetched_at=fetched,
    )
    meteo = analyze_meteo(
        request,
        bundle,
        station,
        registry=registry,
        scope=scope,
        policy=policy,
        fetched_at=fetched,
    )
    process = assess_process(request, bundle, station, spatial, meteo, policy=policy)
    allowlist = build_claim_allowlist(
        request, station, spatial, meteo, process, policy=policy
    )
    return TracingAnalysis(
        request=request,
        station=station,
        spatial=spatial,
        meteo=meteo,
        process=process,
        allowlist=allowlist,
    )
