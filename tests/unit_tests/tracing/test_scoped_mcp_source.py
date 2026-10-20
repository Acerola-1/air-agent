"""Business MCP data source: product mapping, scope binding and zero-call fail-closed."""

from __future__ import annotations

import asyncio
import json

import pytest
from fixtures import (
    BUSINESS_CODE,
    BUSINESS_PERMISSION,
    BUSINESS_POLICY_VERSION,
    build_bundle,
    make_request,
    make_scope,
)

from intelligent_tracing import data_access
from intelligent_tracing.data_access import (
    TRACING_PRODUCT_TOOLS,
    BusinessMCPDataSource,
    StaticStationCatalog,
)
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.runtime import TracingScopeError
from intelligent_tracing.schemas import StationMeta
from intelligent_tracing.sources import CachedDataSource, DataSourceUnavailable


def _config(**extra: object) -> dict:
    configurable = {
        "thread_id": "thread-a",
        "ledger_business_code": BUSINESS_CODE,
        "ledger_business_policy_version": BUSINESS_POLICY_VERSION,
        "ledger_business_permission": BUSINESS_PERMISSION,
        "ledger_graph_id": "intelligent-tracing",
        "langgraph_auth_user": {
            "identity": "user-a",
            "tenant_id": "tenant-a",
            "membership_id": "membership-a",
            "session_id": "session-a",
            "session_ctx_version": 1,
        },
    }
    configurable.update(extra)
    return {"configurable": configurable}


def _catalog() -> tuple[StationMeta, ...]:
    return (
        StationMeta(
            station_code="S001",
            name="目标站",
            region_code="410100",
            region_name="测试市",
            latitude=34.75,
            longitude=113.62,
            distance_km=0,
        ),
        StationMeta(
            station_code="S002",
            name="邻站",
            region_code="410100",
            region_name="测试市",
            latitude=34.78,
            longitude=113.65,
            distance_km=4,
        ),
    )


def _station_payload(code: str = "S001") -> dict:
    return {
        "success": True,
        "code": 200,
        "data": [
            {
                "stationKey": code,
                "updateTime": "2026-01-01 00:00:00",
                "pm25": "35.0",
            },
            {
                "stationKey": code,
                "updateTime": "2026-01-01 01:00:00",
                "pm25": "-",
            },
        ],
    }


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def __call__(self, server_key, tool_id, args, config):
        self.calls.append((server_key, tool_id, dict(args)))
        if tool_id == TRACING_PRODUCT_TOOLS["station_catalog"]:
            return {
                "success": True,
                "code": 200,
                "data": [
                    {
                        "stationKey": "S001",
                        "name": "目标站",
                        "regionKey": "410100",
                        "regionName": "测试市",
                        "latitude": "34.75",
                        "longitude": "113.62",
                        "distance": "0",
                    },
                    {
                        "stationKey": "S002",
                        "name": "邻站",
                        "regionKey": "410100",
                        "regionName": "测试市",
                        "latitude": "34.78",
                        "longitude": "113.65",
                        "distance": "4000",
                    },
                ],
            }
        if tool_id == TRACING_PRODUCT_TOOLS["station_series"]:
            return _station_payload(str(args.get("station")))
        if tool_id == TRACING_PRODUCT_TOOLS["nwp"]:
            return {
                "success": True,
                "code": 200,
                "data": [
                    [
                        {
                            "updateTime": "2026-01-01 00:00:00",
                            "windSpeed": "2.0",
                            "windDirection": "90",
                            "pblh": "-1",
                            "humidity": "50",
                            "pressure": "1000",
                            "temperature": "5",
                        }
                    ]
                ],
            }
        if tool_id == TRACING_PRODUCT_TOOLS["grid_series"]:
            return {
                "success": True,
                "code": 200,
                "data": [
                    {
                        "name": "A1",
                        "updateTime": "2026-01-01 00:00:00",
                        "value": "50",
                    }
                ],
            }
        if tool_id == TRACING_PRODUCT_TOOLS["grid_geometry"]:
            return {
                "success": True,
                "code": 200,
                "data": [
                    {
                        "name": "A1",
                        "latitude": "34.76",
                        "longitude": "113.63",
                    }
                ],
            }
        raise AssertionError(tool_id)


def test_product_mapping_has_no_trajectory_or_pscf():
    for tool_id in TRACING_PRODUCT_TOOLS.values():
        assert "trajectory" not in tool_id
        assert "pscf" not in tool_id


@pytest.mark.anyio
async def test_missing_business_snapshot_makes_zero_mcp_calls(monkeypatch):
    executor = RecordingExecutor()
    source = BusinessMCPDataSource(
        station_catalog=StaticStationCatalog(_catalog()), executor=executor
    )
    monkeypatch.setattr(
        data_access,
        "_run_config",
        lambda: _config(**{"ledger_business_code": ""}),
    )
    with pytest.raises(TracingScopeError, match="业务快照"):
        await source.fetch(make_request(), scope=make_scope(), policy=DEFAULT_POLICY)
    assert executor.calls == []


@pytest.mark.anyio
async def test_business_mismatch_fails_closed_before_calls(monkeypatch):
    executor = RecordingExecutor()
    source = BusinessMCPDataSource(
        station_catalog=StaticStationCatalog(_catalog()), executor=executor
    )
    monkeypatch.setattr(data_access, "_run_config", lambda: _config())
    scope = make_scope().model_copy(
        update={"business_permission": "agent:business:basic-qa:use"}
    )
    with pytest.raises(TracingScopeError, match="不一致"):
        await source.fetch(make_request(), scope=scope, policy=DEFAULT_POLICY)
    assert executor.calls == []


