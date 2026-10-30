"""独立摘要模型的完整输入与失败传播契约."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from common.tool_output_summary import summarize_large_tool_output
from common.models import ModelRegistry
from common.text_reduction import summarize_complete_text


@pytest.mark.anyio
@pytest.mark.parametrize("reason", ["length", "content_filter"])
async def test_provider_truncation_is_not_a_complete_summary(monkeypatch, reason):
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(
        content="半段摘要", response_metadata={"finish_reason": reason}
    )
    monkeypatch.setattr(ModelRegistry, "summary_llm", model)
    with pytest.raises(ValueError, match="未完整"):
        await summarize_complete_text("完整原文资料", instruction="保留用户约束")
    assert model.ainvoke.await_count == 1


@pytest.mark.anyio
async def test_tool_summary_keeps_question_source_and_full_coverage(monkeypatch):
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(content="保留时间、单位与目标事实")
    monkeypatch.setattr(ModelRegistry, "summary_llm", model)
    raw = "资料" * 10000 + "尾部目标777"
    text, coverage = await summarize_large_tool_output(
        user_question="查询尾部目标",
        tool_name="source",
        tool_args={"region": "测试范围"},
        raw_output=raw,
    )
    assert text == "保留时间、单位与目标事实"
    assert coverage["characters_covered"] == len(raw) and coverage["complete"] is True
    prompt = model.ainvoke.await_args.args[0][1].content
    assert "尾部目标777" in prompt and "测试范围" in prompt and "source" in prompt
    assert "nostream" in model.ainvoke.await_args.kwargs["config"]["tags"]


@pytest.mark.anyio
async def test_timeout_has_no_fake_prefix_summary(monkeypatch):
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
        return AIMessage(content="迟到结果")

    model = AsyncMock()
    model.ainvoke.side_effect = slow
    monkeypatch.setattr(ModelRegistry, "summary_llm", model)
    with pytest.raises(TimeoutError):
        await summarize_large_tool_output(
            user_question="全量资料",
            tool_name="source",
            tool_args={},
            raw_output="头部资料" + ("x" * 10000) + "尾部资料",
            timeout_seconds=0.01,
        )
