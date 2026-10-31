"""中间件共享的运行时上下文辅助工具."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig


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
