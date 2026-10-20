"""Multi-pollutant comparison with explicit unit and primary-pollutant rules."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Frozen model for multi-pollutant inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PollutantMeasurement(StrictModel):
    """One pollutant measurement with its averaging period and unit."""

    pollutant: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    averaging_period: str = Field(min_length=1)
    normalized_score: float | None = None
    normalization_method: str | None = None
    source: str = "platform"


class PrimaryPollutantDecision(StrictModel):
    """Primary-pollutant decision basis, never raw concentration ranking."""

    primary_pollutant: str | None = None
    basis: str
    limitations: tuple[str, ...] = ()


def group_measurements_by_unit(
    measurements: tuple[PollutantMeasurement, ...],
) -> dict[str, tuple[PollutantMeasurement, ...]]:
    """Group measurements by unit without comparing values across groups."""
    grouped: dict[str, list[PollutantMeasurement]] = {}
    for measurement in measurements:
        grouped.setdefault(measurement.unit, []).append(measurement)
    return {unit: tuple(items) for unit, items in grouped.items()}


def select_primary_pollutant(
    measurements: tuple[PollutantMeasurement, ...],
    *,
    platform_primary: str | None = None,
) -> PrimaryPollutantDecision:
    """Select only from an explicit platform field or a shared normalization.

    Raw concentrations in different units (or even the same unit but different
    averaging periods) are never ranked as a "primary pollutant".
    """
    if platform_primary:
        if any(item.pollutant == platform_primary for item in measurements):
            return PrimaryPollutantDecision(
                primary_pollutant=platform_primary,
                basis="platform_field",
            )
        return PrimaryPollutantDecision(
            basis="platform_field_missing_measurement",
            limitations=("platform_primary_without_measurement",),
        )
    if not measurements:
        return PrimaryPollutantDecision(
            basis="no_measurements", limitations=("no_measurements",)
        )
    periods = {item.averaging_period for item in measurements}
    if len(periods) > 1:
        return PrimaryPollutantDecision(
            basis="mixed_averaging_periods",
            limitations=("primary_pollutant_requires_same_period",),
        )
    normalized = [
        item
        for item in measurements
        if item.normalized_score is not None and item.normalization_method
    ]
    if len(normalized) == len(measurements):
        methods = {item.normalization_method for item in normalized}
        if len(methods) == 1:
            best = max(normalized, key=lambda item: item.normalized_score or 0.0)
            return PrimaryPollutantDecision(
                primary_pollutant=best.pollutant,
                basis="shared_normalization",
            )
        return PrimaryPollutantDecision(
            basis="mixed_normalization_methods",
            limitations=("primary_pollutant_requires_shared_normalization",),
        )
    return PrimaryPollutantDecision(
        basis="raw_concentration_not_comparable",
        limitations=(
            "primary_pollutant_requires_platform_or_normalized_basis",
            "raw_concentrations_not_comparable_across_pollutants",
        ),
    )
