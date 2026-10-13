"""企业授权作用域的 MCP 工具层（任务 7.1–7.5）.

结构：
- `MCPToolRegistry`（下方保留）：只承载**公共 schema 目录**——工具名/描述/入参
  结构（平台匿名 tools/list 可枚举，属非敏感定义数据）。任何可执行调用都不走
  这里的对象。
- `ScopedMCPToolFactory`：真正发起调用的执行层。每个授权作用域
  （tenant + external_subject + server_key + binding revision）持有独立的
  已发现工具列表、连接、退避状态；每次调用前重新解析绑定（撤销/轮换在下一次
  调用或重试即刻生效），凭据与主体参数由拦截器在发送边界注入。
- `build_proxy_tools()`：给图/模型披露的代理工具——schema 来自公共目录，执行
  分发到 ScopedMCPToolFactory；没有已认证身份的调用失败关闭，绝不匿名直达。

真实统一业务平台的企业认证协议未核验（docs/mcp-auth-contract.md），因此生产
上所有企业调用在 `resolve_binding` 处被拒绝（protocol_version=unverified）；
已核验协议（如本地 fixture 的 v1-fixture）按字段映射注入主体。
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from loguru import logger

from common.config import config
from common.config.logging_config import LoggingConfigurator
from common.identity.runtime import RuntimeIdentity, identity_from_config
from common.mcp_authorization import (
    McpAuthorizationError,
    ScopeBinding,
    resolve_binding,
    verified_subject_fields,
)

LoggingConfigurator.configure()


# --------------------------------------------------------------------- 授权作用域注册表


@dataclass
class _ScopeState:
    tools: list[Any] = field(default_factory=list)
    discovered: bool = False
    last_error: str | None = None
    retry_after: float = 0.0


class ScopedMCPToolFactory:
    """按授权作用域缓存发现结果并执行调用；跨线程/跨 event loop 去重."""

    def __init__(self, server_urls: dict[str, str]) -> None:
        """以 server_key → url 映射初始化授权作用域工厂."""
        self._urls = server_urls
        self._states: dict[tuple, _ScopeState] = {}
        self._lock = threading.RLock()
        self._loading: dict[tuple, threading.Event] = {}
        self._catalog: dict[str, dict[str, Any]] = {}
        self._catalog_server: dict[str, str] = {}

    # -- 发现 ------------------------------------------------------------------

    async def tools_for(
        self, identity: RuntimeIdentity, server_key: str, *, force_retry: bool = False
    ) -> list[Any]:
        """返回该身份授权作用域已发现的可执行工具列表."""
        binding = await resolve_binding(
            tenant_id=identity.tenant_id,
            membership_id=identity.membership_id,
            server_key=server_key,
        )
        state = await self._ensure_discovered(binding, force_retry=force_retry)
        return list(state.tools)

    def _gates(self) -> None:  # pragma: no cover - 文档占位
        return None

    async def _ensure_discovered(
        self, binding: ScopeBinding, *, force_retry: bool = False
    ) -> _ScopeState:
        key = binding.cache_scope
        while True:
            with self._lock:
                state = self._states.get(key)
                if state is None:
                    state = _ScopeState()
                    self._states[key] = state
                if state.discovered and not force_retry:
                    return state
                if not force_retry and time.monotonic() < state.retry_after:
                    return state
                waiter = self._loading.get(key)
                if waiter is None:
                    self._loading[key] = threading.Event()
                    break
            await _wait_thread_event(waiter)
        try:
            tools = await self._discover(binding)
        except BaseException as exc:  # 含取消：必须释放门闩，失败退避按作用域隔离
            logger.warning(
                "MCP 作用域发现失败: scope={}, error={}",
                binding.redacted(),
                type(exc).__name__,
            )
            with self._lock:
                state.discovered = False
                state.last_error = type(exc).__name__
                state.retry_after = time.monotonic() + max(
                    1, int(getattr(config, "MCP_RETRY_BACKOFF_SECONDS", 30))
                )
                event = self._loading.pop(key, None)
                if event is not None:
                    event.set()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise McpAuthorizationError("MCP 服务当前不可用") from exc
        with self._lock:
            state.tools = tools
            state.discovered = True
            state.last_error = None
            state.retry_after = 0.0
            for tool in tools:
                self._catalog[tool.name] = {
                    "name": tool.name,
                    "description": tool.description,
                    "args_schema": tool.args_schema,
                }
                self._catalog_server[tool.name] = binding.server_key
            event = self._loading.pop(key, None)
            if event is not None:
                event.set()
        return state

    async def _discover(self, binding: ScopeBinding) -> list[Any]:
        url = self._urls.get(binding.server_key)
        if not url:
            raise McpAuthorizationError("未知 MCP 服务")
        # R07：认证 discovery——initialize/tools/list 与 tools/call 使用同一
        # 绑定凭据（发送边界解析），不再允许匿名握手；失败不重试到其他作用域。
        from common.mcp_authorization import DEFAULT_PROVIDER

        client = MultiServerMCPClient(
            {
                binding.server_key: {
                    "url": url,
                    "transport": "streamable_http",
                    "headers": DEFAULT_PROVIDER.auth_headers(binding),
                }
            },
            tool_interceptors=[_build_interceptor(binding)],
        )
        return await client.get_tools(server_name=binding.server_key)

    # -- 执行 ------------------------------------------------------------------

    async def call(
        self,
        identity: RuntimeIdentity,
        server_key: str,
        tool_name: str,
        args: dict[str, Any],
    ) -> Any:
        """执行一次授权调用：每次重新解析绑定（撤销/轮换即时生效）."""
        binding = await resolve_binding(
            tenant_id=identity.tenant_id,
            membership_id=identity.membership_id,
            server_key=server_key,
        )
        state = await self._ensure_discovered(binding)
        tool = next((t for t in state.tools if t.name == tool_name), None)
        if tool is None:
            raise McpAuthorizationError("工具不在当前授权作用域已发现清单内")
        result = await tool.ainvoke(args)
        return _normalize_call_output(result)

    def register_catalog(self, tools: list[Any], *, server_key: str) -> None:
        """把公共目录（schema 定义）并入 catalog（匿名 tools/list 的结果）."""
        with self._lock:
            for tool in tools:
                self._catalog[tool.name] = {
                    "name": tool.name,
                    "description": getattr(tool, "description", "") or tool.name,
                    "args_schema": tool.args_schema,
                }
                self._catalog_server[tool.name] = server_key

    # -- 披露 ------------------------------------------------------------------

    def catalog_entries(self) -> list[dict[str, Any]]:
        """公共目录条目（schema 定义）."""
        with self._lock:
            return list(self._catalog.values())

    def server_of(self, tool_name: str) -> str | None:
        """工具所属 server_key."""
        with self._lock:
            return self._catalog_server.get(tool_name)

    def known_tool_names(self) -> set[str]:
        """公共目录中的工具名集合."""
        with self._lock:
            return set(self._catalog)

    def reset_for_tests(self) -> None:
        """清空作用域状态与目录，供单元测试使用."""
        with self._lock:
            self._states.clear()
            self._loading.clear()
            self._catalog.clear()
            self._catalog_server.clear()


def _build_interceptor(binding: ScopeBinding) -> Callable[..., Awaitable[Any]]:
    """发送边界拦截器：认证 header + 已核验主体字段覆盖（模型伪造无效）."""
    fields = verified_subject_fields(binding.protocol_version)

    async def interceptor(
        request: MCPToolCallRequest,
        handler: Callable[[MCPToolCallRequest], Awaitable[Any]],
    ) -> Any:
        from common.mcp_authorization import DEFAULT_PROVIDER

        headers = dict(request.headers or {})
        headers.update(DEFAULT_PROVIDER.auth_headers(binding))
        args = dict(request.args or {})
        for param, source in fields.items():
            if source == "external_subject":
                args[param] = binding.external_subject
        return await handler(request.override(headers=headers, args=args))

    return interceptor


# --------------------------------------------------------------------- 全局单例与代理工具

_FACTORY: ScopedMCPToolFactory | None = None


def get_factory() -> ScopedMCPToolFactory:
    """返回进程级授权工厂单例."""
    global _FACTORY
    if _FACTORY is None:
        _FACTORY = ScopedMCPToolFactory({config.MCP_SERVER_NAME: config.MCP_SERVER_URL})
    return _FACTORY


def set_factory(factory: ScopedMCPToolFactory | None) -> None:
    """替换工厂单例（测试注入用）."""
    global _FACTORY
    _FACTORY = factory


async def execute_mcp_tool(
    server_key: str, tool_name: str, args: dict[str, Any], run_config: Any
) -> Any:
    """授权执行入口（ToolNode / DeepAgents 中间件在 handler 之前调用本函数）."""
    identity = identity_from_config(dict(run_config or {}))
    if identity is None:
        raise McpAuthorizationError("缺少已认证运行身份，MCP 调用被拒绝")
    return await get_factory().call(identity, server_key, tool_name, args)


def build_proxy_tools() -> list[StructuredTool]:
    """从公共 schema 目录生成代理工具（仅承载 schema）.

    真实执行必须经 composed_tool_wrapper / DeepAgents 守卫层的
    `execute_mcp_tool`；直接调用代理协程属于绕过授权路径，一律失败关闭。
    """
    factory = get_factory()
    tools: list[StructuredTool] = []
    for entry in factory.catalog_entries():

        def _make(name: str = entry["name"]) -> StructuredTool:
            async def _blocked(**kwargs: Any) -> Any:
                raise McpAuthorizationError(
                    "MCP 工具必须经授权执行层调用，直接调用被拒绝"
                )

            return StructuredTool.from_function(
                coroutine=_blocked,
                name=name,
                description=entry["description"] or name,
                args_schema=entry["args_schema"],
                handle_tool_error=False,
            )

        tools.append(_make())
    return tools


async def _wait_thread_event(event: threading.Event) -> None:
    while not event.is_set():
        await asyncio.sleep(0.01)


def _normalize_call_output(result: Any) -> Any:
    """把 MCP content block 列表归一化成文本（与治理链路的字符串约定一致）."""
    if isinstance(result, list):
        parts: list[str] = []
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                text_attr = getattr(block, "text", None)
                parts.append(str(text_attr if text_attr is not None else block))
        return "\n".join(parts)
    return result
