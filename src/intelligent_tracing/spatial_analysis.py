"""Deterministic spatial pattern analysis."""

from __future__ import annotations

from datetime import UTC, datetime
from math import asin, cos, radians, sin, sqrt

from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.quality import clean_concentration_series
from intelligent_tracing.schemas import (
    DataBundle,
    GridCell,
    GridSummary,
    NeighborSync,
    RuntimeScope,
    SpatialFindings,
    StationFindings,
    TracingRequest,
)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    radius = 6371.0088
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lambda = radians(lon2 - lon1)
    value = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2) ** 2
    return 2 * radius * asin(sqrt(value))


def _overlap_hours(
    target_segments: tuple,
    neighbor_rows: tuple,
    threshold: float,
) -> int:
    overlap = 0
    for segment in target_segments:
        for row in neighbor_rows:
            if row.value is None:
                continue
            if segment.start <= row.timestamp < segment.end and row.value >= threshold:
                overlap += 1
    return overlap


def _grid_contiguous(cells: tuple[GridCell, ...], policy: AnalysisPolicy) -> int:
    # An unlocated or cross-time point set has no proven adjacency.
    if not cells or any(cell.row is None or cell.col is None for cell in cells):
        return 0
    if len({cell.observed_at for cell in cells}) != 1 or cells[0].observed_at is None:
        return 0
    high = {
        cell.cell_id: cell
        for cell in cells
        if cell.value is not None and cell.value >= policy.spatial.grid_high_value
    }
    if not high:
        return 0
    positions = {
        (cell.row, cell.col): cell
        for cell in high.values()
        if cell.row is not None and cell.col is not None
    }
    visited: set[tuple[int, int]] = set()
    largest = 0
    for position in positions:
        if position in visited:
            continue
        stack = [position]
        size = 0
        visited.add(position)
        while stack:
            row, col = stack.pop()
            size += 1
            for d_row, d_col in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                candidate = (row + d_row, col + d_col)
                if candidate in positions and candidate not in visited:
                    visited.add(candidate)
                    stack.append(candidate)
        largest = max(largest, size)
    return largest


def _grid_relation(cells: tuple[GridCell, ...]) -> str:
    relations = {cell.source_relation for cell in cells}
    if "station_interpolated" in relations:
        return "station_interpolated"
    if relations <= {"independent"} and relations:
        return "independent"
    return "unknown"


