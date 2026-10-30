"""当前显式业务图节点、终答身份与完整上下文接线回归."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.graph.message import add_messages

import basic_qa.nodes as module
from basic_qa.builder import (
    _route_after_plan,
    _route_after_tool_calls,
)
from basic_qa.capabilities.runtime import create_load_skill_tool
from basic_qa.nodes import BasicQANodes
from common.config import config
from common.models import ModelRegistry
from common.output_contract import visible_result
from common.runtime_tools import get_business_tools
from data_analysis.nodes import finalize_output

_RUN_CONFIG = {
    "configurable": {
        "ledger_attempt_id": "controlled-attempt",
        "ledger_submission_id": "controlled-submission",
        "capability_selection_id": "controlled-selection",
        "capability_selection_digest": "controlled-digest",
    }
}


def nodes():
    return BasicQANodes(
        router=SimpleNamespace(),
        load_skill_tool=create_load_skill_tool(),
        graph_name="test",
    )


def test_routes_and_computation_scope():
    from basic_qa.graph import graph

    edges = graph({}).get_graph().edges
    assert any(
        e.source == "classify_intent" and e.target == "prepare_model" for e in edges
    )
    assert any(
        e.source == "classify_intent" and e.target == "ledger_finalize" for e in edges
    )
    assert (
        _route_after_plan({"messages": [AIMessage(content="终答")]}) == "answer_model"
    )
    assert (
        _route_after_plan(
            {
                "messages": [
                    AIMessage(
                        content="",
                        additional_kwargs={
                            "ui_visible": True,
                            "control_code": "control",
                        },
                    )
                ]
            }
        )
        == "__end__"
    )
    assert {
        (e.source, e.target, e.conditional)
        for e in edges
        if e.source == "prepare_model"
    } == {("prepare_model", "call_model", False)}
    assert "resolve_skill" not in graph({}).get_graph().nodes
    assert (
        _route_after_tool_calls(
            {"messages": [ToolMessage(content="x", tool_call_id="c")]}
        )
        == "call_model"
    )
    assert "compute_artifact" not in [tool.name for tool in get_business_tools()]
    assert "compute_artifact" in [
        tool.name for tool in get_business_tools(include_computation=True)
    ]


def test_general_knowledge_tool_remains_in_current_projection():
    from basic_qa.capabilities.projection import disclosed_tools

    tools = disclosed_tools(get_business_tools(include_computation=True), None)
    assert "knowledge_search" in {tool.name for tool in tools}


@pytest.mark.anyio
async def test_prepare_prompt_builds_prompt_without_implicit_summary(monkeypatch):
    for intent in ["chitchat", "knowledge", "data_query"]:
        result = await nodes().prepare_prompt({"messages": [], "intent": intent}, {})
        assert result["system_prompt"]
        assert "summary" not in result
        assert "summary_covered_message_id" not in result
        assert "needs_planning" not in result


@pytest.mark.anyio
async def test_model_projects_watermark_and_marks_only_final(monkeypatch):
    model = AsyncMock()
    model.bind_tools = Mock(return_value=model)
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
    result = await nodes().plan_tools(state, _RUN_CONFIG, writer=lambda event: None)
    # 规划阶段不公开正文：内容清空、标记为内部规划，最终正文由 compose_answer 生成。
    assert result["messages"][0].content == ""
    assert result["messages"][0].additional_kwargs["ui_visible"] is False
    assert result["planning_draft"] == "最终答案"
    input_messages = model.ainvoke.await_args.args[0]
    assert all(message.content != "旧问" for message in input_messages)
    assert any(message.content == "新问" for message in input_messages)
    assert "nostream" in model.ainvoke.await_args.kwargs["config"]["tags"]
    model.ainvoke.return_value = AIMessage(
        content="计划", tool_calls=[{"id": "c", "name": "tool", "args": {}}]
    )
    result = await nodes().plan_tools(state, _RUN_CONFIG, writer=lambda event: None)
    assert result["messages"][0].additional_kwargs["ui_visible"] is False


@pytest.mark.anyio
async def test_budget_and_terminal_computation_do_not_call_model(monkeypatch):
    model = AsyncMock()
    monkeypatch.setattr(ModelRegistry, "main_llm", model)
    monkeypatch.setattr(config, "MODEL_PROVIDER_INPUT_LIMIT_TOKENS", 200)
    result = await nodes().plan_tools(
        {"messages": [HumanMessage(content="x" * 1000)], "intent": "chitchat"},
        _RUN_CONFIG,
        writer=lambda event: None,
    )
    assert "超出" in result["messages"][0].content
    model.ainvoke.assert_not_awaited()
    result = await nodes().plan_tools(
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
        _RUN_CONFIG,
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
    assert result["messages"][0].id.startswith("msg_")
    # 原稿保留为内部消息；最终正文使用稳定发布器消息 ID，不覆盖草稿对象。
    assert len(add_messages(history, result["messages"])) == 3
    assert result["messages"][0].content == "终稿"
    assert events[-1]["type"] == "final_output_done"
    assert events[-1]["message_id"] == result["messages"][0].id
    assert events[-1]["message_id"] != "draft"

    async def failure(*args, **kwargs):
        yield AIMessageChunk(content="半段终稿")
        raise TimeoutError()

    monkeypatch.setattr(ModelRegistry, "main_llm", SimpleNamespace(astream=failure))
    events.clear()
    result = await finalize_output({"messages": history}, {}, writer=events.append)
    assert "整理未完成" in result["messages"][0].content
    assert events[-1]["type"] == "final_output_done"
    assert "整理未完成" in events[-1]["message"]
    assert all(event.get("message") != "半段终稿" for event in events)

    async def truncated(*args, **kwargs):
        yield AIMessageChunk(
            content="被截断的清洗草稿", response_metadata={"finish_reason": "length"}
        )

    monkeypatch.setattr(ModelRegistry, "main_llm", SimpleNamespace(astream=truncated))
    events.clear()
    result = await finalize_output({"messages": history}, {}, writer=events.append)
    assert events[-1]["type"] == "final_output_done"
    assert all("被截断" not in (event.get("message") or "") for event in events)
    result = visible_result(
        AIMessage(content="半段主答案", response_metadata={"finish_reason": "length"}),
        _RUN_CONFIG,
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


@pytest.mark.anyio
async def test_tool_then_answer_uses_no_tool_streaming_final_call(monkeypatch):
    from langchain_core.messages import AIMessageChunk

    class Fake:
        def __init__(self):
            self.bound = 0
            self.streams = 0

        def bind_tools(self, schemas):
            self.bound += 1
            return self

        async def ainvoke(self, messages, config=None):
            return AIMessage(
                content="",
                tool_calls=[
                    {"id": "c1", "name": "knowledge_search", "args": {"query": "x"}}
                ],
            )

        async def astream(self, messages, config=None, **kwargs):
            assert kwargs.get("tool_choice") == "none"
            self.streams += 1
            for text in ("工具后", "最终回答。"):
                yield AIMessageChunk(content=text)

    fake = Fake()
    monkeypatch.setattr(ModelRegistry, "main_llm", fake)
    state = {
        "messages": [
            HumanMessage(content="查一下"),
            ToolMessage(content="{}", tool_call_id="c1", name="knowledge_search"),
        ],
        "intent": "knowledge",
        "system_prompt": "规则",
    }
    planning = await nodes().plan_tools(state, _RUN_CONFIG, writer=lambda event: None)
    assert planning["messages"][0].tool_calls
    events: list[dict] = []
    result = await nodes().compose_answer(
        {**state, "planning_draft": ""}, _RUN_CONFIG, writer=events.append
    )
    assert result["messages"][0].content == "工具后最终回答。"
    assert result["messages"][0].additional_kwargs["ui_visible"] is True
    assert fake.bound == 1  # only the planning call had tool schemas
    assert fake.streams == 1  # exactly one final generation
    assert [event["type"] for event in events][0] == "final_output_start"
    assert [event["type"] for event in events][-1] == "final_output_done"


@pytest.mark.anyio
async def test_compose_answer_rejects_over_capacity_without_stream(monkeypatch):
    class Fake:
        streams = 0

        async def astream(self, *args, **kwargs):
            Fake.streams += 1
            if False:  # pragma: no cover - keeps this an async generator
                yield AIMessageChunk(content="")

    monkeypatch.setattr(ModelRegistry, "main_llm", Fake())
    monkeypatch.setattr(config, "MODEL_PROVIDER_INPUT_LIMIT_TOKENS", 1)
    result = await nodes().compose_answer(
        {"messages": [HumanMessage(content="查一下")], "system_prompt": "规则"},
        _RUN_CONFIG,
        writer=lambda event: None,
    )
    assert "超出" in result["messages"][0].content
    assert Fake.streams == 0


@pytest.mark.anyio
async def test_finish_planning_sentinel_routes_to_answer_without_tool_execution(
    monkeypatch,
):
    class Fake:
        def bind_tools(self, schemas):
            return self

        async def ainvoke(self, messages, config=None):
            return AIMessage(
                content="",
                tool_calls=[{"id": "f", "name": "finish_planning", "args": {}}],
            )

    monkeypatch.setattr(ModelRegistry, "main_llm", Fake())
    result = await nodes().plan_tools(
        {
            "messages": [HumanMessage(content="你好")],
            "intent": "knowledge",
            "system_prompt": "规则",
        },
        _RUN_CONFIG,
        writer=lambda event: None,
    )
    message = result["messages"][0]
    assert message.tool_calls == []
    assert message.content == ""
    assert message.additional_kwargs["planning_finished"] is True


@pytest.mark.anyio
async def test_knowledge_question_can_plan_and_execute_retrieval(monkeypatch):

    question = "请根据知识库解释臭氧是怎么形成的"
    service = nodes()
    intent = "knowledge"
    state = {"messages": [HumanMessage(content=question)], "intent": intent}
    prepared = await service.prepare_prompt(state, {})
    assert prepared["system_prompt"]
    model = AsyncMock()
    model.bind_tools = Mock(return_value=model)
    model.ainvoke.return_value = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "knowledge-c1",
                "name": "knowledge_search",
                "args": {"query": question},
            }
        ],
    )
    monkeypatch.setattr(ModelRegistry, "main_llm", model)
    planned = await service.plan_tools({**state, **prepared}, {}, writer=lambda e: None)
    assert _route_after_plan(planned) == "execute_tools"
    schemas = model.bind_tools.call_args.args[0]
    assert any(
        item.get("name") == "knowledge_search"
        or item.get("function", {}).get("name") == "knowledge_search"
        for item in schemas
    )


@pytest.mark.anyio
async def test_cleanup_outer_timeout_after_public_delta_stays_incomplete(monkeypatch):
    import asyncio

    import data_analysis.nodes as analysis_nodes

    events = []

    async def slow(*args, **kwargs):
        yield AIMessageChunk(content="这是一段已经检查的分析正文。\n")
        await asyncio.sleep(10)

    monkeypatch.setattr(ModelRegistry, "main_llm", SimpleNamespace(astream=slow))
    monkeypatch.setattr(
        analysis_nodes.app_config, "FINAL_OUTPUT_CLEANUP_TIMEOUT_SECONDS", 0.05
    )
    result = await finalize_output(
        {"messages": [HumanMessage(content="分析情况")]}, {}, writer=events.append
    )
    public = [e for e in events if e.get("type", "").startswith("final_output_")]
    assert [e["type"] for e in public] == [
        "final_output_start",
        "final_output_delta",
        "final_output_aborted",
    ]
    assert len({e["output_id"] for e in public}) == 1
    assert result["messages"][0].additional_kwargs["ui_visible"] is False
    assert result["messages"][0].additional_kwargs["output_aborted"]


@pytest.mark.anyio
async def test_compiled_knowledge_graph_reaches_actual_tool_before_streamed_answer(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from langchain_core.tools import StructuredTool

    import basic_qa.builder as builder
    import basic_qa.capabilities.projection as integration
    import basic_qa.capabilities.runtime as runtime
    import common.capabilities as capabilities
    import common.identity.runtime as identity

    monkeypatch.setattr(
        runtime,
        "guard_runtime_call",
        AsyncMock(return_value=SimpleNamespace(tenant_id="tenant", user_id="user")),
    )
    calls = []

    async def search(query: str) -> str:
        """Search synthetic knowledge for the routing regression."""
        calls.append(query)
        return "知识库资料：臭氧由光化学过程形成。"

    tool = StructuredTool.from_function(
        coroutine=search, name="knowledge_search", description="知识检索"
    )
    monkeypatch.setattr(builder, "get_business_tools", lambda **kw: [tool])
    monkeypatch.setattr(module, "get_business_tools", lambda **kw: [tool])
    monkeypatch.setattr(
        identity, "guard_runtime_call", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        identity, "guard_request_call", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        capabilities, "filter_capability_mcp_tools", lambda tools, *a, **k: tools
    )

    async def finalize(state, config):
        return {}

    monkeypatch.setattr(builder, "make_ledger_finalize_node", lambda _: finalize)
    model = AsyncMock()
    model.bind_tools = Mock(return_value=model)
    question = "请根据知识库解释臭氧是怎么形成的"
    model.ainvoke.side_effect = [
        AIMessage(
            content="",
            tool_calls=[
                {"id": "k1", "name": "knowledge_search", "args": {"query": question}}
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "finish",
                    "name": "finish_planning",
                    "args": {},
                }
            ],
        ),
    ]

    async def answer(*args, **kwargs):
        yield AIMessageChunk(content="臭氧由光化学过程形成。")

    model.astream = answer
    monkeypatch.setattr(ModelRegistry, "main_llm", model)
    graph = builder.build_basic_qa_graph(skills_dir=tmp_path, name="basic-qa")
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=question)]},
        {
            "configurable": {
                "thread_id": "knowledge-thread",
                "ledger_graph_id": "basic-qa",
                "ledger_attempt_id": "a",
                "ledger_submission_id": "s",
                "capability_selection_id": "c",
                "capability_selection_digest": "d",
            }
        },
    )
    assert calls == [question]
    visible = [
        m
        for m in result["messages"]
        if getattr(m, "additional_kwargs", {}).get("ui_visible")
    ]
    assert len(visible) == 1 and visible[0].content == "臭氧由光化学过程形成。"
    assert model.ainvoke.await_count == 2


@pytest.mark.anyio
async def test_self_identity_questions_keep_native_model_decision(monkeypatch):
    from basic_qa.capabilities import projection

    monkeypatch.setattr(projection, "initialize", AsyncMock(return_value={}))
    service = BasicQANodes(
        router=SimpleNamespace(),
        load_skill_tool=None,
        graph_name="basic-qa",
    )
    for question in ("你是谁", "你能做什么", "你好，请问你是谁"):
        state = await service.prepare_session(
            {"messages": [HumanMessage(content=question)]}, {}
        )
        assert state["intent"] == "data_query"


@pytest.fixture(autouse=True)
def empty_session_projection(monkeypatch):
    from basic_qa.capabilities import projection

    monkeypatch.setattr(projection, "current_material", AsyncMock(return_value=None))
