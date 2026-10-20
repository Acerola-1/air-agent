"""Validated request, evidence, finding and report schemas for tracing.

All models forbid extra fields.  The schemas are deliberately narrow: a model
cannot add free-form evidence, facts or validation flags to a report.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

GRAPH_ID = "intelligent-tracing"
REQUEST_SCHEMA_VERSION = "tracing-request-v1"
REPORT_GATE_VERSION = "tracing-gate-v1"
SUPPORTED_TIMEZONES = ("Asia/Shanghai",)


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and is immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Target(StrictModel):
    """Canonical target station or area."""

    station_code: str = Field(min_length=1, max_length=128)
    station_name: str = Field(default="", max_length=200)
    region_code: str = Field(default="", max_length=64)
    # 行政区中文名（MCP 查询参数）；来自请求候选或目录记录，不作为授权证明.
    region_name: str = Field(default="", max_length=128)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    coordinate_source: Literal["platform_catalog", "user", "fixture", "unknown"] = (
        "platform_catalog"
    )
    admin_scope: tuple[str, ...] = ()


class TimeWindow(StrictModel):
    """Left-closed, right-open hour window with an explicit timezone."""

    start: datetime
    end: datetime
    timezone: str = "Asia/Shanghai"

    @model_validator(mode="after")
    def _validate_window(self) -> TimeWindow:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("时间窗必须带时区")
        if self.end <= self.start:
            raise ValueError("时间窗结束必须晚于开始")
        if self.start.minute or self.start.second or self.start.microsecond:
            raise ValueError("时间窗起点必须对齐整点")
        if self.end.minute or self.end.second or self.end.microsecond:
            raise ValueError("时间窗终点必须对齐整点")
        if (self.end - self.start).total_seconds() > 31 * 24 * 3600:
            raise ValueError("时间窗不能超过 31 天")
        return self


class ParentAnalysisRef(StrictModel):
    """Reference to a previous revision in the same thread."""

    analysis_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    changed_fields: tuple[str, ...] = ()


class TracingRequest(StrictModel):
    """Server-validated and frozen domain request.

    Identity, tenant, thread, graph, context version and execution epoch are
    bound from trusted runtime context and deliberately absent here.
    """

    schema_version: Literal["tracing-request-v1"] = REQUEST_SCHEMA_VERSION
    analysis_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    target: Target
    pollutant: str = Field(min_length=1, max_length=32)
    time_window: TimeWindow
    neighbor_radius_km: float = Field(ge=1, le=50)
    baseline_window: TimeWindow | None = None
    analysis_policy_version: str = Field(min_length=1, max_length=64)
    # 能力/技能选择引用只用于回读已受理选择，不承担业务授权语义.
    capability_snapshot_ref: str = Field(default="", max_length=256)
    # 以下字段只来自平台注入的可信业务快照，模型输入不能赋值.
    business_code: str = Field(min_length=1, max_length=64)
    business_policy_version: str = Field(min_length=1, max_length=64)
    business_permission: str = Field(min_length=1, max_length=128)
    graph_id: str = Field(min_length=1, max_length=64)
    parent_analysis_ref: ParentAnalysisRef | None = None
    request_digest: str = ""

    @model_validator(mode="after")
    def _validate_analysis_id(self) -> TracingRequest:
        if not self.analysis_id.strip():
            raise ValueError("analysis_id 不能为空")
        return self

    def canonical_digest(self) -> str:
        """Return a stable digest excluding the mutable digest field."""
        payload = self.model_dump(mode="json", exclude={"request_digest"})
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def frozen(self) -> TracingRequest:
        """Return a copy carrying the canonical request digest."""
        return self.model_copy(update={"request_digest": self.canonical_digest()})


class RuntimeScope(StrictModel):
    """Trusted run scope used to isolate evidence and reports."""

    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    graph_id: str = GRAPH_ID
    context_version: str = Field(min_length=1)
    epoch: str = Field(min_length=1)
    membership_id: str = ""
    business_code: str = Field(min_length=1, max_length=64)
    business_policy_version: str = Field(min_length=1, max_length=64)
    business_permission: str = Field(min_length=1, max_length=128)


class EvidenceRecord(StrictModel):
    """Execution-layer evidence; models cannot construct or register it."""

    evidence_id: str = Field(min_length=1, max_length=128)
    analysis_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    tool_id: str = Field(min_length=1, max_length=200)
    parameter_digest: str = Field(min_length=1, max_length=128)
    source_type: Literal[
        "observation", "nwp", "derived", "fixture", "mcp", "controlled"
    ]
    product: str = Field(min_length=1, max_length=128)
    fetched_at: datetime
    valid_from: datetime
    valid_to: datetime
    timezone: str = "Asia/Shanghai"
    unit: str = Field(min_length=1, max_length=32)
    quality_flags: tuple[str, ...] = ()
    sample_count: int = Field(ge=0)
    raw_digest: str = Field(min_length=1, max_length=128)
    algorithm_version: str = Field(min_length=1, max_length=64)
    analysis_policy_version: str = Field(min_length=1, max_length=64)
    derived_from: tuple[str, ...] = ()
    source_relation: Literal[
        "independent", "station_interpolated", "unknown", "not_applicable"
    ] = "not_applicable"
    nwp_run_time: datetime | None = None
    nwp_lead_hours: int | None = Field(default=None, ge=0, le=240)
    metadata: dict[str, str] = Field(default_factory=dict)
    business_code: str = Field(min_length=1, max_length=64)
    business_policy_version: str = Field(min_length=1, max_length=64)
    business_permission: str = Field(min_length=1, max_length=128)
    scope: RuntimeScope

    @model_validator(mode="after")
    def _validate_validity(self) -> EvidenceRecord:
        if self.valid_to < self.valid_from:
            raise ValueError("证据有效时间倒置")
        return self

    def public_view(self) -> dict[str, Any]:
        """Return non-raw evidence metadata for report/audit views."""
        return self.model_dump(mode="json")


class HourlyObservation(StrictModel):
    """One hourly concentration observation."""

    station_code: str = Field(min_length=1)
    timestamp: datetime
    value: float | None = None
    unit: str = "ug/m3"
    source: str = "fixture"
    qc_flags: tuple[str, ...] = ()


class HourlyWeather(StrictModel):
    """One hourly weather record; NWP provenance is explicit."""

    timestamp: datetime
    wind_speed: float | None = None
    wind_direction: float | None = None
    u: float | None = None
    v: float | None = None
    pblh: float | None = None
    humidity: float | None = None
    pressure: float | None = None
    temperature: float | None = None
    source_type: Literal["observation", "nwp", "fixture"] = "fixture"
    run_time: datetime | None = None
    lead_hours: int | None = None
    qc_flags: tuple[str, ...] = ()


class StationMeta(StrictModel):
    """Station catalogue entry with geometry."""

    station_code: str = Field(min_length=1)
    name: str = ""
    region_code: str = ""
    region_name: str = ""
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    distance_km: float = Field(ge=0)
    station_type: str = "unknown"
    source_relation: Literal[
        "independent", "station_interpolated", "unknown", "not_applicable"
    ] = "not_applicable"


class GridCell(StrictModel):
    """One grid cell; row/column support deterministic contiguity."""

    cell_id: str = Field(min_length=1)
    row: int
    col: int
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    value: float | None = None
    observed_at: datetime | None = None
    source_relation: Literal[
        "independent", "station_interpolated", "unknown", "not_applicable"
    ] = "unknown"
    derived_from: tuple[str, ...] = ()


class DataBundle(StrictModel):
    """A scoped data snapshot supplied to deterministic subagents."""

    snapshot_id: str = Field(min_length=1)
    target_station_code: str = Field(min_length=1)
    region_code: str = ""
    observations: tuple[HourlyObservation, ...] = ()
    neighbors: dict[str, tuple[HourlyObservation, ...]] = Field(default_factory=dict)
    stations: tuple[StationMeta, ...] = ()
    grid_cells: tuple[GridCell, ...] = ()
    weather: tuple[HourlyWeather, ...] = ()
    nwp: tuple[HourlyWeather, ...] = ()
    source_type: Literal["fixture", "mcp", "controlled"] = "fixture"
    missing: tuple[str, ...] = ()
    # 产品 → 实际执行工具，供证据记录真实 tool_id；fixture 为空.
    source_tools: dict[str, str] = Field(default_factory=dict)


class Coverage(StrictModel):
    """Coverage accounting for one series."""

    expected_hours: int = Field(ge=0)
    valid_hours: int = Field(ge=0)
    ratio: float = Field(ge=0, le=1)
    max_gap_hours: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    invalid_rows: int = Field(ge=0)
    sufficient: bool


class AnomalySegment(StrictModel):
    """A contiguous anomalous period."""

    start: datetime
    end: datetime
    duration_hours: int = Field(ge=1)
    peak_value: float
    peak_at: datetime
    mean_value: float
    max_z: float
    direction: Literal["above", "below"]


class ChildExecution(StrictModel):
    """Per-subtask execution linked to the existing run/attempt, not a new request."""

    child_execution_id: str = Field(min_length=1, max_length=128)
    role: Literal["station-data", "grid-data", "meteo-analyst", "evidence-reviewer"]
    analysis_id: str
    revision: int = Field(ge=1)
    run_id: str = ""
    attempt_id: str = ""
    started_at: datetime
    finished_at: datetime
    status: Literal["succeeded", "failed", "partial"]
    tool_call_count: int = Field(default=0, ge=0)
    error_class: str = ""


class EvaluationSummary(StrictModel):
    """A pollutant-specific evaluation metric with explicit validity."""

    metric: Literal["daily_max_8h", "24h_sliding"]
    period_start: datetime
    period_end: datetime
    value: float
    unit: str
    valid_hours: int = Field(ge=0)
    required_hours: int = Field(ge=0)
    limitations: tuple[str, ...] = ()
    evidence_id: str = ""


class StationFindings(StrictModel):
    """Deterministic station findings, owned by station-data."""

    analysis_id: str
    revision: int = Field(ge=1)
    station_code: str
    status: Literal["anomaly", "no_anomaly", "insufficient"]
    coverage: Coverage
    baseline_coverage: Coverage | None = None
    baseline_median: float | None = None
    baseline_mad: float | None = None
    anomaly_segments: tuple[AnomalySegment, ...] = ()
    peak_value: float | None = None
    peak_at: datetime | None = None
    min_value: float | None = None
    mean_value: float | None = None
    change_rate: float | None = None
    evaluations: tuple[EvaluationSummary, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class NeighborSync(StrictModel):
    """One neighbor's synchrony with the target anomaly."""

    station_code: str
    distance_km: float = Field(ge=0)
    overlap_hours: int = Field(ge=0)
    peak_value: float | None = None
    anomalous: bool = False
    evidence_id: str = ""


