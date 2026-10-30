"""当前用户轮的确定性工具失败止损；不跨用户轮、不放宽授权."""

import json
from collections import Counter
from typing import Any, Sequence

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from common.observability.tool_adapter import classify_tool_output
from common.runtime_tools import tool_name

_CONTROL = frozenset({"search_skills", "load_skill", "search_mcp", "load_mcp_tool"})


def _signature(call: dict[str, Any]) -> tuple[str, str]:
    return str(call.get("name") or ""), json.dumps(
        call.get("args") or {}, sort_keys=True, ensure_ascii=False, default=str
    )


def control_request_exhausted(messages: Sequence[Any], call: dict[str, Any]) -> bool:
    """Bound a repeated control request without disabling other skills or queries."""
    target = _signature(call)
    if target[0] not in _CONTROL:
        return False
    calls = {}
    failures = 0
    for message in turn_messages(messages):
        if isinstance(message, AIMessage):
            calls.update({c.get("id"): _signature(c) for c in message.tool_calls})
        if (
            not isinstance(message, ToolMessage)
            or calls.get(message.tool_call_id) != target
        ):
            continue
        try:
            payload = json.loads(message.content)
        except (TypeError, ValueError):
            payload = {}
        status = payload.get("status") if isinstance(payload, dict) else None
        if (
            message.status == "error"
            or isinstance(status, str)
            and status
            in {
                "unavailable",
                "invalid_query",
                "search_timeout",
                "search_unavailable",
                "index_unavailable",
            }
        ):
            failures += 1
    return failures >= 2


def control_budget_result(call: dict[str, Any]) -> ToolMessage:
    """Return a paired failure without invoking an exhausted control request."""
    return ToolMessage(
        content=json.dumps(
            {
                "code": 422,
                "success": False,
                "error_code": "control_retry_budget",
                "message": "相同控制请求连续失败已达本轮上限；可选择其他能力或说明不可用。",
            },
            ensure_ascii=False,
        ),
        tool_call_id=str(call.get("id") or ""),
        name=str(call.get("name") or ""),
        status="error",
    )


def turn_messages(messages: Sequence[Any]) -> Sequence[Any]:
    """只检查最近一次用户输入之后的执行记录."""
    start = next(
        (
            i
            for i in range(len(messages) - 1, -1, -1)
            if isinstance(messages[i], HumanMessage)
        ),
        -1,
    )
    return messages[start + 1 :]


def exhausted_tools(messages: Sequence[Any]) -> set[str]:
    """授权拒绝不重试；参数拒绝允许修正一次，之后停止本轮该工具."""
    rejected: Counter[str] = Counter()
    denied: set[str] = set()
    for message in turn_messages(messages):
        if not isinstance(message, ToolMessage) or not message.name:
            continue
        outcome = classify_tool_output(message.content)
        if outcome == "permission_denied":
            denied.add(message.name)
        elif message.name in _CONTROL:
            # Execution guards limit the exact failed arguments. Keep the
            # function disclosed so another capability can still be loaded.
            continue
        elif outcome == "validation_rejected":
            rejected[message.name] += 1
        elif outcome == "succeeded" and message.status != "error":
            rejected[message.name] = 0
    return denied | {name for name, count in rejected.items() if count >= 2}


def recoverable_tools(tools: list[Any], messages: Sequence[Any]) -> list[Any]:
    """从下一次模型 schema 中移除本轮已耗尽的工具."""
    blocked = exhausted_tools(messages)
    return [tool for tool in tools if tool_name(tool) not in blocked]


def planning_exhausted(messages: Sequence[Any]) -> bool:
    """限制工具规划轮数，避免换工具后继续无限尝试."""
    return (
        sum(
            isinstance(message, AIMessage) and bool(message.tool_calls)
            for message in turn_messages(messages)
        )
        >= 8
    )
