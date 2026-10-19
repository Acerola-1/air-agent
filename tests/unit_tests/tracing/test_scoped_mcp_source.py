"""Scoped MCP dependency intersection and zero-call exclusions."""

from __future__ import annotations

import pytest
from fixtures import build_bundle, make_request, make_scope

from intelligent_tracing.sources import DataSourceUnavailable, ScopedMCPDataSource


@pytest.mark.anyio
async def test_excluded_dependency_is_never_called(monkeypatch):
    calls: list[str] = []

    async def executor(tool_id, args, config):
        calls.append(tool_id)
        return {"rows": []}

    source = ScopedMCPDataSource(
        tool_map={
            "station": "mcp:svc:station_hourly",
            "weather": "mcp:svc:weather_hourly",
        },
        normalizer=lambda _kind, _products, _request: build_bundle(),
        executor=executor,
    )
    monkeypatch.setattr(
        source,
        "_run_config",
        lambda: {
            "configurable": {
                "capability_mcp_mode": "allowlist",
                "capability_tool_ids": ["mcp:svc:station_hourly"],
            }
        },
    )
    with pytest.raises(DataSourceUnavailable, match="dependency_excluded:weather"):
        await source.fetch(make_request(), scope=make_scope(), policy=None)  # type: ignore[arg-type]
    assert calls == []


@pytest.mark.anyio
async def test_allowed_dependencies_use_executor_once(monkeypatch):
    calls: list[str] = []

    async def executor(tool_id, args, config):
        calls.append(tool_id)
        return {"rows": []}

    source = ScopedMCPDataSource(
        tool_map={
            "station": "mcp:svc:station_hourly",
            "weather": "mcp:svc:weather_hourly",
        },
        normalizer=lambda _kind, _products, _request: build_bundle(),
        executor=executor,
    )
    monkeypatch.setattr(
        source,
        "_run_config",
        lambda: {
            "configurable": {
                "capability_mcp_mode": "allowlist",
                "capability_tool_ids": [
                    "mcp:svc:station_hourly",
                    "mcp:svc:weather_hourly",
                ],
            }
        },
    )
    bundle = await source.fetch(make_request(), scope=make_scope(), policy=None)  # type: ignore[arg-type]
    assert bundle.target_station_code == "S001"
    assert calls == ["mcp:svc:station_hourly", "mcp:svc:weather_hourly"]


def test_trajectory_tool_cannot_be_assembled():
    with pytest.raises(ValueError, match="轨迹/PSCF"):
        ScopedMCPDataSource(
            tool_map={"trajectory": "mcp:svc:trajectory"},
            normalizer=lambda _kind, _products, _request: build_bundle(),
        )


@pytest.mark.anyio
async def test_non_allowlist_mode_fails_closed_by_default(monkeypatch):
    calls: list[str] = []

    async def executor(tool_id, args, config):
        calls.append(tool_id)
        return {}

    source = ScopedMCPDataSource(
        tool_map={"station": "mcp:svc:station_hourly"},
        normalizer=lambda _kind, _products, _request: build_bundle(),
        executor=executor,
    )
    monkeypatch.setattr(
        source,
        "_run_config",
        lambda: {"configurable": {"capability_mcp_mode": "auto"}},
    )
    with pytest.raises(
        DataSourceUnavailable, match="capability_mcp_mode_not_allowlist"
    ):
        await source.fetch(make_request(), scope=make_scope(), policy=None)  # type: ignore[arg-type]
    assert calls == []


@pytest.mark.anyio
async def test_cached_source_deduplicates_concurrent_fetches():
    import asyncio

    from intelligent_tracing.sources import CachedDataSource

    calls = 0

    class SlowSource:
        async def fetch(self, request, *, scope, policy):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return build_bundle()

    cached = CachedDataSource(SlowSource())
    first, second = await asyncio.gather(
        cached.fetch(make_request(), scope=make_scope(), policy=None),  # type: ignore[arg-type]
        cached.fetch(make_request(), scope=make_scope(), policy=None),  # type: ignore[arg-type]
    )
    assert calls == 1
    assert first.snapshot_id == second.snapshot_id
