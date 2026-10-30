"""Reject externally injected Basic QA internal state."""

from typing import Any

from langgraph_sdk import Auth


def validate_input(value: Any) -> None:
    """Allow only messages in the externally submitted graph input."""
    if not isinstance(value, dict):
        return
    kwargs = value.get("kwargs")
    candidates = [value.get("input")]
    if isinstance(kwargs, dict):
        candidates.append(kwargs.get("input"))
    for payload in candidates:
        if isinstance(payload, dict) and set(payload) - {"messages"}:
            raise Auth.exceptions.HTTPException(
                status_code=403, detail="基础问答只接受用户消息，不能注入运行状态。"
            )
