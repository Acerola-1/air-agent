"""MCP 工具披露接线（change simplify-agent-business-authorization）.

旧"匿名公共目录预热"已移除：不再在导入/启动时发起任何 MCP 网络请求，也不再
从环境变量读取业务地址。目录来自**已受理业务**的受保护连接发现
（`ScopedMCPToolFactory`），这里只提供代理工具与名称查询的薄封装。
"""

from __future__ import annotations

from typing import Any

from common.config.logging_config import LoggingConfigurator
from common.mcp_scoped import build_proxy_tools, get_factory

LoggingConfigurator.configure()

# 兼容旧引用：datacenter MCP 已下线，权限工具不可用时自动降级。
datacenter_mcp_tools: list[Any] = []
datacenter_mcp_tools_name: list[str] = []
profile_mcp_tools: list[Any] = []
region_mcp_tools: list[Any] = []


def get_business_mcp_tools() -> list[Any]:
    """返回披露用代理工具（schema 来自已发现目录；执行走业务授权层）."""
    return build_proxy_tools()


def get_business_mcp_tool_names() -> list[str]:
    """返回已发现目录中的工具名（供治理/测试断言）."""
    return sorted(get_factory().known_tool_names())


def get_permission_mcp_tools() -> list[Any]:
    """权限校验 MCP 工具：datacenter 已下线，恒空（降级语义保持不变）."""
    return []


def get_profile_mcp_tools() -> list[Any]:
    """用户画像工具：datacenter 已下线，恒空."""
    return []


def get_region_mcp_tools() -> list[Any]:
    """行政区解析工具：datacenter 已下线，恒空."""
    return []


def reset_mcp_state_for_tests() -> None:
    """重置 MCP 状态，供单元测试使用."""
    get_factory().reset_for_tests()
