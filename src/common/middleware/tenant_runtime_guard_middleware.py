"""企业运行守卫中间件（DeepAgents 图）.

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
    runtime.server_info.user，见 `identity_from_request`）。不同框架的 Runtime
    属性不同，身份提取需覆盖这些来源。
    """

    tools: Sequence[BaseTool] = ()

    def __init__(self, *, inject_mcp_tools: bool = True) -> None:
        """Preserve the default MCP disclosure; tracing can opt out explicitly."""
        super().__init__()
        self.inject_mcp_tools = inject_mcp_tools

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        await guard_request_call(request)
        if not self.inject_mcp_tools:
            # Tracing lead/reviewer must not receive MCP data tools even when
            # the current capability selection contains them.
            return await handler(request)
        # 检查 MCP 工具披露白名单与动态代理注入
        from common.capabilities import (
            filter_capability_mcp_tools,
            refresh_authorized_mcp_tools,
        )
        from common.mcp_scoped import build_proxy_tools
        from common.observability.context import current_observation_config

        run_config = current_observation_config(getattr(request, "runtime", None))
        await refresh_authorized_mcp_tools(run_config)
        existing_tools = list(getattr(request, "tools", []) or [])
        base_tools = [
            t
            for t in existing_tools
            if not (getattr(t, "metadata", None) or {}).get("is_mcp")
            and not getattr(t, "_is_mcp", False)
            and not getattr(t, "is_mcp", False)
            and not str(getattr(t, "name", "")).startswith("mcp_")
        ]
        current_proxies = build_proxy_tools()
        filtered_mcp = filter_capability_mcp_tools(current_proxies, run_config)
        if hasattr(request, "override"):
            request = request.override(tools=base_tools + filtered_mcp)
        else:
            try:
                request.tools = base_tools + filtered_mcp
            except Exception:
                pass
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
        from common.tool_execution.mcp import (
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
        if request.tool_call.get("name") == "knowledge_search":
            from common.observability.context import current_observation_config
            from common.observability.tool_adapter import observed_tool_call

            return await observed_tool_call(
                current_observation_config(getattr(request, "runtime", None)),
                tool_name="knowledge_search",
                service_key="knowledge-api",
                logical_call_id=str(request.tool_call.get("id") or "") or None,
                # The adapter may reject before HTTP; unknown is not zero.
                network_attempted=None,
                invoke=lambda: handler(request),
            )
        return await handler(request)
