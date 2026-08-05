"""基于 Skill 渐进披露工具的中间件."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, override

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.tools import BaseTool

from common.skill_discovery import SkillDiscoveryState

UnmatchedPolicy = str
ToolProvider = Callable[[], Sequence[BaseTool | dict[str, Any]]]
ToolNameProvider = Callable[[], Sequence[str]]
SyncRefresh = Callable[[], None]
AsyncRefresh = Callable[[], Awaitable[None]]


def tool_name(tool: BaseTool | dict[str, Any]) -> str | None:
    """提取模型可见的工具名."""
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class SkillToolRegistryMiddleware(AgentMiddleware[Any, Any, Any]):
    """注册所有业务图工具，供后续动态披露."""

    def __init__(
        self,
        business_tools: Sequence[BaseTool | dict[str, Any]] | ToolProvider,
        *,
        sync_refresh: SyncRefresh | None = None,
        async_refresh: AsyncRefresh | None = None,
    ) -> None:
        """初始化工具注册中间件."""
        if callable(business_tools):
            self._business_tools_provider = business_tools
        else:
            static_tools = list(business_tools)
            self._business_tools_provider = lambda: static_tools
        self.tools = list(self._business_tools_provider())
        self._sync_refresh = sync_refresh
        self._async_refresh = async_refresh

    def _tool_by_name(self) -> dict[str, BaseTool | dict[str, Any]]:
        """按工具名索引当前业务工具."""
        return {
            name: tool
            for tool in self._business_tools_provider()
            if isinstance(name := tool_name(tool), str)
        }

    def _request_with_current_tools(
        self,
        request: ModelRequest[Any],
    ) -> ModelRequest[Any]:
        """把运行时新发现的业务工具加入模型可见工具列表."""
        current_tools = self._tool_by_name()
        tools = list(request.tools)
        seen = {name for tool in tools if isinstance(name := tool_name(tool), str)}
        for name, current_tool in current_tools.items():
            if name not in seen:
                tools.append(current_tool)
        return request.override(tools=tools)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """同步模型调用前刷新运行时业务工具."""
        if self._sync_refresh is not None:
            self._sync_refresh()
        return handler(self._request_with_current_tools(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """异步模型调用前刷新运行时业务工具."""
        if self._async_refresh is not None:
            await self._async_refresh()
        return await handler(self._request_with_current_tools(request))

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        """同步工具调用前补齐运行时发现的工具实例."""
        if request.tool is not None:
            return handler(request)
        name = request.tool_call.get("name")
        if (
            isinstance(name, str)
            and (tool := self._tool_by_name().get(name)) is not None
        ):
            return handler(request.override(tool=tool))
        return handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        """异步工具调用前补齐运行时发现的工具实例."""
        if request.tool is not None:
            return await handler(request)
        name = request.tool_call.get("name")
        if (
            isinstance(name, str)
            and (tool := self._tool_by_name().get(name)) is not None
        ):
            return await handler(request.override(tool=tool))
        return await handler(request)


class SkillToolFilterMiddleware(AgentMiddleware[Any, Any, Any]):
    """按所选 Skill 的 allowed-tools 过滤模型可见业务工具."""

    state_schema = SkillDiscoveryState
    tools: Sequence[BaseTool] = ()

    def __init__(
        self,
        *,
        business_tool_names: Sequence[str] | ToolNameProvider,
        always_visible_tool_names: Sequence[str] = (
            "find_skill",
            "get_beijing_time",
            "knowledge_retriever_tool",
        ),
        unmatched_policy: UnmatchedPolicy = "strict",
    ) -> None:
        """初始化工具过滤中间件."""
        if unmatched_policy not in {"strict", "native"}:
            msg = "unmatched_policy must be 'strict' or 'native'"
            raise ValueError(msg)
        if callable(business_tool_names):
            self._business_tool_names_provider = business_tool_names
        else:
            static_tool_names = tuple(business_tool_names)
            self._business_tool_names_provider = lambda: static_tool_names
        self._always_visible_tool_names = set(always_visible_tool_names)
        self._unmatched_policy = unmatched_policy

    def _business_tool_names(self) -> set[str]:
        """返回当前业务工具名集合."""
        return set(self._business_tool_names_provider())

    def _filter_tools(
        self,
        tools: list[BaseTool | dict[str, Any]],
        state: dict[str, Any],
    ) -> list[BaseTool | dict[str, Any]]:
        allowed_tools = set(state.get("selected_skill_allowed_tools") or [])
        skill_search_attempted = state.get("skill_search_attempted") is True

        if not skill_search_attempted:
            allowed_tools = set()

        if (
            skill_search_attempted
            and not allowed_tools
            and self._unmatched_policy == "native"
        ):
            return tools

        filtered: list[BaseTool | dict[str, Any]] = []
        for current_tool in tools:
            name = tool_name(current_tool)
            if not name:
                filtered.append(current_tool)
                continue
            if name in self._always_visible_tool_names:
                filtered.append(current_tool)
                continue
            if name not in self._business_tool_names():
                filtered.append(current_tool)
                continue
            if name in allowed_tools:
                filtered.append(current_tool)
        return filtered

    @override
    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """同步模型调用前过滤业务工具."""
        filtered = self._filter_tools(request.tools, request.state)
        return handler(request.override(tools=filtered))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """异步模型调用前过滤业务工具."""
        filtered = self._filter_tools(request.tools, request.state)
        return await handler(request.override(tools=filtered))
