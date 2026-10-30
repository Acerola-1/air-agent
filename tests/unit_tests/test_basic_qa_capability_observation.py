"""Facts distinguish load, reuse and execution and never carry query/result bodies."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from basic_qa.capabilities import observation as obs

VERSION = "sha256:" + "a" * 64
SKILL = "skill:basic-qa:air-quality-basic-query"
TOOL = "mcp:ipp:query"


def request(name, state=None):
    return SimpleNamespace(
        tool_call={
            "name": name,
            "id": "call-1",
            "args": {"query": "private-query-marker", "password": "secret-marker"},
        },
        state=state or {},
    )


def receipt(name, payload, *, network=None):
    return ToolMessage(
        content=json.dumps(payload),
        name=name,
        tool_call_id="call-1",
        additional_kwargs={} if network is None else {"network_attempted": network},
    )


def test_direct_load_is_not_matched_and_repeat_does_not_make_new_load(monkeypatch):
    fact = Mock()
    stage = Mock()
    monkeypatch.setattr(obs, "emit_fact", fact)
    monkeypatch.setattr("common.observability.skill_adapter.emit_skill_stage", stage)
    load = receipt(
        "load_skill",
        {"status": "loaded", "skill_id": SKILL, "material_version": VERSION},
    )
    obs.observe_control({}, request("load_skill"), Command(update={"messages": [load]}))
    assert [c.kwargs["action"] for c in fact.call_args_list] == ["selected", "loaded"]
    assert [c.args[2] for c in stage.call_args_list] == ["selected", "loaded"]
    fact.reset_mock()
    stage.reset_mock()
    obs.observe_control(
        {},
        request("load_skill"),
        receipt(
            "load_skill",
            {
                "status": "already_active",
                "skill_id": SKILL,
                "material_version": VERSION,
            },
        ),
    )
    assert [c.kwargs["action"] for c in fact.call_args_list] == ["selected", "reused"]
    stage.assert_not_called()
    assert "executed" not in [c.kwargs["action"] for c in fact.call_args_list]


def test_top3_candidates_and_embedding_counts_are_control_facts(monkeypatch):
    fact = Mock()
    monkeypatch.setattr(obs, "emit_fact", fact)
    obs.observe_control(
        {},
        request("search_mcp"),
        receipt(
            "search_mcp",
            {
                "status": "ok",
                "catalog_version": VERSION,
                "candidates": [{"tool_id": f"mcp:ipp:t{i}"} for i in range(3)],
                "embedding_counts": {"documents": 109, "queries": 1},
            },
        ),
    )
    assert fact.call_args.kwargs["action"] == "searched"
    assert len(fact.call_args.kwargs["candidates"]) == 3
    assert fact.call_args.kwargs["embedding_counts"] == {"documents": 109, "queries": 1}
    assert "private-query-marker" not in str(fact.call_args)


def test_reused_skill_and_independent_sources_do_not_duplicate_tool_execution(
    monkeypatch,
):
    fact = Mock()
    stage = Mock()
    monkeypatch.setattr(obs, "emit_fact", fact)
    monkeypatch.setattr("common.observability.skill_adapter.emit_skill_stage", stage)
    monkeypatch.setattr(
        "basic_qa.capabilities.projection.mcp_stable_id", lambda _: TOOL
    )
    state = {
        "bqa_disclosure": {
            "sources": {TOOL: [SKILL, "independent"]},
            "schema_versions": {TOOL: VERSION},
        },
        "bqa_capabilities": {
            "loaded_skills": {
                SKILL: {"material_version": VERSION, "protected_attempt": "old"}
            },
            "loaded_tools": {
                TOOL: {"schema_version": VERSION, "protected_attempt": "old"}
            },
        },
    }
    cfg = {
        "configurable": {
            "ledger_attempt_id": "new",
            "ledger_submission_id": "s",
            "capability_selection_id": "sel",
            "capability_selection_digest": "digest",
        }
    }
    obs.observe_execution(
        cfg,
        request("query", state),
        receipt("query", {"data": "private-response-marker"}, network=True),
    )
    tool_exec = [
        c
        for c in fact.call_args_list
        if c.kwargs["kind"] == "mcp_tool" and c.kwargs["action"] == "executed"
    ]
    assert len(tool_exec) == 1 and set(tool_exec[0].kwargs["sources"]) == {
        SKILL,
        "independent",
    }
    assert sum(c.kwargs["action"] == "reused" for c in fact.call_args_list) == 2
    assert "private-response-marker" not in str(fact.call_args_list)
    assert stage.call_count == 1 and stage.call_args.args[1:3] == (SKILL, "executed")
    fact.reset_mock()
    stage.reset_mock()
    obs.observe_execution(
        cfg, request("query", state), receipt("query", {"code": 403}, network=False)
    )
    fact.assert_not_called()
    stage.assert_not_called()


@pytest.mark.anyio
async def test_queued_payload_validates_without_sensitive_markers(monkeypatch):
    import common.observability.collector as collector
    import common.observability.context as context
    import common.observability.store as store
    from common.event_ledger.contract import samples, validate_event

    items = []
    monkeypatch.setattr(store, "observability_enabled", lambda: True)
    monkeypatch.setattr(
        context,
        "observation_context_from_config",
        lambda _: SimpleNamespace(
            tenant_id="11111111-1111-4111-8111-111111111111",
            attempt_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        ),
    )
    monkeypatch.setattr(
        collector, "get_collector", lambda: SimpleNamespace(enqueue=items.append)
    )
    write = AsyncMock()
    monkeypatch.setattr(store, "record_capability_fact", write)
    obs.emit_fact(
        {},
        kind="mcp_tool",
        action="searched",
        ref="opaque-call",
        status="succeeded",
        candidates=[TOOL],
        catalog_version=VERSION,
        embedding_counts={"documents": 1, "queries": 1},
    )
    assert len(items) == 1
    await items[0].fn()
    payload = write.call_args.args[1]
    source = next(
        c["event"]
        for c in samples()["cases"]
        if c["name"] == "bqa-capability-search-valid"
    )
    assert validate_event({**source, "payload": payload}).valid
    assert not {"query", "rules", "password", "result", "arguments"} & payload.keys()
