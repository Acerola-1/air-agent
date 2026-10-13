"""MCP 公共目录韧性测试（7.5：迁移到身份作用域契约）.

原测试针对"两个全局共享 server"；datacenter MCP 下线与目录/执行分层后：
- ensure_mcp_tools 只填充**公共 schema 目录**（匿名 tools/list），发现结果注册
  进授权工厂 catalog；失败退避、并发去重、取消重试语义在目录层保留。
- 真实调用按企业授权作用域执行（去重、隔离、轮换见
  test_mcp_scoped_authorization.py）。
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest

import common.mcp_client as mcp_client

pytestmark = pytest.mark.anyio


def _fake_tools(*names: str) -> list[Any]:
    tools = []
    for name in names:
        tool = SimpleNamespace(
            name=name,
            description=name,
            args_schema={"type": "object", "properties": {}},
        )
        tools.append(tool)
    return tools


async def test_ensure_mcp_tools_populates_public_catalog(monkeypatch) -> None:
    mcp_client.reset_mcp_state_for_tests()
    calls = {"n": 0}

    async def _fake_discover(self) -> list[Any]:
        calls["n"] += 1
        return _fake_tools("ipp_tool")

    monkeypatch.setattr(
        mcp_client.MCPCatalogRegistry, "_discover_public", _fake_discover
    )
    await mcp_client.ensure_mcp_tools()
    await mcp_client.ensure_mcp_tools()  # 已发现：不再重复
    assert calls["n"] == 1
    assert mcp_client.get_business_mcp_tool_names() == ["ipp_tool"]


async def test_discovery_failure_backs_off_without_blocking_others(monkeypatch) -> None:
    mcp_client.reset_mcp_state_for_tests()
    calls = {"n": 0}

    async def _fail(self) -> list[Any]:
        calls["n"] += 1
        raise RuntimeError("server down")

    monkeypatch.setattr(mcp_client.MCPCatalogRegistry, "_discover_public", _fail)
    monkeypatch.setattr(mcp_client, "_retry_backoff_seconds", lambda: 60)

    await mcp_client.ensure_mcp_tools()
    await mcp_client.ensure_mcp_tools()  # 退避窗口内不再重试
    assert calls["n"] == 1
    assert mcp_client.get_business_mcp_tool_names() == []


async def test_concurrent_ensure_deduplicates_discovery(monkeypatch) -> None:
    mcp_client.reset_mcp_state_for_tests()
    gate = asyncio.Event()
    calls = {"n": 0}

    async def _blocked(self) -> list[Any]:
        calls["n"] += 1
        await gate.wait()
        return _fake_tools("ipp_tool")

    monkeypatch.setattr(mcp_client.MCPCatalogRegistry, "_discover_public", _blocked)

    tasks = [asyncio.create_task(mcp_client.ensure_mcp_tools()) for _ in range(3)]
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.gather(*tasks)
    assert calls["n"] == 1


async def test_cancelled_initialization_allows_next_retry(monkeypatch) -> None:
    mcp_client.reset_mcp_state_for_tests()
    gate = asyncio.Event()
    calls = {"n": 0}

    async def _blocked(self) -> list[Any]:
        calls["n"] += 1
        await gate.wait()
        return _fake_tools("ipp_tool")

    monkeypatch.setattr(mcp_client.MCPCatalogRegistry, "_discover_public", _blocked)

    task = asyncio.create_task(mcp_client.ensure_mcp_tools())
    for _ in range(50):
        if calls["n"] == 1:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    gate.set()
    await mcp_client.ensure_mcp_tools()
    assert calls["n"] == 2


async def test_cross_thread_event_loop_ensure_deduplicates(monkeypatch) -> None:
    """跨线程/跨 event loop：同步入口同进程只实际发现一次（第二轮等首轮回池）."""
    mcp_client.reset_mcp_state_for_tests()
    release = threading.Event()
    entered = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    async def _blocked(self) -> list[Any]:
        nonlocal call_count
        with call_lock:
            call_count += 1
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return _fake_tools("ipp_tool")

    monkeypatch.setattr(mcp_client.MCPCatalogRegistry, "_discover_public", _blocked)

    first = threading.Thread(target=mcp_client.ensure_mcp_tools_sync)
    second = threading.Thread(target=mcp_client.ensure_mcp_tools_sync)
    first.start()
    assert entered.wait(timeout=2)
    second.start()
    await asyncio.sleep(0.05)
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert call_count == 1


async def test_output_catalog_has_no_secrets(monkeypatch) -> None:
    """公共目录只含 schema 定义（名称/描述/参数结构），不含凭据字段."""
    mcp_client.reset_mcp_state_for_tests()

    async def _fake_discover(self) -> list[Any]:
        return _fake_tools("query_air")

    monkeypatch.setattr(
        mcp_client.MCPCatalogRegistry, "_discover_public", _fake_discover
    )
    await mcp_client.ensure_mcp_tools()
    from common.mcp_scoped import get_factory

    blob = str(get_factory().catalog_entries()).lower()
    for word in ("authorization", "bearer", "token", "password", "credential_ref"):
        assert word not in blob
