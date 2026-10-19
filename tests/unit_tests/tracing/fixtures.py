"""Deterministic PM2.5 fixtures for tracing unit tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from intelligent_tracing.schemas import (
    DataBundle,
    GridCell,
    HourlyObservation,
    HourlyWeather,
    RuntimeScope,
    StationMeta,
    Target,
    TimeWindow,
    TracingRequest,
)

TZ = ZoneInfo("Asia/Shanghai")
START = datetime(2026, 10, 1, 0, 0, tzinfo=TZ)
END = datetime(2026, 10, 2, 0, 0, tzinfo=TZ)
BASELINE_START = START - timedelta(days=7)
ANALYSIS_ID = "analysis-pm25-001"


def hourly_values(start: datetime, count: int, factory) -> tuple:
    return tuple(
        factory(start + timedelta(hours=index), index) for index in range(count)
    )


def baseline_observation(
    timestamp: datetime, index: int, station: str = "S001"
) -> HourlyObservation:
    value = 20.0 + (index % 7) * 0.4 + (index % 3) * 0.2
    return HourlyObservation(
        station_code=station,
        timestamp=timestamp,
        value=value,
        unit="ug/m3",
        source="fixture",
    )


TARGET_VALUES = [
    22,
    22,
    21,
    21,
    22,
    23,
    30,
    45,
    70,
    95,
    110,
    100,
    80,
    60,
    45,
    35,
    28,
    25,
    24,
    22,
    21,
    20,
    19,
    18,
]
NEIGHBOR_UPWIND_A = [
    21,
    21,
    22,
    25,
    50,
    70,
    90,
    100,
    95,
    80,
    65,
    50,
    40,
    32,
    28,
    25,
    23,
    22,
    21,
    21,
    20,
    20,
    19,
    19,
]
NEIGHBOR_UPWIND_B = [
    22,
    22,
    22,
    24,
    40,
    55,
    70,
    85,
    90,
    75,
    60,
    48,
    38,
    30,
    26,
    24,
    23,
    22,
    21,
    21,
    20,
    20,
    20,
    19,
]
NEIGHBOR_DOWNWIND = [
    20,
    20,
    21,
    21,
    22,
    23,
    24,
    25,
    28,
    32,
    36,
    38,
    34,
    30,
    27,
    25,
    24,
    23,
    22,
    22,
    21,
    21,
    20,
    20,
]
NEIGHBOR_SOUTH = [
    21,
    21,
    21,
    22,
    22,
    23,
    23,
    24,
    24,
    25,
    25,
    26,
    25,
    24,
    24,
    23,
    23,
    22,
    22,
    21,
    21,
    21,
    20,
    20,
]


def _observations(station: str, values: list[float]) -> tuple[HourlyObservation, ...]:
    return tuple(
        HourlyObservation(
            station_code=station,
            timestamp=START + timedelta(hours=index),
            value=float(value),
            unit="ug/m3",
            source="fixture",
        )
        for index, value in enumerate(values)
    )


def _weather(
    wind_direction: float = 290.0, wind_speed: float = 3.0, pblh: float = 900.0
) -> tuple[HourlyWeather, ...]:
    rows = []
    for index in range(24):
        direction = wind_direction + (index % 3 - 1) * 5
        rows.append(
            HourlyWeather(
                timestamp=START + timedelta(hours=index),
                wind_speed=wind_speed,
                wind_direction=direction,
                u=-wind_speed * 0.94,
                v=wind_speed * 0.34,
                pblh=pblh + index * 2,
                humidity=55.0,
                pressure=1005.0,
                temperature=18.0,
                source_type="observation",
            )
        )
    return tuple(rows)


def _nwp() -> tuple[HourlyWeather, ...]:
    run_time = START - timedelta(hours=6)
    return tuple(
        HourlyWeather(
            timestamp=START + timedelta(hours=index),
            wind_speed=3.2,
            wind_direction=295.0,
            u=-3.0,
            v=0.36,
            pblh=850.0,
            source_type="nwp",
            run_time=run_time,
            lead_hours=index + 6,
        )
        for index in range(24)
    )


def _stations() -> tuple[StationMeta, ...]:
    return (
        StationMeta(
            station_code="S001",
            name="目标站",
            region_code="410100",
            latitude=34.75,
            longitude=113.62,
            distance_km=0.0,
            station_type="S-10",
            source_relation="independent",
        ),
        StationMeta(
            station_code="S002",
            name="上风向A",
            region_code="410100",
            latitude=34.80,
            longitude=113.55,
            distance_km=8.0,
            station_type="S-10",
            source_relation="independent",
        ),
        StationMeta(
            station_code="S003",
            name="上风向B",
            region_code="410100",
            latitude=34.75,
            longitude=113.54,
            distance_km=7.5,
            station_type="S-10",
            source_relation="independent",
        ),
        StationMeta(
            station_code="S004",
            name="下风向",
            region_code="410100",
            latitude=34.75,
            longitude=113.70,
            distance_km=7.5,
            station_type="S-10",
            source_relation="independent",
        ),
        StationMeta(
            station_code="S005",
            name="南侧站",
            region_code="410100",
            latitude=34.68,
            longitude=113.62,
            distance_km=7.8,
            station_type="S-10",
            source_relation="independent",
        ),
    )


def _grid(source_relation: str = "independent") -> tuple[GridCell, ...]:
    cells = []
    values = [[40, 45, 50, 40], [55, 85, 95, 60], [45, 80, 90, 55], [35, 40, 45, 35]]
    for row in range(4):
        for col in range(4):
            cells.append(
                GridCell(
                    cell_id=f"G{row}{col}",
                    row=row,
                    col=col,
                    latitude=34.68 + row * 0.04,
                    longitude=113.50 + col * 0.04,
                    value=float(values[row][col]),
                    observed_at=START + timedelta(hours=10),
                    source_relation=source_relation,
                )
            )
    return tuple(cells)


def make_request(
    *,
    analysis_id: str = ANALYSIS_ID,
    revision: int = 1,
    radius: float = 10.0,
    start: datetime = START,
    end: datetime = END,
    baseline: bool = True,
    pollutant: str = "pm2.5",
) -> TracingRequest:
    request = TracingRequest(
        analysis_id=analysis_id,
        revision=revision,
        target=Target(
            station_code="S001",
            station_name="目标站",
            region_code="410100",
            latitude=34.75,
            longitude=113.62,
            coordinate_source="fixture",
            admin_scope=("410100",),
        ),
        pollutant=pollutant,
        time_window=TimeWindow(start=start, end=end, timezone="Asia/Shanghai"),
        neighbor_radius_km=radius,
        baseline_window=(
            TimeWindow(
                start=BASELINE_START,
                end=START,
                timezone="Asia/Shanghai",
            )
            if baseline
            else None
        ),
        analysis_policy_version="analysis-policy-v1",
        capability_snapshot_ref="sel-001",
    )
    return request.frozen()


def make_scope(
    *, context_version: str = "cv-1", epoch: str = "epoch-1"
) -> RuntimeScope:
    return RuntimeScope(
        tenant_id="tenant-a",
        user_id="user-a",
        thread_id="thread-a",
        graph_id="intelligent-tracing",
        context_version=context_version,
        epoch=epoch,
        membership_id="membership-a",
    )


def baseline_bundle() -> tuple[HourlyObservation, ...]:
    return hourly_values(BASELINE_START, 168, baseline_observation)


def build_bundle(
    *,
    scenario: str = "transport",
    grid_relation: str = "independent",
    weather: tuple[HourlyWeather, ...] | None = None,
    nwp: tuple[HourlyWeather, ...] | None = None,
) -> DataBundle:
    target_values = list(TARGET_VALUES)
    neighbor_a = list(NEIGHBOR_UPWIND_A)
    neighbor_b = list(NEIGHBOR_UPWIND_B)
    neighbor_d = list(NEIGHBOR_DOWNWIND)
    neighbor_s = list(NEIGHBOR_SOUTH)
    observations = (*baseline_bundle(), *_observations("S001", target_values))
    if scenario == "isolated":
        neighbor_a = [22.0 + (index % 3) * 0.5 for index in range(24)]
        neighbor_b = [21.0 + (index % 4) * 0.4 for index in range(24)]
        neighbor_d = [20.0 + (index % 2) * 0.3 for index in range(24)]
        neighbor_s = [21.0 + (index % 3) * 0.3 for index in range(24)]
    elif scenario == "no_anomaly":
        target_values = [20.0 + (index % 5) * 0.4 for index in range(24)]
        observations = (*baseline_bundle(), *_observations("S001", target_values))
    elif scenario == "insufficient":
        observations = (
            *baseline_bundle(),
            *_observations("S001", target_values[:6]),
        )
    if weather is None:
        if scenario == "stagnation":
            weather = _weather(wind_direction=180.0, wind_speed=0.2, pblh=120.0)
        elif scenario == "calm":
            weather = _weather(wind_direction=0.0, wind_speed=0.1, pblh=200.0)
        else:
            weather = _weather()
    bundle = DataBundle(
        snapshot_id=f"fixture-{scenario}-v1",
        target_station_code="S001",
        region_code="410100",
        observations=observations,
        neighbors={
            "S002": _observations("S002", neighbor_a),
            "S003": _observations("S003", neighbor_b),
            "S004": _observations("S004", neighbor_d),
            "S005": _observations("S005", neighbor_s),
        },
        stations=_stations(),
        grid_cells=_grid(grid_relation),
        weather=weather,
        nwp=nwp if nwp is not None else _nwp(),
        source_type="fixture",
    )
    return bundle


def build_stage2_bundle(pollutant: str) -> DataBundle:
    """Return a deterministic stage-two bundle with the right unit scale."""
    base = build_bundle(scenario="transport")
    if pollutant == "co":
        scale = 0.05
        unit = "mg/m3"
    else:
        scale = 1.0
        unit = "ug/m3"

    def scaled(rows):
        return tuple(
            row.model_copy(
                update={
                    "value": None if row.value is None else row.value * scale,
                    "unit": unit,
                }
            )
            for row in rows
        )

    return base.model_copy(
        update={
            "snapshot_id": f"fixture-stage2-{pollutant}-v1",
            "observations": scaled(base.observations),
            "neighbors": {key: scaled(rows) for key, rows in base.neighbors.items()},
        }
    )
