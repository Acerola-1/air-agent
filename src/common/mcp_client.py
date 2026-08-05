"""MCP 客户端与工具延迟初始化."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, List

from langchain_mcp_adapters.client import MultiServerMCPClient
from loguru import logger

from common.config import config
from common.config.logging_config import LoggingConfigurator

LoggingConfigurator.configure()

ipp_mcp_client = MultiServerMCPClient(
    {
        "ipp-air-mcp-server": {
            "url": config.MCP_SERVER_URL,
            "transport": "streamable_http",
        }
    }
)
datacenter_mcp_client = MultiServerMCPClient(
    {
        "datacenter-statistics-mcp-server": {
            "url": config.MCP_SERVER_URL2,
            "transport": "streamable_http",
        }
    }
)

ipp_mcp_tools: List[Any] = []
datacenter_mcp_tools: List[Any] = []
ipp_mcp_tools_name: List[str] = []
datacenter_mcp_tools_name: List[str] = []
profile_mcp_tools: List[Any] = []
region_mcp_tools: List[Any] = []

_mcp_initialized: bool = False


@dataclass
class _MCPServerSpec:
    """单个 MCP server 的连接配置."""

    key: str
    server_name: str
    client_provider: Callable[[], MultiServerMCPClient]


@dataclass
class _MCPServerState:
    """单个 MCP server 的初始化状态."""

    tools: list[Any] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    initialized: bool = False
    last_error: str | None = None
    retry_after: float = 0.0


def _retry_backoff_seconds() -> int:
    return max(1, int(getattr(config, "MCP_RETRY_BACKOFF_SECONDS", 30)))


class MCPToolRegistry:
    """MCP 工具懒加载注册表.

    注册表只负责运行时生命周期策略：懒加载、部分成功、失败退避和
    并发去重。LangChain MCP adapter 仍然负责实际的 ``get_tools`` 调用。
    """

    def __init__(self, specs: list[_MCPServerSpec]) -> None:
        """使用 MCP server 配置初始化注册表."""
        self._specs = specs
        self._states = {spec.key: _MCPServerState() for spec in specs}
        self._lock = threading.RLock()
        self._loading = False
        self._loading_event = threading.Event()
        self._loading_event.set()

    @property
    def initialized(self) -> bool:
        """所有 MCP server 均初始化成功时返回 True."""
        with self._lock:
            return all(state.initialized for state in self._states.values())

    def get_tools(self, *keys: str) -> list[Any]:
        """返回当前已发现的工具."""
        with self._lock:
            states = self._selected_states(keys)
            return [tool for state in states for tool in state.tools]

    def get_tool_names(self, *keys: str) -> list[str]:
        """返回当前已发现的工具名."""
        with self._lock:
            states = self._selected_states(keys)
            return [name for state in states for name in state.tool_names]

    def get_permission_tools(self) -> list[Any]:
        """返回权限校验 MCP 工具."""
        return [
            tool
            for tool in self.get_tools("datacenter")
            if getattr(tool, "name", None) in ("permission_vaild",)
        ]

    def get_profile_tools(self) -> list[Any]:
        """返回用户权限画像 MCP 工具."""
        return [
            tool
            for tool in self.get_tools("datacenter")
            if getattr(tool, "name", None) == "get_user_profile"
        ]

    def get_region_tools(self) -> list[Any]:
        """返回行政区解析 MCP 工具."""
        return [
            tool
            for tool in self.get_tools("datacenter")
            if getattr(tool, "name", None) == "resolve_region_scope"
        ]

    def reset(self) -> None:
        """重置注册表状态，供测试使用."""
        with self._lock:
            for state in self._states.values():
                state.tools = []
                state.tool_names = []
                state.initialized = False
                state.last_error = None
                state.retry_after = 0.0
            self._loading = False
            self._loading_event.set()

    async def ensure_loaded(self, *, force_retry: bool = False) -> bool:
        """异步加载 MCP 工具，返回本轮是否实际发起加载."""
        if self.initialized and not force_retry:
            return False

        pending = await self._claim_pending(force_retry=force_retry)
        if not pending:
            return False

        logger.info(
            "开始异步初始化 MCP 工具: servers={}",
            [spec.server_name for spec in pending],
        )

        try:
            loaded_results = await asyncio.gather(
                *[
                    _load_server_tools(
                        client=spec.client_provider(),
                        server_name=spec.server_name,
                    )
                    for spec in pending
                ]
            )
        except BaseException:
            self._release_loading()
            raise

        with self._lock:
            for spec, loaded in zip(pending, loaded_results):
                state = self._states[spec.key]
                if loaded is not None:
                    state.tools = list(loaded)
                    state.tool_names = [tool.name for tool in state.tools]
                    state.initialized = True
                    state.last_error = None
                    state.retry_after = 0.0
                else:
                    state.initialized = False
                    state.last_error = f"{spec.server_name} unavailable"
                    state.retry_after = time.monotonic() + _retry_backoff_seconds()
            self._release_loading_locked()
        return True

    def ensure_loaded_sync(self) -> None:
        """同步加载 MCP 工具.

        同步入口只在当前线程没有运行中 event loop 时执行加载；已有 event loop
        的场景应走 async middleware，避免在事件循环线程里阻塞等待。
        """
        if self.initialized:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.ensure_loaded())
            return
        logger.debug("检测到运行中 event loop，跳过同步 MCP 初始化")

    async def _claim_pending(
        self,
        *,
        force_retry: bool,
    ) -> list[_MCPServerSpec]:
        """声明本轮需要加载的 server；其他并发调用等待当前加载完成."""
        while True:
            with self._lock:
                if self.initialized and not force_retry:
                    return []
                if self._loading:
                    event = self._loading_event
                else:
                    now = time.monotonic()
                    pending = [
                        spec
                        for spec in self._specs
                        if self._should_load(
                            self._states[spec.key],
                            now=now,
                            force_retry=force_retry,
                        )
                    ]
                    if not pending:
                        return []
                    self._loading = True
                    self._loading_event.clear()
                    return pending
            await _wait_thread_event(event)

    @staticmethod
    def _should_load(
        state: _MCPServerState,
        *,
        now: float,
        force_retry: bool,
    ) -> bool:
        if state.initialized and not force_retry:
            return False
        return force_retry or now >= state.retry_after

    def _release_loading(self) -> None:
        """释放全局加载标记."""
        with self._lock:
            self._release_loading_locked()

    def _release_loading_locked(self) -> None:
        self._loading = False
        self._loading_event.set()

    def _selected_states(self, keys: tuple[str, ...]) -> list[_MCPServerState]:
        if not keys:
            return [self._states[spec.key] for spec in self._specs]
        return [self._states[key] for key in keys if key in self._states]


_mcp_registry = MCPToolRegistry(
    [
        _MCPServerSpec(
            key="ipp",
            server_name="ipp-air-mcp-server",
            client_provider=lambda: ipp_mcp_client,
        ),
        _MCPServerSpec(
            key="datacenter",
            server_name="datacenter-statistics-mcp-server",
            client_provider=lambda: datacenter_mcp_client,
        ),
    ]
)


def _publish_registry_tools() -> None:
    """将注册表状态发布到兼容旧代码的模块级变量."""
    global \
        ipp_mcp_tools, \
        datacenter_mcp_tools, \
        ipp_mcp_tools_name, \
        datacenter_mcp_tools_name, \
        permission_mcp_tools, \
        profile_mcp_tools, \
        region_mcp_tools, \
        _mcp_initialized

    ipp_mcp_tools = _mcp_registry.get_tools("ipp")
    ipp_mcp_tools_name = _mcp_registry.get_tool_names("ipp")
    datacenter_mcp_tools = _mcp_registry.get_tools("datacenter")
    datacenter_mcp_tools_name = _mcp_registry.get_tool_names("datacenter")
    permission_mcp_tools = _mcp_registry.get_permission_tools()
    profile_mcp_tools = _mcp_registry.get_profile_tools()
    region_mcp_tools = _mcp_registry.get_region_tools()
    _mcp_initialized = _mcp_registry.initialized


def get_business_mcp_tools() -> list[Any]:
    """返回当前已发现的业务 MCP 工具."""
    return _mcp_registry.get_tools()


def get_business_mcp_tool_names() -> list[str]:
    """返回当前已发现的业务 MCP 工具名称."""
    return _mcp_registry.get_tool_names()


def get_permission_mcp_tools() -> list[Any]:
    """返回权限校验相关 MCP 工具（含 permission_vaild、get_user_profile、resolve_region_scope）."""
    return (
        _mcp_registry.get_permission_tools()
        + _mcp_registry.get_profile_tools()
        + _mcp_registry.get_region_tools()
    )


def get_profile_mcp_tools() -> list[Any]:
    """返回用户权限画像 MCP 工具."""
    return _mcp_registry.get_profile_tools()


def get_region_mcp_tools() -> list[Any]:
    """返回行政区解析 MCP 工具."""
    return _mcp_registry.get_region_tools()


def reset_mcp_state_for_tests() -> None:
    """重置 MCP 状态，供单元测试使用."""
    global _mcp_initialized
    _mcp_registry.reset()
    _mcp_initialized = False
    _publish_registry_tools()


async def _wait_thread_event(event: threading.Event) -> None:
    """异步等待跨线程 event，避免占用默认线程池阻塞等待."""
    while not event.is_set():
        await asyncio.sleep(0.01)


async def _load_server_tools(
    *,
    client: MultiServerMCPClient,
    server_name: str,
) -> list[Any] | None:
    """从单个 MCP 服务加载工具，失败时不拖垮整个服务."""
    try:
        return await client.get_tools(server_name=server_name)
    except Exception as exc:
        logger.exception(
            "MCP 工具加载失败，服务将降级运行: server={}, error={}",
            server_name,
            exc,
        )
        return None


async def ensure_mcp_tools(*, force_retry: bool = False) -> None:
    """异步填充 MCP 工具列表，仅在首次请求时执行."""
    did_load = await _mcp_registry.ensure_loaded(force_retry=force_retry)
    _publish_registry_tools()
    if not did_load:
        return
    if _mcp_initialized:
        logger.info(
            "MCP 工具初始化完成: ipp={}, datacenter={}",
            len(ipp_mcp_tools),
            len(datacenter_mcp_tools),
        )
        return
    logger.warning(
        "MCP 工具未完整初始化，服务将降级运行: ipp={}, datacenter={}",
        "ok" if ipp_mcp_tools else "unavailable",
        "ok" if datacenter_mcp_tools else "unavailable",
    )


def ensure_mcp_tools_sync() -> None:
    """同步初始化 MCP 工具，供 DeepAgent 图编译前使用."""
    _mcp_registry.ensure_loaded_sync()
    _publish_registry_tools()


logger.info("MCP 工具延迟初始化模式就绪（首次请求时异步填充）")
