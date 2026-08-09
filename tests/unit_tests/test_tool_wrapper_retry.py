"""composed_tool_wrapper MCP 代码级重试单元测试.

回归重点：重试策略已从 skill 提示词（LLM 驱动、每次多烧一轮 LLM）
下沉到代码级——MCP 远程工具失败后退避重试 1 次；本地工具不重试。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import ToolMessage

import common.business_graph.tool_wrappers as tw

pytestmark = pytest.mark.anyio


def _request(name: str) -> SimpleNamespace:
    """构造最小工具调用请求：已绑定工具实例、无 stream_writer."""
    return SimpleNamespace(
        tool=object(),
        tool_call={"name": name, "args": {}, "id": "call-1"},
        runtime=SimpleNamespace(stream_writer=None),
    )


def _ok_message(name: str) -> ToolMessage:
    return ToolMessage(content="ok", tool_call_id="call-1", name=name)


async def _run(request: SimpleNamespace, handler: Any) -> Any:
    """在 MCP 名单/退避/富输出均被隔离的环境下执行包装器."""
    with (
        patch.object(tw, "get_mcp_tool_names", return_value=["mcp_tool_a"]),
        patch.object(tw.asyncio, "sleep", new=AsyncMock()) as sleep_mock,
        patch.object(tw, "_handle_artifact", side_effect=lambda _r, res: res),
    ):
        result = await tw.composed_tool_wrapper(request, handler)
    return result, sleep_mock


async def test_mcp_tool_retry_succeeds_on_second_attempt() -> None:
    request = _request("mcp_tool_a")
    handler = AsyncMock(side_effect=[RuntimeError("抖动"), _ok_message("mcp_tool_a")])

    result, sleep_mock = await _run(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.content == "ok"
    assert handler.await_count == 2
    sleep_mock.assert_awaited_once_with(tw._MCP_RETRY_BACKOFF_SECONDS)


async def test_mcp_tool_retry_exhausted_degrades_to_fallback() -> None:
    request = _request("mcp_tool_a")
    handler = AsyncMock(side_effect=RuntimeError("持续失败"))

    result, _ = await _run(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert '"code": 503' in str(result.content)
    # 首次 + 重试 1 次
    assert handler.await_count == 1 + tw._MCP_RETRY_ATTEMPTS


async def test_local_tool_failure_no_retry() -> None:
    request = _request("text2sql_local_tool")
    handler = AsyncMock(side_effect=RuntimeError("确定性错误"))

    result, sleep_mock = await _run(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert '"code": 500' in str(result.content)
    assert handler.await_count == 1
    sleep_mock.assert_not_awaited()
