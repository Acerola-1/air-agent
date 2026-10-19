"""PM2.5 deterministic kernel tests."""

from __future__ import annotations

from fixtures import build_bundle, make_request, make_scope

from intelligent_tracing.claims import fallback_plan
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.gate import ReportGate
from intelligent_tracing.orchestrator import analyze_bundle
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.schemas import ClaimType


def _run(scenario: str):
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
    result = gate.validate_and_render(
        fallback_plan(analysis.allowlist), analysis.allowlist, request, scope
    )
    return analysis, result


def test_transport_fixture_is_anomalous_and_not_causal():
    analysis, result = _run("transport")
    assert analysis.station.status == "anomaly"
    assert analysis.station.peak_value == 110.0
    assert analysis.station.anomaly_segments[0].duration_hours == 9
    assert analysis.spatial.pattern == "regional_contiguous"
    assert analysis.process.assessment == "transport_association"
    assert "trajectory_unavailable" in analysis.allowlist.limitations
    assert result.ok
    report = result.report
    assert report is not None
    assert "无法计算贡献率" in report.body_markdown
    assert "污染主要由" not in report.body_markdown
    assert "贡献率为" not in report.body_markdown
    assert report.content_sha256 == report.compute_digest()
    hypothesis_claims = [
        claim
        for claim in analysis.allowlist.claims
        if claim.claim_type == ClaimType.SOURCE_HYPOTHESIS
    ]
    assert hypothesis_claims
    assert all(claim.level is None for claim in hypothesis_claims)
    assert all(
        claim.parameters["status"] == "unverified" for claim in hypothesis_claims
    )
    assert all(
        claim.claim_type not in {ClaimType.SOURCE_HYPOTHESIS} or claim.level is None
        for claim in analysis.allowlist.claims
    )


def test_stagnation_is_local_accumulation_not_transport():
    analysis, result = _run("stagnation")
    assert analysis.process.assessment == "local_accumulation_consistent"
    assert "spatial_sync" in analysis.process.transport_components
    assert "wind_direction_stable" not in analysis.process.transport_components
    assert "calm_dominant" in analysis.process.counter_evidence
    assert not analysis.process.source_hypotheses
    assert result.ok


def test_isolated_anomaly_keeps_alternative_explanation():
    analysis, result = _run("isolated")
    assert analysis.station.status == "anomaly"
    assert analysis.process.assessment in {"mixed", "insufficient"}
    assert analysis.process.assessment != "transport_association"
    assert result.ok


def test_no_anomaly_and_insufficient_are_distinct():
    no_anomaly, no_result = _run("no_anomaly")
    insufficient, insufficient_result = _run("insufficient")
    assert no_anomaly.station.status == "no_anomaly"
    assert no_anomaly.allowlist.claims
    assert any(
        claim.claim_type == ClaimType.NO_ANOMALY
        for claim in no_anomaly.allowlist.claims
    )
    assert no_result.ok
    assert insufficient.station.status == "insufficient"
    assert any(
        claim.claim_type == ClaimType.DATA_INSUFFICIENT
        for claim in insufficient.allowlist.claims
    )
    assert insufficient_result.ok


def test_deterministic_replay_is_identical():
    request = make_request()
    scope = make_scope()
    first_registry = EvidenceRegistry()
    second_registry = EvidenceRegistry()
    first = analyze_bundle(
        request,
        build_bundle(scenario="transport"),
        registry=first_registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    second = analyze_bundle(
        request,
        build_bundle(scenario="transport"),
        registry=second_registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    assert first.station == second.station
    assert first.spatial == second.spatial
    assert first.meteo == second.meteo
    assert first.process == second.process
    assert first.allowlist.digest == second.allowlist.digest
    assert first.station.evidence_ids == second.station.evidence_ids


def test_fixture_evidence_provenance_and_derivation():
    request = make_request()
    scope = make_scope()
    registry = EvidenceRegistry()
    analysis = analyze_bundle(
        request,
        build_bundle(scenario="transport"),
        registry=registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    station_records = registry.get_many(
        analysis.station.evidence_ids,
        scope=scope,
        analysis_id=request.analysis_id,
        revision=request.revision,
    )
    baseline = next(r for r in station_records if r.product == "baseline_series")
    target = next(r for r in station_records if r.product == "station_series")
    assert target.source_type == "fixture"
    assert baseline.source_type == "fixture"
    assert target.derived_from == ()
    assert baseline.evidence_id not in target.derived_from
    evaluation = next(r for r in station_records if r.product == "evaluation_metric")
    assert evaluation.derived_from == (target.evidence_id,)
    grid = next(
        record
        for record in registry.get_many(
            analysis.spatial.evidence_ids,
            scope=scope,
            analysis_id=request.analysis_id,
            revision=request.revision,
        )
        if record.product == "spatial_grid"
    )
    assert grid.source_type == "fixture"
    weather = next(
        record
        for record in registry.get_many(
            analysis.meteo.evidence_ids,
            scope=scope,
            analysis_id=request.analysis_id,
            revision=request.revision,
        )
        if record.product == "weather_series"
    )
    assert weather.source_type == "fixture"
