"""Stage-two pollutant profiles, O3 8h evaluation and multi-pollutant rules."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fixtures import (
    START,
    build_stage2_bundle,
    make_request,
    make_scope,
)

from intelligent_tracing.claims import fallback_plan
from intelligent_tracing.evaluation import daily_max_8h, pm25_24h_sliding
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.gate import ReportGate
from intelligent_tracing.multi_pollutant import (
    PollutantMeasurement,
    group_measurements_by_unit,
    select_primary_pollutant,
)
from intelligent_tracing.orchestrator import analyze_bundle
from intelligent_tracing.policy import STAGE2_POLICY
from intelligent_tracing.schemas import HourlyObservation


@pytest.mark.parametrize("pollutant", ["pm10", "o3", "no2", "so2", "co"])
def test_stage2_pollutants_use_their_own_profile(pollutant: str):
    request = make_request(pollutant=pollutant)
    scope = make_scope()
    registry = EvidenceRegistry()
    analysis = analyze_bundle(
        request,
        build_stage2_bundle(pollutant),
        registry=registry,
        scope=scope,
        policy=STAGE2_POLICY,
    )
    gate = ReportGate(registry=registry, policy=STAGE2_POLICY)
    result = gate.validate_and_render(
        fallback_plan(analysis.allowlist), analysis.allowlist, request, scope
    )
    profile = STAGE2_POLICY.require_supported(pollutant)
    assert result.ok
    assert result.report is not None
    assert profile.display_name in result.report.body_markdown
    if pollutant != "pm10":
        assert "PM2.5 污染过程与来向线索分析" not in result.report.body_markdown
        assert "PM2.5" not in result.report.canvas.title
    assert profile.display_name in result.report.canvas.title
    if pollutant == "co":
        assert "毫克/立方米" in result.report.body_markdown
        assert "微克/立方米" not in result.report.body_markdown
    if pollutant in {"pm10", "no2", "so2", "co"}:
        assert "evaluation_profile_not_defined" in analysis.station.limitations


def _o3_series(values: list[float | None]) -> tuple[HourlyObservation, ...]:
    return tuple(
        HourlyObservation(
            station_code="S001",
            timestamp=START + timedelta(hours=index),
            value=value,
            unit="ug/m3",
        )
        for index, value in enumerate(values)
    )


def test_o3_daily_max_8h_and_partial_validity():
    values = [40.0 + (index % 10) * 2 for index in range(24)]
    values[10] = None
    values[11] = None
    summaries = daily_max_8h(
        _o3_series(values), timezone="Asia/Shanghai", min_valid_hours=6
    )
    assert summaries
    assert summaries[0].metric == "daily_max_8h"
    assert summaries[0].required_hours == 8
    assert summaries[0].valid_hours >= 6


def test_o3_8h_cross_day_window_is_labeled():
    values = [10.0] * 24 + [200.0] * 24
    summaries = daily_max_8h(_o3_series(values), timezone="Asia/Shanghai")
    assert summaries
    # A high late-night block forces the second day's first windows to use
    # previous-day hours; the limitation must be explicit.
    assert any("cross_day_8h_window" in summary.limitations for summary in summaries)


def test_pm25_24h_sliding_is_supplementary_not_natural_daily():
    values = [20.0 + index for index in range(24)]
    summaries = pm25_24h_sliding(
        _o3_series(values), timezone="Asia/Shanghai", min_valid_hours=18
    )
    assert summaries
    assert summaries[0].metric == "24h_sliding"
    assert "pm25_24h_sliding_supplementary" in summaries[0].limitations


def test_multi_pollutant_never_ranks_raw_cross_unit_concentrations():
    measurements = (
        PollutantMeasurement(
            pollutant="pm2.5", value=75.0, unit="ug/m3", averaging_period="hourly"
        ),
        PollutantMeasurement(
            pollutant="co", value=1.2, unit="mg/m3", averaging_period="hourly"
        ),
    )
    decision = select_primary_pollutant(measurements)
    assert decision.primary_pollutant is None
    assert decision.basis == "raw_concentration_not_comparable"
    grouped = group_measurements_by_unit(measurements)
    assert set(grouped) == {"ug/m3", "mg/m3"}


def test_primary_pollutant_accepts_platform_field_or_shared_normalization():
    measurements = (
        PollutantMeasurement(
            pollutant="pm2.5",
            value=75.0,
            unit="ug/m3",
            averaging_period="daily",
            normalized_score=1.2,
            normalization_method="policy-x",
        ),
        PollutantMeasurement(
            pollutant="o3",
            value=160.0,
            unit="ug/m3",
            averaging_period="daily",
            normalized_score=1.5,
            normalization_method="policy-x",
        ),
    )
    assert (
        select_primary_pollutant(measurements, platform_primary="pm2.5").basis
        == "platform_field"
    )
    normalized = select_primary_pollutant(measurements)
    assert normalized.primary_pollutant == "o3"
    assert normalized.basis == "shared_normalization"
    mixed = (
        measurements[0],
        measurements[1].model_copy(update={"normalization_method": "policy-y"}),
    )
    assert select_primary_pollutant(mixed).primary_pollutant is None
