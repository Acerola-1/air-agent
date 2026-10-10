"""Subagent 总结提炼子代理单元测试."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

from common.business_graph.subagent_summarizer import (
    _build_summarizer_prompt,
    _fallback_summary,
    summarize_large_tool_output,
)


def test_subagent_prompt_and_fallback_builder():
    """测试提示词构建与安全降级格式."""
    prompt = _build_summarizer_prompt(
        user_question="海淀区空气质量",
        tool_name="mcp_weather",
        tool_args={"region": ""},
        char_length=50000,
        raw_output="WEATHER_DATA_" * 1000,
    )
    assert "海淀区空气质量" in prompt
    assert "mcp_weather" in prompt
    assert "50000" in prompt

    fallback = _fallback_summary(
        tool_name="mcp_weather",
        char_length=50000,
        raw_output="SAMPLE_HEAD_12345" + "X" * 10000 + "SAMPLE_TAIL_67890",
        reason="测试超时",
    )
    assert "工具超大输出降级摘要" in fallback
    assert "测试超时" in fallback
    assert "SAMPLE_HEAD_12345" in fallback
    assert "SAMPLE_TAIL_67890" in fallback


def test_subagent_summarize_success(monkeypatch):
    """测试 Subagent 正常提炼流程."""
    async def _run():
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AIMessage(
            content="【海淀区空气质量摘要】AQI: 45, 优, PM2.5: 12μg/m³。"
        )
        monkeypatch.setattr("common.business_graph.subagent_summarizer.ModelRegistry.main_llm", mock_model)

        result = await summarize_large_tool_output(
            user_question="海淀区今天空气质量如何？",
            tool_name="mcp_weather",
            tool_args={"region": ""},
            raw_output="RAW_DATA_" * 2000,
        )
        assert "【海淀区空气质量摘要】" in result
        assert "PM2.5: 12μg/m³" in result
        assert mock_model.ainvoke.called

    asyncio.run(_run())


def test_subagent_summarize_timeout_fallback(monkeypatch):
    """测试 Subagent 超时安全降级."""
    async def _run():
        mock_model = AsyncMock()

        async def _slow_call(*args, **kwargs):
            await asyncio.sleep(1.0)
            return AIMessage(content="late")

        mock_model.ainvoke = _slow_call
        monkeypatch.setattr("common.business_graph.subagent_summarizer.ModelRegistry.main_llm", mock_model)

        result = await summarize_large_tool_output(
            user_question="海淀区天气",
            tool_name="mcp_weather",
            tool_args={"region": ""},
            raw_output="FAST_DATA_CHUNK_" * 500,
            timeout_seconds=0.05,
        )
        assert "工具超大输出降级摘要" in result
        assert "提炼超时" in result

    asyncio.run(_run())
