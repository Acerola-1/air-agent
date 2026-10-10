"""模型调用前只读上下文修剪单元测试."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from common.context_guard import (
    DEFAULT_TOOL_CLEAR_PLACEHOLDER,
    prepare_model_context,
)


def test_prepare_model_context_under_budget():
    """未超预算时应原样返回，不发生修剪."""
    messages = [
        HumanMessage(content="你好"),
        AIMessage(content="你好！请问有什么可以帮助您的？"),
    ]
    result = prepare_model_context(messages, budget_tokens=50000)
    assert len(result) == 2
    assert result[0].content == "你好"
    assert result[1].content == "你好！请问有什么可以帮助您的？"


def test_prepare_model_context_current_turn_immunity():
    """当前正在进行的轮次工具结果享有绝对豁免权，即便超预算也不可修剪."""
    # 只有一个单轮对话，工具调用在最新 HumanMessage 之后
    messages = [
        HumanMessage(content="查询北京空气质量"),
        AIMessage(
            content="",
            tool_calls=[{"id": "call_curr_01", "name": "get_air", "args": {"city": "北京"}}],
        ),
        ToolMessage(
            content="A" * 50000,
            tool_call_id="call_curr_01",
            name="get_air",
        ),
    ]
    # 设置很低的预算
    result = prepare_model_context(messages, budget_tokens=100)
    assert len(result) == 3
    # 当期轮次工具结果必须完整保留
    assert result[2].content == "A" * 50000


def test_prepare_model_context_prunes_historical_tools_only():
    """多轮会话超预算时，仅修剪历史已完成轮次的旧工具，保留当期轮次."""
    history_tool_call_id = "call_hist_01"
    curr_tool_call_id = "call_curr_02"

    messages = [
        # 第 1 轮（历史轮次）
        HumanMessage(content="昨天的天气怎么样？"),
        AIMessage(
            content="",
            tool_calls=[
                {"id": history_tool_call_id, "name": "get_history_weather", "args": {"day": "yesterday"}}
            ],
        ),
        ToolMessage(
            content="HISTORICAL_LARGE_DATA_" * 1000,
            tool_call_id=history_tool_call_id,
            name="get_history_weather",
        ),
        AIMessage(content="昨天天气多云转晴，气温适中。"),
        # 第 2 轮（当前正在进行的轮次）
        HumanMessage(content="那今天呢？"),
        AIMessage(
            content="",
            tool_calls=[{"id": curr_tool_call_id, "name": "get_today_weather", "args": {"day": "today"}}],
        ),
        ToolMessage(
            content="TODAY_CRITICAL_DATA_KEEP_THIS",
            tool_call_id=curr_tool_call_id,
            name="get_today_weather",
        ),
    ]

    # 设低预算触发修剪
    result = prepare_model_context(messages, budget_tokens=500)

    # 1. 消息总数保持不变
    assert len(result) == len(messages)

    # 2. 历史工具消息被修剪为占位符
    assert result[2].content == DEFAULT_TOOL_CLEAR_PLACEHOLDER
    assert result[2].tool_call_id == history_tool_call_id

    # 3. 历史 AIMessage 的 tool_call 参数被清空为 {}，但保留 call id
    hist_ai = result[1]
    assert hist_ai.tool_calls[0]["id"] == history_tool_call_id
    assert hist_ai.tool_calls[0]["args"] == {}

    # 4. 当前轮次的工具消息 100% 完整保留，绝无误裁！
    assert result[6].content == "TODAY_CRITICAL_DATA_KEEP_THIS"
    assert result[6].tool_call_id == curr_tool_call_id

    # 5. 验证原始 messages 列表未被污染（纯函数只读保证）
    assert messages[2].content.startswith("HISTORICAL_LARGE_DATA_")
    assert messages[1].tool_calls[0]["args"] == {"day": "yesterday"}
