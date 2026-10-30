"""Budget dormancy and material restoration through the actual request adapter."""

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from basic_qa.capabilities import projection as integration
from basic_qa.capabilities.state import (
    dormant_next,
    empty_capabilities,
    touch_executed,
)
from basic_qa.context.preparation import PreparedRequest


def state():
    caps = empty_capabilities(
        {"tenant_id": "t", "user_id": "u", "thread_id": "th", "graph_id": "basic-qa"}
    )
    caps["loaded_skills"] = {
        "older": {"last_used": 1, "load_ref": "a"},
        "needed": {"last_used": 2, "protected_attempt": "current", "load_ref": "b"},
    }
    caps["active_skill_ids"] = ["older", "needed"]
    caps["loaded_tools"] = {"mcp:ipp:independent": {"last_used": 3, "load_ref": "c"}}
    caps["independent_active_tool_ids"] = ["mcp:ipp:independent"]
    return {
        "messages": [HumanMessage(content="query", id="q")],
        "bqa_capabilities": caps,
    }


def test_lru_preserves_references_and_current_combination():
    original = state()
    caps = dormant_next(original, "current")
    assert caps["active_skill_ids"] == ["needed"]
    assert "older" in caps["loaded_skills"]
    assert original["bqa_capabilities"]["active_skill_ids"] == ["older", "needed"]
    caps = dormant_next({**original, "bqa_capabilities": caps}, "current")
    assert caps["independent_active_tool_ids"] == []
    assert dormant_next({**original, "bqa_capabilities": caps}, "current") is None


@pytest.mark.anyio
async def test_summary_updates_survive_dormancy_and_rules_schemas_leave_together(
    monkeypatch,
):
    import common.capabilities as capabilities

    monkeypatch.setattr(
        capabilities, "filter_capability_mcp_tools", lambda tools, *a, **k: tools
    )

    async def context(value, config):
        names = value["bqa_capabilities"]["active_skill_ids"]
        return "rules:" + ",".join(names), None

    monkeypatch.setattr(integration, "prompt_context", context)
    captures = []

    async def prepare(value, config, **kwargs):
        captures.append((value, kwargs))
        return PreparedRequest(
            [],
            [],
            {},
            updates={"summary": "retained", "summary_covered_message_id": "prior"},
            within_capacity="older" not in kwargs["system_messages"][0],
        )

    cfg = {
        "configurable": {
            "ledger_attempt_id": "current",
            "ledger_submission_id": "s",
            "capability_selection_id": "sel",
            "capability_selection_digest": "digest",
        }
    }
    result = await integration.prepare_projection(
        state(),
        cfg,
        base_prompt="base",
        addenda=["planning"],
        controls=[],
        role="planning",
        prepare=prepare,
        business=lambda **_: [],
    )
    prepared, context, _, working, _, _, _, _ = result
    assert not prepared.over_capacity and len(captures) == 2
    assert "older" not in context and "needed" in context
    assert prepared.updates["summary"] == "retained"
    assert prepared.updates["bqa_capabilities"]["active_skill_ids"] == ["needed"]
    assert working["bqa_capabilities"]["loaded_skills"]["older"]["load_ref"] == "a"
    assert captures[1][0]["summary_covered_message_id"] == "prior"


@pytest.mark.anyio
async def test_required_combination_cannot_fit_is_explained_without_truncation(
    monkeypatch,
):
    original = state()
    original["bqa_capabilities"]["active_skill_ids"] = ["needed"]
    original["bqa_capabilities"]["independent_active_tool_ids"] = []
    monkeypatch.setattr(
        integration,
        "prompt_context",
        AsyncMock(return_value=("complete needed rules", None)),
    )
    prepare = AsyncMock(
        return_value=PreparedRequest(
            [], [], {}, within_capacity=False, failure_reason="组合超出容量，请拆分任务"
        )
    )
    cfg = {
        "configurable": {
            "ledger_attempt_id": "current",
            "ledger_submission_id": "s",
            "capability_selection_id": "sel",
            "capability_selection_digest": "digest",
        }
    }
    result = await integration.prepare_projection(
        original,
        cfg,
        base_prompt="base",
        addenda=[],
        controls=[],
        role="answer",
        prepare=prepare,
        business=lambda **_: [],
    )
    assert result[0].over_capacity and "拆分" in result[0].failure_reason
    assert result[3]["bqa_capabilities"]["active_skill_ids"] == ["needed"]
    assert prepare.await_count == 1


def test_projection_only_strips_historical_body_not_checkpoint_or_usage():
    original = state()
    original["messages"] += [
        ToolMessage(
            content='{"status":"loaded","rules":"old body"}',
            name="load_skill",
            tool_call_id="a",
        )
    ]
    projected = integration.projected_state(original)
    assert "old body" not in projected["messages"][-1].content
    assert "old body" in original["messages"][-1].content
    original["bqa_disclosure"] = {
        "tools": {"mcp:ipp:one": "one"},
        "sources": {"mcp:ipp:one": ["needed", "independent"]},
    }
    touched = touch_executed(original, {"one"}, "new-attempt")
    assert touched["loaded_skills"]["needed"]["protected_attempt"] == "new-attempt"
    assert (
        original["bqa_capabilities"]["loaded_skills"]["needed"]["protected_attempt"]
        == "current"
    )
