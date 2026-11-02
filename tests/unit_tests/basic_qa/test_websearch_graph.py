"""图级：批内工具只执行一次，审批恢复只重跑 gate 并生成联网引用."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import basic_qa.builder as builder
import basic_qa.capabilities.runtime as runtime
import basic_qa.nodes as nodes
from basic_qa.capabilities import tool_definitions, websearch
from common.models import ModelRegistry

pytestmark = pytest.mark.anyio

_CONFIG = {
    "configurable": {
        "thread_id": "websearch-thread",
        "ledger_attempt_id": "attempt-ws",
        "ledger_submission_id": "submission-ws",
        "capability_selection_id": "selection-ws",
        "capability_selection_digest": "digest-ws",
        "ledger_graph_id": "basic-qa",
    }
}


def _call(name, call_id, args):
    return AIMessage(
        content="", tool_calls=[{"name": name, "id": call_id, "args": args}]
    )


@pytest.fixture
def graph_env(monkeypatch, tmp_path):
    import common.capabilities as capabilities
    import common.identity.runtime as identity

    probe_calls: list[int] = []
    search_calls: list[str] = []

    async def probe() -> dict[str, str]:
        """Counted local tool that is already disclosed for every planning turn."""
        probe_calls.append(1)
        return {"beijing_time": "2026-10-31 12:00:00"}

    probe_tool = StructuredTool.from_function(
        coroutine=probe, name="get_beijing_time", description="test clock"
    )

    async def fake_search(query: str, *, count: int):
        search_calls.append(query)
        return [
            SimpleNamespace(
                title="新规发布",
                url="https://example.com/policy",
                site="生态环境部",
                date="2026-10-01",
                snippet="政策摘要",
            )
        ]

    monkeypatch.setattr(runtime, "guard_runtime_call", AsyncMock(return_value=SimpleNamespace(tenant_id="t", user_id="u")))
    monkeypatch.setattr(identity, "guard_runtime_call", AsyncMock(return_value=object()))
    monkeypatch.setattr(identity, "guard_request_call", AsyncMock(return_value=object()))
    monkeypatch.setattr(runtime, "resolve_material", AsyncMock(return_value=None))
    monkeypatch.setattr(
        tool_definitions, "mcp_stable_id", lambda name: None
    )
    monkeypatch.setattr(
        capabilities, "filter_capability_mcp_tools", lambda tools, *a, **k: tools
    )
    monkeypatch.setattr(websearch, "search_web", fake_search)
    monkeypatch.setattr(builder, "get_business_tools", lambda **_: [probe_tool])
    monkeypatch.setattr(nodes, "get_business_tools", lambda **_: [probe_tool])

    async def finalize(state, config):
        return {}

    monkeypatch.setattr(builder, "make_ledger_finalize_node", lambda _: finalize)
    model = AsyncMock()
    model.bind_tools = Mock(return_value=model)
    monkeypatch.setattr(ModelRegistry, "main_llm", model)
    graph = builder.build_basic_qa_graph(
        skills_dir=tmp_path, name="basic-qa", checkpointer=InMemorySaver()
    )
    return graph, model, probe_calls, search_calls


async def test_resume_runs_only_gate_and_emits_server_owned_references(graph_env):
    graph, model, probe_calls, search_calls = graph_env
    model.ainvoke.side_effect = [
        # 同一批：已披露的本地工具 + 联网申请
        AIMessage(
            content="",
            tool_calls=[
                {"name": "get_beijing_time", "id": "clock-1", "args": {}},
                {
                    "name": "web_search",
                    "id": "ws-1",
                    "args": {"query": "最新空气质量政策", "reason": "政策公告"},
                },
            ],
        ),
        AIMessage(content="材料已经足够。"),
    ]

    async def answer(messages, *args, **kwargs):
        yield AIMessageChunk(content="新规提出了更严格的要求[web:1]。")

    model.astream = answer

    first = await graph.ainvoke(
        {"messages": [HumanMessage(content="最近有什么空气质量新政策？")]}, _CONFIG
    )
    assert "__interrupt__" in first
    interrupt_payload = first["__interrupt__"][0].value
    assert interrupt_payload["type"] == "websearch_approval"
    assert interrupt_payload["query"] == "最新空气质量政策"
    assert interrupt_payload["remaining"] == 3
    assert probe_calls == [1]
    assert search_calls == []

    # 刷新恢复依赖 aget_state 的 next/tasks.interrupts，而不是内存中的流事件。
    snapshot = await graph.aget_state(_CONFIG)
    assert snapshot.next == ("websearch_gate",)
    interrupts = [item for task in snapshot.tasks for item in task.interrupts]
    assert interrupts and interrupts[0].value["type"] == "websearch_approval"

    final = await graph.ainvoke(Command(resume={"approved": True}), _CONFIG)

    assert search_calls == ["最新空气质量政策"]
    # 恢复只重跑 gate：同批工具没有第二次执行
    assert probe_calls == [1]
    assert final["websearch_used"] == 1
    assert final["websearch_granted"] is True
    assert final["websearch_denied"] is False
    assert final["websearch_sources"][0]["n"] == 1
    receipts = [
        m
        for m in final["messages"]
        if isinstance(m, ToolMessage) and m.tool_call_id == "ws-1"
    ]
    # 占位回执被 gate 的最终回执原地替换，不会留下两条。
    assert len(receipts) == 1
    assert '"status": "ok"' in str(receipts[0].content)
    answer_messages = [
        m
        for m in final["messages"]
        if isinstance(m, AIMessage)
        and m.additional_kwargs.get("ui_visible") is True
    ]
    assert answer_messages
    content = str(answer_messages[-1].content)
    assert "[web:1]" in content
    assert "**互联网来源**" in content
    assert "https://example.com/policy" in content
    assert "新规发布" in content


async def test_denial_continues_without_search_or_reference(graph_env):
    graph, model, probe_calls, search_calls = graph_env
    model.ainvoke.side_effect = [
        _call("web_search", "ws-2", {"query": "政策", "reason": "背景"}),
        AIMessage(content="只能基于已有数据回答。"),
    ]

    async def answer(messages, *args, **kwargs):
        yield AIMessageChunk(content="部分数据暂时无法获取，无法进一步分析。")

    model.astream = answer
    await graph.ainvoke(
        {"messages": [HumanMessage(content="有什么新政策？")]}, _CONFIG
    )
    final = await graph.ainvoke(Command(resume={"approved": False}), _CONFIG)

    assert search_calls == []
    assert final["websearch_denied"] is True
    assert final["websearch_used"] == 0
    assert final["websearch_sources"] == []
    content = str(
        [
            m
            for m in final["messages"]
            if isinstance(m, AIMessage)
            and m.additional_kwargs.get("ui_visible") is True
        ][-1].content
    )
    assert "互联网来源" not in content
    assert "[web:" not in content
