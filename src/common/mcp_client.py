"""MCP 公共 schema 目录与授权执行层接线（任务 7.4/7.5 重构）.

历史职责（懒发现、部分成功、退避、并发去重）保留，但语义收敛：

- 这里只维护**公共 schema 目录**：工具名/描述/入参结构（平台匿名 tools/list
  可枚举的定义数据，非业务数据）。发现结果注册进 `mcp_scoped.ScopedMCPToolFactory`
  的 catalog，供图/模型披露。
- **可执行工具对象不再从本模块流出**：实际调用必须经
  `mcp_scoped.execute_mcp_tool` → 按企业/成员授权作用域重新解析绑定 →
  拦截器在发送边界注入凭据与主体。无授权直接调用一律失败关闭；未核验协议
  下企业调用全部禁用（docs/mcp-auth-contract.md）。
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Any, List

from langchain_mcp_adapters.client import MultiServerMCPClient
from loguru import logger

from common.config import config
from common.config.logging_config import LoggingConfigurator
from common.mcp_scoped import build_proxy_tools, get_factory

LoggingConfigurator.configure()

# 兼容旧引用：datacenter MCP 已下线，权限工具不可用时自动降级。
datacenter_mcp_tools: List[Any] = []
datacenter_mcp_tools_name: List[str] = []
profile_mcp_tools: List[Any] = []
region_mcp_tools: List[Any] = []


@dataclass
class _ServerState:
    """公共目录发现（匿名 tools/list）的状态与退避."""

    discovered: bool = False
    last_error: str | None = None
    retry_after: float = 0.0


def _retry_backoff_seconds() -> int:
    return max(1, int(getattr(config, "MCP_RETRY_BACKOFF_SECONDS", 30)))


class MCPCatalogRegistry:
    """MCP 公共 schema 目录的懒加载注册表（跨线程/跨 event loop 去重）."""

    def __init__(self) -> None:
        """按 config 中的 server_key/url 初始化公共目录注册表."""
        self._server_key = config.MCP_SERVER_NAME
        self._url = config.MCP_SERVER_URL
        self._state = _ServerState()
        self._lock = threading.RLock()
        self._loading = False
        self._loading_event = threading.Event()
        self._loading_event.set()

    @property
    def discovered(self) -> bool:
        """公共目录是否已成功发现."""
        with self._lock:
            return self._state.discovered

    async def ensure_loaded(self, *, force_retry: bool = False) -> bool:
        """确保公共目录已发现；返回本轮是否实际发起加载."""
        if self.discovered and not force_retry:
            return False
        while True:
            with self._lock:
                state = self._state
                if state.discovered and not force_retry:
                    return False
                if self._loading:
                    event = self._loading_event
                elif force_retry or time.monotonic() >= state.retry_after:
                    self._loading = True
                    self._loading_event.clear()
                    break
                else:
                    return False
            await _wait_thread_event(event)

        try:
            tools = await self._discover_public()
        except asyncio.CancelledError:
            # 取消也必须释放加载门闩，否则后续请求永久等待
            with self._lock:
                self._loading = False
                self._loading_event.set()
            raise
        except Exception as exc:
            logger.exception(
                "MCP 公共目录发现失败，工具层降级为空目录: error={}", type(exc).__name__
            )
            with self._lock:
                state = self._state
                state.discovered = False
                state.last_error = type(exc).__name__
                state.retry_after = time.monotonic() + _retry_backoff_seconds()
                self._loading = False
                self._loading_event.set()
            return False
        with self._lock:
            get_factory().register_catalog(tools, server_key=self._server_key)
            self._state.discovered = True
            self._state.last_error = None
            self._state.retry_after = 0.0
            self._loading = False
            self._loading_event.set()
        logger.info("MCP 公共 schema 目录就绪: {} 个工具", len(tools))
        return True

    async def _discover_public(self) -> list[Any]:
        client = MultiServerMCPClient(
            {
                self._server_key: {
                    "url": self._url,
                    "transport": "streamable_http",
                }
            }
        )
        return await client.get_tools(server_name=self._server_key)

    def ensure_loaded_sync(self) -> None:
        """同步入口：仅在没有运行中 event loop 时执行加载."""
        if self.discovered:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.ensure_loaded())
            return
        logger.debug("检测到运行中 event loop，跳过同步 MCP 目录初始化")

    def reset_for_tests(self) -> None:
        """清空发现状态与作用域工厂，供单元测试使用."""
        with self._lock:
            self._state = _ServerState()
            self._loading = False
            self._loading_event.set()
        get_factory().reset_for_tests()


_registry = MCPCatalogRegistry()


def get_business_mcp_tools() -> list[Any]:
    """返回披露用代理工具（schema 来自公共目录；执行走授权工厂）."""
    return build_proxy_tools()


def get_business_mcp_tool_names() -> list[str]:
    """返回公共目录中已发现的业务 MCP 工具名（供治理/测试断言）."""
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
    _registry.reset_for_tests()


async def _wait_thread_event(event: threading.Event) -> None:
    """异步等待跨线程 event，避免占用默认线程池阻塞等待."""
    while not event.is_set():
        await asyncio.sleep(0.01)


async def ensure_mcp_tools(*, force_retry: bool = False) -> None:
    """异步填充公共 schema 目录（发现不是业务调用；调用授权另行解析）."""
    await _registry.ensure_loaded(force_retry=force_retry)


def ensure_mcp_tools_sync() -> None:
    """同步填充公共目录，供 DeepAgents 图编译前使用."""
    _registry.ensure_loaded_sync()


logger.info("MCP 公共目录懒加载 + 企业授权执行层就绪")
