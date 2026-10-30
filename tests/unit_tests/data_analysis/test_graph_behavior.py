"""封存数据分析图的菜单和受控工具循环."""

from __future__ import annotations

import importlib

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from data_analysis.nodes import resolve_skill
from data_analysis.prompts import filter_business_tools


@pytest.mark.anyio
async def test_menu_selection():
    state = {"messages": [HumanMessage(content="分析")]}

    def writer(event):
        return None

    resolved = await resolve_skill(
        state, {"configurable": {"menu_name": "cityhourbroadcast"}}, writer=writer
    )
    assert resolved["selected_skill"] == "broadcast-hour"
    assert resolved["skill_rules_content"]
    missing = await resolve_skill(
        state, {"configurable": {"menu_name": "unknown"}}, writer=writer
    )
    assert missing["selected_skill"] is None
    assert missing["selected_skill_allowed_tools"] == []


def test_no_menu_restricts_business_tools():
    class Tool:
        def __init__(self, name):
            self.name = name

    tools = [Tool("query"), Tool("knowledge_search"), Tool("compute_artifact")]
    assert [tool.name for tool in filter_business_tools(tools, [])] == [
        "knowledge_search",
        "compute_artifact",
    ]


@pytest.mark.anyio
async def test_compiled_graph_returns_from_tool_to_model_and_finalizes(monkeypatch):
    module = importlib.import_module("data_analysis.graph")
    counts = {"model": 0, "tool": 0, "final": 0}

    async def prepare(state):
        return {"system_prompt": "controlled"}

    async def model(state):
        counts["model"] += 1
        if counts["model"] == 1:
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "controlled", "args": {}, "id": "call-1"}],
                    )
                ]
            }
        assert isinstance(state["messages"][-1], ToolMessage)
        return {"messages": [AIMessage(content="最终回答")]}

    async def tool(state):
        counts["tool"] += 1
        return {"messages": [ToolMessage(content="资料", tool_call_id="call-1")]}

    async def final(state):
        counts["final"] += 1
        assert state["messages"][-1].content == "最终回答"
        return {}

    async def ledger(state):
        return {}

    monkeypatch.setattr(module, "prepare_model", prepare)
    monkeypatch.setattr(module, "call_model", model)
    monkeypatch.setattr(module, "_build_tool_node", lambda: tool)
    monkeypatch.setattr(module, "finalize_output", final)
    monkeypatch.setattr(module, "make_ledger_finalize_node", lambda graph: ledger)
    graph = module.build_graph()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="分析")]},
        {"configurable": {"menu_name": "CityHourBroadcast"}},
    )
    assert result["selected_skill"] == "broadcast-hour"
    assert result["messages"][-1].content == "最终回答"
    assert counts == {"model": 2, "tool": 1, "final": 1}


@pytest.mark.anyio
@pytest.mark.parametrize("reject_twice", [False, True])
async def test_actual_model_nodes_bound_overflow_without_replaying_tool(
    monkeypatch, reject_twice
):
    from unittest.mock import AsyncMock

    from langchain_core.tools import StructuredTool

    from common.models import ModelRegistry
    from data_analysis import nodes
    from data_analysis.context import preparation

    module = importlib.import_module("data_analysis.graph")
    counts = {"model": 0, "tool": 0}

    class Overflow(Exception):
        status_code = 400

    class Model:
        def bind_tools(self, schemas):
            return self

    async def execute(query: str):
        """返回合成知识资料。"""
        counts["tool"] += 1
        return "合成完整资料"

    tool = StructuredTool.from_function(
        coroutine=execute, name="knowledge_search", description="合成只读资料"
    )
    monkeypatch.setattr(module, "get_business_tools", lambda **_: [tool])
    monkeypatch.setattr(nodes, "get_business_tools", lambda **_: [tool])
    monkeypatch.setattr(nodes, "refresh_authorized_mcp_tools", AsyncMock())
    monkeypatch.setattr(nodes, "build_system_prompt", lambda **_: "固定封存规则")
    monkeypatch.setattr(ModelRegistry, "main_llm", Model())
    summary = AsyncMock(return_value="已经完成的历史摘要")
    monkeypatch.setattr(preparation, "summarize_conversation_history", summary)

    async def invoke(*args, **kwargs):
        counts["model"] += 1
        if counts["model"] == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "knowledge_search",
                        "args": {"query": "合成问题"},
                        "id": "once",
                    }
                ],
            )
        if counts["model"] == 2 or reject_twice:
            raise Overflow("maximum context length exceeded")
        return AIMessage(content="根据合成资料完成")

    monkeypatch.setattr(nodes, "invoke_with_data_deadline", invoke)

    async def finalize(state):
        return {}

    monkeypatch.setattr(module, "finalize_output", finalize)
    monkeypatch.setattr(module, "make_ledger_finalize_node", lambda _: finalize)
    history = [
        m
        for i in range(5)
        for m in (
            HumanMessage(content="旧问题" * 80, id=f"old-h-{i}"),
            AIMessage(content="旧答案" * 80, id=f"old-a-{i}"),
        )
    ]
    result = await module.build_graph().ainvoke(
        {"messages": [*history, HumanMessage(content="解释资料")]},
        {"configurable": {"menu_name": "CityHourBroadcast"}},
    )
    assert counts == {"model": 3, "tool": 1}
    assert summary.await_count == 1
    assert result.get("summary_covered_message_id")
    assert {m.id for m in history} <= {m.id for m in result["messages"]}
    assert sum(isinstance(m, ToolMessage) for m in result["messages"]) == 1
    assert (
        "自动整理后模型上下文仍超限" if reject_twice else "根据合成资料完成"
    ) in result["messages"][-1].content