class GridSummary(StrictModel):
    """Grid highlight and contiguity summary."""

    high_cell_count: int = Field(ge=0)
    contiguous_cell_count: int = Field(ge=0)
    source_relation: Literal[
        "independent", "station_interpolated", "unknown", "not_applicable"
    ] = "unknown"
    cells: tuple[str, ...] = ()
    evidence_id: str = ""


class SpatialFindings(StrictModel):
    """Descriptive spatial pattern; never a transport verdict by itself."""

    analysis_id: str
    revision: int = Field(ge=1)
    pattern: Literal[
        "isolated", "multi_station_sync", "regional_contiguous", "mixed", "unknown"
    ]
    target_anomalous: bool
    valid_neighbor_count: int = Field(ge=0)
    sync_neighbors: tuple[NeighborSync, ...] = ()
    grid: GridSummary | None = None
    evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class WindSector(StrictModel):
    """One wind-from sector summary."""

    sector_start: float
    sector_end: float
    mean_concentration: float
    sample_count: int = Field(ge=0)
    fraction: float = Field(ge=0, le=1)


class WindStats(StrictModel):
    """Circular wind statistics and paired sector coverage."""

    paired_samples: int = Field(ge=0)
    calm_ratio: float = Field(ge=0, le=1)
    mean_speed: float | None = None
    resultant_direction: float | None = None
    resultant_length: float | None = Field(default=None, ge=0, le=1)
    sector_coverage: float = Field(ge=0, le=1)
    dominant_sector: WindSector | None = None
    sectors: tuple[WindSector, ...] = ()
    direction_claim_allowed: bool = False
    evidence_id: str = ""
    limitations: tuple[str, ...] = ()


