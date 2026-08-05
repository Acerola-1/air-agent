"""全局异常兜底中间件."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, override

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from loguru import logger

MODEL_USER_MESSAGE = "抱歉，当前智能问答服务暂时不可用，请稍后再试。"
TOOL_USER_MESSAGE = "暂未获取到相关数据，请稍后重试。"
# 内部日志与外部 payload 分离：模型只能看到业务语言，不暴露任何技术细节
_TOOL_INTERNAL_LOG_MESSAGE = "工具调用失败，已转为降级 ToolMessage: tool={}, error={}"


class GlobalExceptionMiddleware(AgentMiddleware):
    """将未处理异常转为用户可见的友好消息.

    该中间件覆盖模型调用和工具调用两类最常见的运行时失败：
    - 模型连接、超时或供应商错误：直接返回 AIMessage，避免页面空白。
    - 普通工具异常：返回错误 ToolMessage，让 Agent 有机会基于降级结果回答。
    """

    tools: Sequence[BaseTool] = ()

    def _model_fallback(
        self,
        exc: Exception,
    ) -> AIMessage:
        """构造模型调用失败时的兜底回答."""
        logger.exception("模型调用失败，已返回用户友好兜底消息: {}", exc)
        return AIMessage(content=MODEL_USER_MESSAGE)

    def _tool_fallback(
        self,
        request: ToolCallRequest,
        exc: Exception,
    ) -> ToolMessage:
        """构造工具调用失败时的降级 ToolMessage."""
        tool_name = str(request.tool_call.get("name") or "")
        tool_call_id = str(request.tool_call.get("id") or "")
        logger.exception(
            _TOOL_INTERNAL_LOG_MESSAGE,
            tool_name,
            exc,
        )
        payload = {
            "code": 500,
            "success": False,
            "message": TOOL_USER_MESSAGE,
        }
        return ToolMessage(
            content=json.dumps(payload, ensure_ascii=False),
            tool_call_id=tool_call_id,
            name=tool_name or None,
            status="error",
        )

    @override
    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Any],
    ) -> Any:
        """捕获同步模型调用中的未处理异常."""
        try:
            return handler(request)
        except Exception as exc:
            return self._model_fallback(exc)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """捕获异步模型调用中的未处理异常."""
        try:
            return await handler(request)
        except Exception as exc:
            return self._model_fallback(exc)

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        """捕获同步工具调用中的未处理异常."""
        try:
            return handler(request)
        except Exception as exc:
            return self._tool_fallback(request, exc)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> ToolMessage | Any:
        """捕获异步工具调用中的未处理异常."""
        try:
            return await handler(request)
        except Exception as exc:
            return self._tool_fallback(request, exc)
