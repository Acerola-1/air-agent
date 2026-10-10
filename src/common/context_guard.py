"""模型调用前只读上下文修剪工具.

在 call_model 节点将消息发送给大模型推理之前执行只读修剪，
严格控制每次模型调用的 Token Budget，防止长多轮会话累积爆窗。

黄金法则：
1. 当期轮次绝对豁免：当前轮次（最后一条 HumanMessage 之后）产生的所有工具结果 100% 完整保留；
2. 历史轮次载荷置换：仅当全量消息超过安全预算时，将历史已完成轮次的旧工具内容置换为简短占位符；
3. tool_call_id 协议闭合：保留消息对象与对应关系，仅清空历史载荷与工具调用参数；
4. 纯函数只读保证：绝不修改 Graph State 中的真实消息与 Checkpointer 状态。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Sequence

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from loguru import logger

from common.config import config
from common.conversation_summarizer import split_messages_by_turns

DEFAULT_TOOL_CLEAR_PLACEHOLDER = "[历史工具调用数据已由上文结论归纳，详细数据已归档]"


def _estimate_tokens(messages: Sequence[AnyMessage]) -> int:
    """快速估算消息列表的大致 Token 数量（按字符数与中英文加权估算）."""
    total_chars = 0
    for m in messages:
        content = getattr(m, "content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            total_chars += sum(len(str(item)) for item in content)
        else:
            total_chars += len(str(content or ""))
    # 中文字符通常 1 char ≈ 0.6 token，加权安全系数设为 0.55
    return int(total_chars * 0.55)


def prepare_model_context(
    messages: Sequence[AnyMessage],
    *,
    summary: str | None = None,
    keep_turns: int | None = None,
    budget_tokens: int | None = None,
    placeholder: str = DEFAULT_TOOL_CLEAR_PLACEHOLDER,
) -> list[AnyMessage]:
    """对即将送入模型的上下文执行只读修剪.

    Args:
        messages: 原始消息序列（来自 state["messages"]）
        summary: 早期对话的大模型全局滚动摘要（来自 state.get("summary")）
        keep_turns: 保留活跃轮次数（默认 config.CONVERSATION_SUMMARY_KEEP_TURNS）
        budget_tokens: 模型安全预算 Tokens（默认为 config.MODEL_CONTEXT_BUDGET_TOKENS）
        placeholder: 历史工具结果被修剪后的占位说明文本

    Returns:
        修剪后的消息列表副本（若未超限则原样返回浅拷贝）
    """
    if not messages:
        return []

    # 1. 若存在早期历史摘要，将已摘要的早期轮次置换，仅保留活跃轮次 + 摘要背景
    active_base: list[AnyMessage]
    prefix_messages: list[AnyMessage] = []

    if summary and summary.strip():
        turns_limit = keep_turns or getattr(
            config, "CONVERSATION_SUMMARY_KEEP_TURNS", 3
        )
        early_messages, active_messages = split_messages_by_turns(
            messages, keep_turns=turns_limit
        )
        if early_messages:
            prefix_messages.append(
                SystemMessage(content=f"## 早期对话历史摘要\n{summary.strip()}")
            )
            active_base = list(active_messages)
        else:
            active_base = list(messages)
    else:
        active_base = list(messages)

    max_budget = budget_tokens or getattr(
        config, "MODEL_CONTEXT_BUDGET_TOKENS", 120_000
    )
    current_tokens = _estimate_tokens(prefix_messages) + _estimate_tokens(active_base)

    # 未超限，直接拼接放行
    if current_tokens <= max_budget:
        return [*prefix_messages, *active_base]

    # 2. 若依然超限，制作只读深拷贝副本，对活跃轮次中的历史工具做进一步修剪
    pruned = deepcopy(active_base)

    # 1. 定位最后一条 HumanMessage 的索引，划分轮次边界
    last_human_idx = -1
    for i in range(len(pruned) - 1, -1, -1):
        if isinstance(pruned[i], HumanMessage):
            last_human_idx = i
            break

    if last_human_idx <= 0:
        # 没有历史轮次（当前就是第一轮），当期轮次不可修剪
        return pruned

    # 2. 仅对历史轮次（index < last_human_idx）中的 ToolMessage 寻找修剪候选
    history_tool_indices = [
        i
        for i in range(last_human_idx)
        if isinstance(pruned[i], ToolMessage)
    ]

    cleared_count = 0
    for idx in history_tool_indices:
        tool_msg = pruned[idx]
        current_content = str(tool_msg.content or "")
        # 如果已经被清理过，跳过
        if current_content == placeholder:
            continue

        # 2.1 置换 ToolMessage 内容为占位符，置空 artifact
        pruned[idx] = tool_msg.model_copy(
            update={
                "content": placeholder,
                "artifact": None,
            }
        )
        cleared_count += 1

        # 2.2 同步清空对应历史 AIMessage 中工具调用的 args 参数
        for prev_idx in range(idx - 1, -1, -1):
            ai_msg = pruned[prev_idx]
            if isinstance(ai_msg, AIMessage) and ai_msg.tool_calls:
                new_calls = []
                matched = False
                for tc in ai_msg.tool_calls:
                    if tc.get("id") == tool_msg.tool_call_id:
                        tc_copy = dict(tc)
                        tc_copy["args"] = {}  # 抹去历史参数
                        new_calls.append(tc_copy)
                        matched = True
                    else:
                        new_calls.append(tc)
                if matched:
                    pruned[prev_idx] = ai_msg.model_copy(
                        update={"tool_calls": new_calls}
                    )
                    break

        # 检查修剪后是否已降到预算内，若已安全则可停止清理
        if _estimate_tokens(pruned) <= max_budget:
            break

    if cleared_count > 0:
        logger.info(
            "模型上下文只读修剪完成: 原始Token约={}, 修剪后Token约={}, 清理历史工具数={}",
            current_tokens,
            _estimate_tokens(pruned),
            cleared_count,
        )

    return [*prefix_messages, *pruned]
