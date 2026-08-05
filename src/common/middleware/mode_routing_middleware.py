"""执行模式上下文中间件.

从 configurable 参数提取 mode，格式化为 runtime_context 追加到系统提示词。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import override

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.tools import BaseTool
from langgraph.config import get_config

from common.context import get_routing_context
from common.prompts import EXPERT_MODE_SYSTEM_PROMPT, FAST_MODE_SYSTEM_PROMPT


class ModeRoutingMiddleware(AgentMiddleware):
    """从 configurable 参数提取 mode，动态注入运行时上下文到系统提示词.

    使用 deepagents 官方的 append_to_system_message 方法追加内容。

    参数:
        从 runtime.config["configurable"] 提取参数：
        - mode: 执行模式，默认 "fast"
    """

    tools: Sequence[BaseTool] = ()

    def _get_runtime_context(self) -> str:
        """获取运行时路由上下文."""
        config = get_config()
        configurable = config.get("configurable", {})
        routing_context = get_routing_context(configurable)
        if routing_context.mode == "expert":
            return f"{routing_context.prompt}\n\n{EXPERT_MODE_SYSTEM_PROMPT}"
        return f"{routing_context.prompt}\n\n{FAST_MODE_SYSTEM_PROMPT}"

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步版本：追加运行时上下文到系统提示词."""
        runtime_context = self._get_runtime_context()
        new_system = append_to_system_message(request.system_message, runtime_context)
        return handler(request.override(system_message=new_system))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步版本：追加运行时上下文到系统提示词."""
        runtime_context = self._get_runtime_context()
        new_system = append_to_system_message(request.system_message, runtime_context)
        return await handler(request.override(system_message=new_system))


# 创建全局实例，保持向后兼容
mode_routing_prompt = ModeRoutingMiddleware()
