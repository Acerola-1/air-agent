"""权限事实 MCP 缓存单元测试.

回归重点：偶发上游抖动返回的无效画像/未命中区域不得写入缓存，
否则会把一次瞬时失败放大成整个 TTL 窗口内的持续拒绝。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import common.permission.mcp_tools as mcp_tools
from common.permission.mcp_tools import (
    clear_permission_fact_caches,
    fetch_user_profile,
    resolve_region,
)

pytestmark = pytest.mark.anyio


def _profile_tool(result: object) -> SimpleNamespace:
    return SimpleNamespace(
        name="get_user_profile", ainvoke=AsyncMock(return_value=result)
    )


def _region_tool(result: object) -> SimpleNamespace:
    return SimpleNamespace(
        name="resolve_region_scope", ainvoke=AsyncMock(return_value=result)
    )


async def test_valid_profile_is_cached_and_reused() -> None:
    clear_permission_fact_caches()
    valid = {
        "found": True,
        "bound_region": {"level": "city", "name": "保定市"},
        "city": {"name": "保定市"},
    }
    tool = _profile_tool(valid)
    with patch.object(
        mcp_tools.mcp_client, "get_profile_mcp_tools", return_value=[tool]
    ):
        first = await fetch_user_profile("uid-1")
        second = await fetch_user_profile("uid-1")

    assert first == valid
    assert second == valid
    # 命中缓存，仅调用一次 MCP
    assert tool.ainvoke.await_count == 1


async def test_invalid_profile_is_not_cached_and_retries() -> None:
    """无效画像（如上游抖动返回 found=False）必须每次重试，不能被缓存放大."""
    clear_permission_fact_caches()
    invalid = {"found": False}
    tool = _profile_tool(invalid)
    with patch.object(
        mcp_tools.mcp_client, "get_profile_mcp_tools", return_value=[tool]
    ):
        first = await fetch_user_profile("uid-2")
        second = await fetch_user_profile("uid-2")

    assert first == invalid
    assert second == invalid
    # 未缓存，两次都真实调用 MCP（上游恢复后即可返回有效画像）
    assert tool.ainvoke.await_count == 2


async def test_profile_recovers_after_transient_failure() -> None:
    """先返回无效画像、后返回有效画像：第二次应拿到有效结果并开始缓存."""
    clear_permission_fact_caches()
    valid = {"found": True, "bound_region": {"level": "city", "name": "保定市"}}
    tool = SimpleNamespace(
        name="get_user_profile",
        ainvoke=AsyncMock(side_effect=[{"found": False}, valid, valid]),
    )
    with patch.object(
        mcp_tools.mcp_client, "get_profile_mcp_tools", return_value=[tool]
    ):
        assert (await fetch_user_profile("uid-3")) == {"found": False}
        assert (await fetch_user_profile("uid-3")) == valid
        # 第三次命中缓存，不再调用 MCP
        assert (await fetch_user_profile("uid-3")) == valid

    assert tool.ainvoke.await_count == 2


async def test_found_region_is_cached_but_unresolved_is_not() -> None:
    clear_permission_fact_caches()
    found_region = {"found": True, "region": {"level": "city", "name": "保定市"}}
    tool = _region_tool(found_region)
    with patch.object(
        mcp_tools.mcp_client, "get_region_mcp_tools", return_value=[tool]
    ):
        assert (await resolve_region("保定市")) == found_region
        assert (await resolve_region("保定市")) == found_region
    assert tool.ainvoke.await_count == 1

    clear_permission_fact_caches()
    not_found = {"found": False}
    tool2 = _region_tool(not_found)
    with patch.object(
        mcp_tools.mcp_client, "get_region_mcp_tools", return_value=[tool2]
    ):
        assert (await resolve_region("不存在的地方")) == not_found
        assert (await resolve_region("不存在的地方")) == not_found
    assert tool2.ainvoke.await_count == 2
