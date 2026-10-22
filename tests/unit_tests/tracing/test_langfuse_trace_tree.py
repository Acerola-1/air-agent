"""Real SDK export assertions for native DeepAgents trace propagation."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from fixtures import (
    BUSINESS_CODE,
    BUSINESS_PERMISSION,
    BUSINESS_POLICY_VERSION,
    build_bundle,
    make_request,
)
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables.config import var_child_runnable_config
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_subagents_deepagents import _graph, _lead_model

import common.event_ledger.lifecycle as lifecycle
import common.event_ledger.store as ledger_store
import common.identity.runtime as identity_runtime
from common.observability.context import current_observation_config
from intelligent_tracing import graph as graph_module
from intelligent_tracing.sources import FixtureDataSource


def _uuid():
    return str(uuid.uuid4())


def run_config(*, attempt=None, thread=None, tenant=None):
    return {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": thread or _uuid(),
            "execution_epoch": _uuid(),
            "ledger_request_id": _uuid(),
            "ledger_attempt_id": attempt or _uuid(),
            "ledger_submission_id": _uuid(),
            "ledger_graph_id": "intelligent-tracing",
            "ledger_business_code": BUSINESS_CODE,
            "ledger_business_permission": BUSINESS_PERMISSION,
            "ledger_business_policy_version": BUSINESS_POLICY_VERSION,
            "capability_selection_id": "sel-001",
            "langgraph_auth_user": {
                "identity": _uuid(),
                "tenant_id": tenant or _uuid(),
                "membership_id": _uuid(),
                "session_id": _uuid(),
                "session_ctx_version": 1,
            },
        }
    }


@pytest.fixture
def sdk(monkeypatch):
    key = f"pk-lf-fixture-{uuid.uuid4().hex}"
    for k, v in {
        "LANGFUSE_ENABLED": "true",
        "LANGFUSE_PUBLIC_KEY": key,
        "LANGFUSE_SECRET_KEY": "sk-lf-fixture",
        "LANGFUSE_BASE_URL": "http://127.0.0.1:1",
        "AGENT_OBSERVABILITY_ENABLED": "false",
        "LANGCHAIN_TRACING_V2": "false",
    }.items():
        monkeypatch.setenv(k, v)
    exporter = InMemorySpanExporter()
    client = Langfuse(tracer_provider=TracerProvider(), span_exporter=exporter)
    # These are controlled dependency boundaries, never the production database.
    monkeypatch.setattr(identity_runtime, "revalidate", AsyncMock())
    monkeypatch.setattr(identity_runtime, "_guard_knowledge_context", AsyncMock())
    monkeypatch.setattr(lifecycle, "_on_graph_end", AsyncMock())
    monkeypatch.setattr(
        ledger_store, "thread_deletion_exists", AsyncMock(return_value=False)
    )
    yield client, exporter
    client.shutdown()


def test_ambient_callbacks_survive_without_business_identity():
    """Trace callbacks are useful even when ledger collection has no identity."""
    marker = object()
    token = var_child_runnable_config.set({"callbacks": [marker]})
    try:
        assert current_observation_config()["callbacks"] == [marker]
    finally:
        var_child_runnable_config.reset(token)


def test_factory_isolates_attempts_and_skips_reads(sdk):
    from langgraph_api._factory_utils import _classify_factory

    assert _classify_factory(graph_module.graph) is not None
    assert graph_module.graph({}) is graph_module._graph
    a = run_config()
    b = run_config(thread=a["configurable"]["thread_id"])
    ga, gb = graph_module.graph(a), graph_module.graph(b)
    ha, hb = ga.config["callbacks"][0], gb.config["callbacks"][0]
    assert ha is not hb
    assert ha._trace_context != hb._trace_context
    resumed = graph_module.graph(a).config["callbacks"][0]
    assert resumed is not ha and resumed._trace_context == ha._trace_context
    assert not graph_module._graph.config.get("callbacks")


async def export_run(monkeypatch, config, *, streaming):
    request = make_request()
    lead = _lead_model()
    lead.responses.insert(
        0,
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "freeze_tracing_request",
                    "id": "freeze",
                    "args": {
                        "station_code": "S001",
                        "region_name": "测试市",
                        "pollutant": "pm2.5",
                        "start": request.time_window.start.isoformat(),
                        "end": request.time_window.end.isoformat(),
                        "neighbor_radius_km": 10,
                        "baseline_start": request.baseline_window.start.isoformat(),
                        "baseline_end": request.baseline_window.end.isoformat(),
                        "analysis_id": request.analysis_id,
                        "capability_snapshot_ref": "sel-001",
                    },
                }
            ],
        ),
    )
    _, compiled = _graph(FixtureDataSource(build_bundle()), lead=lead)
    monkeypatch.setattr(graph_module, "_graph", compiled)
    graph = graph_module.graph(config)
    payload = {"messages": [HumanMessage(content="PRIVATE_TRACE_FIXTURE")]}
    # Invoke-time metadata must not override the trusted grouping attributes.
    config = {
        **config,
        "metadata": {"langfuse_trace_name": "forged", "langfuse_user_id": _uuid()},
    }
    if streaming:
        source = graph.astream(
            payload,
            config,
            stream_mode=["messages", "updates", "custom"],
            subgraphs=True,
        )
        # The native server advances the iterator from separate async tasks.
        while True:
            try:
                await asyncio.create_task(anext(source))
            except StopAsyncIteration:
                break
    else:
        result = await graph.ainvoke(payload, config)
        assert result["validated_report"] is not None
    handler = graph.config["callbacks"][0]
    assert not handler._runs
    return handler._trace_context["trace_id"]


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_full_deepagents_tree_has_one_root_and_no_orphans(
    sdk, monkeypatch, streaming
):
    client, exporter = sdk
    config = run_config()
    trace_id = await export_run(monkeypatch, config, streaming=streaming)
    client.flush()
    spans = exporter.get_finished_spans()
    assert spans
    assert {format(s.context.trace_id, "032x") for s in spans} == {trace_id}
    ids = {s.context.span_id for s in spans}
    roots = [s for s in spans if s.attributes.get("langfuse.internal.as_root")]
    assert len(roots) == 1 and roots[0].name == "intelligent-tracing"
    assert roots[0].attributes["langfuse.internal.is_app_root"] is True
    # The SDK uses a virtual OTel parent for a custom trace ID; its as_root
    # flag makes Langfuse ingestion clear that parent for the application root.
    assert all(s.parent and s.parent.span_id in ids for s in spans if s not in roots)
    names = {s.name for s in spans}
    assert {
        "freeze_tracing_request",
        "task",
        "ScriptedChatModel",
        "station-data",
        "grid-data",
        "meteo-analyst",
        "evidence-reviewer",
        "build_validated_report",
    } <= names
    assert "PRIVATE_TRACE_FIXTURE" in roots[0].attributes["langfuse.observation.input"]
    assert roots[0].attributes["langfuse.observation.output"]
    for span in spans:
        if span.name in {
            "ScriptedChatModel",
            "task",
            "freeze_tracing_request",
            "build_validated_report",
        }:
            assert span.attributes.get("langfuse.observation.input")
            assert span.attributes.get("langfuse.observation.output")
    root_attrs = roots[0].attributes
    assert root_attrs["langfuse.trace.name"] == "intelligent-tracing"
    assert (
        root_attrs["user.id"]
        == config["configurable"]["langgraph_auth_user"]["identity"]
    )
    assert root_attrs["session.id"] == config["configurable"]["thread_id"]


def test_factory_preserves_platform_callbacks_and_degrades(sdk, monkeypatch):
    from langchain_core.callbacks import AsyncCallbackManager, BaseCallbackHandler

    platform_handler = BaseCallbackHandler()
    config = run_config()
    manager = AsyncCallbackManager(
        [platform_handler], inheritable_handlers=[platform_handler]
    )
    config["callbacks"] = manager
    result = graph_module.graph(config)
    assert manager.handlers == [platform_handler]
    assert len(result.config["callbacks"]) == 1

    def fail(*args, **kwargs):
        raise RuntimeError("fixture")

    monkeypatch.setattr(graph_module, "with_langfuse_tracing", fail)
    assert graph_module.graph(config) is graph_module._graph


@pytest.mark.anyio
async def test_two_concurrent_attempts_do_not_share_a_trace(sdk, monkeypatch):
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    from common.langfuse_tracing import with_langfuse_tracing

    class State(TypedDict):
        value: int

    async def child(state):
        await asyncio.sleep(0.01)
        return {"value": state["value"] + 1}

    builder = StateGraph(State)
    builder.add_node("child", child)
    builder.add_edge(START, "child")
    builder.add_edge("child", END)
    compiled = builder.compile(name="intelligent-tracing")
    a, b = run_config(), run_config()
    ga = with_langfuse_tracing(compiled, config=a)
    gb = with_langfuse_tracing(compiled, config=b)
    await asyncio.gather(ga.ainvoke({"value": 0}, a), gb.ainvoke({"value": 0}, b))
    client, exporter = sdk
    client.flush()
    spans = exporter.get_finished_spans()
    traces = {s.context.trace_id for s in spans}
    assert len(traces) == 2
    for trace in traces:
        group = [s for s in spans if s.context.trace_id == trace]
        root = [s for s in group if s.attributes.get("langfuse.internal.as_root")]
        assert len(root) == 1
        assert all(
            s.parent.span_id == root[0].context.span_id for s in group if s not in root
        )


@pytest.mark.parametrize("manager", [False, True])
def test_platform_callbacks_receive_each_event_once(sdk, monkeypatch, manager):
    from langchain_core.callbacks import BaseCallbackHandler, CallbackManager
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    class State(TypedDict):
        value: int

    class Counter(BaseCallbackHandler):
        def __init__(self):
            self.names = []

        def on_chain_start(self, serialized, inputs, *, name=None, **kwargs):
            self.names.append(name)

    counter = Counter()
    config = run_config()
    callbacks = (
        CallbackManager([counter], inheritable_handlers=[counter])
        if manager
        else [counter]
    )
    config["callbacks"] = callbacks
    builder = StateGraph(State)
    builder.add_node("child", lambda state: {"value": state["value"] + 1})
    builder.add_edge(START, "child")
    builder.add_edge("child", END)
    monkeypatch.setattr(
        graph_module, "_graph", builder.compile(name="intelligent-tracing")
    )
    graph = graph_module.graph(config)
    assert graph.invoke({"value": 0}, config)["value"] == 1
    assert counter.names.count("intelligent-tracing") == 1
    assert counter.names.count("child") == 1
    assert config["callbacks"] is callbacks
