"""中间件共享的运行时上下文辅助工具."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig


@dataclass(frozen=True)
class RoutingContext:
    """运行模式上下文，来自 runnable configurable 参数."""

    mode: str = "fast"

    @property
    def prompt(self) -> str:
        """返回注入到 system prompt 的运行时上下文."""
        return f"""<runtime_context>
mode: {self.mode}
</runtime_context>"""


def get_routing_context(
    configurable: dict[str, Any] | None,
    *,
    default_mode: str = "fast",
) -> RoutingContext:
    """从 configurable 中提取当前业务图的运行模式."""
    values = configurable or {}
    mode = str(values.get("chat_mode") or values.get("mode") or default_mode)

    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"fast", "expert"}:
        normalized_mode = "fast"

    return RoutingContext(mode=normalized_mode)


def get_user_info(config: RunnableConfig) -> str:
    """从配置中获取用户 id 并生成提示文本."""
    user_id = None
    if config and "configurable" in config:
        configurable = config["configurable"]
        user_id = configurable.get("user_id")

    if user_id:
        return f"""
        - 当前对话的 userId 为：{user_id}
        - 当你调用任何需要 user_id/userId/用户 id 的工具时，直接使用这个 userId。
        """
    return ""


def get_message_content(message: Any) -> str:
    """统一提取消息内容为字符串."""
    content = message.content
    return content if isinstance(content, str) else str(content)
