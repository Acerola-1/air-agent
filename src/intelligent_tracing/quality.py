"""Field-level quality control for tracing inputs.

Sentinel handling is deliberately per field: ``pblh == -1`` is a known
sentinel, while negative ``u``/``v`` components are legitimate.  Real zero
concentrations and zero wind speeds are preserved.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from intelligent_tracing.policy import AnalysisPolicy, PollutantProfile
from intelligent_tracing.schemas import (
    Coverage,
    HourlyObservation,
    HourlyWeather,
)

_UNIT_FACTORS = {
    "ug/m3": 1.0,
    "µg/m³": 1.0,
    "μg/m³": 1.0,
    "ug/m^3": 1.0,
    "mg/m3": 1000.0,
    "mg/m³": 1000.0,
}


@dataclass(frozen=True)
class SeriesQuality:
    """Cleaned concentration series and its coverage record."""

    observations: tuple[HourlyObservation, ...]
    coverage: Coverage
    flags: tuple[str, ...]


@dataclass(frozen=True)
class WeatherQuality:
    """Cleaned weather rows and field-level flags."""

    rows: tuple[HourlyWeather, ...]
    flags: tuple[str, ...]
    pblh_unavailable: bool
    duplicates: int = 0
    invalid_rows: int = 0


def is_missing(value: object) -> bool:
    """Return whether a raw field is a textual/numeric placeholder.

    ``0`` and ``-1`` are intentionally not treated as missing here; callers
    apply field-specific sentinel rules.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "-", "null", "none", "nan"}
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def normalize_concentration_unit(unit: str) -> float | None:
    """Return the factor to ug/m3, or ``None`` for an unknown unit."""
    return _UNIT_FACTORS.get(unit.strip().lower())


