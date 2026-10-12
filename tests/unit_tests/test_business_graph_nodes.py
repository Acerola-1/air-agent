"""当前显式业务图节点、终答身份与完整上下文接线回归."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.graph.message import add_messages

import common.business_graph.nodes as module
from common.business_graph.builder import (
    _route_after_intent,
    _route_after_model,
    _route_after_tools,
)
from common.business_graph.nodes import BusinessGraphNodes
from common.config import config
from common.models import ModelRegistry
from common.output_contract import visible_result
from common.runtime_tools import get_business_tools
from data_analysis.nodes import finalize_output


def nodes():
    return BusinessGraphNodes(
        router=SimpleNamespace(),
        base_system_prompt="规则",
        load_skill_tool=SimpleNamespace(name="load_skill"),
        graph_name="test",
    )


def test_routes_and_computation_scope():
    assert _route_after_intent({"intent": "data_query"}) == "resolve_skill"
    assert _route_after_intent({"intent": "knowledge"}) == "prepare_model"
    assert _route_after_intent({"intent": "input_overflow"}) == "__end__"
    assert _route_after_model({"messages": [AIMessage(content="终答")]}) == "__end__"
    assert (
        _route_after_tools({"messages": [ToolMessage(content="x", tool_call_id="c")]})
        == "call_model"
    )
    assert "compute_artifact" not in [tool.name for tool in get_business_tools()]
    assert "compute_artifact" in [
        tool.name for tool in get_business_tools(include_computation=True)
    ]


@pytest.mark.anyio
async def test_all_intents_update_summary(monkeypatch):
    update = AsyncMock(
        return_value={"summary": "记忆", "summary_covered_message_id": "a"}
    )
    monkeypatch.setattr(module, "update_conversation_summary", update)
    monkeypatch.setattr(module, "ensure_mcp_tools", AsyncMock())
    for intent in ["chitchat", "knowledge", "data_query"]:
        result = await nodes().prepare_model({"messages": [], "intent": intent}, {})
        assert result["summary_covered_message_id"] == "a"
    assert update.await_count == 3


@pytest.mark.anyio
async def test_model_projects_watermark_and_marks_only_final(monkeypatch):
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(
        content="<think>内部</think>最终答案", id="result"
    )
    monkeypatch.setattr(ModelRegistry, "main_llm", model)
    state = {
        "messages": [
            HumanMessage(content="旧问", id="h1"),
            AIMessage(content="旧答", id="a1"),
            HumanMessage(content="新问", id="h2"),
        ],
        "intent": "chitchat",
        "summary": "记忆",
        "summary_covered_message_id": "a1",
        "system_prompt": "规则",
    }
    result = await nodes().call_model(state, {}, writer=lambda event: None)
    assert result["messages"][0].content == "最终答案"
    assert result["messages"][0].additional_kwargs["ui_visible"] is True
    input_messages = model.ainvoke.await_args.args[0]
    assert all(message.content != "旧问" for message in input_messages)
    assert any(message.content == "新问" for message in input_messages)
    assert "nostream" in model.ainvoke.await_args.kwargs["config"]["tags"]
    model.ainvoke.return_value = AIMessage(
        content="计划", tool_calls=[{"id": "c", "name": "tool", "args": {}}]
    )
    result = await nodes().call_model(state, {}, writer=lambda event: None)
    assert result["messages"][0].additional_kwargs["ui_visible"] is False


@pytest.mark.anyio
async def test_budget_and_terminal_computation_do_not_call_model(monkeypatch):
    model = AsyncMock()
    monkeypatch.setattr(ModelRegistry, "main_llm", model)
    monkeypatch.setattr(config, "MODEL_CONTEXT_BUDGET_TOKENS", 100)
    result = await nodes().call_model(
        {"messages": [HumanMessage(content="x" * 1000)], "intent": "chitchat"},
        {},
        writer=lambda event: None,
    )
    assert "超出" in result["messages"][0].content
    model.ainvoke.assert_not_awaited()
    result = await nodes().call_model(
        {
            "messages": [
                HumanMessage(content="统计"),
                ToolMessage(
                    content="失败",
                    tool_call_id="c",
                    additional_kwargs={"processing_terminal": True},
                ),
            ],
            "intent": "chitchat",
        },
        {},
        writer=lambda event: None,
    )
    assert "未完成" in result["messages"][0].content
    model.ainvoke.assert_not_awaited()


@pytest.mark.anyio
async def test_cleanup_replaces_id_and_failure_overwrites_partial(monkeypatch):
    events = []

    async def chunks(*args, **kwargs):
        yield AIMessageChunk(content="终稿")

    monkeypatch.setattr(ModelRegistry, "main_llm", SimpleNamespace(astream=chunks))
    draft = AIMessage(
        content="原稿", id="draft", additional_kwargs={"ui_visible": False}
    )
    history = [HumanMessage(content="问题", id="h"), draft]
    result = await finalize_output({"messages": history}, {}, writer=events.append)
    assert result["messages"][0].id == "draft"
    assert len(add_messages(history, result["messages"])) == 2
    assert result["messages"][0].content == "终稿"
    assert (
        events[-1]["type"] == "final_output_done"
        and events[-1]["message_id"] == "draft"
    )

    async def failure(*args, **kwargs):
        yield AIMessageChunk(content="半段终稿")
        raise TimeoutError()

    monkeypatch.setattr(ModelRegistry, "main_llm", SimpleNamespace(astream=failure))
    events.clear()
    result = await finalize_output({"messages": history}, {}, writer=events.append)
    assert "整理未完成" in result["messages"][0].content
    assert events[-1]["message"] != "半段终稿"
    assert all(event.get("message") != "半段终稿" for event in events)
    assert all(event["type"] != "final_output_delta" for event in events)

    async def truncated(*args, **kwargs):
        yield AIMessageChunk(
            content="被截断的清洗草稿", response_metadata={"finish_reason": "length"}
        )

    monkeypatch.setattr(ModelRegistry, "main_llm", SimpleNamespace(astream=truncated))
    events.clear()
    result = await finalize_output({"messages": history}, {}, writer=events.append)
    assert "未完成" in result["messages"][0].content
    assert all("被截断" not in event.get("message", "") for event in events)
    result = visible_result(
        AIMessage(content="半段主答案", response_metadata={"finish_reason": "length"}),
        {},
        writer=events.append,
    )
    assert "未完整生成" in result["messages"][0].content
    assert "半段主答案" not in events[-1]["message"]


@pytest.mark.anyio
async def test_cleanup_does_not_replace_previous_turn(monkeypatch):
    async def chunks(*args, **kwargs):
        yield AIMessageChunk(content="本轮未完成")

    monkeypatch.setattr(ModelRegistry, "main_llm", SimpleNamespace(astream=chunks))
    history = [
        HumanMessage(content="旧问"),
        AIMessage(content="旧答案", id="old"),
        HumanMessage(content="新问"),
    ]
    result = await finalize_output({"messages": history}, {}, writer=lambda event: None)
    assert result["messages"][0].id != "old"
    assert add_messages(history, result["messages"])[1].content == "旧答案"
