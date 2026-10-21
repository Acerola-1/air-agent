"""业务图终答身份与流事件契约."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage

from common.runtime_events import emit_event, event_payload


def visible_result(
    response: AIMessage,
    run_config: dict[str, Any],
    *,
    metrics: dict[str, Any] | None = None,
    publish: bool = True,
    writer: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """标记唯一终答并持久化调用指标."""
    visible = publish and not response.tool_calls
    if visible and response.response_metadata.get("finish_reason") in {
        "length",
        "content_filter",
    }:
        response = response.model_copy(
            update={"content": "模型输出未完整生成，请缩小范围或重试。"}
        )
    if visible and isinstance(response.content, str):
        text = re.sub(r"<think>[\s\S]*?</think>", "", response.content).strip()
        text = re.sub(r"<think>[\s\S]*$", "", text).strip()
        try:
            payload = json.loads(text)
            if isinstance(payload, dict) and (
                payload.get("success") is False
                or str(payload.get("code", "")).isdigit()
                and int(payload["code"]) >= 400
            ):
                text = "查询未完成，请调整条件或稍后重试。"
        except (ValueError, TypeError):
            pass
        response = response.model_copy(
            update={"content": text or "未生成完整答案，请重试。"}
        )
    response = response.model_copy(
        update={
            "id": response.id or str(uuid.uuid4()),
            "additional_kwargs": {**response.additional_kwargs, "ui_visible": visible},
        }
    )
    # 新协议输出已在流式发布器中完成 start/delta/done；旧调用方仍走 done-only。
    already_streamed = bool(
        response.additional_kwargs.get("output_protocol_version")
    )
    if visible and not already_streamed:
        payload = event_payload(
            run_config,
            "final_output_done",
            message=response.content,
            message_id=response.id,
        )
        if writer is not None:
            writer(payload)
        else:
            emit_event(
                run_config,
                "final_output_done",
                message=response.content,
                message_id=response.id,
            )
    result: dict[str, Any] = {"messages": [response]}
    if metrics is not None:
        usage = response.usage_metadata or {}
        result["context_metrics"] = {**metrics, "latest_usage": usage}
        emit_event(run_config, "context_metrics", **result["context_metrics"])
    return result
