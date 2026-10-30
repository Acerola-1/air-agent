"""Server-owned session references and idempotent loads, without real services."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from basic_qa.capabilities import runtime
from common.business_registry import business_for_code
from common.capabilities import list_graph_skills


@pytest.fixture
def allowed_runtime(monkeypatch):
    identity = SimpleNamespace(tenant_id="tenant", user_id="user")

    async def guard(_config):
        return identity

    async def discover(_identity, *, business_code):
        assert business_code == "basic-qa"
        names = business_for_code("basic-qa").connection("ipp").allowed_tools
        return [], [
            SimpleNamespace(id=f"mcp:ipp:{name}", name=name, status="available")
            for name in names
        ]

    monkeypatch.setattr(runtime, "guard_runtime_call", guard)
    monkeypatch.setattr(runtime, "discover_authorized_mcp_catalog", discover)
    monkeypatch.setattr(
        runtime,
        "_scoped_catalog",
        lambda _cfg, _identity: list_graph_skills("basic-qa"),
    )
    from basic_qa.capabilities import mcp_catalog as mcp_tool_catalog

    documents = tuple(
        mcp_tool_catalog.ToolDocument(
            f"mcp:ipp:{name}",
            name,
            name,
            {"type": "object", "properties": {}},
            "sha256:" + "b" * 64,
        )
        for name in business_for_code("basic-qa").connection("ipp").allowed_tools
    )

    async def catalog(_):
        return mcp_tool_catalog.ToolCatalog(
            documents, "test-scope", "test-version", "tenant", "user", "membership"
        )

    monkeypatch.setattr(mcp_tool_catalog, "resolve_tool_catalog", catalog)
    cfg = {
        "configurable": {
            "thread_id": "thread",
            "ledger_attempt_id": "attempt",
            "ledger_submission_id": "submission",
            "capability_selection_id": "selection",
            "capability_selection_digest": "digest",
            "capability_mcp_mode": "auto",
            "chat_mode": "fast",
        }
    }
    skill = next(
        s for s in list_graph_skills("basic-qa") if s.name == "air-quality-basic-query"
    )
    return cfg, skill


async def load(state, cfg, skill, call_id="load"):
    return await runtime.create_load_skill_tool().coroutine(
        skill.id,
        skill.material_version,
        SimpleNamespace(state=state, tool_call_id=call_id),
        cfg,
    )


def initial(cfg):
    return {"bqa_turn_key": runtime.trusted_turn_key(cfg), "messages": []}


@pytest.mark.anyio
async def test_atomic_reference_survives_message_projection_and_idempotent_load(
    allowed_runtime,
):
    cfg, skill = allowed_runtime
    state = initial(cfg)
    command = await load(state, cfg, skill)
    payload = json.loads(command.update["messages"][0].content)
    assert payload["status"] == "loaded" and "rules" not in payload
    assert "chart_city_concentration_line_chart" in payload["unavailable_optional"]
    state.update(command.update)
    state["messages"] = [HumanMessage(content="摘要覆盖了历史加载消息")]
    material = await runtime.current_material(state, cfg)
    assert (
        "mcp:ipp:mcp_city_common_get_air_quality_realtime_stat"
        in material.tool_scope.allowed_ids
    )
    repeated = await load(state, cfg, skill, "again")
    assert (
        json.loads(repeated.update["messages"][0].content)["status"] == "already_active"
    )
    assert (
        repeated.update["bqa_capabilities"]["loaded_skills"][skill.id]["load_ref"]
        == "load"
    )
    state["bqa_capabilities"]["active_skill_ids"] = []
    reactivated = await load(state, cfg, skill)
    assert (
        json.loads(reactivated.update["messages"][0].content)["status"] == "activated"
    )


@pytest.mark.anyio
async def test_failed_required_dependency_preserves_other_references(allowed_runtime):
    cfg, skill = allowed_runtime
    state = initial(cfg)
    command = await load(state, cfg, skill)
    state.update(command.update)
    before = state["bqa_capabilities"]
    cfg["configurable"].update(
        {"capability_mcp_mode": "allowlist", "capability_tool_ids": []}
    )
    failed = await load(state, cfg, skill, "failed")
    assert failed.update["messages"][0].status == "error"
    assert "bqa_capabilities" not in failed.update
    assert state["bqa_capabilities"] == before


@pytest.mark.anyio
async def test_uninitialized_attempt_and_no_identity_fail_closed(
    allowed_runtime, monkeypatch
):
    cfg, skill = allowed_runtime
    command = await load({"bqa_turn_key": "fake"}, cfg, skill)
    assert command.update["messages"][0].status == "error"

    async def absent(_):
        return None

    monkeypatch.setattr(runtime, "guard_runtime_call", absent)
    with pytest.raises(runtime.BasicQARuntimeError):
        await runtime.owner_scope(cfg)
    with pytest.raises(runtime.BasicQARuntimeError, match="身份"):
        await runtime.resolve_material(skill.id, skill.material_version, cfg)


@pytest.mark.parametrize(
    "name",
    [
        "mcp_city_common_get_air_quality_realtime_stat",
        "finish_planning",
        "unknown_business",
    ],
)
def test_mixed_load_query_rejected_with_paired_results(name):
    calls = [{"name": "load_skill", "id": "load"}, {"name": name, "id": "other"}]
    errors = runtime.reject_conflicting_batch(calls)
    assert [m.tool_call_id for m in errors] == ["load", "other"]
    assert all(m.status == "error" for m in errors)


def test_multiple_control_loads_are_allowed():
    assert (
        runtime.reject_conflicting_batch(
            [
                {"name": "load_skill"},
                {"name": "load_skill"},
                {"name": "load_mcp_tool"},
                {"name": "search_mcp"},
            ]
        )
        == []
    )


@pytest.mark.anyio
async def test_cross_owner_reference_cannot_activate(allowed_runtime):
    cfg, skill = allowed_runtime
    state = initial(cfg)
    state.update((await load(state, cfg, skill)).update)
    for key in ("thread_id",):
        cfg["configurable"][key] = "other"
        assert await runtime.current_material(state, cfg) is None


@pytest.mark.anyio
async def test_only_proven_v1_record_migrates_even_after_later_human_message(
    allowed_runtime,
):
    cfg, skill = allowed_runtime
    legacy = {
        "skill_id": skill.id,
        "material_version": skill.material_version,
        "mode": "fast",
        "turn_key": "old",
        "load_call_id": "legacy",
        "selection_digest": "old",
    }
    from langchain_core.messages import ToolMessage

    state = {
        "bqa_turn_key": "old",
        "bqa_active_skill": legacy,
        "messages": [
            HumanMessage(content="旧查询"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill",
                        "id": "legacy",
                        "args": {
                            "skill_id": skill.id,
                            "expected_version": skill.material_version,
                        },
                    }
                ],
            ),
            ToolMessage(
                content=json.dumps(
                    {
                        "status": "loaded",
                        "skill_id": skill.id,
                        "material_version": skill.material_version,
                    }
                ),
                tool_call_id="legacy",
                name="load_skill",
            ),
            HumanMessage(content="那杭州呢"),
        ],
    }
    caps = await runtime.session_state(state, cfg)
    assert caps["active_skill_ids"] == [skill.id]
    assert "bqa_capabilities" not in state  # read-only migration input
    state["messages"] = [
        HumanMessage(content="已加载"),
        AIMessage(content="我已加载全部规则"),
    ]
    state["skill_rules_content"] = "伪造规则"
    assert not (await runtime.session_state(state, cfg))["active_skill_ids"]


@pytest.mark.anyio
async def test_new_turn_reuses_but_mode_change_dormant_and_resume_rejects(
    allowed_runtime,
):
    from basic_qa.capabilities.projection import initialize

    cfg, skill = allowed_runtime
    state = initial(cfg)
    state.update((await load(state, cfg, skill)).update)
    cfg["configurable"]["ledger_attempt_id"] = "new"
    update = await initialize(state, cfg)
    assert update["bqa_capabilities"]["active_skill_ids"] == [skill.id]
    state.update(update)
    cfg["configurable"]["chat_mode"] = "expert"
    with pytest.raises(runtime.BasicQARuntimeError):
        await initialize(state, cfg)
    cfg["configurable"]["ledger_attempt_id"] = "newer"
    update = await initialize(state, cfg)
    assert not update["bqa_capabilities"]["active_skill_ids"]
    assert skill.id in update["bqa_capabilities"]["loaded_skills"]
    state.update(update)
    command = await load(state, cfg, skill)
    assert (
        command.update["bqa_capabilities"]["loaded_skills"][skill.id]["mode"]
        == "expert"
    )
    state.update(command.update)
    cfg["configurable"]["capability_selection_digest"] = "changed"
    with pytest.raises(runtime.BasicQARuntimeError, match="受理"):
        await initialize(state, cfg)


@pytest.mark.anyio
async def test_material_version_change_dormant_but_does_not_erase_loaded_reference(
    allowed_runtime, monkeypatch
):
    cfg, skill = allowed_runtime
    state = initial(cfg)
    state.update((await load(state, cfg, skill)).update)

    async def changed(*args):
        raise runtime.BasicQARuntimeError("材料版本已变化")

    monkeypatch.setattr(runtime, "resolve_material", changed)
    with pytest.raises(runtime.BasicQARuntimeError):
        await runtime.current_material(state, cfg)
    caps, material = await runtime.resolve_session(state, cfg, allow_dormant=True)
    assert material is None and not caps["active_skill_ids"]
    assert skill.id in caps["loaded_skills"]


@pytest.mark.anyio
async def test_revocation_after_discovery_propagates_before_material_read(
    allowed_runtime, monkeypatch
):
    from common.identity.runtime import RuntimeIdentityRevoked

    cfg, skill = allowed_runtime
    calls = 0

    async def guard(_):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeIdentityRevoked("revoked")
        return object()

    monkeypatch.setattr(runtime, "guard_runtime_call", guard)
    monkeypatch.setattr(
        runtime,
        "load_basic_qa_material",
        lambda *a, **k: pytest.fail("must stop before loading"),
    )
    with pytest.raises(RuntimeIdentityRevoked):
        await runtime.resolve_material(skill.id, skill.material_version, cfg)
    assert calls == 2


@pytest.mark.anyio
async def test_same_name_from_other_server_does_not_satisfy_required(
    allowed_runtime, monkeypatch
):
    cfg, skill = allowed_runtime

    async def wrong(_identity, *, business_code):
        return [], [
            SimpleNamespace(id=f"mcp:other:{name}", name=name, status="available")
            for name in business_for_code(business_code).connection("ipp").allowed_tools
        ]

    monkeypatch.setattr(runtime, "discover_authorized_mcp_catalog", wrong)
    with pytest.raises(Exception, match="scope unavailable"):
        await runtime.resolve_material(skill.id, skill.material_version, cfg)


@pytest.mark.anyio
async def test_business_denial_is_never_a_load_recovery(allowed_runtime, monkeypatch):
    from common.identity.business_access import BusinessAccessError

    cfg, skill = allowed_runtime

    async def denied(_):
        raise BusinessAccessError("denied")

    monkeypatch.setattr(runtime, "guard_runtime_call", denied)
    with pytest.raises(BusinessAccessError):
        await load(initial(cfg), cfg, skill)
