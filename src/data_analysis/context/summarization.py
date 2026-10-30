"""按完整轮次和成功覆盖水位管理会话摘要，完整聊天历史保持不变.

触发不由“是否已有摘要”决定：调用方先按当前模型窗口比例判断完整待发请求，
达到水位后才进入这里。摘要只覆盖水位之后、最近若干完整轮次之前的原文。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AnyMessage, HumanMessage

from common.config import config
from common.text_reduction import summarize_complete_text
from data_analysis.context.projection import covered_index


def split_messages_by_turns(
    messages: Sequence[AnyMessage], keep_turns: int = 3
) -> tuple[list[AnyMessage], list[AnyMessage]]:
    """按用户消息边界划分完整轮次."""
    if keep_turns < 1:
        raise ValueError("至少保留一轮")
    human_indices = [
        i for i, message in enumerate(messages) if isinstance(message, HumanMessage)
    ]
    if len(human_indices) <= keep_turns:
        return [], list(messages)
    split_idx = human_indices[-keep_turns]
    return list(messages[:split_idx]), list(messages[split_idx:])


def pending_history(
    messages: Sequence[AnyMessage],
    *,
    watermark: str | None,
    keep_turns: int,
) -> list[AnyMessage]:
    """返回尚未被成功摘要覆盖、且早于保留轮次的原文."""
    early, _ = split_messages_by_turns(messages, keep_turns)
    return early[covered_index(messages, watermark) + 1 :]


def _format_messages_for_prompt(messages: Sequence[AnyMessage]) -> str:
    # 全量序列化模型可见内容，不能只取工具前 200 字符或忽略调用参数。
    return "\n".join(
        json.dumps(
            {
                "role": message.type,
                "content": message.content,
                "tool_calls": getattr(message, "tool_calls", []),
                "tool_call_id": getattr(message, "tool_call_id", None),
                "status": getattr(message, "status", None),
            },
            ensure_ascii=False,
            default=str,
        )
        for message in messages
    )


async def summarize_conversation_history(
    messages: Sequence[AnyMessage],
    existing_summary: str | None = None,
    *,
    keep_turns: int | None = None,
    timeout_seconds: float | None = None,
    watermark: str | None = None,
    run_config: dict[str, Any] | None = None,
) -> str:
    """全量归纳水位之后的历史，失败向上传播."""
    pending = pending_history(
        messages,
        watermark=watermark,
        keep_turns=keep_turns or config.CONVERSATION_SUMMARY_KEEP_TURNS,
    )
    if not pending:
        return existing_summary or ""
    text = f"先前摘要：\n{existing_summary or ''}\n新历史：\n{_format_messages_for_prompt(pending)}"
    result, _ = await summarize_complete_text(
        text,
        instruction="整合会话背景、用户约束、已确认事实及引用，保留新历史完整语义。",
        timeout_seconds=timeout_seconds,
        run_config=run_config,
    )
    return result
