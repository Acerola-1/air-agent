"""单次用户文本限制：服务端权威、按 Unicode 码点计数.

限制独立于模型 token 窗口；模型窗口扩大不会自动放大用户输入字符上限。
页面读取同一受保护配置，后端对实际提交字符串做最终校验。
"""

from __future__ import annotations

from dataclasses import dataclass

from common.config import config

CHARACTER_COUNT_MODE = "unicode_code_point"


@dataclass(frozen=True)
class InputLimit:
    """当前生效的单次用户文本限制."""

    max_chars: int
    character_count_mode: str = CHARACTER_COUNT_MODE


def current_input_limit() -> InputLimit:
    """读取受保护部署配置；非法值明确失败，不静默改成其他上限."""
    value = getattr(config, "MAX_INPUT_CHARS", 8000)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("MAX_INPUT_CHARS 必须是正整数")
    return InputLimit(max_chars=value)


def count_user_characters(text: str) -> int:
    """按 Unicode 码点计数（Python ``len`` 与页面 [...text] 语义一致）."""
    return len(text or "")


def input_overflow_message(count: int, limit: int) -> str:
    """与页面提示一致的服务端拒绝说明."""
    return (
        f"本次输入共 {count} 个字符，超过单次上限 {limit} 个字符。"
        "请精简或分段发送，草稿不会被截断。"
    )
