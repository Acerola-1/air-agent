"""当前轮多个较小工具结果累计过大时的有界资料治理.

单工具阈值在入口已处理过一次；本模块只处理“每个都不大、累计却超过阈值”的
情况。复用既有归档、结构化 manifest/受限计算和非结构化全量摘要，不重新执行
工具，也不把局部截断冒充完整数据。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from loguru import logger

from common.artifact_scope import runtime_scope
from common.business_graph.subagent_summarizer import summarize_large_tool_output
from common.config import config
from common.context import get_message_content
from common.data_manifest import build_manifest
from common.storage.artifact_db import save_tool_artifact


def _latest_question(messages: Sequence[AnyMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return get_message_content(message)
    return ""


def _tool_args(messages: Sequence[AnyMessage]) -> dict[str, dict[str, Any]]:
    args: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or []:
            call_id = str(call.get("id") or "")
            if call_id:
                args[call_id] = dict(call.get("args") or {})
    return args


def _stored(message: ToolMessage) -> bool:
    artifact = message.artifact if isinstance(message.artifact, dict) else {}
    return artifact.get("storage_status") == "stored"


async def govern_current_turn_tools(
    messages: list[AnyMessage],
    run_config: dict[str, Any],
    *,
    max_items: int = 8,
    min_chars: int = 2000,
) -> list[ToolMessage] | None:
    """返回需要替换的工具消息；无收益或失败时返回 None，不修改原历史."""
    last_human = max(
        (
            index
            for index, message in enumerate(messages)
            if isinstance(message, HumanMessage)
        ),
        default=-1,
    )
    turn = messages[last_human + 1 :]
    tools = [
        message
        for message in turn
        if isinstance(message, ToolMessage)
        and isinstance(message.content, str)
        and not _stored(message)
        and len(message.content) >= min_chars
    ]
    total = sum(
        len(message.content)
        for message in turn
        if isinstance(message, ToolMessage) and isinstance(message.content, str)
    )
    if total <= config.TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS or not tools:
        return None

    try:
        tenant_id, user_id, thread_id, run_id = runtime_scope(run_config)
    except Exception:
        return None

    question = _latest_question(messages)
    args_by_call = _tool_args(messages)
    replacements: list[ToolMessage] = []
    for message in tools[:max_items]:
        raw = str(message.content)
        try:
            artifact_id = await save_tool_artifact(
                tool_call_id=message.tool_call_id,
                tool_name=str(message.name or "unknown_tool"),
                raw_payload=raw,
                tenant_id=tenant_id,
                user_id=user_id,
                thread_id=thread_id,
                run_id=run_id,
            )
            manifest = build_manifest(raw, artifact_id)
            if manifest is not None:
                reduced = json.dumps(manifest, ensure_ascii=False)
            else:
                reduced, _coverage = await summarize_large_tool_output(
                    user_question=question,
                    tool_name=str(message.name or "unknown_tool"),
                    tool_args=args_by_call.get(message.tool_call_id, {}),
                    raw_output=raw,
                    run_config=dict(run_config or {}),
                )
        except Exception as exc:  # noqa: BLE001 - 治理失败不伪造替代内容
            logger.warning(
                "当前轮工具结果累计治理失败: {}: {}",
                type(exc).__name__,
                str(exc)[:120],
            )
            return None
        if len(reduced) >= len(raw):
            continue
        replacements.append(
            message.model_copy(
                update={
                    "content": reduced,
                    "artifact": {
                        "storage_type": "database",
                        "storage_status": "stored",
                        "artifact_id": artifact_id,
                        "tool_call_id": message.tool_call_id,
                        "char_length": len(raw),
                    },
                }
            )
        )
        total -= len(raw) - len(reduced)
        if total <= config.TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS:
            break
    return replacements or None


def apply_replacements(
    messages: list[AnyMessage], replacements: Sequence[ToolMessage]
) -> list[AnyMessage]:
    """按消息 ID 替换治理后的工具结果，保持原有顺序."""
    replaced = {message.id: message for message in replacements if message.id}
    return [replaced.get(message.id, message) for message in messages]
