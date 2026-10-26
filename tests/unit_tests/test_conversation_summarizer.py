"""窗口比例触发、覆盖水位、容量收缩与失败恢复回归."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from common.config import config
from common.context_preparation import prepare_model_request
from common.conversation_summarizer import (
    pending_history,
    split_messages_by_turns,
    summarize_conversation_history,
)
from common.model_capacity import capacity_from_window
from common.models import ModelRegistry
from common.text_reduction import split_text_for_budget, summarize_complete_text


def history(turns: int = 5, *, size: int = 10) -> list:
    filler = "x" * size
    return [
        message
        for i in range(turns)
        for message in (
            HumanMessage(content=f"问题{i} {filler}", id=f"h{i}"),
            AIMessage(content=f"事实{i} {filler}", id=f"a{i}"),
        )
    ]


def capacity(window: int, *, output: int = 100, safety: int = 100):
    return capacity_from_window(
        window,
        role="main",
        output_reserved_tokens=output,
        safety_tokens=safety,
    )


def test_split_and_pending_boundaries():
    early, active = split_messages_by_turns(history(), 3)
    assert len(early) == 4 and len(active) == 6
    assert [m.id for m in pending_history(history(), watermark=None, keep_turns=3)] == [
        "h0",
        "a0",
        "h1",
        "a1",
    ]
    assert [m.id for m in pending_history(history(), watermark="a0", keep_turns=3)] == [
        "h1",
        "a1",
    ]
    with pytest.raises(ValueError):
        split_messages_by_turns(history(), 0)


@pytest.mark.anyio
async def test_summary_covers_only_new_history(monkeypatch):
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(content="更新摘要")
    monkeypatch.setattr(ModelRegistry, "summary_llm", model)
    result = await summarize_conversation_history(
        history(), "先前摘要", watermark="a0", keep_turns=3
    )
    assert result == "更新摘要"
    prompt = model.ainvoke.await_args.args[0][1].content
    assert "事实1" in prompt and "事实0" not in prompt
    assert "先前摘要" in prompt
    assert "nostream" in model.ainvoke.await_args.kwargs["config"]["tags"]


@pytest.mark.anyio
async def test_below_watermark_does_not_summarize(monkeypatch):
    summarize = AsyncMock(return_value="不应调用")
    monkeypatch.setattr(
        "common.context_preparation.summarize_conversation_history", summarize
    )
    prepared = await prepare_model_request(
        {"messages": history(20, size=50)},
        {},
        capacity=capacity(10_000_000),
    )
    assert not prepared.compacted
    assert prepared.within_capacity
    summarize.assert_not_awaited()


@pytest.mark.anyio
async def test_reaching_watermark_compacts_all_pending(monkeypatch):
    summarize = AsyncMock(return_value="新摘要")
    monkeypatch.setattr(
        "common.context_preparation.summarize_conversation_history", summarize
    )
    prepared = await prepare_model_request(
        {"messages": history(20)},
        {},
        capacity=capacity(500),
    )
    assert prepared.compacted and prepared.within_capacity
    assert prepared.summary == "新摘要"
    assert prepared.watermark == "a16"
    assert prepared.kept_turns == 3
    projected_ids = [getattr(message, "id", None) for message in prepared.messages]
    assert "h17" in projected_ids and "h0" not in projected_ids
    assert summarize.await_args.kwargs["watermark"] is None


@pytest.mark.anyio
async def test_existing_summary_does_not_retrigger_per_turn(monkeypatch):
    summarize = AsyncMock(return_value="不应调用")
    monkeypatch.setattr(
        "common.context_preparation.summarize_conversation_history", summarize
    )
    prepared = await prepare_model_request(
        {
            "messages": history(10, size=20),
            "summary": "已覆盖到第七轮",
            "summary_covered_message_id": "a6",
        },
        {},
        capacity=capacity(10_000_000),
    )
    assert not prepared.compacted
    summarize.assert_not_awaited()


@pytest.mark.anyio
async def test_accumulated_turns_merge_into_existing_summary(monkeypatch):
    summarize = AsyncMock(return_value="合并摘要")
    monkeypatch.setattr(
        "common.context_preparation.summarize_conversation_history", summarize
    )
    prepared = await prepare_model_request(
        {
            "messages": history(20),
            "summary": "旧摘要",
            "summary_covered_message_id": "a6",
        },
        {},
        capacity=capacity(500),
    )
    assert prepared.compacted
    assert summarize.await_args.args[1] == "旧摘要"
    assert prepared.watermark == "a16"
    assert prepared.summary == "合并摘要"


@pytest.mark.anyio
async def test_summary_failure_does_not_advance_watermark(monkeypatch):
    summarize = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(
        "common.context_preparation.summarize_conversation_history", summarize
    )
    prepared = await prepare_model_request(
        {
            "messages": history(20),
            "summary": "既有",
            "summary_covered_message_id": "a6",
        },
        {},
        capacity=capacity(500),
    )
    assert not prepared.compacted
    assert "summary" not in prepared.updates
    assert "summary_covered_message_id" not in prepared.updates
    assert prepared.failure_reason
    assert len(history(20)) == 40


@pytest.mark.anyio
async def test_keep_turns_shrink_to_one_under_capacity_pressure(monkeypatch):
    summarize = AsyncMock(return_value="摘要")
    monkeypatch.setattr(
        "common.context_preparation.summarize_conversation_history", summarize
    )
    prepared = await prepare_model_request(
        {"messages": history(20, size=3000)},
        {},
        capacity=capacity(3000),
    )
    assert prepared.compacted and prepared.within_capacity
    assert prepared.watermark == "a18"
    assert prepared.kept_turns == 1
    assert summarize.await_count == 3
    kept = {
        getattr(message, "id", None)
        for message in prepared.messages
        if isinstance(message, HumanMessage)
    }
    assert kept == {"h19"}


@pytest.mark.anyio
async def test_old_thread_without_watermark_compacts(monkeypatch):
    summarize = AsyncMock(return_value="首次摘要")
    monkeypatch.setattr(
        "common.context_preparation.summarize_conversation_history", summarize
    )
    prepared = await prepare_model_request(
        {"messages": history(20)},
        {},
        capacity=capacity(500),
    )
    assert prepared.compacted
    assert prepared.watermark == "a16"
    assert prepared.summary == "首次摘要"


@pytest.mark.anyio
async def test_empty_output_is_failure(monkeypatch):
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(content="")
    monkeypatch.setattr(ModelRegistry, "summary_llm", model)
    with pytest.raises(ValueError):
        await summarize_conversation_history(history(), keep_turns=3)


@pytest.mark.anyio
async def test_full_text_batches_include_tail(monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_MODEL_CONTEXT_TOKENS", 3500)
    monkeypatch.setattr(config, "SUMMARY_MODEL_OUTPUT_TOKENS", 300)
    monkeypatch.setattr(config, "SUMMARY_MODEL_SAFETY_TOKENS", 100)
    raw = "x" * 126000 + "TAIL_TARGET_777"
    chunks = split_text_for_budget(raw, "找目标")
    assert "".join(chunks) == raw
    seen = []

    async def invoke(messages, **kwargs):
        text = messages[1].content
        seen.append(text)
        return AIMessage(
            content="TAIL_TARGET_777" if "TAIL_TARGET_777" in text else "无本批目标"
        )

    model = AsyncMock()
    model.ainvoke.side_effect = invoke
    monkeypatch.setattr(ModelRegistry, "summary_llm", model)
    result, coverage = await summarize_complete_text(raw, instruction="找目标")
    assert "TAIL_TARGET_777" in result
    assert coverage["characters_covered"] == len(raw) and coverage["complete"]
    assert len(seen) > 1
    monkeypatch.setattr(config, "SUMMARY_MODEL_CONTEXT_TOKENS", 6500)
    assert len(split_text_for_budget(raw, "找目标")) < len(chunks)


@pytest.mark.anyio
async def test_oversized_summary_output_is_not_truncated(monkeypatch):
    monkeypatch.setattr(config, "SUMMARY_MODEL_OUTPUT_TOKENS", 100)
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(content="x" * 800)
    monkeypatch.setattr(ModelRegistry, "summary_llm", model)
    with pytest.raises(ValueError, match="输出超限"):
        await summarize_complete_text("输入资料", instruction="归纳")