def analyze_spatial(
    request: TracingRequest,
    bundle: DataBundle,
    station_findings: StationFindings,
    *,
    registry: EvidenceRegistry,
    scope: RuntimeScope,
    policy: AnalysisPolicy,
    fetched_at: datetime | None = None,
) -> SpatialFindings:
    """Describe spatial pattern without asserting transport or source."""
    fetched = fetched_at or datetime.now(UTC)
    limitations: list[str] = [
        code
        for code in bundle.missing
        if code
        in {"grid_topology_unknown", "grid_bounded_snapshot", "neighbors_truncated"}
    ]
    if station_findings.status != "anomaly":
        return SpatialFindings(
            analysis_id=request.analysis_id,
            revision=request.revision,
            pattern="unknown",
            target_anomalous=False,
            valid_neighbor_count=0,
            limitations=tuple(limitations)
            + (
                ("no_target_anomaly",)
                if station_findings.status == "no_anomaly"
                else ("target_anomaly_unknown",)
            ),
        )
    threshold = (station_findings.baseline_median or 0.0) + policy.require_supported(
        request.pollutant
    ).anomaly_abs_delta
    station_by_code = {station.station_code: station for station in bundle.stations}
    sync: list[NeighborSync] = []
    valid_neighbors = 0
    for code, rows in bundle.neighbors.items():
        if code == request.target.station_code:
            continue
        meta = station_by_code.get(code)
        if meta is None:
            limitations.append("neighbor_geometry_unknown")
            continue
        distance = haversine_km(
            request.target.latitude,
            request.target.longitude,
            meta.latitude,
            meta.longitude,
        )
        if distance > request.neighbor_radius_km:
            continue
        cleaned = clean_concentration_series(
            rows,
            start=request.time_window.start,
            end=request.time_window.end,
            timezone=request.time_window.timezone,
            policy=policy,
            profile=policy.require_supported(request.pollutant),
        )
        if not cleaned.coverage.sufficient:
            limitations.append("neighbor_coverage_insufficient")
            continue
        valid_neighbors += 1
        overlap = _overlap_hours(
            station_findings.anomaly_segments,
            cleaned.observations,
            threshold,
        )
        peak = max(
            (row.value for row in cleaned.observations if row.value is not None),
            default=None,
        )
        evidence = registry.register(
            scope=scope,
            analysis_id=request.analysis_id,
            revision=request.revision,
            tool_id="spatial.neighbor_series",
            parameters={
                "station": code,
                "distance_km": round(distance, 3),
                "threshold": threshold,
            },
            source_type=_bundle_source_type(bundle),  # type: ignore[arg-type]
            product="neighbor_series",
            fetched_at=fetched,
            valid_from=request.time_window.start,
            valid_to=request.time_window.end,
            unit=policy.require_supported(request.pollutant).unit,
            algorithm_version="spatial-analysis-v1",
            analysis_policy_version=policy.version,
            raw_payload=[row.model_dump(mode="json") for row in cleaned.observations],
            quality_flags=cleaned.flags,
            sample_count=cleaned.coverage.valid_hours,
            source_relation=meta.source_relation,
        )
        sync.append(
            NeighborSync(
                station_code=code,
                distance_km=distance,
                overlap_hours=overlap,
                peak_value=peak,
                anomalous=overlap >= policy.spatial.sync_overlap_hours,
                evidence_id=evidence.evidence_id,
            )
        )
    grid_summary = None
    grid_evidence = ""
    if bundle.grid_cells:
        if any(cell.row is None or cell.col is None for cell in bundle.grid_cells):
            limitations.append("grid_topology_unknown")
        if (
            len({cell.observed_at for cell in bundle.grid_cells}) != 1
            or bundle.grid_cells[0].observed_at is None
        ):
            limitations.append("grid_time_inconsistent")
        contiguous = _grid_contiguous(bundle.grid_cells, policy)
        relation = _grid_relation(bundle.grid_cells)
        high_cells = [
            cell
            for cell in bundle.grid_cells
            if cell.value is not None and cell.value >= policy.spatial.grid_high_value
        ]
        derived_from = tuple(
            sorted(
                {
                    evidence_id
                    for cell in high_cells
                    for evidence_id in cell.derived_from
                }
            )
        )
        grid_record = registry.register(
            scope=scope,
            analysis_id=request.analysis_id,
            revision=request.revision,
            tool_id="spatial.grid_summary",
            parameters={
                "high_value": policy.spatial.grid_high_value,
                "min_contiguous": policy.spatial.grid_min_contiguous_cells,
                "source_relation": relation,
            },
            source_type=(
                "derived" if relation != "independent" else _bundle_source_type(bundle)
            ),  # type: ignore[arg-type]
            product="spatial_grid",
            fetched_at=fetched,
            valid_from=request.time_window.start,
            valid_to=request.time_window.end,
            unit=policy.require_supported(request.pollutant).unit,
            algorithm_version="spatial-analysis-v1",
            analysis_policy_version=policy.version,
            raw_payload=[cell.model_dump(mode="json") for cell in bundle.grid_cells],
            quality_flags=(),
            sample_count=len(bundle.grid_cells),
            derived_from=derived_from,
            source_relation=relation,  # type: ignore[arg-type]
        )
        grid_evidence = grid_record.evidence_id
        grid_summary = GridSummary(
            high_cell_count=len(high_cells),
            contiguous_cell_count=contiguous,
            source_relation=relation,  # type: ignore[arg-type]
            cells=tuple(cell.cell_id for cell in high_cells),
            evidence_id=grid_evidence,
        )
        if relation == "station_interpolated":
            limitations.append("grid_station_interpolated")
        elif relation == "unknown":
            limitations.append("grid_source_unknown")
    sync_count = sum(1 for item in sync if item.anomalous)
    contiguous = grid_summary.contiguous_cell_count if grid_summary else 0
    if valid_neighbors < policy.spatial.min_valid_neighbors:
        # Insufficient neighbours mean "unknown", never "isolated": missing
        # data must not be turned into a spatial conclusion.
        pattern = "unknown"
        limitations.append("valid_neighbors_insufficient")
    elif (
        sync_count >= policy.spatial.min_sync_neighbors
        and contiguous >= policy.spatial.grid_min_contiguous_cells
    ):
        pattern = "regional_contiguous"
    elif sync_count >= policy.spatial.min_sync_neighbors:
        pattern = "multi_station_sync"
    elif contiguous >= policy.spatial.grid_min_contiguous_cells:
        pattern = "mixed"
    elif sync_count == 0:
        pattern = "isolated"
    else:
        pattern = "mixed"
    evidence_ids = tuple(item.evidence_id for item in sync if item.evidence_id)
    if grid_evidence:
        evidence_ids = (*evidence_ids, grid_evidence)
    return SpatialFindings(
        analysis_id=request.analysis_id,
        revision=request.revision,
        pattern=pattern,  # type: ignore[arg-type]
        target_anomalous=True,
        valid_neighbor_count=valid_neighbors,
        sync_neighbors=tuple(sync),
        grid=grid_summary,
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