class LagAssociation(StrictModel):
    """Best 0-6 hour lag correlation, explicitly non-causal."""

    neighbor_code: str
    best_lag_hours: int = Field(ge=0, le=6)
    r: float = Field(ge=-1, le=1)
    pairs: int = Field(ge=0)
    evidence_id: str = ""
    limitations: tuple[str, ...] = ()


class MeteoFindings(StrictModel):
    """Deterministic meteorology and wind-association findings."""

    analysis_id: str
    revision: int = Field(ge=1)
    pblh_available_ratio: float = Field(ge=0, le=1)
    low_pblh_ratio: float = Field(ge=0, le=1)
    mean_pblh: float | None = None
    mean_wind_speed: float | None = None
    calm_ratio: float = Field(ge=0, le=1)
    source_types: tuple[str, ...] = ()
    nwp_run_times: tuple[datetime, ...] = ()
    nwp_lead_hours: tuple[int, ...] = ()
    wind: WindStats | None = None
    lag_associations: tuple[LagAssociation, ...] = ()
    upwind_neighbors: tuple[str, ...] = ()
    early_rise_neighbors: tuple[str, ...] = ()
    upwind_gradient: float | None = None
    evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class ProcessResult(StrictModel):
    """Separate process explanation with support, counter and alternatives."""

    analysis_id: str
    revision: int = Field(ge=1)
    assessment: Literal[
        "local_accumulation_consistent",
        "transport_association",
        "mixed",
        "insufficient",
    ]
    transport_components: tuple[str, ...] = ()
    counter_evidence: tuple[str, ...] = ()
    alternative_explanations: tuple[str, ...] = ()
    source_hypotheses: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class MeteoNarrative(StrictModel):
    """Advisory narrative from the meteo analyst; numbers remain deterministic."""

    summary: str = ""
    supplemental_checks: tuple[str, ...] = ()
    limitations_observed: tuple[str, ...] = ()


