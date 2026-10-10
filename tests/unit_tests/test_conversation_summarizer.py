from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from common.context_guard import prepare_model_context
from common.conversation_summarizer import (
    should_summarize_conversation,
    split_messages_by_turns,
    summarize_conversation_history,
)


def test_split_messages_by_turns():
    # 构造 4 轮问答
    messages = [
        HumanMessage(content="第1问"),
        AIMessage(content="第1答"),
        HumanMessage(content="第2问"),
        AIMessage(content="第2答"),
        HumanMessage(content="第3问"),
        AIMessage(content="第3答"),
        HumanMessage(content="第4问"),
        AIMessage(content="第4答"),
    ]

    # 保留 2 轮
    early, active = split_messages_by_turns(messages, keep_turns=2)
    assert len(early) == 4
    assert [m.content for m in early] == ["第1问", "第1答", "第2问", "第2答"]
    assert len(active) == 4
    assert [m.content for m in active] == ["第3问", "第3答", "第4问", "第4答"]

    # 轮次不足时
    early_empty, active_all = split_messages_by_turns(messages, keep_turns=5)
    assert early_empty == []
    assert len(active_all) == 8


def test_should_summarize_conversation():
    # 构造 5 轮，每轮文本较长
    messages = []
    for i in range(5):
        messages.append(HumanMessage(content=f"提问轮次{i}: " + "长文本" * 100))
        messages.append(AIMessage(content=f"回答轮次{i}: " + "回答内容" * 100))

    # keep_turns=3, threshold_tokens 设低一点进行测试
    assert should_summarize_conversation(messages, keep_turns=3, threshold_tokens=100) is True

    # 阈值设置极高时返回 False
    assert should_summarize_conversation(messages, keep_turns=3, threshold_tokens=1_000_000) is False

    # 轮次不足时返回 False
    assert should_summarize_conversation(messages[:4], keep_turns=3, threshold_tokens=100) is False


@pytest.mark.anyio
async def test_summarize_conversation_history_success():
    messages = [
        HumanMessage(content="查询北京今天天气"),
        AIMessage(content="北京今天晴朗，温度20度"),
        HumanMessage(content="查询天津今天天气"),
        AIMessage(content="天津今天多云，温度18度"),
        HumanMessage(content="查询石家庄今天天气"),
        AIMessage(content="石家庄今天轻度污染"),
        HumanMessage(content="对比前三个城市"),
        AIMessage(content="北京天气最好，石家庄最差"),
    ]

    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AIMessage(
        content="用户之前查询并对比了北京、天津和石家庄的天气与污染情况，确认北京天气良好而石家庄有轻度污染。"
    )

    with patch("common.conversation_summarizer.ModelRegistry.main_llm", mock_llm):
        summary = await summarize_conversation_history(
            messages,
            existing_summary="【早期背景】用户是环保部门巡查员。",
            keep_turns=2,
        )

        assert "用户之前查询并对比了北京" in summary
        assert mock_llm.ainvoke.await_count == 1
        prompt_passed = mock_llm.ainvoke.await_args[0][0]
        # 检查是否传入了 existing_summary 和早期待压缩消息
        assert "【早期背景】用户是环保部门巡查员。" in prompt_passed[1].content
        assert "北京今天晴朗" in prompt_passed[1].content


@pytest.mark.anyio
async def test_summarize_conversation_history_timeout_fallback():
    messages = [
        HumanMessage(content="查询超长数据1"),
        AIMessage(content="回答1"),
        HumanMessage(content="查询超长数据2"),
        AIMessage(content="回答2"),
        HumanMessage(content="查询超长数据3"),
        AIMessage(content="回答3"),
        HumanMessage(content="查询超长数据4"),
        AIMessage(content="回答4"),
    ]

    mock_llm = AsyncMock()

    async def _mock_slow(*args, **kwargs):
        await asyncio.sleep(0.5)
        return AIMessage(content="超时结果")

    mock_llm.ainvoke.side_effect = _mock_slow

    with patch("common.conversation_summarizer.ModelRegistry.main_llm", mock_llm):
        # 设置超时 0.05 秒
        summary = await summarize_conversation_history(
            messages,
            keep_turns=2,
            timeout_seconds=0.05,
        )

        assert "【历史讨论要点】" in summary
        assert "查询超长数据1" in summary


def test_prepare_model_context_with_summary_injection():
    # 模拟 5 轮对话
    messages = [
        HumanMessage(content="轮次1"),
        AIMessage(content="回答1"),
        HumanMessage(content="轮次2"),
        AIMessage(content="回答2"),
        HumanMessage(content="轮次3"),
        AIMessage(content="回答3"),
        HumanMessage(content="轮次4"),
        AIMessage(content="回答4"),
        HumanMessage(content="轮次5"),
        AIMessage(content="回答5"),
    ]

    summary_text = "用户前期探讨了轮次1至轮次2的业务目标。"
    pruned = prepare_model_context(messages, summary=summary_text, keep_turns=3)

    # 期望：前置一条 SystemMessage 包含摘要，并且仅保留后 3 轮（轮次3, 4, 5）
    assert isinstance(pruned[0], SystemMessage)
    assert "早期对话历史摘要" in pruned[0].content
    assert summary_text in pruned[0].content

    # 检查保留的消息内容
    human_msgs = [m.content for m in pruned if isinstance(m, HumanMessage)]
    assert human_msgs == ["轮次3", "轮次4", "轮次5"]
    assert "轮次1" not in [m.content for m in pruned]
