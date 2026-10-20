"""Gate, evidence semantics and publication attack tests."""

from __future__ import annotations

import pytest
from fixtures import build_bundle, make_request, make_scope
from pydantic import ValidationError

from intelligent_tracing.claims import fallback_plan
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.gate import ReportGate
from intelligent_tracing.orchestrator import analyze_bundle
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.schemas import (
    ClaimType,
    ReportPlan,
)


def _analysis(scenario: str = "transport"):
    request = make_request()
    scope = make_scope()
    registry = EvidenceRegistry()
    analysis = analyze_bundle(
        request,
        build_bundle(scenario=scenario),
        registry=registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    gate = ReportGate(registry=registry, policy=DEFAULT_POLICY)
    return request, scope, registry, analysis, gate


def test_forged_claim_id_is_rejected_without_free_text_channel():
    request, scope, _registry, analysis, gate = _analysis()
    forged = ReportPlan(
        analysis_id=request.analysis_id,
        revision=1,
        claim_ids=("claim.pollution_from_industry",),
    )
    result = gate.validate_and_render(forged, analysis.allowlist, request, scope)
    assert result.ok is False
    assert any("claim_not_allowlisted" in error for error in result.errors)
    with pytest.raises(ValidationError):
        ReportPlan(
            analysis_id=request.analysis_id,
            revision=1,
            claim_ids=(),
            free_text="污染主要由某方位工业活动造成",
        )


def test_plan_cannot_add_reviewer_claims():
    request, scope, _registry, analysis, gate = _analysis()
    forged = ReportPlan(
        analysis_id=request.analysis_id,
        revision=1,
        claim_ids=tuple(claim.claim_id for claim in analysis.allowlist.claims),
        review_claim_ids=("reviewer.says_industry",),
    )
    result = gate.validate_and_render(forged, analysis.allowlist, request, scope)
    assert not result.ok
    assert "review_claim_not_allowlisted" in result.errors


def test_existing_but_unsupported_evidence_is_rejected():
    request, scope, registry, analysis, gate = _analysis()
    spatial_claim = next(
        claim
        for claim in analysis.allowlist.claims
        if claim.claim_type == ClaimType.SPATIAL_PATTERN
    )
    station_evidence = next(
        record.evidence_id
        for record in registry.get_many(
            analysis.station.evidence_ids,
            scope=scope,
            analysis_id=request.analysis_id,
            revision=1,
        )
        if record.product == "station_series"
    )
    tampered = spatial_claim.model_copy(
        update={"supporting_evidence_ids": (station_evidence,)}
    )
    fake_allowlist = analysis.allowlist.model_copy(
        update={
            "claims": tuple(
                tampered if claim.claim_id == tampered.claim_id else claim
                for claim in analysis.allowlist.claims
            )
        }
    )
    plan = ReportPlan(
        analysis_id=request.analysis_id,
        revision=1,
        claim_ids=tuple(claim.claim_id for claim in fake_allowlist.claims),
    )
    result = gate.validate_and_render(plan, fake_allowlist, request, scope)
    assert not result.ok
    assert any("spatial" in error for error in result.errors)


def test_cross_scope_evidence_is_rejected():
    request, scope, registry, analysis, gate = _analysis()
    other_scope = make_scope(context_version="cv-other")
    record = registry.register(
        scope=other_scope,
        analysis_id=request.analysis_id,
        revision=1,
        tool_id="fixture",
        parameters={"x": 1},
        source_type="fixture",
        product="station_series",
        fetched_at=analysis.station.anomaly_segments[0].start,
        valid_from=request.time_window.start,
        valid_to=request.time_window.end,
        unit="ug/m3",
        algorithm_version="v1",
        analysis_policy_version=DEFAULT_POLICY.version,
        raw_payload={"x": 1},
        sample_count=1,
    )
    coverage_claim = next(
        claim
        for claim in analysis.allowlist.claims
        if claim.claim_type == ClaimType.COVERAGE
    )
    tampered = coverage_claim.model_copy(
        update={"supporting_evidence_ids": (record.evidence_id,)}
    )
    fake_allowlist = analysis.allowlist.model_copy(
        update={
            "claims": tuple(
                tampered if claim.claim_id == tampered.claim_id else claim
                for claim in analysis.allowlist.claims
            )
        }
    )
    plan = ReportPlan(
        analysis_id=request.analysis_id,
        revision=1,
        claim_ids=tuple(claim.claim_id for claim in fake_allowlist.claims),
    )
    result = gate.validate_and_render(plan, fake_allowlist, request, scope)
    assert not result.ok
    assert any("evidence_invalid" in error for error in result.errors)


def test_legal_percentages_and_refusal_are_not_blocked():
    request, scope, _registry, analysis, gate = _analysis()
    result = gate.validate_and_render(
        fallback_plan(analysis.allowlist), analysis.allowlist, request, scope
    )
    assert result.ok
    assert result.report is not None
    assert "%" in result.report.body_markdown
    assert "无法计算贡献率" in result.report.body_markdown


def test_missing_fixed_limitation_is_rejected():
    request, scope, _registry, analysis, gate = _analysis()
    filtered = analysis.allowlist.model_copy(
        update={
            "claims": tuple(
                claim
                for claim in analysis.allowlist.claims
                if claim.claim_id != "limitation.trajectory_unavailable"
            ),
            "limitations": tuple(
                item
                for item in analysis.allowlist.limitations
                if item != "trajectory_unavailable"
            ),
        }
    )
    plan = ReportPlan(
        analysis_id=request.analysis_id,
        revision=1,
        claim_ids=tuple(claim.claim_id for claim in filtered.claims),
    )
    result = gate.validate_and_render(plan, filtered, request, scope)
    assert not result.ok
    assert "fixed_limitations_missing" in result.errors


def test_knowledge_heading_cannot_exempt_free_attribution_prose():
    request, scope, _registry, analysis, gate = _analysis()
    result = gate.validate_and_render(
        fallback_plan(analysis.allowlist), analysis.allowlist, request, scope
    )
    report = result.report
    forged = report.model_copy(
        update={
            "body_markdown": report.body_markdown
            + "\n### 知识依据\n贡献率为99%，已确认企业。"
        }
    )
    forged = forged.model_copy(update={"content_sha256": forged.compute_digest()})
    assert "attribution_overclaim_in_text" in gate._validate_text_boundary(
        forged, analysis.allowlist
    )


def test_knowledge_digest_old_roundtrip_and_registered_quote_positive_control():
    from intelligent_tracing.schemas import KnowledgeBasisItem, ValidatedReport

    request, scope, _registry, analysis, gate = _analysis()
    plan = fallback_plan(analysis.allowlist)
    old = gate.validate_and_render(plan, analysis.allowlist, request, scope).report
    old_json = old.model_dump(mode="json")
    old_json.pop("knowledge_basis")
    reread = ValidatedReport.model_validate(old_json)
    assert reread.compute_digest() == old.content_sha256
    basis = KnowledgeBasisItem(
        citation_id="11111111-1111-4111-8111-111111111111",
        doc_name="F-I 测试方法",
        excerpt="某过程贡献率为30%，此句仅供软件测试。",
        location=None,
        location_status="not_provided",
        source_version="test-v1",
        source_sha256="a" * 64,
    )
    plan = plan.model_copy(update={"knowledge_reference_ids": (basis.citation_id,)})
    result = gate.validate_and_render(
        plan, analysis.allowlist, request, scope, knowledge_basis=(basis,)
    )
    assert result.ok
    assert result.report.claim_ids == old.claim_ids
    assert result.report.evidence_ids == old.evidence_ids
    assert result.report.knowledge_basis == (basis,)
    assert result.report.compute_digest() == result.report.content_sha256
    modified = result.report.model_copy(update={"knowledge_basis": ()})
    assert modified.compute_digest() != result.report.content_sha256
