"""以完整轮次和覆盖水位管理会话摘要，完整聊天历史保持不变."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage

from common.config import config
from common.context_guard import covered_index, prepare_model_context
from common.model_budget import (
    estimate_request_tokens,
    fit_tool_schemas,
    request_metrics,
)
from common.runtime_events import emit_event
from common.text_reduction import summarize_complete_text


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


def should_summarize_conversation(
    messages: Sequence[AnyMessage],
    *,
    keep_turns: int | None = None,
    threshold_tokens: int | None = None,
    watermark: str | None = None,
) -> bool:
    """检查尚未覆盖的历史是否需要摘要."""
    early, _ = split_messages_by_turns(
        messages, keep_turns or config.CONVERSATION_SUMMARY_KEEP_TURNS
    )
    pending = early[covered_index(messages, watermark) + 1 :]
    tokens, _ = estimate_request_tokens(
        pending,
        model_name=config.SUMMARY_LLM or config.MAIN_LLM_MODEL,
        tokenizer_encoding=config.SUMMARY_TOKENIZER_ENCODING,
    )
    return bool(pending) and (
        bool(watermark)
        or tokens >= (threshold_tokens or config.CONVERSATION_SUMMARY_THRESHOLD_TOKENS)
    )


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
    early, _ = split_messages_by_turns(
        messages, keep_turns or config.CONVERSATION_SUMMARY_KEEP_TURNS
    )
    pending = early[covered_index(messages, watermark) + 1 :]
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


async def update_conversation_summary(
    state: dict[str, Any], run_config: dict[str, Any], *, tools: Sequence[Any] = ()
) -> dict[str, Any]:
    """所有意图共用，失败保留旧摘要和水位，状态可在刷新后恢复."""
    messages = state.get("messages", [])
    watermark = state.get("summary_covered_message_id")
    if not should_summarize_conversation(messages, watermark=watermark):
        return {}
    early, active = split_messages_by_turns(
        messages, config.CONVERSATION_SUMMARY_KEEP_TURNS
    )
    prefix = (
        [SystemMessage(content=state["system_prompt"])]
        if state.get("system_prompt")
        else []
    )
    before_messages = [
        *prefix,
        *prepare_model_context(
            messages, summary=state.get("summary"), summary_covered_message_id=watermark
        ),
    ]
    before = request_metrics(before_messages, fit_tool_schemas(before_messages, tools))[
        "input_tokens"
    ]
    emit_event(
        run_config,
        "context_status",
        stage="history",
        status="started",
        input_before=before,
    )
    try:
        if not early[-1].id:
            raise ValueError("历史消息缺少稳定 ID，不能安全推进覆盖")
        summary = await summarize_conversation_history(
            messages,
            state.get("summary"),
            watermark=watermark,
            run_config=run_config,
        )
        after_messages = [
            *prefix,
            *prepare_model_context(
                messages, summary=summary, summary_covered_message_id=early[-1].id
            ),
        ]
        schemas = fit_tool_schemas(after_messages, tools)
        before = request_metrics(before_messages, schemas)["input_tokens"]
        after_metrics = request_metrics(after_messages, schemas)
        after, source = (
            after_metrics["input_tokens"],
            after_metrics["measurement_source"],
        )
        status = emit_event(
            run_config,
            "context_status",
            stage="history",
            status="completed",
            input_before=before,
            input_after=after,
            kept_turns=sum(isinstance(m, HumanMessage) for m in active),
            measurement_source=source,
        )
        return {
            "summary": summary,
            "summary_covered_message_id": early[-1].id,
            "context_status": status,
        }
    except Exception:
        status = emit_event(
            run_config,
            "context_status",
            stage="history",
            status="failed",
            message="历史整理未完成，尚未归纳的消息仍保留。",
        )
        return {"context_status": status}
