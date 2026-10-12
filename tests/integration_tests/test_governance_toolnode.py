"""真实 ToolNode + 归档 + QuickJS 接线回归，模型与远端 MCP 不联网."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from common.business_graph import tool_wrappers as wrappers
from common.business_graph.state import BusinessGraphState
from common.computation.tool import compute_artifact
from common.storage import artifact_db


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URI", raising=False)
    monkeypatch.setenv("AUTH_SECRET", "test-only-scope-secret")
    monkeypatch.setattr(
        artifact_db, "_get_sqlite_path", lambda: str(tmp_path / "archive.db")
    )
    monkeypatch.setattr(artifact_db, "_initialized", False)
    body = (
        base64.urlsafe_b64encode(
            json.dumps(
                {"userId": "u", "threadId": "t", "exp": time.time() + 3600}
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        b"test-only-scope-secret", body.encode(), hashlib.sha256
    ).hexdigest()
    return {
        "configurable": {
            "thread_id": "t",
            "processing_run_id": "r",
            "__artifact_scope": body + "." + signature,
        }
    }


def graph(tools):
    builder = StateGraph(BusinessGraphState)
    builder.add_node(
        "tools",
        ToolNode(
            tools,
            handle_tool_errors=False,
            awrap_tool_call=wrappers.composed_tool_wrapper,
        ),
    )
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return builder.compile()


@pytest.mark.anyio
async def test_real_mcp_retry_manifest_and_full_compute(isolated, monkeypatch):
    calls = 0
    raw = json.dumps([{"value": i, "padding": "x" * 25} for i in range(10001)])

    @tool
    async def fetch_mcp() -> str:
        """获取完整数据."""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("transient")
        return raw

    monkeypatch.setattr(wrappers, "get_mcp_tool_names", lambda: ["fetch_mcp"])
    monkeypatch.setattr(wrappers, "_MCP_RETRY_BACKOFF_SECONDS", 0)
    runtime = graph([fetch_mcp, compute_artifact])
    state = await runtime.ainvoke(
        {
            "messages": [
                HumanMessage(content="全量求和", id="h"),
                AIMessage(
                    content="",
                    tool_calls=[{"id": "raw", "name": "fetch_mcp", "args": {}}],
                ),
            ]
        },
        isolated,
    )
    assert calls == 2
    archive_message = state["messages"][-1]
    assert archive_message.status == "success"
    manifest = json.loads(archive_message.content)
    assert manifest["collections"][0]["record_count"] == 10001
    artifact = manifest["artifact_id"]
    code = "let sum=0; helpers.scan('',row=>{sum+=row.value;}); return {result:sum,scope:'全部 10001 条输入，原始 value 单位'};"
    state["messages"].append(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "compute",
                    "name": "compute_artifact",
                    "args": {"artifact_id": artifact, "code": code},
                }
            ],
        )
    )
    result = await runtime.ainvoke(state, isolated)
    computed = json.loads(result["messages"][-1].content)
    assert computed["result"] == sum(range(10001))
    assert result["data_status"]["status"] == "completed"
    assert len(await artifact_db.list_tool_artifacts(user_id="u", thread_id="t")) == 2


@pytest.mark.anyio
async def test_business_400_and_validation_no_blind_retry(isolated, monkeypatch):
    @tool
    async def invalid_mcp() -> str:
        """返回参数校验错误."""
        return json.dumps(
            {"code": 400, "success": False, "message": "validation failed"}
        )

    runtime = graph([invalid_mcp])
    result = await runtime.ainvoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "bad", "name": "invalid_mcp", "args": {}}],
                )
            ]
        },
        isolated,
    )
    assert result["messages"][-1].status == "error"
    assert "参数校验" in result["messages"][-1].additional_kwargs["error_message"]


@pytest.mark.anyio
async def test_bubble_up_on_retry(isolated, monkeypatch):
    monkeypatch.setattr(wrappers, "get_mcp_tool_names", lambda: ["mcp"])
    monkeypatch.setattr(wrappers, "_MCP_RETRY_BACKOFF_SECONDS", 0)
    from types import SimpleNamespace

    request = SimpleNamespace(
        tool=object(),
        tool_call={"id": "c", "name": "mcp", "args": {}},
        runtime=SimpleNamespace(stream_writer=None, config=isolated),
    )
    handler = AsyncMock(
        side_effect=[ConnectionError("transient"), GraphBubbleUp("interrupt")]
    )
    with pytest.raises(GraphBubbleUp):
        await wrappers.composed_tool_wrapper(request, handler)


@pytest.mark.anyio
async def test_code_correction_limit_and_scope_refusal(isolated):
    artifact = await artifact_db.save_tool_artifact(
        tool_call_id="raw",
        tool_name="source",
        raw_payload='[{"value":7}]',
        user_id="u",
        thread_id="t",
        run_id="r",
    )
    runtime = graph([compute_artifact])
    messages = [HumanMessage(content="计算")]
    for attempt in range(3):
        messages.append(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": f"c{attempt}",
                        "name": "compute_artifact",
                        "args": {
                            "artifact_id": artifact,
                            "code": 'throw Error("bad code");',
                        },
                    }
                ],
            )
        )
        state = await runtime.ainvoke({"messages": messages}, isolated)
        assert state["messages"][-1].status == "error"
        assert state["messages"][-1].additional_kwargs["processing_terminal"] == (
            attempt >= 1
        )
        messages = state["messages"]


@pytest.mark.anyio
async def test_storage_failure_has_no_fake_reference(isolated, monkeypatch):
    @tool
    async def large_mcp() -> str:
        """返回超限数据."""
        return json.dumps([{"v": i, "padding": "x" * 20} for i in range(1000)])

    monkeypatch.setattr(
        wrappers,
        "save_tool_artifact",
        AsyncMock(side_effect=ConnectionError("storage unavailable")),
    )
    result = await graph([large_mcp]).ainvoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "c", "name": "large_mcp", "args": {}}],
                )
            ]
        },
        isolated,
    )
    message = result["messages"][-1]
    assert message.status == "error" and message.artifact is None
    assert "storage_failed" not in message.content


@pytest.mark.anyio
async def test_model_generation_shares_phase_timeout(isolated, monkeypatch):
    from langchain_core.messages import ToolMessage

    from common.data_governance import DataProcessingFailure, invoke_with_data_deadline

    artifact = await artifact_db.save_tool_artifact(
        tool_call_id="raw",
        tool_name="source",
        raw_payload="[]",
        user_id="u",
        thread_id="t",
        run_id="r",
    )
    state = [
        HumanMessage(content="完整统计"),
        ToolMessage(
            content=json.dumps({"kind": "structured_data", "artifact_id": artifact}),
            tool_call_id="raw",
            artifact={"storage_status": "stored", "artifact_id": artifact},
        ),
    ]
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(content="未经计算声称的统计")
    result = await invoke_with_data_deadline(model, state, state, isolated)
    assert "尚未完成全量计算" in result.content

    async def slow(*args, **kwargs):
        import asyncio

        await asyncio.sleep(1)
        return AIMessage(content="迟到")

    model.ainvoke.side_effect = slow
    from common import data_governance

    monkeypatch.setattr(
        data_governance,
        "claim_computation",
        AsyncMock(return_value={"remaining_seconds": 0.01, "attempt": 0}),
    )
    with pytest.raises(DataProcessingFailure):
        await invoke_with_data_deadline(model, state, state, isolated)


@pytest.mark.anyio
async def test_parallel_large_outputs_merge_status_without_conflict(isolated):
    @tool
    async def large_one() -> str:
        """返回第一份完整资料."""
        return json.dumps([{"value": i, "padding": "x" * 30} for i in range(1000)])

    @tool
    async def large_two() -> str:
        """返回第二份完整资料."""
        return json.dumps([{"value": i, "padding": "y" * 30} for i in range(1000)])

    runtime = graph([large_one, large_two])
    state = await runtime.ainvoke(
        {
            "messages": [
                HumanMessage(content="比较两份资料"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "a", "name": "large_one", "args": {}},
                        {"id": "b", "name": "large_two", "args": {}},
                    ],
                ),
            ]
        },
        isolated,
    )
    assert len(state["data_status"]["artifacts"]) == 2
    assert state["data_status"]["status"] == "started"
    assert len(await artifact_db.list_tool_artifacts(user_id="u", thread_id="t")) == 2
