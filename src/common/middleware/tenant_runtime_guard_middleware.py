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
        # 任务 4.3: 检查 MCP 工具披露白名单与动态代理注入（F03）
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
        cfg = run_config.get("configurable") or {}
        if cfg.get("capability_skill_mode") == "explicit":
            # DeepAgents 没有业务图的 resolve_skill 节点；必须在第一次模型调用前
            # 确定性加载，不能依赖模型是否主动调用 find_skill。
            from langchain_core.messages import SystemMessage

            from common.business_graph.skill_content import load_skill_rules
            from common.capabilities import GRAPH_SKILLS_DIRS, validate_skill_selection
            from common.context import get_routing_context
            from common.model_budget import enforce_request_budget
            from common.observability.skill_adapter import emit_skill_stage
            from common.skill_router import SkillSemanticRouter

            skill_id = str(cfg.get("capability_skill_id") or "")
            parts = skill_id.split(":")
            if len(parts) != 3:
                raise RuntimeIdentityError("已受理技能标识无效")
            _, graph_id, slug = parts
            declared_graph = cfg.get("ledger_graph_id")
            if declared_graph and declared_graph != graph_id:
                raise RuntimeIdentityError("已受理技能不属于本次业务图")
            item = validate_skill_selection(graph_id, skill_id)
            expected = cfg.get("capability_material_version")
            if expected and expected != item.material_version:
                raise RuntimeIdentityError("已受理技能材料已变化，请重新提交")
            rules = load_skill_rules(
                SkillSemanticRouter(GRAPH_SKILLS_DIRS[graph_id]),
                slug,
                get_routing_context(cfg).mode,
            )
            if not rules.strip():
                raise RuntimeIdentityError("已受理技能规则无法加载")
            emit_skill_stage(run_config, slug, "selected", body=rules, source="manual")
            emit_skill_stage(
                run_config,
                slug,
                "loaded",
                body=rules,
                source="manual",
                status="succeeded",
            )
            current = request.system_message
            content = current.content if current else ""
            instruction = (
                f"\n\n本次用户已指定技能，必须遵循以下规则，不得自动更换：\n{rules}"
            )
            content = (
                content + instruction
                if isinstance(content, str)
                else [
                    *content,
                    {"type": "text", "text": instruction},
                ]
            )
            system = (
                current.model_copy(update={"content": content})
                if current
                else SystemMessage(content=content)
            )
            enforce_request_budget(
                [system, *request.messages], base_tools + filtered_mcp
            )
            request = request.override(system_message=system)
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
