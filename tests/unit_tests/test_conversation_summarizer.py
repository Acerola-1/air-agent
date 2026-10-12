"""全量摘要、水位和失败恢复回归."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from common.config import config
from common.conversation_summarizer import (
    should_summarize_conversation,
    split_messages_by_turns,
    summarize_conversation_history,
    update_conversation_summary,
)
from common.models import ModelRegistry
from common.text_reduction import split_text_for_budget, summarize_complete_text


def history():
    return [
        message
        for i in range(5)
        for message in (
            HumanMessage(content=f"问题{i}", id=f"h{i}"),
            AIMessage(content=f"事实{i}", id=f"a{i}"),
        )
    ]


def test_split_and_threshold():
    early, active = split_messages_by_turns(history(), 3)
    assert len(early) == 4 and len(active) == 6
    assert should_summarize_conversation(history(), threshold_tokens=1)
    assert not should_summarize_conversation(history(), threshold_tokens=100000)
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
async def test_failure_does_not_advance_watermark(monkeypatch):
    model = AsyncMock()
    model.ainvoke.side_effect = TimeoutError()
    monkeypatch.setattr(ModelRegistry, "summary_llm", model)
    monkeypatch.setattr(config, "CONVERSATION_SUMMARY_THRESHOLD_TOKENS", 1)
    state = {
        "messages": history(),
        "summary": "既有",
        "summary_covered_message_id": "a0",
    }
    update = await update_conversation_summary(state, {})
    assert "summary" not in update and "summary_covered_message_id" not in update
    assert update["context_status"]["status"] == "failed"
    assert len(state["messages"]) == 10


@pytest.mark.anyio
async def test_success_advances_to_whole_turn(monkeypatch):
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(content="完整摘要")
    monkeypatch.setattr(ModelRegistry, "summary_llm", model)
    monkeypatch.setattr(config, "CONVERSATION_SUMMARY_THRESHOLD_TOKENS", 1)
    update = await update_conversation_summary(
        {"messages": history()}, {"configurable": {"thread_id": "t"}}
    )
    assert update["summary_covered_message_id"] == "a1"
    assert update["context_status"]["thread_id"] == "t"
    assert update["context_status"]["kept_turns"] == 3


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
