"""通用请求绑定、MCP识别与受保护执行入口."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import ToolMessage
from loguru import logger

from common.observability.context import current_observation_config
from common.runtime_tools import get_mcp_tool_names, tool_name
from common.tool_execution.policy import bind_tool_request


def _bind_tool_instance(request: Any) -> Any:
    """动态绑定工具实例（等效于 SkillToolRegistryMiddleware.awrap_tool_call）."""
    if request.tool is not None:
        return request

    name = request.tool_call.get("name")
    if not isinstance(name, str):
        return request

    # 从运行时业务工具列表中查找工具实例
    from common.runtime_tools import get_business_tools

    for t in get_business_tools(include_computation=True):
        if tool_name(t) == name:
            return request.override(tool=t)

    return request


def _is_mcp_tool(request: Any) -> bool:
    """判断本次请求是否指向已知 MCP 远程工具（含短名、代理别名及稳定 ID，不含本地业务工具）."""
    tool = getattr(request, "tool", None)
    if tool is not None:
        if (
            (getattr(tool, "metadata", None) or {}).get("is_mcp")
            or getattr(tool, "_is_mcp", False)
            or getattr(tool, "is_mcp", False)
        ):
            return True

    tool_call = getattr(request, "tool_call", None)
    tool_name_str = (
        tool_call.get("name")
        if isinstance(tool_call, dict)
        else getattr(request, "name", None)
    )
    if not isinstance(tool_name_str, str):
        return False

    if tool_name_str.startswith("mcp_") or tool_name_str.startswith("mcp:"):
        return True

    if tool_name_str in set(get_mcp_tool_names()):
        return True

    try:
        from common.mcp_scoped import get_factory

        factory = get_factory()
        if factory is not None and (
            tool_name_str in factory.known_tool_names()
            or tool_name_str in factory.known_tool_ids()
            or tool_name_str in getattr(factory, "_aliases", {})
        ):
            return True
    except Exception:
        pass

    return False


async def execute_mcp_tool_request(request: Any) -> ToolMessage:
    """企业授权执行路径（两种图机制共用；任务 7.2/7.3）.

    直接 handler(request) 只会触达 schema 代理（其协程拒绝直接执行）。这里按
    已认证运行身份解析绑定后调用真实工具；授权错误转为可读降级 ToolMessage，
    响应不含凭据。调用**每次**重新解析绑定，重试天然复查授权。
    """
    from common.mcp_authorization import McpAuthorizationError
    from common.mcp_scoped import execute_mcp_tool, get_factory

    name = str(request.tool_call.get("name") or "")
    tool_call_id = str(request.tool_call.get("id") or "")
    server_key = get_factory().server_of(name) or ""
    run_config = current_observation_config(request.runtime)
    run_config["metadata"] = {
        **(run_config.get("metadata") or {}),
        "obs_tool_call_id": tool_call_id,
    }
    try:
        with bind_tool_request(request):
            payload = await execute_mcp_tool(
                server_key, name, dict(request.tool_call.get("args") or {}), run_config
            )
    except McpAuthorizationError as exc:
        logger.warning(
            "MCP 调用被授权层拒绝: tool={}, category={}", name, type(exc).__name__
        )
        content = json.dumps(
            {"code": 403, "success": False, "message": "当前授权不允许调用该工具"},
            ensure_ascii=False,
        )
        return ToolMessage(
            content=content,
            tool_call_id=tool_call_id,
            name=name or None,
            status="error",
            additional_kwargs={"network_attempted": False},
        )
    content = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, default=str)
    )
    return ToolMessage(
        content=content,
        tool_call_id=tool_call_id,
        name=name or None,
        status="error" if getattr(payload, "is_error", False) else "success",
        additional_kwargs={"network_attempted": True},
    )
