"""MCP 初始化韧性测试."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest

import common.mcp_client as mcp_client

pytestmark = pytest.mark.anyio


class FakeMCPClient:
    def __init__(
        self,
        tools: list[Any] | None = None,
        *,
        gate: asyncio.Event | None = None,
        fail: bool = False,
    ) -> None:
        self.tools = tools or []
        self.gate = gate
        self.fail = fail
        self.calls = 0

    async def get_tools(self, *, server_name: str) -> list[Any]:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError(f"{server_name} failed")
        return self.tools


async def test_ensure_mcp_tools_loads_servers_in_parallel(monkeypatch) -> None:
    mcp_client.reset_mcp_state_for_tests()
    started: list[str] = []
    gate = asyncio.Event()

    class BlockingClient(FakeMCPClient):
        async def get_tools(self, *, server_name: str) -> list[Any]:
            self.calls += 1
            started.append(server_name)
            if len(started) == 2:
                gate.set()
            await gate.wait()
            return self.tools

    ipp = BlockingClient([SimpleNamespace(name="ipp_tool")])
    datacenter = BlockingClient([SimpleNamespace(name="permission_vaild")])
    monkeypatch.setattr(mcp_client, "ipp_mcp_client", ipp)
    monkeypatch.setattr(mcp_client, "datacenter_mcp_client", datacenter)

    await asyncio.wait_for(mcp_client.ensure_mcp_tools(), timeout=0.2)

    assert ipp.calls == 1
    assert datacenter.calls == 1
    assert mcp_client.ipp_mcp_tools_name == ["ipp_tool"]
    assert mcp_client.datacenter_mcp_tools_name == ["permission_vaild"]
    assert [tool.name for tool in mcp_client.permission_mcp_tools] == [
        "permission_vaild"
    ]


async def test_ensure_mcp_tools_keeps_partial_success_and_backs_off(
    monkeypatch,
) -> None:
    mcp_client.reset_mcp_state_for_tests()
    ipp = FakeMCPClient([SimpleNamespace(name="ipp_tool")])
    datacenter = FakeMCPClient(fail=True)
    monkeypatch.setattr(mcp_client, "ipp_mcp_client", ipp)
    monkeypatch.setattr(mcp_client, "datacenter_mcp_client", datacenter)
    monkeypatch.setattr(mcp_client, "_retry_backoff_seconds", lambda: 60)

    await mcp_client.ensure_mcp_tools()
    await mcp_client.ensure_mcp_tools()

    assert ipp.calls == 1
    assert datacenter.calls == 1
    assert mcp_client.ipp_mcp_tools_name == ["ipp_tool"]
    assert mcp_client.datacenter_mcp_tools_name == []


async def test_concurrent_ensure_mcp_tools_deduplicates_discovery(monkeypatch) -> None:
    mcp_client.reset_mcp_state_for_tests()
    gate = asyncio.Event()
    ipp = FakeMCPClient([SimpleNamespace(name="ipp_tool")], gate=gate)
    datacenter = FakeMCPClient([SimpleNamespace(name="dc_tool")], gate=gate)
    monkeypatch.setattr(mcp_client, "ipp_mcp_client", ipp)
    monkeypatch.setattr(mcp_client, "datacenter_mcp_client", datacenter)

    tasks = [asyncio.create_task(mcp_client.ensure_mcp_tools()) for _ in range(3)]
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(*tasks)

    assert ipp.calls == 1
    assert datacenter.calls == 1


async def test_cancelled_initialization_allows_next_retry(monkeypatch) -> None:
    mcp_client.reset_mcp_state_for_tests()
    gate = asyncio.Event()
    ipp = FakeMCPClient([SimpleNamespace(name="ipp_tool")], gate=gate)
    datacenter = FakeMCPClient([SimpleNamespace(name="dc_tool")], gate=gate)
    monkeypatch.setattr(mcp_client, "ipp_mcp_client", ipp)
    monkeypatch.setattr(mcp_client, "datacenter_mcp_client", datacenter)

    task = asyncio.create_task(mcp_client.ensure_mcp_tools())
    for _ in range(20):
        if ipp.calls == 1 and datacenter.calls == 1:
            break
        await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    gate.set()
    await mcp_client.ensure_mcp_tools()

    assert ipp.calls == 2
    assert datacenter.calls == 2
    assert mcp_client.ipp_mcp_tools_name == ["ipp_tool"]
    assert mcp_client.datacenter_mcp_tools_name == ["dc_tool"]


async def test_cross_thread_event_loop_ensure_mcp_tools_deduplicates_discovery(
    monkeypatch,
) -> None:
    mcp_client.reset_mcp_state_for_tests()
    release = threading.Event()
    entered = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    class ThreadBlockingClient(FakeMCPClient):
        async def get_tools(self, *, server_name: str) -> list[Any]:
            nonlocal call_count
            with call_lock:
                call_count += 1
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            return self.tools

    ipp = ThreadBlockingClient([SimpleNamespace(name="ipp_tool")])
    datacenter = ThreadBlockingClient([SimpleNamespace(name="dc_tool")])
    monkeypatch.setattr(mcp_client, "ipp_mcp_client", ipp)
    monkeypatch.setattr(mcp_client, "datacenter_mcp_client", datacenter)

    first = threading.Thread(target=mcp_client.ensure_mcp_tools_sync)
    second = threading.Thread(target=mcp_client.ensure_mcp_tools_sync)
    first.start()
    assert entered.wait(timeout=1)
    second.start()
    await asyncio.sleep(0.05)
    release.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert call_count == 2
    assert mcp_client.ipp_mcp_tools_name == ["ipp_tool"]
    assert mcp_client.datacenter_mcp_tools_name == ["dc_tool"]
