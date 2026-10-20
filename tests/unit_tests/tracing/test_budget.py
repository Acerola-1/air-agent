"""Run-budget exhaustion returns a validated partial result."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fake_model import ScriptedChatModel
from fixtures import build_bundle, make_request, make_scope
from langchain_core.messages import AIMessage, HumanMessage

from intelligent_tracing.graph import build_tracing_graph
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.report_store import InMemoryReportStore
from intelligent_tracing.sources import FixtureDataSource


@pytest.mark.anyio
async def test_budget_exhaustion_publishes_partial_validated_report():
    request = make_request()
    bundle = build_bundle()
    lead = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "站点",
                            "subagent_type": "station-data",
                        },
                        "id": "t1",
                    },
                    {
                        "name": "task",
                        "args": {
                            "description": "网格",
                            "subagent_type": "grid-data",
                        },
                        "id": "t2",
                    },
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "气象",
                            "subagent_type": "meteo-analyst",
                        },
                        "id": "t3",
                    }
                ],
            ),
            AIMessage(content="不应发布"),
        ]
    )
    meteo = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "MeteoNarrative",
                        "args": {
                            "summary": "不足",
                            "supplemental_checks": [],
                            "limitations_observed": [],
                        },
                        "id": "m1",
                    }
                ],
            )
        ]
    )
    policy = replace(
        DEFAULT_POLICY,
        budget=replace(DEFAULT_POLICY.budget, max_model_calls=1),
    )
    graph = build_tracing_graph(
        model=lead,
        meteo_model=meteo,
        reviewer_model=meteo,
        data_source=FixtureDataSource(bundle),
        station_catalog=bundle.stations,
        report_store=InMemoryReportStore(),
        policy=policy,
    )
    out = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="分析")],
            "request": request,
            "scope": make_scope(),
        },
        config={
            "configurable": {
                "thread_id": "thread-a",
                "execution_epoch": "epoch-1",
            }
        },
    )
    assert out.get("budget_exhausted") is True
    report = out["validated_report"]
    assert "budget_exhausted" in report.limitations
    assert "预算" in report.body_markdown
    assert "无法计算贡献率" in report.body_markdown


@pytest.mark.anyio
async def test_task_count_and_concurrency_are_hard_bounded():
    import asyncio
    from types import SimpleNamespace

    from langgraph.types import Command

    from intelligent_tracing.guards import TracingBudgetMiddleware
    from intelligent_tracing.policy import BudgetPolicy

    budget = BudgetPolicy(
        max_subagent_tasks=2,
        max_concurrency=1,
        total_timeout_seconds=10,
    )
    middleware = TracingBudgetMiddleware(budget)
    active = 0
    peak = 0

    async def handler(_request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.03)
        active -= 1
        return "ok"

    requests = [
        SimpleNamespace(tool_call={"name": "task", "id": f"t{index}"})
        for index in range(3)
    ]
    results = await asyncio.gather(
        *(middleware.awrap_tool_call(request, handler) for request in requests)
    )
    assert peak == 1
    assert sum(isinstance(result, Command) for result in results) == 1
    stopped = next(result for result in results if isinstance(result, Command))
    assert stopped.update["budget_exhausted"] is True


@pytest.mark.anyio
async def test_total_timeout_stops_before_next_call():
    from types import SimpleNamespace

    from intelligent_tracing.guards import TracingBudgetMiddleware
    from intelligent_tracing.policy import BudgetPolicy

    middleware = TracingBudgetMiddleware(
        BudgetPolicy(total_timeout_seconds=0, max_model_calls=10)
    )

    async def handler(_request):
        return "late"

    response = await middleware.awrap_model_call(SimpleNamespace(), handler)
    assert response.command.update["budget_exhausted"] is True


@pytest.mark.anyio
async def test_scoped_source_retries_transient_once_only(monkeypatch):
    from fixtures import make_request, make_scope

    from intelligent_tracing.policy import BudgetPolicy
    from intelligent_tracing.sources import ScopedMCPDataSource

    calls = 0

    async def flaky_executor(tool_id, args, config):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary timeout")
        return {"rows": []}

    source = ScopedMCPDataSource(
        tool_map={"station": "mcp:svc:station"},
        normalizer=lambda _kind, _products, _request: build_bundle(),
        executor=flaky_executor,
    )
    monkeypatch.setattr(
        source,
        "_run_config",
        lambda: {
            "configurable": {
                "capability_mcp_mode": "allowlist",
                "capability_tool_ids": ["mcp:svc:station"],
            }
        },
    )
    policy = replace(
        DEFAULT_POLICY,
        budget=BudgetPolicy(max_tool_retries=1),
    )
    await source.fetch(make_request(), scope=make_scope(), policy=policy)
    assert calls == 2

    calls = 0

    async def denied_executor(tool_id, args, config):
        nonlocal calls
        calls += 1
        raise PermissionError("403 forbidden")

    denied = ScopedMCPDataSource(
        tool_map={"station": "mcp:svc:station"},
        normalizer=lambda _kind, _products, _request: build_bundle(),
        executor=denied_executor,
    )
    monkeypatch.setattr(
        denied,
        "_run_config",
        lambda: {
            "configurable": {
                "capability_mcp_mode": "allowlist",
                "capability_tool_ids": ["mcp:svc:station"],
            }
        },
    )
    with pytest.raises(PermissionError):
        await denied.fetch(make_request(), scope=make_scope(), policy=policy)
    assert calls == 1


@pytest.mark.anyio
async def test_total_timeout_cancels_long_model_call():
    import asyncio
    import time
    from types import SimpleNamespace

    from intelligent_tracing.guards import TracingBudgetMiddleware
    from intelligent_tracing.policy import BudgetPolicy

    middleware = TracingBudgetMiddleware(
        BudgetPolicy(total_timeout_seconds=0, max_model_calls=10)
    )

    async def slow(_request):
        await asyncio.sleep(0.5)
        return "late"

    started = time.monotonic()
    response = await middleware.awrap_model_call(SimpleNamespace(), slow)
    assert response.command.update["budget_exhausted"] is True
    assert time.monotonic() - started < 0.3
