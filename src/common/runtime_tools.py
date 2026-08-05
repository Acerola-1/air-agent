"""运行时业务工具 provider."""

from __future__ import annotations

from typing import Any

import common.mcp_client as mcp_client
from common.tools import get_beijing_time, parse_region_tool, websearch_tool


def get_business_tools() -> list[Any]:
    """返回当前可用的业务工具,MCP 工具按运行时初始化状态动态追加.

    本地化说明:知识检索与 text2sql 工具依赖 Milvus/Ollama,本地不可用,已移除。
    保留工具: get_beijing_time / parse_region_tool / websearch_tool + 运行时 MCP 工具。
    """
    return [
        get_beijing_time,
        parse_region_tool,
        websearch_tool,
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
