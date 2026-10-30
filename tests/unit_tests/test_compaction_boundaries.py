"""真实节点与锁定SDK的容量边界，模型发送使用受控替身。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from deepagents.backends import StateBackend
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.exceptions import ContextOverflowError
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

import basic_qa.context.preparation as preparation
import basic_qa.context.recovery as recovery
import basic_qa.nodes as business
import data_analysis.context.preparation as data_preparation
import data_analysis.nodes as analysis
from basic_qa.capabilities.runtime import create_load_skill_tool
from common.config import config
from common.model_capacity import capacity_from_window
from intelligent_tracing.guards import guarded_summarization


class ProviderOverflow(Exception):
    status_code = 400


@pytest.mark.anyio
@pytest.mark.parametrize("always_reject", [False, True])
@pytest.mark.parametrize("graph", ["basic-qa", "data-analysis"])
async def test_node_overflow_recovery_sends_at_most_twice(
    monkeypatch, always_reject, graph
):
    counts = {"provider": 0, "summary": 0}

    async def summary(*args, **kwargs):
        counts["summary"] += 1
        return "已完成历史的受控摘要"

    async def invoke(*args, **kwargs):
        counts["provider"] += 1
        if always_reject or counts["provider"] == 1:
            raise ProviderOverflow("maximum context length exceeded")
        return AIMessage(content="正常的规划材料")

    # 本例隔离容量恢复；明确提供空能力目录，不让旧的无身份夹具在目录入口先失败。
    if graph == "basic-qa":
        from basic_qa.capabilities import projection

        monkeypatch.setattr(
            projection, "basic_qa_skill_catalog", AsyncMock(return_value=[])
        )
        monkeypatch.setattr(
            projection, "current_material", AsyncMock(return_value=None)
        )
    target_preparation = preparation if graph == "basic-qa" else data_preparation
    monkeypatch.setattr(target_preparation, "summarize_conversation_history", summary)
    monkeypatch.setattr(business, "invoke_with_data_deadline", invoke)
    monkeypatch.setattr(analysis, "invoke_with_data_deadline", invoke)
    monkeypatch.setattr(analysis, "get_business_tools", lambda **kwargs: [])
    monkeypatch.setattr(business.asyncio, "sleep", AsyncMock())
    service = business.BasicQANodes(
        router=SimpleNamespace(),
        load_skill_tool=create_load_skill_tool(),
        graph_name="basic-qa",
    )
    history = [
        message
        for index in range(4)
        for message in (
            HumanMessage(
                content="历史资料" * 100 if index == 0 else f"问题{index}",
                id=f"h{index}",
            ),
            AIMessage(content=f"回答{index}", id=f"a{index}"),
        )
    ]
    call = service.call_model if graph == "basic-qa" else analysis.call_model
    result = await call(
        {"messages": history, "system_prompt": "规则", "intent": "chitchat"},
        {},
        writer=lambda event: None,
    )
    assert counts == {"provider": 2, "summary": 1}
    assert result["summary_covered_message_id"] == "a0"
    if not always_reject and graph == "basic-qa":
        assert result["planning_draft"] == "正常的规划材料"


@pytest.mark.anyio
@pytest.mark.parametrize("profile", [None, {"max_input_tokens": 2000}])
@pytest.mark.parametrize("provider_limit", [500, 10])
async def test_sdk_honors_bound_input_limit_even_without_matching_profile(
    provider_limit, profile
):
    model = FakeMessagesListChatModel(
        responses=[AIMessage(content="ok")], profile=profile
    )
    capacity = capacity_from_window(
        1000,
        role="main",
        output_reserved_tokens=100,
        safety_tokens=0,
        provider_input_limit_tokens=provider_limit,
    )
    middleware = guarded_summarization(model, StateBackend(), capacity=capacity)
    messages = [HumanMessage(content="x" * 100, id="h")]
    request = ModelRequest(
        model=model,
        messages=messages,
        tools=[],
        state={"messages": messages},
        runtime=SimpleNamespace(),
        system_message=None,
    )
    sent = []

    async def handler(actual):
        sent.append(
            middleware._count_tokens(
                actual.messages, actual.system_message, actual.tools
            )
        )
        return ModelResponse(result=[AIMessage(content="ok")])

    assert middleware._input_budget(request) == provider_limit
    if provider_limit == 10:
        with pytest.raises(ContextOverflowError):
            await middleware.awrap_model_call(request, handler)
        assert sent == []
    else:
        await middleware.awrap_model_call(request, handler)
        assert len(sent) == 1 and sent[0] <= provider_limit


@pytest.mark.anyio
async def test_recovery_advances_prepared_coverage_and_never_resends_same_input(
    monkeypatch,
):
    monkeypatch.setattr(config, "CONVERSATION_SUMMARY_KEEP_TURNS", 3)
    capacity = capacity_from_window(
        1000, role="main", output_reserved_tokens=100, safety_tokens=0
    )
    history = [
        HumanMessage(content="old " * 2000, id="h0"),
        AIMessage(content="旧答", id="a0"),
    ]
    for index in range(1, 4):
        history.extend(
            [
                HumanMessage(content=f"问题{index}", id=f"h{index}"),
                AIMessage(content=f"回答{index}", id=f"a{index}"),
            ]
        )
    state = {"messages": history}
    monkeypatch.setattr(
        preparation, "summarize_conversation_history", AsyncMock(return_value="短摘要")
    )
    kwargs = {"capacity": capacity}
    prepared = await preparation.prepare_model_request(state, {}, **kwargs)
    assert prepared.watermark == "a0"
    sent = []

    async def invoke(actual):
        sent.append([(message.type, message.content) for message in actual.messages])
        raise ProviderOverflow("maximum context length exceeded")

    with pytest.raises(preparation.ContextRecoveryFailed) as failed:
        await recovery.call_with_context_recovery(
            invoke, prepared, state=state, run_config={}, prepare_kwargs=kwargs
        )
    assert len(sent) == 2 and sent[0] != sent[1]
    assert failed.value.prepared.watermark == "a1"
    assert failed.value.prepared.updates["summary_covered_message_id"] == "a1"
    assert state["messages"][0].content == "old " * 2000


@pytest.mark.anyio
async def test_no_progress_stops_before_second_send(monkeypatch):
    prepared = preparation.PreparedRequest(
        messages=[HumanMessage(content="不可继续整理", id="h")],
        schemas=[],
        metrics={"input_tokens": 100, "input_budget_tokens": 500},
    )
    monkeypatch.setattr(
        recovery, "prepare_model_request", AsyncMock(return_value=prepared)
    )
    invoke = AsyncMock(side_effect=ProviderOverflow("maximum context length exceeded"))
    with pytest.raises(preparation.ContextRecoveryFailed, match="没有可继续减小"):
        await recovery.call_with_context_recovery(
            invoke, prepared, state={"messages": []}, run_config={}, prepare_kwargs={}
        )
    assert invoke.await_count == 1
