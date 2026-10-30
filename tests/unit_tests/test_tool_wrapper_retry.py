"""composed_tool_wrapper MCP 代码级重试单元测试（已迁移到企业授权执行层契约）.

回归重点：重试策略在代码级（失败退避重试 1 次；本地工具不重试）；MCP 工具
经 execute_mcp_tool_request → 授权执行层，重试前重新进入执行层（授权复查）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import ToolMessage

import basic_qa.tool_runtime as tw

pytestmark = pytest.mark.anyio


_AUTH_CONFIG = {
    "configurable": {
        "thread_id": "t",
        "langgraph_auth_user": {
            "identity": "u-1",
            "tenant_id": "t-a",
            "membership_id": "m-a",
            "session_id": "s-1",
        },
    }
}


def _request(name: str) -> SimpleNamespace:
    """构造最小工具调用请求：已绑定工具实例、无 stream_writer、含可信运行身份."""
    return SimpleNamespace(
        tool=object(),
        tool_call={"name": name, "args": {}, "id": "call-1"},
        runtime=SimpleNamespace(stream_writer=None, config=dict(_AUTH_CONFIG)),
    )


def _ok_message(name: str) -> ToolMessage:
    return ToolMessage(content="ok", tool_call_id="call-1", name=name)


async def _run(request: SimpleNamespace, handler: Any) -> Any:
    """在 MCP 名单/授权执行/守卫复查/退避均被隔离的环境下执行包装器."""
    import common.mcp_scoped as scoped
    from common.identity.db import IdentityState

    ok_state = IdentityState(
        session_active=True,
        session_unexpired=True,
        session_ctx_version=1,
        user_active=True,
        account="u",
        user_auth_revision=0,
        tenant_status="active",
        tenant_auth_revision=1,
        tenant_code="t-a",
        membership_status="active",
        membership_revision=1,
    )
    with (
        patch(
            "common.tool_execution.mcp.get_mcp_tool_names", return_value=["mcp_tool_a"]
        ),
        patch.object(tw.asyncio, "sleep", new=AsyncMock()) as sleep_mock,
        patch.object(scoped, "execute_mcp_tool", handler),
        patch.object(scoped.get_factory(), "server_of", return_value="ipp"),
        patch(
            "common.identity.runtime.fetch_identity_state",
            new=AsyncMock(return_value=ok_state),
        ),
    ):
        from basic_qa.tool_recovery import exhausted_tools

        blocked = exhausted_tools(
            (getattr(request, "state", None) or {}).get("messages", [])
        )
        result = await tw.composed_tool_wrapper(
            request, handler, exhausted_tools=blocked
        )
    return result, sleep_mock


async def test_mcp_tool_retry_succeeds_on_second_attempt() -> None:
    request = _request("mcp_tool_a")
    handler = AsyncMock(side_effect=[RuntimeError("抖动"), "ok"])

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
    # 首次 + 重试 1 次（重试重新进入授权执行层，而非复用旧许可）
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


async def test_parameter_error_is_actionable_without_leaking_input() -> None:
    from pydantic import BaseModel, ValidationError

    class Args(BaseModel):
        city_id: int

    try:
        Args(city_id="private-input-do-not-echo")
    except ValidationError as exc:
        error = exc
    handler = AsyncMock(side_effect=error)
    result, sleep_mock = await _run(_request("local_query"), handler)
    payload = json.loads(result.content)
    assert payload["code"] == 422
    assert payload["fields"][0]["field"] == "city_id"
    assert "private-input-do-not-echo" not in result.content
    handler.assert_awaited_once()
    sleep_mock.assert_not_awaited()


async def test_exhausted_tool_does_not_reach_handler_or_network() -> None:
    from langchain_core.messages import HumanMessage

    request = _request("local_query")
    request.state = {
        "messages": [
            HumanMessage(content="查询"),
            *[
                ToolMessage(
                    name="local_query",
                    content='{"code":422,"success":false}',
                    tool_call_id=f"failed-{i}",
                    status="error",
                )
                for i in range(2)
            ],
        ]
    }
    handler = AsyncMock()
    result, _ = await _run(request, handler)
    assert result.status == "error"
    assert "耗尽" in result.content
    handler.assert_not_awaited()
