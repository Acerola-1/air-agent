"""企业运行守卫中间件（DeepAgents 图，任务 5.4）.

每次模型调用与工具调用前复查可信运行身份：会话撤销、账号/企业/成员停用、
授权修订变化后，下一次外呼直接拒绝（可读错误，绝不继续调用模型或 MCP）.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, override

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.tools import BaseTool

from common.identity.runtime import RuntimeIdentityError, guard_request_call


class TenantRuntimeGuardMiddleware(AgentMiddleware):
    """DeepAgents 模型/工具调用守卫。无可信身份时由 guard 失败关闭.

    身份提取走框架支持的可信来源（RunnableConfig 环境 / ToolRuntime.config /
    runtime.server_info.user，见 `identity_from_request`）；R04 修复前的
    `_config_of` 只读 `runtime.config`/`get_config()` 属性，而镜像内 langgraph
    1.2.11 的 `Runtime` 两者都不存在，导致合法运行被整体误拒。
    """

    tools: Sequence[BaseTool] = ()

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        await guard_request_call(request)
        return await handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        """工具实际执行前复查；被拒时返回可读错误（不发外部请求）.

        MCP 远程工具改走企业授权执行层（DeepAgents 与显式图共用同一 helper）；
        本地工具保持原 handler。
        """
        from common.business_graph.tool_wrappers import (
            _is_mcp_tool,
            execute_mcp_tool_request,
        )

        try:
            await guard_request_call(request)
        except RuntimeIdentityError as exc:
            from langchain_core.messages import ToolMessage

            return ToolMessage(
                content=f"授权状态已变化，本次工具调用被拒绝：{exc}",
                tool_call_id=str(request.tool_call.get("id") or ""),
                name=str(request.tool_call.get("name") or ""),
                status="error",
            )
        if _is_mcp_tool(request):
            return await execute_mcp_tool_request(request)
        return await handler(request)
