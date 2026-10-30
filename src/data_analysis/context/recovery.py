"""数据分析图的一次超窗恢复与状态合并."""

from __future__ import annotations

from typing import Any

from common.model_errors import is_context_overflow
from data_analysis.context.preparation import (
    ContextRecoveryFailed,
    PreparedRequest,
    prepare_model_request,
)


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
    from data_analysis.context.tool_results import apply_replacements

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
