"""显式图的统一发送前准备：投影、工具定义、计量、整理与重查.

所有业务模型发送点都经过这里，包括工具循环回来的请求、规划补充、最终回答
和最终清洗。压缩只改变这一次的模型输入；``state.messages`` 和成功覆盖位置
由调用方合并写回，完整历史始终保留。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage

from common.config import config
from common.context_guard import prepare_model_context
from common.conversation_summarizer import (
    pending_history,
    split_messages_by_turns,
    summarize_conversation_history,
)
from common.model_budget import ContextBudgetExceeded, fit_tool_schemas, request_metrics
from common.model_capacity import ModelCapacity, capacity_for_role
from common.model_errors import is_context_overflow
from common.runtime_events import emit_event


@dataclass
class PreparedRequest:
    """一次实际模型发送的输入、计量和需写回状态的变化."""

    messages: list[AnyMessage]
    schemas: list[Any]
    metrics: dict[str, Any]
    updates: dict[str, Any] = field(default_factory=dict)
    summary: str | None = None
    watermark: str | None = None
    kept_turns: int = 0
    compacted: bool = False
    within_capacity: bool = True
    failure_reason: str | None = None
    message_updates: list[AnyMessage] = field(default_factory=list)

    @property
    def over_capacity(self) -> bool:
        """本次输入是否仍超过有效输入边界."""
        return not self.within_capacity


class ContextRecoveryFailed(ContextBudgetExceeded):
    """终止本次逻辑调用，并保留已经成功整理的状态供节点写回."""

    def __init__(self, message: str, prepared: PreparedRequest) -> None:
        """关联终止原因和应持久化的请求状态."""
        super().__init__(message)
        self.prepared = prepared


def _role_capacity(role: str, capacity: ModelCapacity | None) -> ModelCapacity:
    return capacity or capacity_for_role("summary" if role == "summary" else "main")


def _build_request(
    *,
    messages: list[AnyMessage],
    summary: str | None,
    watermark: str | None,
    system_messages: list[str],
    tools: list[Any],
    tail_messages: list[AnyMessage],
    capacity: ModelCapacity,
) -> tuple[list[AnyMessage], list[Any], dict[str, Any]]:
    projected = prepare_model_context(
        messages,
        summary=summary,
        summary_covered_message_id=watermark,
    )
    prefix = [SystemMessage(content=text) for text in system_messages if text]
    full = [*prefix, *projected, *tail_messages]
    schemas = fit_tool_schemas(full, tools, capacity=capacity) if tools else []
    metrics = request_metrics(full, schemas, capacity=capacity)
    return full, schemas, metrics


async def prepare_model_request(
    state: dict[str, Any],
    run_config: dict[str, Any],
    *,
    system_messages: list[str] | None = None,
    tools: list[Any] | None = None,
    tail_messages: list[AnyMessage] | None = None,
    role: str = "main",
    force_compact: bool = False,
    capacity: ModelCapacity | None = None,
    allow_tool_governance: bool = True,
) -> PreparedRequest:
    """构造完整待发请求；达到窗口水位或容量边界时整理并重新计量."""
    messages = list(state.get("messages") or [])
    summary = state.get("summary")
    watermark = state.get("summary_covered_message_id")
    system_texts = list(system_messages or [])
    tool_list = list(tools or [])
    tail = list(tail_messages or [])
    resolved = _role_capacity(role, capacity)
    target_keep = max(1, config.CONVERSATION_SUMMARY_KEEP_TURNS)

    full, schemas, metrics = _build_request(
        messages=messages,
        summary=summary,
        watermark=watermark,
        system_messages=system_texts,
        tools=tool_list,
        tail_messages=tail,
        capacity=resolved,
    )
    updates: dict[str, Any] = {}

    def current_kept_turns() -> int:
        _, active = split_messages_by_turns(messages, target_keep)
        return sum(isinstance(message, HumanMessage) for message in active)

    over_capacity = metrics["input_tokens"] > metrics["input_budget_tokens"]
    trigger_reached = metrics["input_tokens"] >= metrics["summary_trigger_tokens"]
    if not force_compact and not over_capacity and not trigger_reached:
        return PreparedRequest(
            messages=full,
            schemas=schemas,
            metrics=metrics,
            summary=summary,
            watermark=watermark,
            kept_turns=current_kept_turns(),
            within_capacity=True,
        )

    candidates = [target_keep]
    if force_compact or over_capacity:
        candidates.extend(keep for keep in (2, 1) if keep < target_keep)
    candidates = list(dict.fromkeys(candidates))

    compacted = False
    failure_reason: str | None = None
    current_keep = target_keep
    status_payload: dict[str, Any] | None = None
    index = 0
    while index < len(candidates):
        keep_turns = candidates[index]
        index += 1
        early, active = split_messages_by_turns(messages, keep_turns)
        pending = pending_history(messages, watermark=watermark, keep_turns=keep_turns)
        if not early or not pending:
            continue
        if not early[-1].id:
            failure_reason = "历史消息缺少稳定 ID，不能安全推进覆盖"
            break

        emit_event(
            run_config,
            "context_status",
            stage="history",
            status="started",
            input_before=metrics["input_tokens"],
            window_tokens=metrics["window_tokens"],
            summary_trigger_ratio=metrics["summary_trigger_ratio"],
            summary_trigger_tokens=metrics["summary_trigger_tokens"],
            kept_turns=sum(isinstance(message, HumanMessage) for message in active),
            reason="forced" if force_compact else "window",
        )
        try:
            new_summary = await summarize_conversation_history(
                messages,
                summary,
                keep_turns=keep_turns,
                watermark=watermark,
                run_config=run_config,
            )
        except Exception as exc:
            failure_reason = f"历史整理未完成：{type(exc).__name__}"
            status_payload = emit_event(
                run_config,
                "context_status",
                stage="history",
                status="failed",
                message="历史整理未完成，尚未归纳的消息仍保留。",
                reason="forced" if force_compact else "window",
            )
            break

        before = metrics["input_tokens"]
        summary = new_summary
        watermark = str(early[-1].id)
        compacted = True
        current_keep = keep_turns
        full, schemas, metrics = _build_request(
            messages=messages,
            summary=summary,
            watermark=watermark,
            system_messages=system_texts,
            tools=tool_list,
            tail_messages=tail,
            capacity=resolved,
        )
        status_payload = emit_event(
            run_config,
            "context_status",
            stage="history",
            status="completed",
            input_before=before,
            input_after=metrics["input_tokens"],
            window_tokens=metrics["window_tokens"],
            summary_trigger_ratio=metrics["summary_trigger_ratio"],
            summary_trigger_tokens=metrics["summary_trigger_tokens"],
            kept_turns=sum(isinstance(message, HumanMessage) for message in active),
            measurement_source=metrics["measurement_source"],
            reason="forced" if force_compact else "window",
        )

        if metrics["input_tokens"] <= metrics["input_budget_tokens"]:
            break
        if metrics["input_tokens"] >= before:
            failure_reason = "整理未能减小输入，已停止继续重试"
            break
        for lower in (2, 1):
            if lower < keep_turns and lower not in candidates:
                candidates.append(lower)

    # 历史已无可整理范围时，再治理当前轮累计过大的工具结果。
    updated_messages: list[AnyMessage] = []
    if (
        allow_tool_governance
        and metrics["input_tokens"] > metrics["input_budget_tokens"]
    ):
        from common.turn_tool_governance import (
            apply_replacements,
            govern_current_turn_tools,
        )

        emit_event(
            run_config,
            "context_status",
            stage="data",
            status="started",
            message="当前轮资料累计过大，正在归档并整理。",
        )
        governed = await govern_current_turn_tools(messages, run_config)
        if governed:
            messages = apply_replacements(messages, governed)
            full, schemas, metrics = _build_request(
                messages=messages,
                summary=summary,
                watermark=watermark,
                system_messages=system_texts,
                tools=tool_list,
                tail_messages=tail,
                capacity=resolved,
            )
            updated_messages = list(governed)
            updates["data_status"] = emit_event(
                run_config,
                "context_status",
                stage="data",
                status="completed",
                message="当前轮资料已归档并整理。",
            )
        else:
            updates["data_status"] = emit_event(
                run_config,
                "context_status",
                stage="data",
                status="failed",
                message="当前轮资料无法在容量内完整整理，请缩小数据范围。",
            )

    kept_turns = sum(
        isinstance(message, HumanMessage)
        for message in split_messages_by_turns(messages, current_keep)[1]
    )
    within_capacity = metrics["input_tokens"] <= metrics["input_budget_tokens"]
    if not within_capacity and failure_reason is None:
        failure_reason = (
            "没有可安全整理的已完成轮次"
            if compacted
            else "当前请求仍超出模型可用输入容量"
        )

    if compacted:
        updates["summary"] = summary
        updates["summary_covered_message_id"] = watermark
    if status_payload is not None:
        updates["context_status"] = status_payload
    return PreparedRequest(
        messages=full,
        schemas=schemas,
        metrics=metrics,
        updates=updates,
        summary=summary,
        watermark=watermark,
        kept_turns=kept_turns,
        compacted=compacted,
        within_capacity=within_capacity,
        failure_reason=failure_reason,
        message_updates=updated_messages,
    )


def merge_prepared_updates(
    prepared: PreparedRequest, payload: dict[str, Any]
) -> dict[str, Any]:
    """把工具治理替换与节点本轮新增消息合并为一次 state 更新."""
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        messages = [messages]
    return {**payload, "messages": [*prepared.message_updates, *messages]}


async def call_with_context_recovery(
    invoke: Any,
    prepared: PreparedRequest,
    *,
    state: dict[str, Any],
    run_config: dict[str, Any],
    prepare_kwargs: dict[str, Any],
) -> tuple[Any, PreparedRequest]:
    """服务商明确超窗后整理一次并只重发失败模型调用.

    工具、受理和整图都不会重放；摘要与恢复发送前重新核对运行身份。
    """
    try:
        return await invoke(prepared), prepared
    except Exception as exc:  # noqa: BLE001 - 仅超窗进入恢复
        if not is_context_overflow(exc):
            raise

    from common.identity.runtime import guard_runtime_call

    await guard_runtime_call(dict(run_config or {}))
    recovered = await prepare_context_recovery(
        prepared, state=state, run_config=run_config, prepare_kwargs=prepare_kwargs
    )
    await guard_runtime_call(dict(run_config or {}))
    try:
        return await invoke(recovered), recovered
    except Exception as exc:
        message = (
            "自动整理后模型上下文仍超限，本次恢复已停止。"
            if is_context_overflow(exc)
            else "自动整理后的模型请求未完成，本次恢复已停止。"
        )
        raise ContextRecoveryFailed(message, recovered) from exc


async def prepare_context_recovery(
    prepared: PreparedRequest,
    *,
    state: dict[str, Any],
    run_config: dict[str, Any],
    prepare_kwargs: dict[str, Any],
) -> PreparedRequest:
    """从已发送的投影继续整理，只允许严格减小后的输入进入恢复发送."""
    from common.turn_tool_governance import apply_replacements

    recovery_state = {
        **state,
        **prepared.updates,
        "messages": apply_replacements(
            list(state.get("messages") or []), prepared.message_updates
        ),
    }
    recovered = await prepare_model_request(
        recovery_state, run_config, force_compact=True, **prepare_kwargs
    )
    recovered.updates = {**prepared.updates, **recovered.updates}
    replacements = {
        message.id: message
        for message in [*prepared.message_updates, *recovered.message_updates]
    }
    recovered.message_updates = list(replacements.values())
    if recovered.over_capacity:
        raise ContextRecoveryFailed(
            "自动整理后仍超出模型可用输入容量，本次恢复已停止。", recovered
        )
    if recovered.metrics["input_tokens"] >= prepared.metrics["input_tokens"]:
        raise ContextRecoveryFailed(
            "没有可继续减小输入的安全整理范围，本次恢复已停止。", recovered
        )
    return recovered
