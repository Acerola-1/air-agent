"""业务作用域 MCP 工具层（change simplify-agent-business-authorization 任务 3.2–3.4）.

结构：
- `ScopedMCPToolFactory`：真正发起调用的执行层。每次发现/调用前按
  **业务 + 企业 + 服务连接**解析（`mcp_authorization.resolve_connection`），
  再按连接修订/认证引用摘要隔离缓存；人员许可不进入缓存。
- `build_proxy_tools()`：给图/模型披露的代理工具——schema 来自已发现目录，
  执行分发到 `ScopedMCPToolFactory.call`；没有可信业务快照的调用失败关闭。
- `MCPOutboundAuth`：真实 HTTP 发送边界；每次发送重新复查身份/业务修订，
  并确认连接修订未轮换。

旧 `mcp_identity_bindings`、fixture 协议白名单和环境变量地址回退均已移除。
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from loguru import logger

from common.config import config
from common.config.logging_config import LoggingConfigurator
from common.identity.mcp_outbound import (
    MCPOutboundAuth,
    mcp_dispatch_scope,
    mcp_identity_scope,
)
from common.identity.runtime import RuntimeIdentity, identity_from_config
from common.mcp_authorization import (
    DEFAULT_PROVIDER,
    McpAuthorizationError,
    ResolvedConnection,
    resolve_connection,
)

LoggingConfigurator.configure()


class MCPResultText(str):
    """保留既有文本返回形状，并在进程内携带协议isError事实."""

    is_error: bool

    def __new__(cls, value, *, is_error=False):
        """创建带协议失败标记的兼容文本."""
        result = super().__new__(cls, value)
        result.is_error = is_error
        return result


# --------------------------------------------------------------------- 连接作用域注册表


@dataclass
class _ScopeState:
    tools: list[Any] = field(default_factory=list)
    discovered: bool = False
    last_error: str | None = None
    retry_after: float = 0.0


def server_clean_key(server_key: str) -> str:
    """唯一单射映射：将 _ 转义为 _u_，将 - 转义为 _h_，避免别名碰撞."""
    return (server_key or "").replace("_", "_u_").replace("-", "_h_")


class ScopedMCPToolFactory:
    """按业务连接作用域缓存发现结果并执行调用；跨线程/跨 event loop 去重."""

    def __init__(self, server_urls: dict[str, str] | None = None) -> None:
        """初始化工厂；`server_urls` 仅作为单元测试的显式注入，生产不使用."""
        # 测试注入 seam：生产 `get_factory()` 永远不传 URL，连接只来自受保护配置。
        self._urls = dict(server_urls or {})
        self._states: dict[tuple, _ScopeState] = {}
        self._lock = threading.RLock()
        self._loading: dict[tuple, threading.Event] = {}
        self._catalog: dict[str, dict[str, Any]] = {}
        self._catalog_server: dict[str, str] = {}
        self._tool_to_server: dict[str, set[str]] = {}
        self._aliases: dict[str, tuple[str, str]] = {}
        self._colliding_aliases: set[str] = set()

    def register_server(self, server_key: str, url: str) -> None:
        """测试注入服务地址（生产连接只来自受保护部署配置）."""
        with self._lock:
            self._urls[server_key] = url

    # -- 发现 ------------------------------------------------------------------

    def _resolve(
        self, identity: RuntimeIdentity, server_key: str
    ) -> ResolvedConnection:
        if not identity.business_code:
            raise McpAuthorizationError("缺少受理时冻结的业务许可，拒绝 MCP 调用")
        return resolve_connection(
            business_code=identity.business_code,
            tenant_id=identity.tenant_id,
            server_key=server_key,
            require_credential=True,
        )

    async def tools_for(
        self, identity: RuntimeIdentity, server_key: str, *, force_retry: bool = False
    ) -> list[Any]:
        """返回该身份当前业务/企业连接下已发现的可执行工具列表."""
        resolved = self._resolve(identity, server_key)
        with mcp_identity_scope(identity):
            state = await self._ensure_discovered(resolved, force_retry=force_retry)
        # 发现缓存可共享服务 schema，但披露必须与逐发送检查取相同交集。
        # 否则模型看得到不属于当前业务的工具，只能在执行时反复碰到 403。
        return [
            tool
            for tool in state.tools
            if resolved.business_scope.allows(tool.name)
            and resolved.connection.allows_tool(tool.name)
        ]

    async def _ensure_discovered(
        self, resolved: ResolvedConnection, *, force_retry: bool = False
    ) -> _ScopeState:
        key = resolved.cache_scope
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
            tools = await self._discover(resolved)
        except BaseException as exc:  # 含取消：必须释放门闩，失败退避按作用域隔离
            logger.warning(
                "MCP 作用域发现失败: scope={}, error={}",
                resolved.redacted(),
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
            self._index_tools_locked(tools, server_key=resolved.server_key)
            event = self._loading.pop(key, None)
            if event is not None:
                event.set()
        return state

    def _index_tools_locked(self, tools: list[Any], *, server_key: str) -> None:
        """在持锁状态下建立工具稳定 ID 与别名映射（无冲突覆盖）."""
        clean_key = server_clean_key(server_key)
        for tool in tools:
            stable_id = f"mcp:{server_key}:{tool.name}"
            alias = f"mcp_{clean_key}__{tool.name}"
            self._catalog[stable_id] = {
                "id": stable_id,
                "server_key": server_key,
                "name": tool.name,
                "display_name": getattr(tool, "display_name", tool.name) or tool.name,
                "description": (getattr(tool, "description", "") or tool.name)[:500],
                "args_schema": getattr(tool, "args_schema", None),
            }
            self._aliases[stable_id] = (server_key, tool.name)

            # 别名碰撞检测（如 service-a 与 service_a 生成相同 alias）
            if alias in self._aliases and self._aliases[alias][0] != server_key:
                self._aliases.pop(alias, None)
                self._catalog_server.pop(alias, None)
                self._colliding_aliases.add(alias)
            elif alias not in self._colliding_aliases:
                self._aliases[alias] = (server_key, tool.name)

            if tool.name not in self._tool_to_server:
                self._tool_to_server[tool.name] = set()
            self._tool_to_server[tool.name].add(server_key)

            if len(self._tool_to_server[tool.name]) == 1:
                self._aliases[tool.name] = (server_key, tool.name)
                self._catalog_server[tool.name] = server_key
            else:
                # 出现跨服务同名工具时，从全局别名移除原始短名，强制使用别名或显式服务
                self._aliases.pop(tool.name, None)
                self._catalog_server.pop(tool.name, None)

    async def _discover(self, resolved: ResolvedConnection) -> list[Any]:
        url = self._urls.get(resolved.server_key) or resolved.connection.url
        if not url:
            raise McpAuthorizationError("MCP 连接缺少地址")
        headers = DEFAULT_PROVIDER.auth_headers(resolved.connection)
        client = MultiServerMCPClient(
            {
                resolved.server_key: {
                    "url": url,
                    "transport": "streamable_http",
                    "headers": headers,
                    "auth": MCPOutboundAuth(resolved),
                }
            },
            tool_interceptors=[_build_interceptor(resolved)],
            # SDK默认把isError吞成普通列表，会绕过既有重试并误计为成功。
            handle_tool_errors=False,
        )
        return await client.get_tools(server_name=resolved.server_key)

    # -- 执行 ------------------------------------------------------------------

    async def call(
        self,
        identity: RuntimeIdentity,
        server_key: str,
        tool_name: str,
        args: dict[str, Any],
        run_config: Any = None,
    ) -> Any:
        """执行一次业务连接调用：每次重新解析连接（轮换/撤权即时生效）."""
        from common.tool_execution.policy import (
            authorize_selection,
            require_execution_policy,
        )

        require_execution_policy(identity)
        resolved_server = server_key
        target_name = tool_name
        with self._lock:
            if tool_name in self._aliases:
                s_key, t_name = self._aliases[tool_name]
                resolved_server = resolved_server or s_key
                target_name = t_name
            elif tool_name.startswith("mcp:"):
                parts = tool_name.split(":", 2)
                if len(parts) == 3:
                    resolved_server = resolved_server or parts[1]
                    target_name = parts[2]
        server_key = resolved_server
        cfg = run_config if isinstance(run_config, dict) else {}
        actual_args = deepcopy(args)
        await authorize_selection(identity, cfg, server_key, target_name)

        try:
            resolved = self._resolve(identity, server_key)
            # 业务白名单与连接白名单必须同时允许（未分类新工具不会自动开放）。
            if not resolved.business_scope.allows(target_name):
                raise McpAuthorizationError(
                    f"工具 {target_name} 不在业务声明的工具范围内"
                )
            if not resolved.connection.allows_tool(target_name):
                raise McpAuthorizationError(
                    f"工具 {target_name} 未在已审核查询工具清单中"
                )
            with mcp_identity_scope(identity):
                state = await self._ensure_discovered(resolved)
            clean_key = server_clean_key(server_key)
            tool = next(
                (
                    t
                    for t in state.tools
                    if t.name == target_name
                    or t.name == tool_name
                    or f"mcp:{server_key}:{t.name}" == tool_name
                    or f"mcp_{clean_key}__{t.name}" == tool_name
                ),
                None,
            )
            if tool is None:
                raise McpAuthorizationError("工具不在当前业务连接已发现清单内")
            disclosed = ((run_config or {}).get("metadata") or {}).get(
                "mcp_disclosed_schema"
            )
            if disclosed is not None:
                from common.tool_execution.schema import schema_version_for

                if disclosed != {
                    "tool_id": f"mcp:{server_key}:{tool.name}",
                    "schema_version": schema_version_for(server_key, tool),
                }:
                    raise McpAuthorizationError("工具定义已变化，拒绝发送旧schema调用")
        except McpAuthorizationError:
            from common.observability.context import observation_context_from_config
            from common.observability.tool_adapter import record_tool_result

            ctx = observation_context_from_config(run_config)
            if (
                ctx is not None
                and ctx.tenant_id == identity.tenant_id
                and ctx.user_id == identity.user_id
            ):
                await record_tool_result(
                    ctx,
                    logical_call_id=str(
                        ((run_config or {}).get("metadata") or {}).get(
                            "obs_tool_call_id"
                        )
                        or ""
                    )
                    or None,
                    tool_name=tool_name,
                    service_key=server_key,
                    status="permission_denied",
                    network_attempted=False,
                )
            raise
        from langchain_core.tools import ToolException

        from common.observability.tool_adapter import observed_tool_call

        async def checked_invoke():
            from common.tool_execution.policy import authorize_send

            await authorize_selection(identity, cfg, server_key, target_name)
            validator = await authorize_send(
                identity, server_key, target_name, tool, dict(cfg), actual_args
            )

            async def dispatch_guard():
                await authorize_selection(identity, cfg, server_key, target_name)
                if validator is not None:
                    validator()

            with mcp_dispatch_scope(dispatch_guard):
                return await tool.ainvoke(actual_args)

        try:
            with mcp_identity_scope(identity):
                result = await observed_tool_call(
                    run_config,
                    tool_name=tool_name,
                    service_key=server_key,
                    invoke=checked_invoke,
                )
        except BaseExceptionGroup as exc:
            denied, other = exc.split(McpAuthorizationError)
            if denied is not None and other is None:
                raise McpAuthorizationError("最终发送授权已拒绝") from exc
            raise
        except ToolException as exc:
            content = getattr(exc, "tool_content", None)
            if content is None:
                raise
            # 与SDK原有handle_tool_errors=True保持业务文本兼容；采集层已准确
            # 记录isError失败，不能把它吞成成功，也不改变既有调用方的异常契约。
            return MCPResultText(_normalize_call_output(content), is_error=True)
        return _normalize_call_output(result)

    def register_catalog(self, tools: list[Any], *, server_key: str) -> None:
        """把已发现目录并入 catalog（测试/受控预热用；不产生授权）."""
        with self._lock:
            self._index_tools_locked(tools, server_key=server_key)

    # -- 披露 ------------------------------------------------------------------

    def catalog_entries(self) -> list[dict[str, Any]]:
        """公共目录条目（schema 定义）."""
        with self._lock:
            return list(self._catalog.values())

    def server_of(self, tool_name: str) -> str | None:
        """工具所属 server_key."""
        with self._lock:
            if tool_name in self._aliases:
                return self._aliases[tool_name][0]
            if tool_name.startswith("mcp:"):
                parts = tool_name.split(":", 2)
                if len(parts) == 3:
                    return parts[1]
            if (
                tool_name in self._tool_to_server
                and len(self._tool_to_server[tool_name]) > 1
            ):
                return None
            return self._catalog_server.get(tool_name)

    def known_tool_names(self) -> set[str]:
        """公共目录中的工具名集合."""
        with self._lock:
            return {entry["name"] for entry in self._catalog.values()}

    def known_tool_ids(self) -> set[str]:
        """公共目录中的稳定工具 ID 集合."""
        with self._lock:
            return set(self._catalog)

    def authorized_servers_for_scope(
        self, tenant_id: str, business_code: str | None = None
    ) -> set[str]:
        """返回已为该企业/业务发现可用的服务集合（缓存键不含人员许可）."""
        with self._lock:
            servers: set[str] = set()
            for key, state in self._states.items():
                if len(key) >= 6 and str(key[0]) == str(tenant_id):
                    if business_code is None or str(key[1]) == str(business_code):
                        if state.discovered and not state.last_error:
                            servers.add(str(key[3]))
            return servers

    def reset_for_tests(self) -> None:
        """清空作用域状态与目录，供单元测试使用."""
        with self._lock:
            self._states.clear()
            self._loading.clear()
            self._catalog.clear()
            self._catalog_server.clear()
            self._tool_to_server.clear()
            self._aliases.clear()


def _build_interceptor(
    resolved: ResolvedConnection,
) -> Callable[..., Awaitable[Any]]:
    """发送边界拦截器：仅注入真实认证 header；不伪造 user_id/租户字段."""

    async def interceptor(
        request: MCPToolCallRequest,
        handler: Callable[[MCPToolCallRequest], Awaitable[Any]],
    ) -> Any:
        headers = dict(request.headers or {})
        headers.update(DEFAULT_PROVIDER.auth_headers(resolved.connection))
        return await handler(request.override(headers=headers))

    return interceptor


# --------------------------------------------------------------------- 全局单例与代理工具

_FACTORY: ScopedMCPToolFactory | None = None


def get_factory() -> ScopedMCPToolFactory:
    """返回进程级工厂单例（连接来自受保护部署配置，不读浏览器/环境地址）."""
    global _FACTORY
    if _FACTORY is None:
        _FACTORY = ScopedMCPToolFactory()
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
    return await get_factory().call(
        identity, server_key, tool_name, args, run_config=run_config
    )


def build_proxy_tools() -> list[StructuredTool]:
    """从已发现目录生成代理工具（仅承载 schema）.

    真实执行必须经 composed_tool_wrapper / DeepAgents 守卫层的
    `execute_mcp_tool`；直接调用代理协程属于绕过授权路径，一律失败关闭。
    """
    factory = get_factory()
    tools: list[StructuredTool] = []
    entries = factory.catalog_entries()
    name_counts: dict[str, int] = {}
    for entry in entries:
        tname = entry["name"]
        name_counts[tname] = name_counts.get(tname, 0) + 1

    for entry in entries:
        raw_name = entry["name"]
        server_key = entry["server_key"]
        clean_key = server_clean_key(server_key)
        if name_counts.get(raw_name, 0) > 1:
            tool_proxy_name = f"mcp_{clean_key}__{raw_name}"
        else:
            tool_proxy_name = raw_name

        def _make(
            p_name: str = tool_proxy_name,
            s_key: str = server_key,
            s_id: str = entry["id"],
            entry_ref: dict[str, Any] = entry,
        ) -> StructuredTool:
            async def _blocked(**kwargs: Any) -> Any:
                raise McpAuthorizationError(
                    "MCP 工具必须经授权执行层调用，直接调用被拒绝"
                )

            st = StructuredTool.from_function(
                coroutine=_blocked,
                name=p_name,
                description=entry_ref["description"] or p_name,
                args_schema=entry_ref["args_schema"],
                handle_tool_error=False,
                metadata={"server_key": s_key, "stable_id": s_id, "is_mcp": True},
            )
            st.__dict__["server_key"] = s_key
            st.__dict__["stable_id"] = s_id
            st.__dict__["is_mcp"] = True
            st.__dict__["_is_mcp"] = True
            return st

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
