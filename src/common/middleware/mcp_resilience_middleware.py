"""MCP 工具调用容错中间件."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, override

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from loguru import logger


class MCPResilienceMiddleware(AgentMiddleware):
    """将 MCP 工具失败转换成模型可读取的 ToolMessage.

    MCP 服务属于外部依赖。临时 502 或连接失败时，应让回答降级，
    而不是用 ExceptionGroup 中断整个 LangGraph 运行。
    """

    tools: Sequence[BaseTool] = []

    def __init__(
        self, mcp_tool_names: Sequence[str] | Callable[[], Sequence[str]]
    ) -> None:
        """使用已知 MCP 工具名初始化中间件."""
        if callable(mcp_tool_names):
            self._mcp_tool_names_provider = mcp_tool_names
        else:
            static_tool_names = tuple(mcp_tool_names)
            self._mcp_tool_names_provider = lambda: static_tool_names

    def _is_mcp_tool_call(self, request: ToolCallRequest) -> bool:
        """判断本次请求是否指向已知 MCP 工具."""
        tool_name = request.tool_call.get("name")
        return isinstance(tool_name, str) and tool_name in set(
            self._mcp_tool_names_provider()
        )

    def _push_status(self, request: ToolCallRequest, tool_name: str) -> None:
        """尽可能向前端推送可见的降级状态."""
        writer = getattr(request.runtime, "stream_writer", None)
        if not callable(writer):
            return
        writer(
            {
                "node": "mcp_tool_call",
                "type": "external_service_status",
                "status": "unavailable",
                "tool_name": tool_name,
                "message": "暂未获取到相关数据，已跳过本次查询",
            }
        )

    def _fallback_message(
        self,
        request: ToolCallRequest,
        exc: Exception,
    ) -> ToolMessage:
        """构造错误 ToolMessage，让 Agent 运行不中断."""
        tool_name = str(request.tool_call.get("name") or "")
        tool_call_id = str(request.tool_call.get("id") or "")
        logger.exception(
            "MCP 工具调用失败，已转为降级 ToolMessage: tool={}, error={}",
            tool_name,
            exc,
        )
        self._push_status(request, tool_name)
        payload = {
            "code": 503,
            "success": False,
            "message": "暂未获取到相关数据，请稍后重试。",
        }
        return ToolMessage(
            content=json.dumps(payload, ensure_ascii=False),
            tool_call_id=tool_call_id,
            name=tool_name or None,
            status="error",
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        """捕获同步 MCP 工具调用失败."""
        try:
            return handler(request)
        except Exception as exc:
            if not self._is_mcp_tool_call(request):
                raise
            return self._fallback_message(request, exc)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> ToolMessage | Any:
        """捕获异步 MCP 工具调用失败."""
        try:
            return await handler(request)
        except Exception as exc:
            if not self._is_mcp_tool_call(request):
                raise
            return self._fallback_message(request, exc)