def window_hours(start: datetime, end: datetime) -> int:
    """Return expected whole hours in a left-closed, right-open window."""
    return max(0, int((end - start).total_seconds() // 3600))


def _to_window_timezone(value: datetime, timezone: str) -> datetime | None:
    if value.tzinfo is None:
        return None
    try:
        return value.astimezone(ZoneInfo(timezone))
    except (KeyError, ValueError):
        return None


def _hour_aligned(value: datetime) -> bool:
    return not (value.minute or value.second or value.microsecond)


def _max_gap(timestamps: list[datetime]) -> int:
    if len(timestamps) < 2:
        return 0
    ordered = sorted(timestamps)
    return max(
        int((later - earlier).total_seconds() // 3600) - 1
        for earlier, later in zip(ordered, ordered[1:], strict=False)
    )


def _coverage(
    timestamps: list[datetime],
    *,
    start: datetime,
    end: datetime,
    invalid_rows: int,
    duplicates: int,
    min_ratio: float,
    min_samples: int,
    max_gap: int,
) -> Coverage:
    expected = window_hours(start, end)
    valid = len(timestamps)
    ratio = valid / expected if expected else 0.0
    gap = _max_gap(timestamps)
    sufficient = (
        expected > 0 and valid >= min_samples and ratio >= min_ratio and gap <= max_gap
    )
    return Coverage(
        expected_hours=expected,
        valid_hours=valid,
        ratio=ratio,
        max_gap_hours=gap,
        duplicates=duplicates,
        invalid_rows=invalid_rows,
        sufficient=sufficient,
    )


def clean_concentration_series(
    observations: tuple[HourlyObservation, ...] | list[HourlyObservation],
    *,
    start: datetime,
    end: datetime,
    timezone: str,
    policy: AnalysisPolicy,
    profile: PollutantProfile,
) -> SeriesQuality:
    """Convert, align, deduplicate and validate hourly concentrations."""
    flags: set[str] = set()
    invalid = 0
    duplicates = 0
    by_hour: dict[datetime, HourlyObservation] = {}
    expected_factor = normalize_concentration_unit(profile.unit)
    if expected_factor is None:
        raise ValueError(f"策略中的浓度单位无效: {profile.unit}")
    window_tz = ZoneInfo(timezone)

    for row in observations:
        local = _to_window_timezone(row.timestamp, timezone)
        if local is None:
            flags.add("naive_or_unknown_timestamp")
            invalid += 1
            continue
        if local < start.astimezone(window_tz) or local >= end.astimezone(window_tz):
            continue
        if not _hour_aligned(local):
            flags.add("not_hour_aligned")
            invalid += 1
            continue
        if is_missing(row.value):
            flags.add("missing_value")
            invalid += 1
            continue
        factor = normalize_concentration_unit(row.unit)
        if factor is None:
            flags.add("unknown_unit")
            invalid += 1
            continue
        value = float(row.value) * factor / expected_factor
        if value < profile.qc_min:
            flags.add("negative_concentration")
            invalid += 1
            continue
        if value > profile.qc_max:
            flags.add("concentration_out_of_range")
            invalid += 1
            continue
        cleaned = row.model_copy(
            update={
                "timestamp": local,
                "value": value,
                "unit": profile.unit,
                "qc_flags": tuple(row.qc_flags)
                + (("unit_converted",) if factor != 1.0 else ()),
            }
        )
        if local in by_hour:
            duplicates += 1
        by_hour[local] = cleaned

    ordered = tuple(by_hour[key] for key in sorted(by_hour))
    coverage = _coverage(
        [row.timestamp for row in ordered],
        start=start,
        end=end,
        invalid_rows=invalid,
        duplicates=duplicates,
        min_ratio=policy.quality.min_coverage_ratio,
        min_samples=policy.quality.min_samples,
        max_gap=policy.quality.max_gap_hours,
    )
    if not coverage.sufficient:
        flags.add("coverage_insufficient")
    if duplicates:
        flags.add("duplicate_timestamps")
    if coverage.max_gap_hours > policy.quality.max_gap_hours:
        flags.add("gap_exceeds_policy")
    return SeriesQuality(
        observations=ordered,
        coverage=coverage,
        flags=tuple(sorted(flags)),
    )


def _valid_wind_speed(value: float | None, policy: AnalysisPolicy) -> float | None:
    if is_missing(value):
        return None
    number = float(value)  # type: ignore[arg-type]
    if number < policy.quality.wind_speed_min or number > policy.quality.wind_speed_max:
        return None
    return number


def _valid_direction(value: float | None, policy: AnalysisPolicy) -> float | None:
    if is_missing(value):
        return None
    number = float(value)  # type: ignore[arg-type]
    if number < policy.quality.direction_min or number > policy.quality.direction_max:
        return None
    return number % 360.0


def _valid_component(value: float | None, policy: AnalysisPolicy) -> float | None:
    if is_missing(value):
        return None
    number = float(value)  # type: ignore[arg-type]
    if (
        number < policy.quality.wind_component_min
        or number > policy.quality.wind_component_max
    ):
        return None
    return number


def _valid_pblh(
    value: float | None, policy: AnalysisPolicy
) -> tuple[float | None, str]:
    if is_missing(value):
        return None, "pblh_missing"
    number = float(value)  # type: ignore[arg-type]
    if number == policy.quality.pblh_sentinel:
        return None, "pblh_sentinel"
    if number <= policy.quality.pblh_min or number > policy.quality.pblh_max:
        return None, "pblh_out_of_range"
    return number, ""


def clean_weather(
    rows: tuple[HourlyWeather, ...] | list[HourlyWeather],
    *,
    policy: AnalysisPolicy,
    start: datetime | None = None,
    end: datetime | None = None,
    timezone: str = "Asia/Shanghai",
) -> WeatherQuality:
    """Validate, align, deduplicate and timezone-normalise weather rows."""
    flags: set[str] = set()
    cleaned: dict[datetime, HourlyWeather] = {}
    pblh_missing = False
    duplicates = 0
    invalid = 0
    window_tz = ZoneInfo(timezone)
    for row in rows:
        local = _to_window_timezone(row.timestamp, timezone)
        if local is None:
            flags.add("naive_or_unknown_timestamp")
            invalid += 1
            continue
        if start is not None and local < start.astimezone(window_tz):
            continue
        if end is not None and local >= end.astimezone(window_tz):
            continue
        if not _hour_aligned(local):
            flags.add("not_hour_aligned")
            invalid += 1
            continue
        wind_speed = _valid_wind_speed(row.wind_speed, policy)
        direction = _valid_direction(row.wind_direction, policy)
        u = _valid_component(row.u, policy)
        v = _valid_component(row.v, policy)
        pblh, pblh_flag = _valid_pblh(row.pblh, policy)
        if pblh_flag:
            pblh_missing = True
            flags.add(pblh_flag)
        if row.wind_speed is not None and wind_speed is None:
            flags.add("wind_speed_invalid")
        if row.wind_direction is not None and direction is None:
            flags.add("wind_direction_invalid")
        if (row.u is not None and u is None) or (row.v is not None and v is None):
            flags.add("wind_component_invalid")
        humidity = row.humidity
        if humidity is not None and not (
            policy.quality.humidity_min
            <= float(humidity)
            <= policy.quality.humidity_max
        ):
            humidity = None
            flags.add("humidity_invalid")
        pressure = row.pressure
        if pressure is not None and not (
            policy.quality.pressure_min
            <= float(pressure)
            <= policy.quality.pressure_max
        ):
            pressure = None
            flags.add("pressure_invalid")
        temperature = row.temperature
        if temperature is not None and not (
            policy.quality.temperature_min
            <= float(temperature)
            <= policy.quality.temperature_max
        ):
            temperature = None
            flags.add("temperature_invalid")
        if row.source_type == "nwp" and row.run_time is None:
            flags.add("nwp_run_time_unknown")
        if local in cleaned:
            duplicates += 1
        cleaned[local] = row.model_copy(
            update={
                "timestamp": local,
                "wind_speed": wind_speed,
                "wind_direction": direction,
                "u": u,
                "v": v,
                "pblh": pblh,
                "humidity": humidity,
                "pressure": pressure,
                "temperature": temperature,
            }
        )
    if duplicates:
        flags.add("duplicate_timestamps")
    if invalid:
        flags.add("weather_rows_invalid")
    return WeatherQuality(
        rows=tuple(cleaned[key] for key in sorted(cleaned)),
        flags=tuple(sorted(flags)),
        pblh_unavailable=pblh_missing,
        duplicates=duplicates,
        invalid_rows=invalid,
    )


def derive_uv_from_direction(
    wind_direction: float, wind_speed: float
) -> tuple[float, float]:
    """Convert meteorological "from" direction to u/v components.

    Meteorological direction is the direction the wind comes *from*; u is
    positive eastward and v positive northward.
    """
    radians = math.radians(wind_direction % 360.0)
    return -wind_speed * math.sin(radians), -wind_speed * math.cos(radians)


def direction_from_uv(u: float, v: float) -> float:
    """Return meteorological "from" direction for u/v components."""
    return (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0
