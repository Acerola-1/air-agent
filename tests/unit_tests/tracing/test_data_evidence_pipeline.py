"""Strict-window, layered-fact and coherent-space regression controls."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fixtures import build_bundle, make_request, make_scope
from test_scoped_mcp_source import RecordingExecutor, _catalog, _config

from common.identity.runtime import RuntimeIdentityRevoked
from common.mcp_authorization import McpAuthorizationError
from intelligent_tracing import data_access
from intelligent_tracing.claims import fallback_plan
from intelligent_tracing.data_access import (
    TRACING_PRODUCT_TOOLS,
    BusinessMCPDataSource,
    StaticStationCatalog,
)
from intelligent_tracing.data_windows import plan_station_windows
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.gate import ReportGate
from intelligent_tracing.orchestrator import analyze_bundle
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.runtime import TracingScopeError
from intelligent_tracing.schemas import ClaimType, GridCell, TimeWindow
from intelligent_tracing.spatial_analysis import _grid_contiguous


def run_bundle(bundle, request=None):
    request = request or make_request()
    registry = EvidenceRegistry()
    scope = make_scope()
    result = analyze_bundle(
        request, bundle, registry=registry, scope=scope, policy=DEFAULT_POLICY
    )
    checked = ReportGate(registry=registry, policy=DEFAULT_POLICY).validate_and_render(
        fallback_plan(result.allowlist), result.allowlist, request, scope
    )
    assert checked.ok, checked.errors
    assert checked.report is not None
    return result, checked.report.body_markdown, registry


def test_windows_merge_history_but_preserve_gap_and_context_budget():
    request = make_request()
    planned = plan_station_windows(request, DEFAULT_POLICY)
    assert planned.auxiliary == (request.baseline_window,)
    assert planned.context.end - planned.context.start == timedelta(hours=23)
    past = request.time_window.start - timedelta(days=20)
    separated = request.model_copy(
        update={"baseline_window": TimeWindow(start=past, end=past + timedelta(days=7))}
    )
    separate = plan_station_windows(separated, DEFAULT_POLICY)
    assert len(separate.auxiliary) == 2
    assert separate.auxiliary[0].end < separate.auxiliary[1].start
    for pollutant, hours in (("o3", 7), ("no2", 0)):
        plan = plan_station_windows(
            request.model_copy(update={"pollutant": pollutant}), DEFAULT_POLICY
        )
        assert (
            (plan.context.end - plan.context.start).total_seconds() / 3600 == hours
            if hours
            else plan.context is None
        )


class StrictWindowExecutor(RecordingExecutor):
    """Historical samples are returned only when explicitly queried."""

    def __init__(self, fail=None):
        super().__init__()
        self.fail = fail

    async def __call__(self, server, tool, args, config):
        if tool != TRACING_PRODUCT_TOOLS["station_series"]:
            return await super().__call__(server, tool, args, config)
        self.calls.append((server, tool, dict(args)))
        request = make_request()
        from datetime import datetime

        start = datetime.fromisoformat(args["sTime"]).replace(
            tzinfo=request.time_window.start.tzinfo
        )
        end = datetime.fromisoformat(args["eTime"]).replace(
            tzinfo=request.time_window.start.tzinfo
        )
        if start < request.time_window.start and self.fail is not None:
            if isinstance(self.fail, Exception):
                raise self.fail
            return self.fail
        rows = []
        for row in build_bundle().observations:
            if start <= row.timestamp < end:
                rows.append(
                    {
                        "stationKey": args["station"],
                        "updateTime": row.timestamp.isoformat(),
                        "pm25": row.value,
                    }
                )
        # Deliberate oversupply must not leak into the requested product.
        rows.append(
            {"stationKey": args["station"], "updateTime": end.isoformat(), "pm25": 999}
        )
        return {"success": True, "code": 200, "data": rows}


async def fetch(monkeypatch, executor, **kw):
    monkeypatch.setattr(data_access, "_run_config", lambda: _config())
    return await BusinessMCPDataSource(
        station_catalog=StaticStationCatalog(_catalog()), executor=executor, **kw
    ).fetch(make_request(), scope=make_scope(), policy=DEFAULT_POLICY)


@pytest.mark.anyio
async def test_realistic_strict_range_fetch_obtains_baseline_and_keeps_target_clean(
    monkeypatch,
):
    executor = StrictWindowExecutor()
    bundle = await fetch(monkeypatch, executor)
    assert len(bundle.observations) == 24
    assert len(bundle.baseline_observations) == 168
    assert len(bundle.context_observations) == 23
    target_calls = [
        c[2]
        for c in executor.calls
        if c[1] == TRACING_PRODUCT_TOOLS["station_series"] and c[2]["station"] == "S001"
    ]
    assert [(c["sTime"], c["eTime"]) for c in target_calls] == [
        ("2026-10-01 00:00:00", "2026-10-02 00:00:00"),
        ("2026-09-24 00:00:00", "2026-10-01 00:00:00"),
    ]
    analysis, body, _ = run_bundle(bundle)
    assert analysis.station.status == "anomaly"
    assert analysis.station.coverage.valid_hours == 24
    assert analysis.station.baseline_coverage.valid_hours == 168
    assert analysis.station.peak_value == 110
    assert "有效 168 / 应有 168" in body


@pytest.mark.anyio
async def test_optional_baseline_failure_preserves_target_facts(monkeypatch):
    bundle = await fetch(
        monkeypatch,
        StrictWindowExecutor(
            {"success": False, "code": 400, "msg": "historical product unavailable"}
        ),
    )
    assert bundle.baseline_observations == ()
    assert "station_history_unavailable" in bundle.missing
    result, text, _ = run_bundle(bundle)
    assert result.station.status == "insufficient"
    assert result.station.anomaly_segments == ()
    assert result.station.peak_value == 110
    assert "峰值 110.0" in text and "最低值 18.0" in text
    assert "有效样本首末变化率" in text
    assert "目标站未检出异常" not in text
    assert "无法判断" in text
    assert not any(
        c.claim_type in {ClaimType.NO_ANOMALY, ClaimType.DURATION}
        for c in result.allowlist.claims
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        {"success": False, "code": 401, "msg": "expired"},
        {"success": False, "code": 403, "msg": "denied"},
        RuntimeIdentityRevoked("IAM unavailable"),
        McpAuthorizationError("revoked"),
        TracingScopeError("scope changed"),
    ],
)
async def test_optional_history_auth_failure_stops_before_neighbors(
    monkeypatch, failure
):
    executor = StrictWindowExecutor(failure)
    with pytest.raises(
        (RuntimeIdentityRevoked, McpAuthorizationError, TracingScopeError)
    ):
        await fetch(monkeypatch, executor)
    assert len(executor.calls) == 2
    assert all(
        c[1] == TRACING_PRODUCT_TOOLS["station_series"] and c[2]["station"] == "S001"
        for c in executor.calls
    )


def test_explicit_empty_baseline_overrides_legacy_data_and_evaluation_uses_context_only():
    bundle = build_bundle()
    full, _, registry = run_bundle(bundle)
    absent, text, _ = run_bundle(
        bundle.model_copy(
            update={"baseline_observations": (), "context_observations": ()}
        )
    )
    assert full.station.status == "anomaly"
    assert absent.station.status == "insufficient"
    assert full.station.peak_value == absent.station.peak_value
    assert full.station.mean_value == absent.station.mean_value
    assert "目标站未检出异常" not in text
    assert len(full.station.evaluations) == 1
    assert full.station.evaluations[0].period_end > make_request().time_window.start
    records = registry.get_many(
        full.station.evidence_ids,
        scope=make_scope(),
        analysis_id=make_request().analysis_id,
        revision=1,
    )
    context = next(r for r in records if r.product == "station_context_series")
    evaluation = next(r for r in records if r.product == "evaluation_metric")
    assert context.evidence_id in evaluation.derived_from
    assert context.sample_count == 23


def test_no_anomaly_still_has_facts_and_insufficient_target_has_no_fake_peak():
    clear, text, _ = run_bundle(build_bundle(scenario="no_anomaly"))
    assert clear.station.status == "no_anomaly"
    assert "### 峰值" in text and "### 观测统计" in text
    sparse, text, _ = run_bundle(build_bundle(scenario="insufficient"))
    assert not sparse.station.coverage.sufficient
    assert "### 峰值" not in text
    assert "目标站未检出异常" not in text


def test_background_weather_is_not_transport_or_direction_claim():
    result, text, _ = run_bundle(build_bundle(scenario="no_anomaly"))
    assert any(
        c.claim_type == ClaimType.WEATHER_BACKGROUND for c in result.allowlist.claims
    )
    assert not any(
        c.claim_type == ClaimType.WIND_SECTOR for c in result.allowlist.claims
    )
    assert "### 气象背景" in text
    assert not any(
        c.claim_type == ClaimType.PROCESS_ASSESSMENT for c in result.allowlist.claims
    )


def test_grid_connectivity_requires_known_geometry_and_single_valid_time():
    t = make_request().time_window.start
    cells = tuple(
        GridCell(
            cell_id=str(i),
            row=0,
            col=i,
            latitude=34.75,
            longitude=113.62,
            value=100,
            observed_at=t,
            source_relation="independent",
        )
        for i in range(4)
    )
    assert _grid_contiguous(cells, DEFAULT_POLICY) == 4
    assert (
        _grid_contiguous(
            (cells[0].model_copy(update={"row": None}), *cells[1:]), DEFAULT_POLICY
        )
        == 0
    )
    shifted = tuple(
        cell.model_copy(update={"observed_at": t + timedelta(hours=i)})
        for i, cell in enumerate(cells)
    )
    assert _grid_contiguous(shifted, DEFAULT_POLICY) == 0


@pytest.mark.anyio
async def test_grid_source_keeps_latest_frame_without_inventing_topology(monkeypatch):
    class Executor(StrictWindowExecutor):
        async def __call__(self, server, tool, args, config):
            if tool == TRACING_PRODUCT_TOOLS["grid_series"]:
                self.calls.append((server, tool, dict(args)))
                return {
                    "data": [
                        {
                            "name": "A1",
                            "updateTime": "2026-10-01 01:00:00",
                            "value": 999,
                        },
                        {
                            "name": "A1",
                            "updateTime": "2026-10-01 02:00:00",
                            "value": 20,
                        },
                        {
                            "name": "A2",
                            "updateTime": "2026-10-01 02:00:00",
                            "value": 100,
                        },
                    ]
                }
            if tool == TRACING_PRODUCT_TOOLS["grid_geometry"]:
                return {
                    "data": [
                        {"name": name, "latitude": 34.76, "longitude": 113.63}
                        for name in ("A1", "A2")
                    ]
                }
            return await super().__call__(server, tool, args, config)

    bundle = await fetch(monkeypatch, Executor())
    assert [c.value for c in bundle.grid_cells] == [20, 100]
    assert len({c.observed_at for c in bundle.grid_cells}) == 1
    assert all(c.row is None and c.col is None for c in bundle.grid_cells)


def test_weather_provenance_known_unknown_and_contradictory():
    base = {"updateTime": "2026-10-01 02:00:00", "windSpeed": 2, "windDirection": 90}
    rows = data_access.BusinessMCPDataSource._normalize_weather(
        {
            "data": [
                base,
                {**base, "runTime": "2026-10-01 00:00:00", "leadHours": 2},
                {**base, "runTime": "2026-10-01 00:00:00", "leadHours": 3},
            ]
        }
    )
    assert rows[0].run_time is None and rows[0].lead_hours is None
    assert rows[1].run_time is not None and rows[1].lead_hours == 2
    assert rows[2].run_time is None and "nwp_time_inconsistent" in rows[2].qc_flags
    assert all(r.source_type == "nwp" for r in rows)


@pytest.mark.anyio
async def test_neighbor_selection_is_distance_sorted_bounded_and_radius_limited(
    monkeypatch,
):
    target, near = _catalog()
    far = near.model_copy(update={"station_code": "S003", "latitude": 34.82})
    outside = near.model_copy(update={"station_code": "S004", "latitude": 35.8})
    chosen = []
    monkeypatch.setattr(data_access, "_run_config", lambda: _config())
    for stations in ((target, far, outside, near), (near, outside, far, target)):
        executor = StrictWindowExecutor()
        bundle = await BusinessMCPDataSource(
            station_catalog=StaticStationCatalog(stations),
            executor=executor,
            max_neighbors=1,
        ).fetch(make_request(), scope=make_scope(), policy=DEFAULT_POLICY)
        neighbor_calls = [
            c[2]["station"]
            for c in executor.calls
            if c[1] == TRACING_PRODUCT_TOOLS["station_series"]
            and c[2]["station"] != "S001"
        ]
        assert neighbor_calls == ["S002"]
        assert "neighbors_truncated" in bundle.missing
        chosen.append(tuple(bundle.neighbors))
    assert chosen[0] == chosen[1]


def test_evaluation_uses_23_hour_context_but_never_its_date_as_output():
    request = make_request()
    fixture = build_bundle()
    target = tuple(
        row.model_copy(update={"value": 10})
        for row in fixture.observations
        if request.time_window.start <= row.timestamp < request.time_window.end
    )
    context = tuple(
        target[0].model_copy(
            update={
                "timestamp": request.time_window.start - timedelta(hours=i),
                "value": 100,
            }
        )
        for i in range(1, 24)
    )
    bundle = fixture.model_copy(
        update={
            "observations": target,
            "context_observations": context,
            "baseline_observations": (),
        }
    )
    analysis, _, _ = run_bundle(bundle)
    assert analysis.station.mean_value == 10
    assert len(analysis.station.evaluations) == 1
    summary = analysis.station.evaluations[0]
    assert summary.period_end == request.time_window.start + timedelta(hours=1)
    assert summary.valid_hours == 24
    assert summary.value == pytest.approx((23 * 100 + 10) / 24)


def test_weather_background_survives_missing_concentration_and_direction():
    fixture = build_bundle(scenario="no_anomaly")
    weather = tuple(
        r.model_copy(update={"wind_direction": None}) for r in fixture.weather
    )
    result, text, _ = run_bundle(
        fixture.model_copy(update={"observations": (), "weather": weather})
    )
    assert result.station.status == "insufficient"
    assert "### 气象背景" in text
    assert not any(
        c.claim_type == ClaimType.WIND_SECTOR for c in result.allowlist.claims
    )


def test_new_fact_claims_reject_cross_scope_evidence():
    request = make_request()
    result, _, registry = run_bundle(build_bundle())
    forged_scope = make_scope().model_copy(update={"tenant_id": "another-tenant"})
    checked = ReportGate(registry=registry, policy=DEFAULT_POLICY).validate_and_render(
        fallback_plan(result.allowlist), result.allowlist, request, forged_scope
    )
    assert not checked.ok
    assert any("evidence" in error or "scope" in error for error in checked.errors)


def test_legacy_bundle_schema_and_coverage_render_without_invented_baseline():
    from intelligent_tracing.claims import _render_claim_blocks
    from intelligent_tracing.schemas import DataBundle

    fixture = build_bundle()
    old = fixture.model_dump(
        mode="json", exclude={"baseline_observations", "context_observations"}
    )
    restored = DataBundle.model_validate(old)
    assert (
        restored.baseline_observations is None and restored.context_observations is None
    )
    result, _, _ = run_bundle(restored)
    coverage = next(
        c for c in result.allowlist.claims if c.claim_type == ClaimType.COVERAGE
    )
    parameters = {
        k: v for k, v in coverage.parameters.items() if not k.startswith("baseline_")
    }
    legacy = coverage.model_copy(update={"parameters": parameters})
    text = "".join(
        _render_claim_blocks(legacy, DEFAULT_POLICY.require_supported("pm2.5"))
    )
    assert "数据完整率 100.0%" in text
    assert "基线覆盖" not in text


@pytest.mark.anyio
async def test_http_and_protocol_auth_failures_cannot_be_downgraded(monkeypatch):
    import httpx

    from common.mcp_scoped import MCPResultText

    failures = [
        httpx.HTTPStatusError(
            "expired",
            request=httpx.Request("POST", "https://fixture.invalid"),
            response=httpx.Response(401),
        ),
        {"error": {"code": "unauthenticated", "message": "expired"}},
    ]
    for failure in failures:
        executor = StrictWindowExecutor(failure)
        with pytest.raises(TracingScopeError):
            await fetch(monkeypatch, executor)
        assert len(executor.calls) == 2
    with pytest.raises(TracingScopeError):
        data_access._parse_payload("test", MCPResultText('{"code":403}', is_error=True))


def test_model_plan_cannot_omit_verified_facts_leaving_only_limitations():
    request = make_request()
    result, _, registry = run_bundle(
        build_bundle().model_copy(update={"baseline_observations": ()})
    )
    plan = fallback_plan(result.allowlist)
    gate = ReportGate(registry=registry, policy=DEFAULT_POLICY)
    omitted = plan.model_copy(
        update={
            "claim_ids": tuple(
                c.claim_id
                for c in result.allowlist.claims
                if c.claim_type
                not in {
                    ClaimType.PEAK,
                    ClaimType.OBSERVATION_SUMMARY,
                    ClaimType.CHANGE_RATE,
                    ClaimType.WEATHER_BACKGROUND,
                }
            )
        }
    )
    refused = gate.validate_and_render(omitted, result.allowlist, request, make_scope())
    assert not refused.ok and "verified_facts_missing" in refused.errors
    accepted = gate.validate_and_render(plan, result.allowlist, request, make_scope())
    assert accepted.ok and "### 峰值" in accepted.report.body_markdown


def test_catalog_missing_coordinates_is_not_a_zero_distance_neighbor():
    rows = data_access.StationCatalogProvider._normalize(
        {
            "data": [
                {"stationKey": "bad", "latitude": None, "longitude": None},
                {"stationKey": "good", "latitude": 34.75, "longitude": 113.62},
            ]
        },
        "测试市",
    )
    assert [row.station_code for row in rows] == ["good"]


def test_auxiliary_merge_never_exceeds_per_query_31_day_bound():
    request = make_request()
    context_start = request.time_window.start - timedelta(hours=23)
    baseline = TimeWindow(start=context_start - timedelta(days=31), end=context_start)
    plan = plan_station_windows(
        request.model_copy(update={"baseline_window": baseline}), DEFAULT_POLICY
    )
    assert len(plan.auxiliary) == 2
    assert all(
        window.end - window.start <= timedelta(days=31) for window in plan.auxiliary
    )


@pytest.mark.anyio
async def test_grid_outside_radius_is_excluded_even_when_geometry_returns_extra(
    monkeypatch,
):
    class Executor(StrictWindowExecutor):
        async def __call__(self, server, tool, args, config):
            if tool == TRACING_PRODUCT_TOOLS["grid_geometry"]:
                return {
                    "data": [
                        {
                            "name": "A1",
                            "latitude": 40,
                            "longitude": 116,
                            "row": 0,
                            "col": 0,
                        }
                    ]
                }
            return await super().__call__(server, tool, args, config)

    bundle = await fetch(monkeypatch, Executor())
    assert bundle.grid_cells == ()
    assert "grid" in bundle.missing
