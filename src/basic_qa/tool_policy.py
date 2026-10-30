"""Basic QA per-call tool and disclosure policy."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from basic_qa.tool_recovery import exhausted_tools
from basic_qa.tool_runtime import composed_tool_wrapper
from common.observability.context import current_observation_config
from common.tool_execution.mcp import _bind_tool_instance, _is_mcp_tool


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _model_call_binding(request: Any) -> tuple[AIMessage, str, dict[str, Any]]:
    """核对真实模型调用与当次工具定义记录，不读取 Skill 或激活状态."""
    from basic_qa.capabilities.tool_definitions import mcp_stable_id
    from common.mcp_authorization import McpAuthorizationError

    state = getattr(request, "state", None)
    call = getattr(request, "tool_call", None)
    if not isinstance(state, dict) or not isinstance(call, dict):
        raise McpAuthorizationError("缺少可信工具调用上下文")
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    name, call_id = call.get("name"), call.get("id")
    if not isinstance(last, AIMessage) or not last.id or not name or not call_id:
        raise McpAuthorizationError("缺少可信模型工具调用")
    if not isinstance(call.get("args"), dict) or not any(
        item.get("id") == call_id
        and item.get("name") == name
        and _canonical(item.get("args")) == _canonical(call["args"])
        for item in last.tool_calls
    ):
        raise McpAuthorizationError("工具调用与模型消息不一致")
    record = state.get("bqa_disclosure") or {}
    stable = mcp_stable_id(name) if _is_mcp_tool(request) else f"local:{name}"
    if (
        not stable
        or record.get("model_message_id") != last.id
        or record.get("tools", {}).get(stable) != name
    ):
        raise McpAuthorizationError("该工具未包含在本次提供给模型的工具定义中")
    return last, stable, record


def _tool_call_denial(request: Any) -> ToolMessage | None:
    """执行入口只核对调用绑定；真实授权由公共执行路径逐次复查."""
    try:
        _model_call_binding(request)
        return None
    except Exception:
        return ToolMessage(
            content=json.dumps(
                {
                    "code": 409,
                    "success": False,
                    "error_code": "tool_call_binding_invalid",
                    "message": "工具调用与本次模型请求提供的工具定义不一致，请使用当前工具定义重新规划。",
                },
                ensure_ascii=False,
            ),
            tool_call_id=str(
                (getattr(request, "tool_call", None) or {}).get("id") or ""
            ),
            name=str((getattr(request, "tool_call", None) or {}).get("name") or "")
            or None,
            status="error",
        )


async def basic_qa_tool_wrapper(
    request: Any, handler: Callable[[Any], Awaitable[Any]]
) -> Any:
    """Check current model-call binding before guarded tool execution."""
    request = _bind_tool_instance(request)
    run_config = current_observation_config(getattr(request, "runtime", None))
    denied = _tool_call_denial(request)
    if denied is None:
        from basic_qa.tool_recovery import (
            control_budget_result,
            control_request_exhausted,
        )

        if control_request_exhausted(
            (getattr(request, "state", None) or {}).get("messages", []),
            request.tool_call,
        ):
            denied = control_budget_result(request.tool_call)
    result = (
        denied
        if denied is not None
        else await composed_tool_wrapper(
            request,
            handler,
            exhausted_tools=exhausted_tools(
                (getattr(request, "state", None) or {}).get("messages", [])
            ),
        )
    )
    try:
        from basic_qa.capabilities.observation import (
            observe_control,
            observe_execution,
        )

        observe_control(run_config, request, result)
        if denied is None and _is_mcp_tool(request):
            observe_execution(run_config, request, result)
    except Exception:  # noqa: BLE001 - observability never grants or denies execution
        pass
    return result


async def authorize_send(
    request: Any,
    run_config: dict[str, Any],
    server_key: str,
    tool_name: str,
    tool: Any,
    actual_args: Mapping[str, Any],
) -> Callable[[], None]:
    """核对实际发送的模型调用、schema 和参数，不以加载记录追加权限."""
    from common.mcp_authorization import McpAuthorizationError
    from common.tool_execution.schema import schema_version_for

    try:
        last, stable, disclosure = _model_call_binding(request)
        actual = f"mcp:{server_key}:{tool_name}"
        if (
            not _is_mcp_tool(request)
            or stable != actual
            or getattr(tool, "name", None) != tool_name
        ):
            raise McpAuthorizationError("工具身份不一致")
        version = (disclosure.get("schema_versions") or {}).get(stable)
        if not version or version != schema_version_for(server_key, tool):
            raise McpAuthorizationError("工具定义已变化")
        if not isinstance(actual_args, Mapping) or _canonical(
            dict(actual_args)
        ) != _canonical(request.tool_call["args"]):
            raise McpAuthorizationError("实际发送参数与模型调用不一致")
        state = request.state

        def fingerprint() -> str:
            messages = request.state.get("messages") or []
            return _canonical(
                {
                    "disclosure": request.state.get("bqa_disclosure"),
                    "messages": [
                        message.model_dump(mode="json") for message in messages[-1:]
                    ],
                    "call": request.tool_call,
                    "args": dict(actual_args),
                    "config": {
                        key: (run_config.get("configurable") or {}).get(key)
                        for key in (
                            "thread_id",
                            "ledger_attempt_id",
                            "ledger_submission_id",
                            "capability_selection_id",
                            "capability_selection_digest",
                            "capability_mcp_mode",
                            "capability_tool_ids",
                        )
                    },
                }
            )

        expected = fingerprint()

        def validate_dispatch() -> None:
            try:
                current_message, current_stable, _ = _model_call_binding(request)
                if current_message is not last or current_stable != actual:
                    raise McpAuthorizationError("模型工具调用绑定在发送前变化")
                if (
                    request.state is not state
                    or fingerprint() != expected
                    or request.state["messages"][-1] is not last
                ):
                    raise McpAuthorizationError("模型工具调用在发送前变化")
                if (
                    not _is_mcp_tool(request)
                    or getattr(tool, "name", None) != tool_name
                    or schema_version_for(server_key, tool) != version
                ):
                    raise McpAuthorizationError("工具身份或定义在发送前变化")
            except McpAuthorizationError:
                raise
            except Exception as exc:
                raise McpAuthorizationError("无法验证最终工具发送") from exc

        validate_dispatch()
        return validate_dispatch
    except McpAuthorizationError:
        raise
    except Exception as exc:
        raise McpAuthorizationError("无法验证工具执行策略") from exc