@pytest.mark.anyio
async def test_products_use_explicit_args_not_universal_params(monkeypatch):
    executor = RecordingExecutor()
    source = BusinessMCPDataSource(
        station_catalog=StaticStationCatalog(_catalog()), executor=executor
    )
    monkeypatch.setattr(data_access, "_run_config", lambda: _config())
    bundle = await source.fetch(
        make_request(), scope=make_scope(), policy=DEFAULT_POLICY
    )
    by_tool = {tool_id: args for _, tool_id, args in executor.calls}
    assert set(by_tool) == set(TRACING_PRODUCT_TOOLS.values()) - {
        TRACING_PRODUCT_TOOLS["station_catalog"]
    }
    for args in by_tool.values():
        assert "radius_km" not in args
    station_calls = [
        args
        for _, tool_id, args in executor.calls
        if tool_id == TRACING_PRODUCT_TOOLS["station_series"]
    ]
    assert {args["station"] for args in station_calls} == {"S001", "S002"}
    for station_args in station_calls:
        assert station_args["region"] == "测试市"
        assert station_args["type"] == "hourly"
        assert "index" not in station_args
    grid_args = by_tool[TRACING_PRODUCT_TOOLS["grid_series"]]
    assert grid_args["index"] == "pm25"
    assert "station" not in grid_args
    weather_args = by_tool[TRACING_PRODUCT_TOOLS["nwp"]]
    assert set(weather_args) == {"region", "sTime", "eTime"}
    assert bundle.source_type == "mcp"
    assert bundle.observations[0].value == 35.0
    assert bundle.observations[1].value is None
    assert bundle.nwp[0].pblh == -1.0
    assert bundle.grid_cells[0].cell_id == "A1"
    assert bundle.missing == ()
    assert set(bundle.neighbors) == {"S002"}
    assert (
        bundle.source_tools["station_series"]
        == (TRACING_PRODUCT_TOOLS["station_series"])
    )


@pytest.mark.anyio
async def test_cached_source_key_separates_business_code(monkeypatch):
    calls = 0

    class CountingSource:
        async def fetch(self, request, *, scope, policy):
            nonlocal calls
            calls += 1
            return build_bundle()

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(CachedDataSource, "_authorize", staticmethod(_noop))
    cached = CachedDataSource(CountingSource())
    request = make_request()
    scope_a = make_scope()
    scope_b = scope_a.model_copy(update={"business_code": "other-business"})
    await cached.fetch(request, scope=scope_a, policy=DEFAULT_POLICY)
    await cached.fetch(request, scope=scope_b, policy=DEFAULT_POLICY)
    assert calls == 2


@pytest.mark.anyio
async def test_cached_source_deduplicates_concurrent_fetches(monkeypatch):
    calls = 0

    class SlowSource:
        async def fetch(self, request, *, scope, policy):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return build_bundle()

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(CachedDataSource, "_authorize", staticmethod(_noop))
    cached = CachedDataSource(SlowSource())
    first, second = await asyncio.gather(
        cached.fetch(make_request(), scope=make_scope(), policy=DEFAULT_POLICY),
        cached.fetch(make_request(), scope=make_scope(), policy=DEFAULT_POLICY),
    )
    assert calls == 1
    assert first.snapshot_id == second.snapshot_id


@pytest.mark.anyio
async def test_station_catalog_uses_authorized_executor_and_caches(monkeypatch):
    executor = RecordingExecutor()
    provider = data_access.StationCatalogProvider(executor=executor)
    monkeypatch.setattr(
        data_access,
        "_require_business_config",
        lambda config: data_access.business_binding_from_config(config),
    )
    stations = await provider.list_stations(config=_config(), region_name="测试市")
    assert {item.station_code for item in stations} == {"S001", "S002"}
    again = await provider.list_stations(config=_config(), region_name="测试市")
    assert again == stations
    assert len(executor.calls) == 1
    assert executor.calls[0][2] == {"region": "测试市", "stationType": "S-10"}


def test_catalog_unavailable_error_is_explicit():
    with pytest.raises(DataSourceUnavailable):
        raise DataSourceUnavailable("station_catalog_unavailable")


def test_payload_parse_rejects_business_failure():
    from intelligent_tracing.data_access import MCPToolError, _parse_payload

    with pytest.raises(MCPToolError) as excinfo:
        _parse_payload(
            "t", json.dumps({"success": False, "code": 400, "msg": "参数错误"})
        )
    assert excinfo.value.transient is False


@pytest.mark.anyio
async def test_target_series_failure_is_explicit_not_empty_success(monkeypatch):
    class FailingExecutor(RecordingExecutor):
        async def __call__(self, server_key, tool_id, args, config):
            if tool_id == TRACING_PRODUCT_TOOLS["station_series"]:
                self.calls.append((server_key, tool_id, dict(args)))
                return {"success": False, "code": 400, "msg": "参数错误"}
            return await super().__call__(server_key, tool_id, args, config)

    executor = FailingExecutor()
    source = BusinessMCPDataSource(
        station_catalog=StaticStationCatalog(_catalog()), executor=executor
    )
    monkeypatch.setattr(data_access, "_run_config", lambda: _config())
    with pytest.raises(DataSourceUnavailable, match="station_series_unavailable"):
        await source.fetch(make_request(), scope=make_scope(), policy=DEFAULT_POLICY)
    # 参数/权限错误不重试：目标站序列只调用一次。
    series_calls = [
        call
        for call in executor.calls
        if call[1] == TRACING_PRODUCT_TOOLS["station_series"]
    ]
    assert len(series_calls) == 1
