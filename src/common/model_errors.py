"""模型服务商错误分类：只识别明确的上下文超限，不吞并普通 400/鉴权/限流."""

from __future__ import annotations

from typing import Any

_CONTEXT_MARKERS = (
    "context_length_exceeded",
    "contextwindowexceedederror",
    "maximum context length",
    "exceeds the context window",
    "exceeds the available context size",
    "context window exceeded",
    "context limit exceeded",
    "input tokens exceed the configured limit",
    "prompt is too long",
    "input is too long",
    "too many tokens",
    "reduce the length of the messages",
)


def _body_text(exc: BaseException) -> str:
    body: Any = getattr(exc, "body", None)
    if isinstance(body, dict):
        return str(body).lower()
    return ""


def is_context_overflow(exc: BaseException) -> bool:
    """服务商明确报告上下文超限时返回 True.

    鉴权、普通参数错误、限流和网络不确定结果都不属于可安全重放的上下文超限。
    """
    if type(exc).__name__ in {"ContextOverflowError", "ContextWindowExceededError"}:
        return True
    status = getattr(exc, "status_code", None)
    if status not in {400, 413, 422}:
        return False
    text = f"{exc} {_body_text(exc)}".lower()
    return any(marker in text for marker in _CONTEXT_MARKERS)
