"""当前时间上下文注入中间件."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import override
from zoneinfo import ZoneInfo

from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.tools import BaseTool


class TimeContextMiddleware(AgentMiddleware):
    """向每次模型调用注入真实北京时间，避免模型凭训练时间回答."""

    tools: Sequence[BaseTool] = ()

    def _get_time_context(self) -> str:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        current_time = now.strftime("%Y-%m-%d %H:%M:%S")
        current_date = now.strftime("%Y-%m-%d")
        return f"""
【当前真实时间】
- 当前北京时间：{current_time}
- 当前日期：{current_date}

【时间回答规则】
- 用户询问当前时间、当前日期、今天是几号、现在几点等通用时间问题时，直接根据上述真实时间回答。
- 用户问题中的今天、昨天、当前、最近、上周、上月等相对时间，必须以上述真实时间换算，不得使用模型训练数据中的年份或日期。
""".strip()

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步模型调用前注入当前时间上下文."""
        time_context = self._get_time_context()
        new_system = append_to_system_message(request.system_message, time_context)
        return handler(request.override(system_message=new_system))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步模型调用前注入当前时间上下文."""
        time_context = self._get_time_context()
        new_system = append_to_system_message(request.system_message, time_context)
        return await handler(request.override(system_message=new_system))
