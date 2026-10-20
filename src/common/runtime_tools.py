"""运行时业务工具 provider."""

from __future__ import annotations

from typing import Any

import common.mcp_client as mcp_client
from common.artifacts.create_artifact_tool import build_create_artifact_tool
from common.computation.tool import compute_artifact
from common.knowledge.tool import build_knowledge_tool
from common.tools import get_beijing_time, parse_region_tool, websearch_tool


def get_business_tools(*, include_computation: bool = False) -> list[Any]:
    """返回当前可用的业务工具,MCP 工具按运行时初始化状态动态追加.

    本地化说明:text2sql 依赖 Milvus/Ollama,本地不可用,已移除。
    保留工具: create_artifact / get_beijing_time / parse_region_tool /
             websearch_tool / knowledge_search + 运行时 MCP 工具。

    ``knowledge_search`` 是不受 MCP 开关影响的受管本地 HTTP 工具：关闭 MCP
    不会移除它；导入本模块不产生任何网络调用或凭据读取。
    """
    return [
        build_create_artifact_tool(),
        *([compute_artifact] if include_computation else []),
        get_beijing_time,
        parse_region_tool,
        websearch_tool,
        build_knowledge_tool(),
        *mcp_client.get_business_mcp_tools(),
    ]


def get_business_tool_names() -> list[str]:
    """返回当前可用的业务工具名称."""
    return [
        name
        for tool in get_business_tools()
        if isinstance(name := tool_name(tool), str)
    ]


def get_mcp_tool_names() -> list[str]:
    """返回当前可用的 MCP 工具名称."""
    return mcp_client.get_business_mcp_tool_names()


def tool_name(tool: Any) -> str | None:
    """提取工具名称."""
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None
