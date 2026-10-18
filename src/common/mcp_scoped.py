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
import os
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
from common.identity.mcp_outbound import MCPOutboundAuth, mcp_identity_scope
from common.identity.runtime import RuntimeIdentity, identity_from_config
from common.mcp_authorization import (
    McpAuthorizationError,
    ScopeBinding,
    resolve_binding,
    verified_subject_fields,
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


# --------------------------------------------------------------------- 授权作用域注册表


@dataclass
class _ScopeState:
    tools: list[Any] = field(default_factory=list)
    discovered: bool = False
    last_error: str | None = None
    retry_after: float = 0.0


def server_clean_key(server_key: str) -> str:
    """唯一单射映射：将 _ 转义为 _u_，将 - 转义为 _h_，避免任意命名（如 svc_a 与 svc--a）别名碰撞."""
    return (server_key or "").replace("_", "_u_").replace("-", "_h_")


class ScopedMCPToolFactory:
    """按授权作用域缓存发现结果并执行调用；跨线程/跨 event loop 去重."""

    def __init__(self, server_urls: dict[str, str]) -> None:
        """以 server_key → url 映射初始化授权作用域工厂."""
        self._urls = dict(server_urls)
        self._states: dict[tuple, _ScopeState] = {}
        self._lock = threading.RLock()
        self._loading: dict[tuple, threading.Event] = {}
        self._catalog: dict[str, dict[str, Any]] = {}
        self._catalog_server: dict[str, str] = {}
        self._tool_to_server: dict[str, set[str]] = {}
        self._aliases: dict[str, tuple[str, str]] = {}
        self._colliding_aliases: set[str] = set()

    def register_server(self, server_key: str, url: str) -> None:
        """动态注册服务地址."""
        with self._lock:
            self._urls[server_key] = url

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
        with mcp_identity_scope(identity):
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
            self._index_tools_locked(tools, server_key=binding.server_key)
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

    async def _discover(self, binding: ScopeBinding) -> list[Any]:
        url = self._urls.get(binding.server_key)
        if not url:
            url = os.environ.get(
                f"MCP_SERVER_URL_{binding.server_key.upper().replace('-', '_')}"
            )
            if url:
                self._urls[binding.server_key] = url
        if not url and binding.server_key == config.MCP_SERVER_NAME:
            url = config.MCP_SERVER_URL
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
                    "auth": MCPOutboundAuth(binding),
                }
            },
            tool_interceptors=[_build_interceptor(binding)],
            # SDK默认把isError吞成普通列表，会绕过既有重试并误计为成功。
            handle_tool_errors=False,
        )
        return await client.get_tools(server_name=binding.server_key)

    # -- 执行 ------------------------------------------------------------------

    async def call(
        self,
        identity: RuntimeIdentity,
        server_key: str,
        tool_name: str,
        args: dict[str, Any],
        run_config: Any = None,
    ) -> Any:
        """执行一次授权调用：每次重新解析绑定（撤销/轮换即时生效）."""
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

        # 任务 4.3 执行侧调度约束：按已受理 MCP 白名单进行实时硬拦截（网络零调用）
        cfg = run_config if isinstance(run_config, dict) else {}
        configurable = cfg.get("configurable") or {}
        metadata = cfg.get("metadata") or {}
        mcp_mode = (
            configurable.get("capability_mcp_mode")
            or metadata.get("capability_mcp_mode")
            or "auto"
        )
        if mcp_mode == "allowlist":
            allowed_ids = configurable.get("capability_tool_ids")
            if allowed_ids is None:
                allowed_ids = metadata.get("capability_tool_ids") or []
            allowed_set = set(allowed_ids)
            clean_key = server_clean_key(server_key)
            stable_id = f"mcp:{server_key}:{target_name}"
            alias = f"mcp_{clean_key}__{target_name}"
            if (
                stable_id not in allowed_set
                and alias not in allowed_set
                and target_name not in allowed_set
                and tool_name not in allowed_set
            ):
                raise McpAuthorizationError(
                    f"未获准调用 MCP 工具 {stable_id}（不在本次已受理工具集合中）"
                )

        try:
            binding = await resolve_binding(
                tenant_id=identity.tenant_id,
                membership_id=identity.membership_id,
                server_key=server_key,
            )
            with mcp_identity_scope(identity):
                state = await self._ensure_discovered(binding)
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
                raise McpAuthorizationError("工具不在当前授权作用域已发现清单内")
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

        try:
            with mcp_identity_scope(identity):
                result = await observed_tool_call(
                    run_config,
                    tool_name=tool_name,
                    service_key=server_key,
                    invoke=lambda: tool.ainvoke(args),
                )
        except ToolException as exc:
            content = getattr(exc, "tool_content", None)
            if content is None:
                raise
            # 与SDK原有handle_tool_errors=True保持业务文本兼容；采集层已准确
            # 记录isError失败，不能把它吞成成功，也不改变既有调用方的异常契约。
            return MCPResultText(_normalize_call_output(content), is_error=True)
        return _normalize_call_output(result)

    def register_catalog(self, tools: list[Any], *, server_key: str) -> None:
        """把公共目录（schema 定义）并入 catalog（匿名 tools/list 的结果）."""
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
        self, tenant_id: str, membership_id: str | None = None
    ) -> set[str]:
        """返回已为该租户/成员发现可用的 MCP server_key 集合."""
        with self._lock:
            servers: set[str] = set()
            for key, state in self._states.items():
                if len(key) >= 4 and str(key[0]) == str(tenant_id):
                    if (
                        membership_id is None
                        or key[1] is None
                        or str(key[1]) == str(membership_id)
                    ):
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
    return await get_factory().call(
        identity, server_key, tool_name, args, run_config=run_config
    )


def build_proxy_tools() -> list[StructuredTool]:
    """从公共 schema 目录生成代理工具（仅承载 schema）.

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