class EvidenceReview(StrictModel):
    """LLM reviewer output; advisory only, never a fact source."""

    analysis_id: str
    revision: int = Field(ge=1)
    issues: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()
    omissions: tuple[str, ...] = ()
    downgrade_suggestions: tuple[str, ...] = ()
    reviewed_claim_ids: tuple[str, ...] = ()
    review_summary: str = ""


class ClaimType(StrEnum):
    """Allowed claim types for the deterministic allowlist."""

    COVERAGE = "coverage"
    ANOMALY_PRESENT = "anomaly_present"
    NO_ANOMALY = "no_anomaly"
    DATA_INSUFFICIENT = "data_insufficient"
    PEAK = "peak"
    DURATION = "duration"
    CHANGE_RATE = "change_rate"
    SPATIAL_PATTERN = "spatial_pattern"
    WIND_SECTOR = "wind_sector"
    LAG_ASSOCIATION = "lag_association"
    PROCESS_ASSESSMENT = "process_assessment"
    SOURCE_HYPOTHESIS = "source_hypothesis"
    LIMITATION = "limitation"
    NEXT_STEP = "next_step"
    EVALUATION = "evaluation"


class Claim(StrictModel):
    """One claim bound to registered evidence."""

    claim_id: str = Field(min_length=1, max_length=128)
    claim_type: ClaimType
    level: Literal[1, 2, 3] | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    supporting_evidence_ids: tuple[str, ...] = ()
    counter_evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class AllowedClaim(Claim):
    """A claim the gate may allow, with semantic requirements."""

    semantic_requirements: tuple[str, ...] = ()


