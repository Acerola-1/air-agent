"""MCP 容错中间件测试."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

MCP_RESILIENCE_MIDDLEWARE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "common"
    / "middleware"
    / "mcp_resilience_middleware.py"
)
spec = importlib.util.spec_from_file_location(
    "mcp_resilience_middleware_under_test",
    MCP_RESILIENCE_MIDDLEWARE_PATH,
)
assert spec is not None
assert spec.loader is not None
mcp_resilience_middleware = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcp_resilience_middleware)
MCPResilienceMiddleware = mcp_resilience_middleware.MCPResilienceMiddleware


def _request(tool_name: str, events: list[dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": tool_name, "id": "call-1"},
        runtime=SimpleNamespace(stream_writer=events.append),
    )


def test_mcp_tool_failure_is_converted_to_tool_message() -> None:
    middleware = MCPResilienceMiddleware(["mcp_tool"])
    events: list[dict[str, Any]] = []

    result = middleware.wrap_tool_call(
        _request("mcp_tool", events),
        lambda _request: (_ for _ in ()).throw(RuntimeError("server down")),
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call-1"
    assert json.loads(result.content) == {
        "code": 503,
        "success": False,
        "message": "暂未获取到相关数据，请稍后重试。",
    }
    assert events == [
        {
            "node": "mcp_tool_call",
            "type": "external_service_status",
            "status": "unavailable",
            "tool_name": "mcp_tool",
            "message": "暂未获取到相关数据，已跳过本次查询",
        }
    ]


def test_non_mcp_tool_failure_is_not_swallowed() -> None:
    middleware = MCPResilienceMiddleware(["mcp_tool"])
    events: list[dict[str, Any]] = []

    with pytest.raises(RuntimeError, match="server down"):
        middleware.wrap_tool_call(
            _request("local_tool", events),
            lambda _request: (_ for _ in ()).throw(RuntimeError("server down")),
        )

    assert events == []


@pytest.mark.anyio
async def test_async_mcp_tool_failure_is_converted_to_tool_message() -> None:
    middleware = MCPResilienceMiddleware(["mcp_tool"])
    events: list[dict[str, Any]] = []

    async def failing_handler(_request: Any) -> ToolMessage:
        raise RuntimeError("server down")

    result = await middleware.awrap_tool_call(
        _request("mcp_tool", events), failing_handler
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert json.loads(result.content)["success"] is False
