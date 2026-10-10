"""多轮纯文本长会话大模型渐进式摘要压缩工具.

解决超长多轮纯对话场景下上下文不断累加导致挤爆模型窗口的痛点。
在模型推理前，当早期历史超出设定阈值时，自动调度 LLM 将早于最近活跃轮次（默认保留 3 轮）
的历史对话提炼为滚动全局摘要，并持久化到状态中。
"""

from __future__ import annotations

import asyncio
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
from common.models import ModelRegistry

_CONVERSATION_SUMMARIZER_SYSTEM_PROMPT = """你是一个专业的长会话摘要与上下文记忆压缩专家。
你的任务是将多轮对话中较早时期的历史记录提炼为紧凑、高密度的全局会话背景摘要。

请遵循以下核心原则：
1. 核心意图与背景：提炼用户在早期讨论的主要需求、关注的目标实体（如特定城市、站点、因子、时间范围）和业务目标；
2. 关键业务结论：保留此前轮次已确认的重要数据结论、分析判断或共识，剔除冗余的寒暄和格式过渡；
3. 滚雪球融合：若已存在先前的历史摘要，将待压缩的新记录自然融进旧摘要中，形成一份统一、连贯的全局摘要；
4. 极致精炼：输出纯文本段落或精炼要点，字数严格控制在 800 字符以内，绝不输出无关废话。"""


def _estimate_tokens(messages: Sequence[AnyMessage]) -> int:
    """估算消息列表的大致 Token 数量（中英文加权估算）."""
    total_chars = 0
    for m in messages:
        content = getattr(m, "content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            total_chars += sum(len(str(item)) for item in content)
        else:
            total_chars += len(str(content or ""))
    return int(total_chars * 0.55)


def split_messages_by_turns(
    messages: Sequence[AnyMessage],
    keep_turns: int = 3,
) -> tuple[list[AnyMessage], list[AnyMessage]]:
    """以 HumanMessage 为边界，将消息序列划分为【早期待压缩消息】与【近期活跃保留消息】.

    Args:
        messages: 全量消息序列
        keep_turns: 保留最近不压缩的完整轮次数量（默认 3 轮）

    Returns:
        (early_messages, active_messages)
    """
    if not messages:
        return [], []

    # 找到所有 HumanMessage 的索引位置
    human_indices = [
        i for i, m in enumerate(messages) if isinstance(m, HumanMessage)
    ]

    # 如果轮次不足 keep_turns，全部算作活跃轮次
    if len(human_indices) <= keep_turns:
        return [], list(messages)

    # 切分边界：倒数第 keep_turns 个 HumanMessage 的位置
    split_idx = human_indices[-keep_turns]

    early_messages = list(messages[:split_idx])
    active_messages = list(messages[split_idx:])
    return early_messages, active_messages


def should_summarize_conversation(
    messages: Sequence[AnyMessage],
    *,
    keep_turns: int | None = None,
    threshold_tokens: int | None = None,
) -> bool:
    """检查是否需要对当前会话早期历史执行大模型摘要压缩.

    规则：
    1. 活跃轮次必须大于 keep_turns（默认 3）；
    2. 早期待压缩消息的预估 Token 超过 threshold_tokens（默认 30,000）时触发。
    """
    turns_limit = keep_turns or getattr(
        config, "CONVERSATION_SUMMARY_KEEP_TURNS", 3
    )
    tokens_limit = threshold_tokens or getattr(
        config, "CONVERSATION_SUMMARY_THRESHOLD_TOKENS", 30_000
    )

    early_messages, _ = split_messages_by_turns(messages, keep_turns=turns_limit)
    if not early_messages:
        return False

    early_tokens = _estimate_tokens(early_messages)
    return early_tokens >= tokens_limit


def _format_messages_for_prompt(messages: Sequence[AnyMessage]) -> str:
    """将消息列表格式化为简洁易读的文本记录用于 Prompt."""
    lines: list[str]
    lines = []
    for m in messages:
        if isinstance(m, HumanMessage):
            lines.append(f"用户: {m.content}")
        elif isinstance(m, AIMessage):
            # 仅保留文本回复，略去复杂的 tool_calls
            content = str(m.content or "")
            if content:
                lines.append(f"助手: {content}")
        elif isinstance(m, ToolMessage):
            # 工具结果只简写，不把内部超大数据全量打印到 Prompt 里
            content_str = str(m.content or "")
            summary_preview = content_str[:200] + ("..." if len(content_str) > 200 else "")
            lines.append(f"[工具返回 ({getattr(m, 'name', 'tool')}): {summary_preview}]")
    return "\n".join(lines)


def _build_summarizer_prompt(
    existing_summary: str | None,
    early_messages: Sequence[AnyMessage],
) -> str:
    """组装摘要提示词."""
    messages_text = _format_messages_for_prompt(early_messages)
    existing_part = (
        f"## 先前的历史对话摘要\n{existing_summary.strip()}\n\n"
        if existing_summary and existing_summary.strip()
        else ""
    )
    return (
        f"{existing_part}"
        f"## 待归纳压缩的新一阶段早期对话记录\n"
        f"{messages_text}\n\n"
        f"请根据上述记录，更新并输出一份精准、连贯的全局会话背景摘要（800 字符内）："
    )


def _fallback_summary(existing_summary: str | None, early_messages: Sequence[AnyMessage]) -> str:
    """超时或异常时的轻量降级摘要."""
    human_queries = [
        str(m.content)[:50] for m in early_messages if isinstance(m, HumanMessage)
    ]
    queries_summary = "；".join(human_queries[:5])
    prefix = f"{existing_summary}\n" if existing_summary else ""
    return f"{prefix}【历史讨论要点】用户早期先后咨询了：{queries_summary}。"


async def summarize_conversation_history(
    messages: Sequence[AnyMessage],
    existing_summary: str | None = None,
    *,
    keep_turns: int | None = None,
    timeout_seconds: float = 20.0,
) -> str:
    """调度大模型对早期对话历史进行滚动摘要提炼.

    Args:
        messages: 当前全量消息列表
        existing_summary: 既有摘要（若已有）
        keep_turns: 保留活跃轮次数（默认 3）
        timeout_seconds: 超时时间（秒）

    Returns:
        提炼更新后的全局会话摘要字符串
    """
    turns_limit = keep_turns or getattr(
        config, "CONVERSATION_SUMMARY_KEEP_TURNS", 3
    )
    early_messages, _ = split_messages_by_turns(messages, keep_turns=turns_limit)

    if not early_messages:
        return existing_summary or ""

    user_prompt = _build_summarizer_prompt(existing_summary, early_messages)
    prompt_messages = [
        SystemMessage(content=_CONVERSATION_SUMMARIZER_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    logger.info(
        "触发大模型会话摘要压缩: 早期待压缩消息数={}, 预估Token={}, 已有摘要长度={}",
        len(early_messages),
        _estimate_tokens(early_messages),
        len(existing_summary or ""),
    )

    # 确定模型实例 (优先复用 main_llm)
    model = ModelRegistry.main_llm

    try:
        response = await asyncio.wait_for(
            model.ainvoke(prompt_messages),
            timeout=timeout_seconds,
        )
        content = getattr(response, "content", "")
        summary_text = str(content).strip()
        if not summary_text:
            return _fallback_summary(existing_summary, early_messages)

        logger.info(
            "大模型会话摘要生成成功: 新摘要字数={}",
            len(summary_text),
        )
        return summary_text
    except Exception as exc:
        logger.warning(
            "大模型会话摘要生成失败或超时，自动使用降级摘要: error={}",
            exc,
        )
        return _fallback_summary(existing_summary, early_messages)
