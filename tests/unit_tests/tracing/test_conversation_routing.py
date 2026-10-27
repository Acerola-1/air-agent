"""Intent, publication and actual dispatch controls, with positive controls."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conversation_fixtures import (
    CASES,
    analysis_prefix,
    analysis_question,
    plan_message,
    run_config,
)
from fake_model import ScriptedChatModel
from fixtures import allow_publication, build_bundle
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from intelligent_tracing.graph import build_tracing_graph
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.runtime import TracingScopeError, runtime_scope_from_config
from intelligent_tracing.schemas import DraftSlot, TracingTurnPlan
from intelligent_tracing.sources import FixtureDataSource
from intelligent_tracing.turn_routing import current_decision, turn_context


@pytest.fixture(autouse=True)
def isolate_identity(monkeypatch):
    import common.identity.runtime as identity

    monkeypatch.setattr(identity, "revalidate", AsyncMock())


class CountingSource(FixtureDataSource):
    def __init__(self):
        super().__init__(build_bundle())
        self.calls = 0

    async def fetch(self, *args, **kwargs):
        self.calls += 1
        return await super().fetch(*args, **kwargs)


def graph_for(responses, **kwargs):
    from intelligent_tracing.report_store import InMemoryReportStore

    source = CountingSource()
    model = ScriptedChatModel(responses=responses)
    graph = build_tracing_graph(
        model=model,
        data_source=source,
        station_catalog=kwargs.pop("station_catalog", None) or build_bundle().stations,
        report_store=InMemoryReportStore(),
        lifecycle_probe=allow_publication,
        **kwargs,
    )
    return graph, model, source


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
async def test_fixed_conversations_are_visible_without_analysis(case):
    graph, model, source = graph_for([plan_message(case["plan"])])
    out = await graph.ainvoke(
        {"messages": [HumanMessage(content=case["input"])]}, run_config()
    )
    decision = out["turn_decision"]
    assert decision.route == case["route"]
    visible = [m for m in out["messages"] if m.additional_kwargs.get("ui_visible")]
    assert len(visible) == 1
    if case.get("contains"):
        assert case["contains"] in visible[0].content
    if case.get("forbidden"):
        assert case["forbidden"] not in visible[0].content
    assert model.calls == 1
    assert source.calls == 0
    assert not out.get("validated_report")
    assert not out.get("child_executions")
    assert visible[0].additional_kwargs["delivery_mode"] == "single_shot"


def test_strict_plan_and_server_context_binding():
    with pytest.raises(ValidationError):
        TracingTurnPlan(intent="conversation", blocks=["greeting"], scope={})
    with pytest.raises(ValidationError):
        DraftSlot(field="tenant_id", value="other", source_text="other")
    config = run_config()
    state = {"messages": [HumanMessage(content="你好", id="m")]}
    context = turn_context(state, config)
    assert context.run_id == "run-test"
    from intelligent_tracing.schemas import TurnDecision

    state["turn_decision"] = TurnDecision(
        context=context, proposed_intent="conversation", route="conversation"
    )
    assert current_decision(state, config)
    assert current_decision(state, run_config(run="new")) is None
    assert current_decision(state, run_config(tenant="other")) is None
    state["messages"] = [HumanMessage(content="再见", id="m")]
    assert current_decision(state, config) is None
    del config["configurable"]["langgraph_auth_user"]
    with pytest.raises(TracingScopeError):
        runtime_scope_from_config(config)


@pytest.mark.anyio
async def test_raw_text_or_mixed_dispatch_cannot_bypass_plan():
    for message in [
        AIMessage(content="污染来自西北企业"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "submit_tracing_turn",
                    "args": {
                        "plan": {"intent": "conversation", "blocks": ["greeting"]}
                    },
                    "id": "p",
                },
                {"name": "freeze_tracing_request", "args": {}, "id": "f"},
            ],
        ),
    ]:
        graph, model, source = graph_for([message])
        out = await graph.ainvoke(
            {"messages": [HumanMessage(content="你好")]}, run_config()
        )
        assert out["turn_decision"].route == "fallback"
        assert source.calls == 0
        assert "西北企业" not in out["messages"][-1].content


@pytest.mark.anyio
async def test_complete_analysis_requires_actual_freeze_before_dispatch():
    graph, model, source = graph_for(analysis_prefix() + [AIMessage(content="结束")])
    out = await graph.ainvoke(
        {"messages": [HumanMessage(content=analysis_question())]}, run_config()
    )
    assert out["turn_decision"].route == "analysis"
    assert out["turn_decision"].frozen
    assert out["request"].target.station_code == "S001"
    assert out["request"].neighbor_radius_km == 10
    assert source.calls == 0


@pytest.mark.anyio
async def test_publication_lifecycle_denial_suppresses_reply():
    async def deny(config, scope):
        return False

    graph, model, source = graph_for(
        [plan_message({"intent": "conversation", "blocks": ["greeting"]})]
    )
    # Exercise the same publication path with a runtime lifecycle refusal.
    from intelligent_tracing.evidence import EvidenceRegistry
    from intelligent_tracing.gate import ReportGate
    from intelligent_tracing.publishing import TracingPublishMiddleware
    from intelligent_tracing.report_store import InMemoryReportStore

    output = await graph.ainvoke(
        {"messages": [HumanMessage(content="你好")]}, run_config()
    )
    registry = EvidenceRegistry()
    middleware = TracingPublishMiddleware(
        registry=registry,
        gate=ReportGate(registry=registry, policy=DEFAULT_POLICY),
        policy=DEFAULT_POLICY,
        report_store=InMemoryReportStore(),
        lifecycle_probe=deny,
        require_turn_decision=True,
    )
    assert (
        await middleware.aafter_agent(output, SimpleNamespace(config=run_config()))
        == {}
    )


@pytest.mark.parametrize(
    "missing",
    ["langgraph_auth_user", "ledger_business_code", "ledger_attempt_id", "run_id"],
)
def test_missing_trusted_context_fails_closed(missing):
    cfg = run_config()
    del cfg["configurable"][missing]
    with pytest.raises(TracingScopeError):
        turn_context({"messages": [HumanMessage(content="你好")]}, cfg)


@pytest.mark.anyio
async def test_actual_dispatch_refuses_every_tool_without_turn_permission():
    from intelligent_tracing.conversation import ANALYSIS_TOOLS
    from intelligent_tracing.turn_routing import TracingTurnMiddleware

    middleware = TracingTurnMiddleware(DEFAULT_POLICY, ANALYSIS_TOOLS)
    handler = AsyncMock()
    for name in ANALYSIS_TOOLS:
        request = SimpleNamespace(
            state={"messages": [HumanMessage(content="你好")]},
            runtime=SimpleNamespace(config=run_config()),
            tool_call={"name": name, "args": {}, "id": "bad"},
        )
        result = await middleware.awrap_tool_call(request, handler)
        assert result.status == "error"
    handler.assert_not_called()


def test_draft_validation_conflicts_provenance_and_scope():
    from conversation_fixtures import analysis_conditions

    from intelligent_tracing.schemas import TracingRequestDraft
    from intelligent_tracing.turn_routing import draft_issues, merge_draft

    state = {"messages": [HumanMessage(content="半径二十公里", id="now")]}
    ctx = turn_context(state, run_config())
    old = TracingRequestDraft(
        scope=ctx.scope, values=analysis_conditions(), sources={"start": "previous"}
    )
    plan = TracingTurnPlan(
        intent="analysis",
        inherit_previous=True,
        slots=[
            DraftSlot(field="neighbor_radius_km", value="20", source_text="二十公里")
        ],
    )
    merged = merge_draft(plan, ctx, state["messages"][0].content, old)
    assert merged.values["neighbor_radius_km"] == "20"
    assert merged.sources["neighbor_radius_km"] == "now"
    assert not any(draft_issues(merged, DEFAULT_POLICY))
    other = old.model_copy(
        update={"scope": old.scope.model_copy(update={"context_version": "2"})}
    )
    isolated = merge_draft(plan, ctx, state["messages"][0].content, other)
    assert "target" in draft_issues(isolated, DEFAULT_POLICY)[0]
    with pytest.raises(ValueError):
        merge_draft(plan, ctx, "没有该条件", old)
    invalid = old.model_copy(
        update={
            "values": {
                **old.values,
                "neighbor_radius_km": "100",
                "end": old.values["start"],
                "baseline_end": "",
            }
        }
    )
    missing, errors = draft_issues(invalid, DEFAULT_POLICY)
    assert "neighbor_radius_km" in errors and "time_window" in errors
    assert "baseline_end" in missing
    reset = merge_draft(
        TracingTurnPlan(
            intent="analysis", inherit_previous=True, reset_fields=["start"]
        ),
        ctx,
        "不沿用开始时间",
        old,
    )
    assert "start" not in reset.values


@pytest.mark.anyio
async def test_persistent_conversation_clarification_report_and_followup(tmp_path):
    from conversation_fixtures import analysis_conditions
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from test_subagents_deepagents import _lead_model, _meteo_model, _reviewer_model

    conditions = analysis_conditions()
    first = {k: v for k, v in conditions.items() if k != "neighbor_radius_km"}
    first_plan = {
        "intent": "analysis",
        "slots": [{"field": k, "value": v, "source_text": v} for k, v in first.items()],
    }
    supplement = {
        "intent": "analysis",
        "inherit_previous": True,
        "slots": [
            {"field": "neighbor_radius_km", "value": "10", "source_text": "十公里"}
        ],
    }
    change = {
        "intent": "analysis",
        "inherit_previous": True,
        "slots": [
            {"field": "neighbor_radius_km", "value": "20", "source_text": "二十公里"}
        ],
    }
    analysis_steps = _lead_model().responses
    responses = [
        plan_message(first_plan),
        plan_message(supplement),
        analysis_prefix()[1],
        *analysis_steps,
        plan_message({"intent": "conversation", "blocks": ["overview", "boundary"]}),
        plan_message(change),
        analysis_prefix()[1],
        *analysis_steps,
    ]
    path = str(tmp_path / "owned-conversation.sqlite")
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        graph, model, source = graph_for(
            responses,
            checkpointer=saver,
            meteo_model=_meteo_model(),
            reviewer_model=_reviewer_model("ignored-advice-id"),
        )

        async def ask(text, run):
            events = []
            out = None
            async for mode, chunk in graph.astream(
                {"messages": [HumanMessage(content=text)]},
                run_config(run=run),
                stream_mode=["custom", "values"],
            ):
                if mode == "custom":
                    events.append(chunk)
                else:
                    out = chunk
            return out, events

        first_out, events = await ask("请分析 " + " ".join(first.values()), "one")
        assert first_out["turn_decision"].missing == ("neighbor_radius_km",)
        assert "站点代码或名称" not in events[-1]["message"]
        assert source.calls == 0
        second, events = await ask("半径十公里", "two")
        assert second["turn_decision"].frozen
        assert second.get("validated_report"), [
            (getattr(m, "name", None), m.content) for m in second["messages"][-5:]
        ]
        assert second["validated_report"].published
        report_id = second["validated_report"].report_id
        assert len([e for e in events if e.get("type") == "final_output_done"]) == 1
        third, events = await ask("你还能做什么", "three")
        assert third["turn_decision"].route == "conversation"
        assert third["validated_report"].report_id == report_id
        assert "validated_report_ref" not in third["messages"][-1].additional_kwargs
        assert source.calls == 1
        assert events[-1]["message"] == third["messages"][-1].content
        assert third["turn_publication"].content_sha256 == events[-1]["content_sha256"]
        fourth, events = await ask("把刚才半径改为二十公里", "four")
        assert fourth["request"].revision == 2
        assert fourth["request"].analysis_id == second["request"].analysis_id
        assert fourth["request"].neighbor_radius_km == 20
        assert fourth["validated_report"].report_id != report_id
        assert source.calls == 2
        snapshot = await graph.aget_state(run_config(run="four"))
        assert snapshot.values["messages"][-1].content == events[-1]["message"]


@pytest.mark.anyio
async def test_new_attempt_cannot_reuse_previous_conversation_decision(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(
        str(tmp_path / "owned-reopen.sqlite")
    ) as saver:
        graph, model, source = graph_for(
            [
                plan_message({"intent": "conversation", "blocks": ["greeting"]}),
                plan_message({"intent": "conversation", "blocks": ["boundary"]}),
            ],
            checkpointer=saver,
        )
        one = await graph.ainvoke(
            {"messages": [HumanMessage(content="你好")]}, run_config(run="one")
        )
        two = await graph.ainvoke(
            {"messages": [HumanMessage(content="你会做轨迹吗")]}, run_config(run="two")
        )
        assert one["turn_context"].key != two["turn_context"].key
        assert one["turn_publication"].message_id != two["turn_publication"].message_id
        assert model.calls == 2 and source.calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["ambiguous", "unavailable", "incomplete"])
async def test_catalog_resolution_is_distinct_from_analysis_fetch(kind):
    from conversation_fixtures import analysis_conditions

    from intelligent_tracing.sources import DataSourceUnavailable

    class Catalog:
        calls = 0

        async def list_stations(self, **kwargs):
            self.calls += 1
            if kind == "unavailable":
                raise DataSourceUnavailable("controlled dependency down")
            station = build_bundle().stations[0]
            return (station, station.model_copy(update={"station_code": "S002"}))

    catalog = Catalog()
    conditions = analysis_conditions()
    conditions.pop("station_code")
    conditions["station_name"] = "目标站"
    if kind == "incomplete":
        conditions.pop("neighbor_radius_km")
    proposal = {
        "intent": "analysis",
        "slots": [
            {"field": k, "value": v, "source_text": v} for k, v in conditions.items()
        ],
    }
    graph, model, source = graph_for(
        [plan_message(proposal), analysis_prefix()[1]], station_catalog=catalog
    )
    out = await graph.ainvoke(
        {"messages": [HumanMessage(content="请分析 " + " ".join(conditions.values()))]},
        run_config(),
    )
    assert source.calls == 0
    assert catalog.calls == (0 if kind == "incomplete" else 1)
    assert out["turn_decision"].route == "clarify"
    if kind != "incomplete":
        assert out["turn_decision"].reason == (
            "station_ambiguous" if kind == "ambiguous" else "station_unavailable"
        )
    if kind == "ambiguous":
        assert out["turn_decision"].candidates == ("S001", "S002")
        assert "S002" in out["messages"][-1].content


def test_catalog_only_describes_enabled_configuration():
    from dataclasses import replace

    from intelligent_tracing.conversation import ANALYSIS_TOOLS, capability_catalog

    policy = replace(DEFAULT_POLICY, enabled_pollutants=frozenset({"pm2.5"}))
    catalog = capability_catalog(policy, ANALYSIS_TOOLS)
    assert (
        "PM2.5" in catalog.entries["pollutants"]
        and "PM10" not in catalog.entries["pollutants"]
    )
    missing = capability_catalog(policy, frozenset())
    assert "overview" not in missing.entries and "canvas" not in missing.entries
    assert missing.version != catalog.version
    assert "不提供轨迹" in missing.entries["boundary"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "reason",
    ["valid", "revoked", "unavailable", "deleted", "cancelled", "failed", "mismatch"],
)
async def test_live_publication_probe_covers_conversation_lifecycle(
    monkeypatch, reason
):
    import intelligent_tracing.publishing as publishing
    from intelligent_tracing.evidence import EvidenceRegistry
    from intelligent_tracing.gate import ReportGate
    from intelligent_tracing.report_store import InMemoryReportStore

    graph, _, _ = graph_for(
        [plan_message({"intent": "conversation", "blocks": ["greeting"]})]
    )
    state = await graph.ainvoke(
        {"messages": [HumanMessage(content="你好")]}, run_config()
    )
    state["turn_publication"] = None
    monkeypatch.setattr(
        publishing,
        "guard_runtime_call",
        AsyncMock(
            side_effect=RuntimeError("controlled")
            if reason in {"revoked", "unavailable"}
            else None
        ),
    )
    monkeypatch.setattr(
        publishing,
        "require_ledger_context",
        lambda cfg: (object(), "request", "attempt"),
    )
    monkeypatch.setattr(
        publishing,
        "thread_deletion_exists",
        AsyncMock(return_value=reason == "deleted"),
    )
    row = {
        "request_id": "wrong" if reason == "mismatch" else "request",
        "state": "failed" if reason == "failed" else "running",
        "cancel_confirmed": reason == "cancelled",
    }
    monkeypatch.setattr(
        publishing, "fetch_attempt_lifecycle", AsyncMock(return_value=row)
    )
    registry = EvidenceRegistry()
    middleware = publishing.TracingPublishMiddleware(
        registry=registry,
        gate=ReportGate(registry=registry, policy=DEFAULT_POLICY),
        policy=DEFAULT_POLICY,
        report_store=InMemoryReportStore(),
        require_turn_decision=True,
    )
    events = []
    monkeypatch.setattr(middleware, "_stream_writer", lambda: events.append)
    result = await middleware.aafter_agent(state, SimpleNamespace(config=run_config()))
    if reason == "valid":
        assert events[-1]["type"] == "final_output_done"
        assert result["messages"][0].content == events[-1]["message"]
        saved = {
            **state,
            **result,
            "messages": [*state["messages"], *result["messages"]],
        }
        assert (
            await middleware.aafter_agent(saved, SimpleNamespace(config=run_config()))
        )["messages"][0].id == result["messages"][0].id
        assert len([e for e in events if e["type"] == "final_output_done"]) == 1
        rebuilt = publishing.TracingPublishMiddleware(
            registry=registry,
            gate=ReportGate(registry=registry, policy=DEFAULT_POLICY),
            policy=DEFAULT_POLICY,
            report_store=InMemoryReportStore(),
            require_turn_decision=True,
        )
        monkeypatch.setattr(rebuilt, "_stream_writer", lambda: events.append)
        await rebuilt.aafter_agent(saved, SimpleNamespace(config=run_config()))
        assert len([e for e in events if e["type"] == "final_output_done"]) == 1
    else:
        assert result == {} and not events


@pytest.mark.parametrize(
    "quote,value",
    [("四公里", "4"), ("十六公里", "16"), ("四十九公里", "49"), ("五十公里", "50")],
)
def test_chinese_radius_and_full_timestamp_provenance(quote, value):
    from intelligent_tracing.turn_routing import _quoted_value

    assert _quoted_value("neighbor_radius_km", value, quote)
    assert not _quoted_value("neighbor_radius_km", "1", "十一公里")
    window = "2026-10-16 00:00 至 2026-10-17 00:00"
    assert _quoted_value("end", "2026-10-17T00:00:00+08:00", window)
    assert not _quoted_value("end", "2026-10-17T00:01:00+08:00", window)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "message",
    [
        AIMessage(content=""),
        plan_message({"intent": "unknown"}),
        plan_message({"intent": "analysis", "body": "未受信正文"}),
    ],
)
async def test_invalid_plan_has_one_bounded_fallback(message):
    graph, model, source = graph_for([message])
    out = await graph.ainvoke(
        {"messages": [HumanMessage(content="你好")]}, run_config()
    )
    assert out["turn_decision"].route == "fallback"
    assert model.calls == 1 and source.calls == 0
    assert "未受信正文" not in out["messages"][-1].content
    assert not out.get("child_executions")


@pytest.mark.anyio
async def test_budget_before_plan_does_not_query_or_publish_model_body():
    from dataclasses import replace

    policy = replace(
        DEFAULT_POLICY, budget=replace(DEFAULT_POLICY.budget, max_model_calls=0)
    )
    graph, model, source = graph_for([AIMessage(content="模型草稿")], policy=policy)
    out = await graph.ainvoke(
        {"messages": [HumanMessage(content="你好")]}, run_config()
    )
    assert model.calls == 0 and source.calls == 0
    assert out["budget_exhausted"]
    visible = [m for m in out["messages"] if m.additional_kwargs.get("ui_visible")]
    assert len(visible) == 1
    assert visible[0].additional_kwargs["control_code"] == "tracing_fallback"
    assert "模型草稿" not in visible[0].content


@pytest.mark.anyio
async def test_parallel_observation_separates_decision_and_publication_without_body(
    caplog,
):
    import asyncio
    import logging

    caplog.set_level(logging.INFO, logger="intelligent_tracing.turn_routing")
    caplog.set_level(logging.INFO, logger="intelligent_tracing.publishing")

    async def invoke(run):
        graph, _, _ = graph_for(
            [plan_message({"intent": "conversation", "blocks": ["greeting"]})]
        )
        return await graph.ainvoke(
            {"messages": [HumanMessage(content="private-input-" + run)]},
            run_config(run=run, thread=run),
        )

    results = await asyncio.gather(invoke("observation-a"), invoke("observation-b"))
    logs = [
        r.getMessage()
        for r in caplog.records
        if "tracing turn " in r.getMessage() or "tracing publication " in r.getMessage()
    ]
    assert len(logs) == 4
    for run, out in zip(("observation-a", "observation-b"), results, strict=True):
        decision = [
            line for line in logs if "tracing turn " in line and "run=" + run in line
        ]
        published = [
            line
            for line in logs
            if "tracing publication " in line and "run=" + run in line
        ]
        assert len(decision) == len(published) == 1
        assert "selected=conversation" in decision[0]
        assert out["turn_publication"].output_id in published[0]
        assert "private-input-" not in " ".join(logs)


@pytest.mark.anyio
async def test_catalog_drift_records_actual_fallback_publication(monkeypatch):
    from dataclasses import replace

    from intelligent_tracing.evidence import EvidenceRegistry
    from intelligent_tracing.gate import ReportGate
    from intelligent_tracing.publishing import TracingPublishMiddleware
    from intelligent_tracing.report_store import InMemoryReportStore

    graph, _, _ = graph_for(
        [plan_message({"intent": "conversation", "blocks": ["greeting"]})]
    )
    state = await graph.ainvoke(
        {"messages": [HumanMessage(content="你好")]}, run_config()
    )
    state["turn_publication"] = None
    registry = EvidenceRegistry()
    middleware = TracingPublishMiddleware(
        registry=registry,
        gate=ReportGate(registry=registry, policy=DEFAULT_POLICY),
        policy=DEFAULT_POLICY,
        report_store=InMemoryReportStore(),
        lifecycle_probe=allow_publication,
        require_turn_decision=True,
    )
    middleware._catalog = replace(middleware._catalog, version="changed")
    events = []
    monkeypatch.setattr(middleware, "_stream_writer", lambda: events.append)
    result = await middleware.aafter_agent(state, SimpleNamespace(config=run_config()))
    assert result["turn_decision"].route == "fallback"
    assert result["turn_decision"].reason == "catalog_changed"
    assert result["messages"][0].additional_kwargs["control_code"] == "tracing_fallback"
    assert events[-1]["type"] == "final_output_done"
