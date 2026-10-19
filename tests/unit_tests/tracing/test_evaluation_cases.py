"""P01-P18 evaluation contracts for the PM2.5 first stage.

P14-P18 security/lifecycle negatives are also exercised in
``test_gate_security.py``, ``test_publishing.py`` and
``test_subagents_deepagents.py``; this file records the business replay cases
and the cross-cutting positive controls that can run without real IAM/MCP.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fixtures import START, build_bundle, make_request, make_scope

from intelligent_tracing.claims import fallback_plan
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.gate import ReportGate
from intelligent_tracing.orchestrator import analyze_bundle
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.quality import clean_concentration_series, clean_weather
from intelligent_tracing.schemas import ClaimType, HourlyObservation, HourlyWeather


def _analysis(scenario: str, **bundle_kwargs):
    request = make_request()
    scope = make_scope()
    registry = EvidenceRegistry()
    analysis = analyze_bundle(
        request,
        build_bundle(scenario=scenario, **bundle_kwargs),
        registry=registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    gate = ReportGate(registry=registry, policy=DEFAULT_POLICY)
    result = gate.validate_and_render(
        fallback_plan(analysis.allowlist), analysis.allowlist, request, scope
    )
    return request, scope, analysis, result


def _claim_types(analysis) -> set[ClaimType]:
    return {claim.claim_type for claim in analysis.allowlist.claims}


def test_p01_process_rise_recomputes_anomaly_without_source_claim():
    _request, _scope, analysis, result = _analysis("transport")
    assert analysis.station.status == "anomaly"
    assert analysis.station.anomaly_segments[0].peak_value == 110.0
    assert result.ok
    assert ClaimType.SOURCE_HYPOTHESIS not in {
        claim.claim_type
        for claim in analysis.allowlist.claims
        if claim.level is not None
    }
    assert "污染主要由" not in result.report.body_markdown


def test_p02_no_anomaly_is_explicit_not_direction():
    _request, _scope, analysis, result = _analysis("no_anomaly")
    assert analysis.station.status == "no_anomaly"
    assert ClaimType.NO_ANOMALY in _claim_types(analysis)
    assert ClaimType.WIND_SECTOR not in _claim_types(analysis)
    assert result.ok


def test_p03_insufficient_is_not_no_anomaly():
    _request, _scope, analysis, result = _analysis("insufficient")
    assert analysis.station.status == "insufficient"
    assert ClaimType.DATA_INSUFFICIENT in _claim_types(analysis)
    assert ClaimType.NO_ANOMALY not in _claim_types(analysis)
    assert result.ok


def test_p04_isolated_anomaly_keeps_instrument_alternative():
    _request, _scope, analysis, result = _analysis("isolated")
    assert analysis.process.assessment != "transport_association"
    assert (
        "instrument_missing_data_or_microenvironment"
        in analysis.process.alternative_explanations
    )
    assert result.ok


def test_p05_common_stagnation_is_not_transport():
    _request, _scope, analysis, result = _analysis("stagnation")
    assert analysis.process.assessment == "local_accumulation_consistent"
    assert "common_stagnation_can_also_produce_multistation_sync" not in (
        analysis.process.alternative_explanations
    )
    assert "regional_stagnation_without_transport_sequence" in (
        analysis.process.alternative_explanations
    )
    assert result.ok


def test_p06_transport_association_requires_all_components():
    _request, _scope, analysis, result = _analysis("transport")
    components = set(analysis.process.transport_components)
    assert {"spatial_sync", "wind_direction_stable", "temporal_lead"}.issubset(
        components
    )
    assert analysis.process.counter_evidence == ()
    assert "common_stagnation_can_also_produce_multistation_sync" in (
        analysis.process.alternative_explanations
    )
    assert result.ok


def test_p07_calm_wind_rejects_direction_claim():
    _request, _scope, analysis, result = _analysis("calm")
    assert (
        analysis.meteo.wind is None or not analysis.meteo.wind.direction_claim_allowed
    )
    assert ClaimType.WIND_SECTOR not in _claim_types(analysis)
    assert ClaimType.LAG_ASSOCIATION not in _claim_types(analysis)
    assert result.ok


def test_p08_sentinel_unit_zero_and_negative_uv():
    weather = clean_weather(
        [
            HourlyWeather(
                timestamp=START,
                wind_speed=0.0,
                wind_direction=0.0,
                u=-1.0,
                v=-1.0,
                pblh=-1.0,
            )
        ],
        policy=DEFAULT_POLICY,
    )
    assert weather.rows[0].pblh is None
    assert weather.rows[0].u == -1.0 and weather.rows[0].v == -1.0
    assert weather.rows[0].wind_speed == 0.0
    series = clean_concentration_series(
        [
            HourlyObservation(station_code="S001", timestamp=START, value=0.0),
            HourlyObservation(
                station_code="S001",
                timestamp=START + timedelta(hours=1),
                value=0.05,
                unit="mg/m3",
            ),
        ],
        start=START,
        end=START + timedelta(hours=2),
        timezone="Asia/Shanghai",
        policy=DEFAULT_POLICY,
        profile=DEFAULT_POLICY.require_supported("pm2.5"),
    )
    assert series.observations[0].value == 0.0
    assert series.observations[1].value == pytest.approx(50.0)


def test_p09_time_alignment_and_nwp_provenance():
    _request, _scope, analysis, result = _analysis("transport")
    assert analysis.meteo.nwp_run_times
    assert analysis.meteo.nwp_lead_hours
    assert "nwp_available_not_used_for_wind" in analysis.meteo.limitations
    # A half-hour timestamp is not silently rounded into the hourly series.
    cleaned = clean_concentration_series(
        [
            HourlyObservation(
                station_code="S001",
                timestamp=START + timedelta(minutes=30),
                value=50.0,
            )
        ],
        start=START,
        end=START + timedelta(hours=2),
        timezone="Asia/Shanghai",
        policy=DEFAULT_POLICY,
        profile=DEFAULT_POLICY.require_supported("pm2.5"),
    )
    assert cleaned.coverage.valid_hours == 0
    assert "not_hour_aligned" in cleaned.flags
    assert result.ok


def test_p10_same_source_grid_is_not_independent_enhancement():
    _request, _scope, analysis, result = _analysis(
        "transport", grid_relation="station_interpolated"
    )
    assert "grid_station_interpolated" in analysis.spatial.limitations
    assert "grid_contiguous_same_source" in analysis.process.transport_components
    assert "independent_grid_contiguous" not in analysis.process.transport_components
    assert result.ok


def test_p11_over_attribution_is_rejected_and_refusal_is_legal():
    request, scope, analysis, _result = _analysis("transport")
    forged = analysis.allowlist.model_copy(
        update={
            "claims": tuple(
                claim.model_copy(
                    update={
                        "claim_type": ClaimType.SOURCE_HYPOTHESIS,
                        "parameters": {
                            "status": "confirmed",
                            "source_class": "industry",
                            "hypothesis": "污染主要由某方位工业活动造成",
                        },
                        "level": None,
                    }
                )
                if claim.claim_type == ClaimType.SOURCE_HYPOTHESIS
                else claim
                for claim in analysis.allowlist.claims
            )
        }
    )
    gate = ReportGate(registry=EvidenceRegistry(), policy=DEFAULT_POLICY)
    # The forged allowlist cannot be registered against the real evidence; the
    # gate still rejects it rather than accepting model-supplied text.
    result = gate.validate_and_render(fallback_plan(forged), forged, request, scope)
    assert not result.ok
    legal = _analysis("transport")[3]
    assert "无法计算贡献率" in legal.report.body_markdown


def test_p12_distance_is_not_derived_from_wind():
    request, scope, analysis, _result = _analysis("transport")
    wind_claim = next(
        claim
        for claim in analysis.allowlist.claims
        if claim.claim_type == ClaimType.WIND_SECTOR
    )
    tampered = wind_claim.model_copy(
        update={"parameters": {**wind_claim.parameters, "distance_km": 5.0}}
    )
    fake_allowlist = analysis.allowlist.model_copy(
        update={
            "claims": tuple(
                tampered if claim.claim_id == tampered.claim_id else claim
                for claim in analysis.allowlist.claims
            )
        }
    )
    gate = ReportGate(registry=EvidenceRegistry(), policy=DEFAULT_POLICY)
    result = gate.validate_and_render(
        fallback_plan(fake_allowlist), fake_allowlist, request, scope
    )
    assert not result.ok
    assert any(
        "evidence_invalid" in error or "source_distance" in error
        for error in result.errors
    )


def test_p13_legal_coverage_percentage_and_refusal_pass():
    _request, _scope, analysis, result = _analysis("transport")
    assert result.ok
    assert "数据完整率" in result.report.body_markdown
    assert "%" in result.report.body_markdown
    assert "无法计算贡献率" in result.report.body_markdown


def test_p09_weather_timezone_alignment_before_pairing():
    from datetime import UTC

    from intelligent_tracing.evidence import EvidenceRegistry as Registry
    from intelligent_tracing.meteo_analysis import analyze_meteo
    from intelligent_tracing.station_analysis import analyze_station

    request = make_request()
    scope = make_scope()
    bundle = build_bundle()
    utc_weather = tuple(
        row.model_copy(update={"timestamp": row.timestamp.astimezone(UTC)})
        for row in bundle.weather
    )
    bundle = bundle.model_copy(update={"weather": utc_weather})
    registry = Registry()
    station = analyze_station(
        request,
        bundle,
        registry=registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    meteo = analyze_meteo(
        request,
        bundle,
        station,
        registry=registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    assert meteo.wind is not None
    assert meteo.wind.paired_samples >= DEFAULT_POLICY.wind.min_paired_samples
    assert "wind_pairs_insufficient" not in meteo.limitations


def test_p10_zero_valid_neighbors_is_unknown_not_isolated():
    from intelligent_tracing.evidence import EvidenceRegistry as Registry
    from intelligent_tracing.spatial_analysis import analyze_spatial
    from intelligent_tracing.station_analysis import analyze_station

    request = make_request()
    scope = make_scope()
    bundle = build_bundle()
    bundle = bundle.model_copy(update={"neighbors": {}})
    registry = Registry()
    station = analyze_station(
        request, bundle, registry=registry, scope=scope, policy=DEFAULT_POLICY
    )
    spatial = analyze_spatial(
        request,
        bundle,
        station,
        registry=registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    assert spatial.pattern == "unknown"
    assert "valid_neighbors_insufficient" in spatial.limitations
    assert spatial.pattern != "isolated"


def test_p10_single_sync_neighbor_is_not_multi_station():
    from intelligent_tracing.evidence import EvidenceRegistry as Registry
    from intelligent_tracing.spatial_analysis import analyze_spatial
    from intelligent_tracing.station_analysis import analyze_station

    request = make_request()
    scope = make_scope()
    bundle = build_bundle()
    flat = tuple(
        row.model_copy(update={"value": 22.0}) for row in bundle.neighbors["S003"]
    )
    flat_downwind = tuple(
        row.model_copy(update={"value": 22.0}) for row in bundle.neighbors["S004"]
    )
    neighbors = dict(bundle.neighbors)
    neighbors["S003"] = flat
    neighbors["S004"] = flat_downwind
    bundle = bundle.model_copy(update={"neighbors": neighbors})
    registry = Registry()
    station = analyze_station(
        request, bundle, registry=registry, scope=scope, policy=DEFAULT_POLICY
    )
    spatial = analyze_spatial(
        request,
        bundle,
        station,
        registry=registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    assert spatial.pattern != "multi_station_sync"
    assert spatial.pattern in {"mixed", "unknown"}
