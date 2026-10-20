"""Policy, unit conversion and field-level quality tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fixtures import START, build_bundle, make_request, make_scope

from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.policy import (
    DEFAULT_POLICY,
    STAGE1_POLICY,
    STAGE2_POLICY,
)
from intelligent_tracing.quality import (
    clean_concentration_series,
    clean_weather,
    derive_uv_from_direction,
    direction_from_uv,
)
from intelligent_tracing.schemas import HourlyObservation, HourlyWeather
from intelligent_tracing.station_analysis import analyze_station


def test_supported_profiles_are_explicit():
    assert DEFAULT_POLICY.version == "analysis-policy-v2"
    assert STAGE2_POLICY is DEFAULT_POLICY
    assert DEFAULT_POLICY.require_supported("pm2.5").unit == "ug/m3"
    for pollutant in ("pm2.5", "pm10", "o3", "no2", "so2", "co"):
        assert DEFAULT_POLICY.require_supported(pollutant).key == pollutant
    assert DEFAULT_POLICY.require_supported("co").unit == "mg/m3"
    with pytest.raises(ValueError, match="当前阶段未实现"):
        DEFAULT_POLICY.require_supported("vocs")
    # v1 保持仅 PM2.5，不被静默扩大。
    assert STAGE1_POLICY.version == "analysis-policy-v1"
    assert STAGE1_POLICY.require_supported("pm2.5").unit == "ug/m3"
    with pytest.raises(ValueError, match="当前阶段未实现"):
        STAGE1_POLICY.require_supported("o3")


def test_pblh_sentinel_negative_uv_and_zero_values():
    rows = [
        HourlyWeather(
            timestamp=START,
            wind_speed=0.0,
            wind_direction=0.0,
            u=-1.0,
            v=-1.0,
            pblh=-1.0,
        ),
        HourlyWeather(
            timestamp=START + timedelta(hours=1),
            wind_speed=2.0,
            wind_direction=270.0,
            u=-2.0,
            v=0.0,
            pblh=800.0,
        ),
    ]
    cleaned = clean_weather(rows, policy=DEFAULT_POLICY)
    assert cleaned.rows[0].pblh is None
    assert cleaned.rows[0].u == -1.0
    assert cleaned.rows[0].v == -1.0
    assert cleaned.rows[0].wind_speed == 0.0
    assert "pblh_sentinel" in cleaned.flags
    assert cleaned.pblh_unavailable is True


def test_negative_uv_roundtrip_is_not_treated_as_missing():
    u, v = derive_uv_from_direction(270.0, 3.0)
    assert u == pytest.approx(3.0)
    assert v == pytest.approx(0.0, abs=1e-9)
    assert direction_from_uv(u, v) == pytest.approx(270.0)


def test_duplicate_unit_conversion_and_zero_concentration():
    observations = [
        HourlyObservation(station_code="S001", timestamp=START, value=0.0),
        HourlyObservation(
            station_code="S001", timestamp=START, value=0.05, unit="mg/m3"
        ),
        HourlyObservation(
            station_code="S001",
            timestamp=START + timedelta(hours=1),
            value=0.0,
            unit="mg/m3",
        ),
        HourlyObservation(
            station_code="S001",
            timestamp=START + timedelta(hours=2),
            value=20.0,
            unit="unknown",
        ),
    ]
    cleaned = clean_concentration_series(
        observations,
        start=START,
        end=START + timedelta(hours=3),
        timezone="Asia/Shanghai",
        policy=DEFAULT_POLICY,
        profile=DEFAULT_POLICY.require_supported("pm2.5"),
    )
    assert cleaned.coverage.duplicates == 1
    assert cleaned.coverage.invalid_rows == 1
    assert len(cleaned.observations) == 2
    # The duplicate keeps the last row and converts mg/m3 to ug/m3.
    assert cleaned.observations[0].value == pytest.approx(50.0)
    assert cleaned.observations[1].value == 0.0
    assert "duplicate_timestamps" in cleaned.flags
    assert "unknown_unit" in cleaned.flags


def test_non_hour_aligned_timestamp_is_rejected():
    observations = [
        HourlyObservation(
            station_code="S001",
            timestamp=START + timedelta(minutes=30),
            value=30.0,
        )
    ]
    cleaned = clean_concentration_series(
        observations,
        start=START,
        end=START + timedelta(hours=2),
        timezone="Asia/Shanghai",
        policy=DEFAULT_POLICY,
        profile=DEFAULT_POLICY.require_supported("pm2.5"),
    )
    assert cleaned.coverage.valid_hours == 0
    assert "not_hour_aligned" in cleaned.flags


def test_insufficient_coverage_is_not_no_anomaly():
    request = make_request()
    bundle = build_bundle(scenario="insufficient")
    findings = analyze_station(
        request,
        bundle,
        registry=EvidenceRegistry(),
        scope=make_scope(),
        policy=DEFAULT_POLICY,
    )
    assert findings.status == "insufficient"
    assert "coverage_insufficient" in findings.limitations


def test_weather_timezone_alignment_dedup_and_half_hour_flag():
    from datetime import UTC

    duplicate_late = HourlyWeather(
        timestamp=START,
        wind_speed=9.0,
        wind_direction=10.0,
        pblh=700.0,
    )
    rows = [
        HourlyWeather(
            timestamp=START.astimezone(UTC),
            wind_speed=2.0,
            wind_direction=270.0,
            pblh=800.0,
        ),
        duplicate_late,
        HourlyWeather(
            timestamp=START + timedelta(minutes=30),
            wind_speed=2.0,
            wind_direction=270.0,
            pblh=800.0,
        ),
    ]
    cleaned = clean_weather(
        rows,
        policy=DEFAULT_POLICY,
        start=START,
        end=START + timedelta(hours=2),
        timezone="Asia/Shanghai",
    )
    assert len(cleaned.rows) == 1
    assert cleaned.rows[0].timestamp == START
    assert cleaned.rows[0].wind_speed == 9.0
    assert cleaned.duplicates == 1
    assert cleaned.invalid_rows == 1
    assert "duplicate_timestamps" in cleaned.flags
    assert "not_hour_aligned" in cleaned.flags
