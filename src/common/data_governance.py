"""把模型代码生成也纳入资料整理阶段的总时限."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from common.artifact_scope import runtime_scope
from common.config import config
from common.storage.artifact_db import ComputationLimit, claim_computation


class DataProcessingFailure(ValueError):
    """完整资料处理尚未完成，不能转为局部统计."""


def pending_artifacts(messages: Sequence[AnyMessage]) -> set[str]:
    """只检查本轮已归档但尚未完成计算的结构化资料."""
    last_human = max(
        (
            index
            for index, message in enumerate(messages)
            if isinstance(message, HumanMessage)
        ),
        default=-1,
    )
    pending = set()
    for message in messages[last_human + 1 :]:
        if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
            continue
        try:
            payload = json.loads(message.content)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("kind") == "structured_data" and isinstance(
            payload.get("artifact_id"), str
        ):
            archive = message.artifact if isinstance(message.artifact, dict) else {}
            if (
                archive.get("storage_status") == "stored"
                and archive.get("artifact_id") == payload["artifact_id"]
            ):
                pending.add(payload["artifact_id"])
        if (
            message.name == "compute_artifact"
            and payload.get("success") is False
            and isinstance(payload.get("source_artifact_id"), str)
        ):
            pending.add(payload["source_artifact_id"])
        if message.name == "compute_artifact" and payload.get("complete") is True:
            pending.discard(payload.get("source_artifact_id", ""))
    return pending


async def invoke_with_data_deadline(
    model: Any,
    input_messages: list[AnyMessage],
    state_messages: Sequence[AnyMessage],
    run_config: dict[str, Any],
) -> AIMessage:
    """待计算资料的生成、一次修正与执行共享 90 秒阶段预算."""
    pending = pending_artifacts(state_messages)
    if not pending:
        invocation = model.ainvoke(
            input_messages, config={"tags": ["nostream", "internal-draft"]}
        )
        has_archive = any(
            isinstance(message, ToolMessage)
            and isinstance(message.artifact, dict)
            and message.artifact.get("storage_status") == "stored"
            for message in input_messages
        )
        if not has_archive:
            return await invocation
        try:
            return await asyncio.wait_for(
                invocation, timeout=config.DATA_PROCESSING_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            raise DataProcessingFailure(
                "资料处理的模型阶段超时，尚未完成。请重试。"
            ) from exc
    try:
        tenant_id, user_id, _, run_id = runtime_scope(run_config)
        remaining = []
        for artifact_id in pending:
            claim = await claim_computation(
                artifact_id,
                run_id,
                tenant_id=tenant_id,
                user_id=user_id,
                attempt=False,
            )
            remaining.append(claim["remaining_seconds"])
        # F02：claim 循环可能耗时，草稿生成外呼前再复查一次运行身份
        from common.identity.runtime import guard_runtime_call

        await guard_runtime_call(dict(run_config or {}))
        response = await asyncio.wait_for(
            model.ainvoke(
                input_messages, config={"tags": ["nostream", "internal-draft"]}
            ),
            timeout=min(remaining),
        )
    except (TimeoutError, ComputationLimit, PermissionError) as exc:
        raise DataProcessingFailure(
            "资料整理阶段未完成，尚无完整统计结果。请重新查询或缩小范围。"
        ) from exc
    if not response.tool_calls:
        # 尚未计算时，模型的文字不能代替完整数据依据。
        return AIMessage(
            content="完整资料已归档，但尚未完成全量计算，暂时无法提供可靠统计。请重试。"
        )
    return response
