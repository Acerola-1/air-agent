"""定义 Agent 可配置参数."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Literal, Optional

from langchain_core.runnables import RunnableConfig


@dataclass(kw_only=True)
class Configuration:
    """Agent 调用时的运行时可配置参数."""

    # 路由模块。auto 表示由主 Agent 根据问题自动选择。
    module: str = "auto"

    # 聊天模式：fast 为默认快速模式，expert 为专家模式。
    chat_mode: Literal["fast", "expert"] = "fast"

    @classmethod
    def from_runnable_config(
        cls, config: Optional[RunnableConfig] = None
    ) -> Configuration:
        """根据 RunnableConfig 创建 Configuration 实例."""
        configurable = (config.get("configurable") or {}) if config else {}
        _fields = {f.name for f in fields(cls) if f.init}
        return cls(**{k: v for k, v in configurable.items() if k in _fields})
