"""Versioned deterministic policy for pollution tracing analysis.

`analysis-policy-v2` enables the six pollutants for phase two; `analysis-policy-v1`
is kept as the frozen PM2.5-only baseline. Both freeze the quality-control,
anomaly, spatial, wind and process rules used by the tracing graph.  Values marked ``research`` are
engineering heuristics selected for reproducibility; they are not regulatory
limits, statistical significance levels or source-distance estimates.  The
policy must be reviewed before production use and any change requires a new
version string.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Final

POLICY_VERSION_V1: Final[str] = "analysis-policy-v1"
POLICY_VERSION_V2: Final[str] = "analysis-policy-v2"
POLICY_VERSION: Final[str] = POLICY_VERSION_V2

# The first delivery phase supports PM2.5 only.  Other pollutants are listed so
# that unsupported requests can be rejected explicitly instead of silently
# reusing PM2.5 rules.
KNOWN_POLLUTANTS: Final[tuple[str, ...]] = ("pm2.5", "pm10", "o3", "no2", "so2", "co")
STAGE1_POLLUTANTS: Final[frozenset[str]] = frozenset({"pm2.5"})
STAGE2_POLLUTANTS: Final[frozenset[str]] = frozenset(KNOWN_POLLUTANTS)
# Production default is phase two.  Policy v1 stays available as an explicit
# baseline and is never widened silently.
SUPPORTED_POLLUTANTS: Final[frozenset[str]] = STAGE2_POLLUTANTS

FIXED_LIMITATIONS: Final[tuple[str, ...]] = (
    "trajectory_unavailable",
    "emission_inventory_missing",
    "no_chemical_composition",
)


@dataclass(frozen=True)
class PollutantProfile:
    """Pollutant-specific units and display conventions."""

    key: str
    display_name: str
    unit: str
    display_unit: str
    qc_min: float
    qc_max: float
    baseline_abs_delta: float
    anomaly_abs_delta: float
    research_note: str


@dataclass(frozen=True)
class QualityPolicy:
    """Field-level quality-control policy."""

    concentration_min: float = 0.0
    concentration_max: float = 1000.0
    wind_speed_min: float = 0.0
    wind_speed_max: float = 60.0
    wind_component_min: float = -60.0
    wind_component_max: float = 60.0
    direction_min: float = 0.0
    direction_max: float = 360.0
    pblh_min: float = 0.0
    pblh_max: float = 5000.0
    pblh_sentinel: float = -1.0
    humidity_min: float = 0.0
    humidity_max: float = 100.0
    pressure_min: float = 300.0
    pressure_max: float = 1100.0
    temperature_min: float = -80.0
    temperature_max: float = 60.0
    max_gap_hours: int = 3
    min_coverage_ratio: float = 0.7
    min_samples: int = 12
    alignment_tolerance_minutes: int = 0
    research_note: str = (
        "research heuristic: plausible physical ranges and gap limits, "
        "not regulatory validation"
    )


@dataclass(frozen=True)
class BaselinePolicy:
    """Baseline and anomaly detection policy."""

    lookback_days: int = 7
    min_coverage_ratio: float = 0.7
    min_samples: int = 48
    robust_z_threshold: float = 3.5
    min_abs_delta: float = 10.0
    min_duration_hours: int = 2
    change_rate_threshold: float = 0.5
    zero_baseline_uses_absolute_delta: bool = True
    research_note: str = (
        "research heuristic: robust median/MAD with absolute floor; "
        "not a statistical significance test or regulatory exceedance"
    )


@dataclass(frozen=True)
class SpatialPolicy:
    """Spatial pattern policy."""

    default_neighbor_radius_km: float = 10.0
    min_neighbor_radius_km: float = 1.0
    max_neighbor_radius_km: float = 50.0
    min_valid_neighbors: int = 2
    min_sync_neighbors: int = 2
    sync_overlap_hours: int = 1
    grid_high_value: float = 75.0
    grid_min_contiguous_cells: int = 3
    grid_neighbor_tolerance_cells: int = 1
    research_note: str = (
        "research heuristic: 75 ug/m3 grid highlight and 3-cell contiguity; "
        "not a source or transport threshold"
    )


@dataclass(frozen=True)
class WindPolicy:
    """Wind, sector and lag association policy."""

    calm_speed_threshold: float = 0.5
    sector_width_degrees: float = 45.0
    min_paired_samples: int = 6
    min_sector_coverage: float = 0.5
    min_resultant_length: float = 0.5
    upwind_angle_tolerance_degrees: float = 45.0
    min_upwind_neighbors: int = 2
    min_upwind_gradient: float = 10.0
    lag_search_hours: int = 6
    lag_min_pairs: int = 12
    lag_min_abs_r: float = 0.5
    early_rise_min_hours: int = 1
    early_rise_max_hours: int = 6
    research_note: str = (
        "research heuristic: circular statistics and Pearson lag; "
        "association is not causation or source distance"
    )


@dataclass(frozen=True)
class ProcessPolicy:
    """Rules combining spatial, temporal and meteorological evidence."""

    low_pblh_threshold: float = 500.0
    low_wind_threshold: float = 2.0
    min_transport_components: int = 3
    allow_source_hypothesis: bool = True
    research_note: str = (
        "research heuristic: transport association requires spatial, temporal "
        "and wind components together, with counter-evidence retained"
    )


@dataclass(frozen=True)
class BudgetPolicy:
    """Run budget limits; exhaustion returns a validated partial result."""

    max_subagent_tasks: int = 4
    max_supplemental_queries: int = 2
    max_model_calls: int = 8
    max_concurrency: int = 2
    total_timeout_seconds: int = 180
    max_tool_retries: int = 1
    retry_only_transient: bool = True
    research_note: str = "engineering budget, not a quality threshold"


@dataclass(frozen=True)
class AnalysisPolicy:
    """Complete frozen policy bundle."""

    version: str = POLICY_VERSION
    phase: int = 2
    enabled_pollutants: frozenset[str] = SUPPORTED_POLLUTANTS
    quality: QualityPolicy = field(default_factory=QualityPolicy)
    baseline: BaselinePolicy = field(default_factory=BaselinePolicy)
    spatial: SpatialPolicy = field(default_factory=SpatialPolicy)
    wind: WindPolicy = field(default_factory=WindPolicy)
    process: ProcessPolicy = field(default_factory=ProcessPolicy)
    budget: BudgetPolicy = field(default_factory=BudgetPolicy)
    pollutants: dict[str, PollutantProfile] = field(
        default_factory=lambda: {
            "pm2.5": PollutantProfile(
                key="pm2.5",
                display_name="PM2.5",
                unit="ug/m3",
                display_unit="微克/立方米",
                qc_min=0.0,
                qc_max=1000.0,
                baseline_abs_delta=10.0,
                anomaly_abs_delta=10.0,
                research_note=(
                    "PM2.5 profile; hourly process and 24h sliding are "
                    "supplementary, not a regulatory daily mean"
                ),
            ),
            "pm10": PollutantProfile(
                key="pm10",
                display_name="PM10",
                unit="ug/m3",
                display_unit="微克/立方米",
                qc_min=0.0,
                qc_max=1500.0,
                baseline_abs_delta=15.0,
                anomaly_abs_delta=15.0,
                research_note="PM10 research profile; no regulatory threshold inferred",
            ),
            "o3": PollutantProfile(
                key="o3",
                display_name="O3",
                unit="ug/m3",
                display_unit="微克/立方米",
                qc_min=0.0,
                qc_max=1000.0,
                baseline_abs_delta=20.0,
                anomaly_abs_delta=20.0,
                research_note=(
                    "O3 profile; hourly process plus daily maximum 8h "
                    "evaluation with explicit validity rules"
                ),
            ),
            "no2": PollutantProfile(
                key="no2",
                display_name="NO2",
                unit="ug/m3",
                display_unit="微克/立方米",
                qc_min=0.0,
                qc_max=2000.0,
                baseline_abs_delta=10.0,
                anomaly_abs_delta=10.0,
                research_note=(
                    "NO2 profile; temporal patterns do not establish traffic sources"
                ),
            ),
            "so2": PollutantProfile(
                key="so2",
                display_name="SO2",
                unit="ug/m3",
                display_unit="微克/立方米",
                qc_min=0.0,
                qc_max=2000.0,
                baseline_abs_delta=10.0,
                anomaly_abs_delta=10.0,
                research_note=(
                    "SO2 profile; wind association does not identify an enterprise"
                ),
            ),
            "co": PollutantProfile(
                key="co",
                display_name="CO",
                unit="mg/m3",
                display_unit="毫克/立方米",
                qc_min=0.0,
                qc_max=50.0,
                baseline_abs_delta=2.0,
                anomaly_abs_delta=2.0,
                research_note=(
                    "CO profile in mg/m3; no combustion-source quantification"
                ),
            ),
        }
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable policy snapshot for evidence."""
        return asdict(self)

    def require_supported(self, pollutant: str) -> PollutantProfile:
        """Return the profile only when the current phase enables it."""
        key = pollutant.strip().lower()
        if key not in self.enabled_pollutants:
            raise ValueError(
                f"当前阶段未实现 {pollutant!r} 的独立分析画像"
                f"（phase={self.phase}，仅放行 {sorted(self.enabled_pollutants)}）"
            )
        profile = self.pollutants.get(key)
        if profile is None:
            raise ValueError(f"污染物画像缺失: {pollutant!r}")
        return profile


STAGE1_POLICY: Final[AnalysisPolicy] = AnalysisPolicy(
    version=POLICY_VERSION_V1,
    phase=1,
    enabled_pollutants=STAGE1_POLLUTANTS,
)
DEFAULT_POLICY: Final[AnalysisPolicy] = AnalysisPolicy(
    version=POLICY_VERSION_V2,
    phase=2,
    enabled_pollutants=STAGE2_POLLUTANTS,
)
STAGE2_POLICY: Final[AnalysisPolicy] = DEFAULT_POLICY
