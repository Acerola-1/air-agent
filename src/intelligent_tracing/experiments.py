"""Shared-kernel factorial helpers (A0/A1 fixed vs B0/B1 DeepAgents)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from common.middleware.tenant_runtime_guard_middleware import (
    TenantRuntimeGuardMiddleware,
)
from common.observability.middleware import ObservabilityModelMiddleware
from intelligent_tracing.claims import fallback_plan
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.gate import ReportGate
from intelligent_tracing.guards import NoFilesystemToolsMiddleware
from intelligent_tracing.orchestrator import TracingAnalysis, analyze_bundle
from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.report_store import InMemoryReportStore
from intelligent_tracing.schemas import (
    DataBundle,
    EvidenceReview,
    RuntimeScope,
    TracingRequest,
    ValidatedReport,
)


@dataclass(frozen=True)
class CaseRunResult:
    """One factorial cell result for a fixture case."""

    group: str
    report: ValidatedReport
    review: EvidenceReview | None
    analysis: TracingAnalysis
    used_fallback: bool
    duration_ms: int


def reviewer_summary(analysis: TracingAnalysis) -> str:
    """Compact, non-raw summary for the advisory LLM reviewer."""
    return json.dumps(
        {
            "analysis_id": analysis.request.analysis_id,
            "revision": analysis.request.revision,
            "station_status": analysis.station.status,
            "coverage_ratio": round(analysis.station.coverage.ratio, 4),
            "spatial_pattern": analysis.spatial.pattern,
            "process_assessment": analysis.process.assessment,
            "transport_components": list(analysis.process.transport_components),
            "counter_evidence": list(analysis.process.counter_evidence),
            "alternative_explanations": list(analysis.process.alternative_explanations),
            "limitations": list(analysis.allowlist.limitations),
            "claim_ids": [claim.claim_id for claim in analysis.allowlist.claims],
        },
        ensure_ascii=False,
    )


def build_reviewer_agent(model: Any):
    """Build the same isolated reviewer used by the DeepAgents path."""
    return create_agent(
        model,
        tools=[],
        system_prompt=(
            "你是独立证据审查员。只根据给定摘要指出反证、遗漏和表达风险；"
            "不得新增事实、数值、证据或来源结论。"
        ),
        middleware=[
            NoFilesystemToolsMiddleware(),
            TenantRuntimeGuardMiddleware(inject_mcp_tools=False),
            ObservabilityModelMiddleware(role="reviewer"),
        ],
        response_format=EvidenceReview,
        name="evidence-reviewer-fixed",
    )


async def run_fixed_case(
    group: str,
    request: TracingRequest,
    bundle: DataBundle,
    *,
    scope: RuntimeScope,
    policy: AnalysisPolicy,
    reviewer_model: Any = None,
    registry: EvidenceRegistry | None = None,
) -> CaseRunResult:
    """Run A0/A1: fixed orchestration over the same kernel and renderer."""
    started = time.monotonic()
    registry = registry or EvidenceRegistry()
    analysis = analyze_bundle(
        request, bundle, registry=registry, scope=scope, policy=policy
    )
    review = None
    if reviewer_model is not None:
        reviewer = build_reviewer_agent(reviewer_model)
        result = await reviewer.ainvoke(
            {"messages": [HumanMessage(content=reviewer_summary(analysis))]}
        )
        review = result.get("structured_response")
    gate = ReportGate(registry=registry, policy=policy)
    validation = gate.validate_and_render(
        fallback_plan(analysis.allowlist), analysis.allowlist, request, scope
    )
    if not validation.ok or validation.report is None:
        raise RuntimeError(f"fixed gate failed: {validation.errors}")
    await InMemoryReportStore().save(validation.report)
    return CaseRunResult(
        group=group,
        report=validation.report,
        review=review,
        analysis=analysis,
        used_fallback=False,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def report_manifest(result: CaseRunResult, *, model_name: str) -> dict[str, Any]:
    """Serializable evidence entry for one factorial cell."""
    return {
        "group": result.group,
        "model": model_name,
        "analysis_id": result.report.analysis_id,
        "revision": result.report.revision,
        "policy_version": result.report.policy_version,
        "claim_ids": list(result.report.claim_ids),
        "report_digest": result.report.content_sha256,
        "allowlist_digest": result.analysis.allowlist.digest,
        "used_fallback": result.used_fallback,
        "review_issue_count": len(result.review.issues) if result.review else 0,
        "duration_ms": result.duration_ms,
        "created_at": datetime.now(UTC).isoformat(),
    }