class ClaimAllowlist(StrictModel):
    """Deterministic allowlist generated from trusted findings."""

    analysis_id: str
    revision: int = Field(ge=1)
    policy_version: str
    claims: tuple[AllowedClaim, ...]
    limitations: tuple[str, ...]
    digest: str = ""


class ReportPlan(StrictModel):
    """Model-proposed plan: claim ids only, no free text or parameters."""

    analysis_id: str
    revision: int = Field(ge=1)
    claim_ids: tuple[str, ...] = ()
    review_claim_ids: tuple[str, ...] = ()
    # 只允许引用已登记的知识 citation_id；原文/文号/页码/URL 仍无输入通道.
    knowledge_reference_ids: tuple[str, ...] = ()


class KnowledgeBasisItem(StrictModel):
    """Server-resolved knowledge basis, independent from pollution evidence."""

    citation_id: str = Field(min_length=1, max_length=64)
    doc_name: str = Field(min_length=1, max_length=500)
    excerpt: str = Field(min_length=1, max_length=500)
    location: dict[str, Any] | None = None
    location_status: Literal["provided", "not_provided"] = "not_provided"
    source_version: str | None = None
    source_sha256: str = Field(min_length=64, max_length=64)


class CanvasContent(StrictModel):
    """Server-rendered canvas content bound to a validated report."""

    title: str = Field(min_length=1, max_length=200)
    content_type: Literal["markdown"] = "markdown"
    content: str = Field(min_length=1)
    open_in: Literal["canvas_window", "inline_below", "modal", "sidebar"] = (
        "canvas_window"
    )


class ValidatedReport(StrictModel):
    """Immutable report produced only by the deterministic publish layer."""

    report_id: str = Field(min_length=1, max_length=128)
    analysis_id: str
    revision: int = Field(ge=1)
    request_digest: str
    policy_version: str
    run_id: str = ""
    claim_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    body_markdown: str
    canvas: CanvasContent
    evidence_ids: tuple[str, ...]
    scope: RuntimeScope
    content_sha256: str
    gate_version: str = REPORT_GATE_VERSION
    created_at: datetime
    published: bool = False
    # 新增：独立知识依据区。旧报告为空，digest 计算保持原口径.
    knowledge_basis: tuple[KnowledgeBasisItem, ...] = ()

    def compute_digest(self) -> str:
        """Hash the user-visible content and pinned metadata."""
        payload = {
            "analysis_id": self.analysis_id,
            "revision": self.revision,
            "request_digest": self.request_digest,
            "policy_version": self.policy_version,
            "run_id": self.run_id,
            "claim_ids": self.claim_ids,
            "limitations": self.limitations,
            "body_markdown": self.body_markdown,
            "canvas": self.canvas.model_dump(mode="json"),
            "evidence_ids": self.evidence_ids,
            "gate_version": self.gate_version,
            # 摘要绑定可信作用域与业务许可，避免跨企业/跨线程复用到同一报告.
            "scope": {
                "tenant_id": self.scope.tenant_id,
                "user_id": self.scope.user_id,
                "thread_id": self.scope.thread_id,
                "graph_id": self.scope.graph_id,
                "context_version": self.scope.context_version,
                "epoch": self.scope.epoch,
                "business_code": self.scope.business_code,
                "business_policy_version": self.scope.business_policy_version,
                "business_permission": self.scope.business_permission,
            },
        }
        # 旧报告没有知识依据：保持原 digest 口径，避免把历史报告判为损坏.
        if self.knowledge_basis:
            payload["knowledge_basis"] = [
                item.model_dump(mode="json") for item in self.knowledge_basis
            ]
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReportValidationResult(StrictModel):
    """Gate outcome; invalid plans always fall back, never retry the model."""

    ok: bool
    errors: tuple[str, ...] = ()
    used_fallback: bool = False
    report: ValidatedReport | None = None
