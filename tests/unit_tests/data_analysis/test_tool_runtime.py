"""数据分析独立工具包装、重试和单工具资料治理契约."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command

import data_analysis.tool_runtime as runtime
from data_analysis import tool_policy


def request(name="query"):
    return SimpleNamespace(
        tool=object(),
        tool_call={"name": name, "id": "call-1", "args": {}},
        runtime=SimpleNamespace(stream_writer=None, config={}),
        state={"messages": [HumanMessage(content="分析")]},
    )


def test_graphs_have_independent_runtime_controllers(monkeypatch):
    import basic_qa.tool_runtime as basic_runtime

    assert tool_policy._composed_tool_wrapper is runtime.composed_tool_wrapper
    assert basic_runtime.composed_tool_wrapper is not runtime.composed_tool_wrapper
    assert basic_runtime._OBS_ATTEMPT_COUNTERS is not runtime._OBS_ATTEMPT_COUNTERS
    original = basic_runtime._invoke_mcp_with_retry
    monkeypatch.setattr(runtime, "_invoke_mcp_with_retry", object())
    assert basic_runtime._invoke_mcp_with_retry is original


@pytest.mark.anyio
async def test_mcp_retry_reenters_single_send_and_is_bounded(monkeypatch):
    monkeypatch.setattr("common.identity.runtime.guard_request_call", AsyncMock())
    monkeypatch.setattr("common.identity.runtime.identity_from_request", lambda _: None)
    monkeypatch.setattr(runtime, "_is_mcp_tool", lambda _: True)
    monkeypatch.setattr(runtime.asyncio, "sleep", AsyncMock())
    send = AsyncMock(
        side_effect=[
            RuntimeError("temporary"),
            ToolMessage(content="ok", tool_call_id="call-1", name="query"),
        ]
    )
    monkeypatch.setattr(runtime, "execute_mcp_tool_request", send)
    result = await tool_policy.composed_tool_wrapper(request(), AsyncMock())
    assert result.content == "ok"
    assert send.await_count == 2
    runtime.asyncio.sleep.assert_awaited_once_with(2.0)

    send.reset_mock(side_effect=True)
    send.side_effect = RuntimeError("unavailable")
    result = await tool_policy.composed_tool_wrapper(request(), AsyncMock())
    assert result.status == "error" and '"code": 503' in result.content
    assert send.await_count == 2


@pytest.mark.anyio
async def test_large_tool_result_is_saved_and_state_is_written(monkeypatch):
    monkeypatch.setattr("common.identity.runtime.guard_request_call", AsyncMock())
    monkeypatch.setattr(runtime, "_is_mcp_tool", lambda _: False)
    monkeypatch.setattr(runtime.config, "TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS", 5)
    monkeypatch.setattr(
        runtime, "runtime_scope", lambda _: ("tenant", "user", "thread", "run")
    )
    save = AsyncMock(return_value="art-1")
    summarize = AsyncMock(return_value=("summary", {"complete": True}))
    update = AsyncMock()
    monkeypatch.setattr(runtime, "save_tool_artifact", save)
    monkeypatch.setattr(runtime, "summarize_large_tool_output", summarize)
    monkeypatch.setattr(runtime, "update_artifact_summary", update)
    monkeypatch.setattr(runtime, "build_manifest", lambda *_: None)
    handler = AsyncMock(
        return_value=ToolMessage(
            content="full contents", tool_call_id="call-1", name="query"
        )
    )
    result = await tool_policy.composed_tool_wrapper(request(), handler)
    assert isinstance(result, Command)
    message = result.update["messages"][0]
    assert message.content == "summary"
    assert message.artifact["storage_status"] == "stored"
    assert result.update["data_status"]["status"] == "completed"
    assert save.await_args.kwargs["raw_payload"] == "full contents"
    summarize.assert_awaited_once()
    update.assert_awaited_once()


@pytest.mark.anyio
async def test_large_tool_result_storage_failure_stays_error(monkeypatch):
    monkeypatch.setattr("common.identity.runtime.guard_request_call", AsyncMock())
    monkeypatch.setattr(runtime, "_is_mcp_tool", lambda _: False)
    monkeypatch.setattr(runtime.config, "TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS", 5)
    monkeypatch.setattr(
        runtime, "runtime_scope", lambda _: ("tenant", "user", "thread", "run")
    )
    monkeypatch.setattr(
        runtime,
        "save_tool_artifact",
        AsyncMock(side_effect=RuntimeError("db unavailable")),
    )
    handler = AsyncMock(
        return_value=ToolMessage(
            content="full contents", tool_call_id="call-1", name="query"
        )
    )
    result = await tool_policy.composed_tool_wrapper(request(), handler)
    assert isinstance(result, Command)
    assert result.update["messages"][0].status == "error"
    assert result.update["messages"][0].artifact is None
    assert result.update["data_status"]["status"] == "failed"
