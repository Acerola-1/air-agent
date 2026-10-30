"""Focused checks for the Basic QA per-send policy."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from basic_qa import tool_policy
from basic_qa.capabilities import runtime, tool_definitions
from common.mcp_authorization import McpAuthorizationError
from common.tool_execution.schema import schema_version_for


def _tool():
    def query(city: str) -> str:
        """Query a city."""
        return city

    return StructuredTool.from_function(query, name="query")


@pytest.mark.asyncio
async def test_send_checks_binding_schema_args_without_load_gate(monkeypatch):
    tool = _tool()
    stable = "mcp:air:query"
    version = schema_version_for("air", tool)

    async def no_material_lookup(*args):
        raise AssertionError("execution must not inspect Skill or activation state")

    monkeypatch.setattr(runtime, "current_material", no_material_lookup)
    monkeypatch.setattr(tool_definitions, "mcp_stable_id", lambda name: stable)
    monkeypatch.setattr(tool_policy, "_is_mcp_tool", lambda request: True)
    request = SimpleNamespace(
        tool_call={"name": "query", "id": "call", "args": {}},
        state={
            "messages": [
                AIMessage(
                    content="",
                    id="message",
                    tool_calls=[{"name": "query", "id": "call", "args": {}}],
                )
            ],
            "bqa_disclosure": {
                "model_message_id": "message",
                "tools": {stable: "query"},
                "schema_versions": {stable: version},
            },
        },
    )
    await tool_policy.authorize_send(request, {}, "air", "query", tool, {})
    with pytest.raises(McpAuthorizationError):
        await tool_policy.authorize_send(
            request, {}, "air", "query", tool, {"city": "other"}
        )
    request.tool_call["args"] = {"enabled": True}
    request.state["messages"][-1].tool_calls[0]["args"] = {"enabled": True}
    await tool_policy.authorize_send(
        request, {}, "air", "query", tool, {"enabled": True}
    )
    with pytest.raises(McpAuthorizationError):
        await tool_policy.authorize_send(
            request, {}, "air", "query", tool, {"enabled": 1}
        )
    request.state["messages"][-1].tool_calls[0]["args"] = {"enabled": False}
    with pytest.raises(McpAuthorizationError):
        await tool_policy.authorize_send(
            request, {}, "air", "query", tool, {"enabled": True}
        )
    request.tool_call["args"] = {}
    request.state["messages"][-1].tool_calls[0]["args"] = {}

    validator = await tool_policy.authorize_send(request, {}, "air", "query", tool, {})
    request.state["bqa_capabilities"] = {"active_skill_ids": [], "loaded_tools": {}}
    validator()
    request.tool_call["args"] = {"city": "changed"}
    request.state["messages"][-1].tool_calls[0]["args"] = {"city": "changed"}
    with pytest.raises(McpAuthorizationError):
        validator()
    request.tool_call["args"] = {}
    request.state["messages"][-1].tool_calls[0]["args"] = {}

    request.state["messages"][-1].id = None
    request.state["bqa_disclosure"]["model_message_id"] = None
    with pytest.raises(McpAuthorizationError):
        await tool_policy.authorize_send(request, {}, "air", "query", tool, {})
    request.state["messages"][-1].id = "message"
    request.state["bqa_disclosure"]["model_message_id"] = "older"
    with pytest.raises(McpAuthorizationError):
        await tool_policy.authorize_send(request, {}, "air", "query", tool, {})
    request.state["bqa_disclosure"]["model_message_id"] = "message"
    # Valid tool definitions remain callable without any Skill or load record.
    await tool_policy.authorize_send(request, {}, "air", "query", tool, {})
    request.state["bqa_disclosure"]["schema_versions"][stable] = "changed"
    with pytest.raises(McpAuthorizationError):
        await tool_policy.authorize_send(request, {}, "air", "query", tool, {})
    with pytest.raises(McpAuthorizationError):
        await tool_policy.authorize_send(None, {}, "air", "query", tool, {})
    with pytest.raises(McpAuthorizationError):
        await tool_policy.authorize_send(request, {}, "other", "query", tool, {})
