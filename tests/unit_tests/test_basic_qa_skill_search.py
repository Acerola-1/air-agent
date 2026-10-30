"""隔离验证 Basic QA 目录和搜索，不接入实际 embedding。"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import basic_qa.capabilities.skill_search as module
from common.business_registry import business_for_graph
from common.capabilities import SkillCatalogItem
from common.identity.business_access import BusinessAccessError
from common.identity.runtime import (
    RuntimeIdentity,
    RuntimeIdentityError,
    RuntimeIdentityRevoked,
)
from common.skill_router import SkillSearchUnavailable


def identity(*, permitted=True):
    business = business_for_graph("basic-qa")
    assert business is not None
    from common.business_registry import BUSINESS_POLICY_VERSION

    return RuntimeIdentity(
        user_id="user",
        tenant_id="tenant",
        membership_id="member",
        session_id="session",
        permissions=frozenset({business.permission} if permitted else set()),
        business_graph="basic-qa",
        business_code=business.code,
        business_permission=business.permission,
        business_policy_version=BUSINESS_POLICY_VERSION,
    )


def config():
    return {
        "configurable": {
            "capability_selection_id": "selection",
            "capability_selection_digest": "digest",
        }
    }


def item(index):
    return SkillCatalogItem(
        id=f"skill:basic-qa:skill-{index}",
        name=f"skill-{index}",
        description=f"简介 {index}",
        material_version=f"sha256:{index}",
        status="available",
        required_tool_ids=["secret-tool"],
    )


@pytest.fixture
def setup_catalog(monkeypatch):
    items = [item(index) for index in range(8)]
    monkeypatch.setattr(module, "list_graph_skills", lambda graph: items)
    guard = AsyncMock(return_value=identity())
    monkeypatch.setattr(module, "guard_runtime_call", guard)
    return items, guard


def test_server_catalog_checks_business_and_admission(setup_catalog):
    items, _ = setup_catalog
    assert module._scoped_catalog(config(), identity()) == items
    with pytest.raises(RuntimeIdentityError):
        module._scoped_catalog(config(), None)
    with pytest.raises(BusinessAccessError):
        module._scoped_catalog(config(), identity(permitted=False))
    missing = config()
    del missing["configurable"]["capability_selection_digest"]
    with pytest.raises(module.SkillCatalogScopeError):
        module._scoped_catalog(missing, identity())
    from dataclasses import replace

    other_graph = replace(identity(), business_graph="data-analysis")
    with pytest.raises(module.SkillCatalogScopeError):
        module._scoped_catalog(config(), other_graph)


@pytest.mark.anyio
async def test_search_matches_allowed_metadata_only_and_preserves_model_query(
    setup_catalog,
):
    items, guard = setup_catalog
    matches = [
        SimpleNamespace(
            name=f"skill-{i}",
            similarity_score=0.9,
            path="/private/path",
            description="secret",
            allowed_tools=["secret-tool"],
        )
        for i in range(8)
    ]
    router = SimpleNamespace(amatch=AsyncMock(return_value=matches))
    search = module.create_search_skills_tool(router)
    query = "查询杭州今天空气质量，延续前面的空气质量问题"
    result = await search.ainvoke({"query": query}, config=config())
    router.amatch.assert_awaited_once_with(query, strict=True)
    assert guard.await_count == 2
    assert result["status"] == "ok" and len(result["candidates"]) == 5
    assert result["candidates"][0] == {
        "skill_id": items[0].id,
        "name": items[0].name,
        "description": items[0].description,
        "material_version": items[0].material_version,
        "status": "available",
        "score": 0.9,
    }
    assert "/private" not in str(result) and "secret" not in str(result)
    assert list(search.args) == ["query"]


@pytest.mark.anyio
async def test_no_search_invalid_empty_timeout_error_and_guard_rejection(setup_catalog):
    _, guard = setup_catalog
    router = SimpleNamespace(amatch=AsyncMock(return_value=[]))
    search = module.create_search_skills_tool(router, timeout=0.01)
    assert (await search.ainvoke({"query": " "}, config=config()))[
        "status"
    ] == "invalid_query"
    assert (await search.ainvoke({"query": "x" * 501}, config=config()))[
        "status"
    ] == "invalid_query"
    router.amatch.assert_not_awaited()
    assert (await search.ainvoke({"query": "空气"}, config=config())) == {
        "status": "ok",
        "candidates": [],
    }

    async def slow(_query, *, strict):
        await asyncio.sleep(1)

    router.amatch.side_effect = slow
    assert (await search.ainvoke({"query": "空气"}, config=config()))[
        "status"
    ] == "search_timeout"
    router.amatch.side_effect = SkillSearchUnavailable("secret")
    assert (await search.ainvoke({"query": "空气"}, config=config())) == {
        "status": "search_unavailable",
        "candidates": [],
    }
    guard.side_effect = RuntimeIdentityRevoked("revoked")
    previous_count = router.amatch.await_count
    with pytest.raises(RuntimeIdentityRevoked):
        await search.ainvoke({"query": "空气"}, config=config())
    assert router.amatch.await_count == previous_count


@pytest.mark.anyio
async def test_revocation_during_search_and_outbound_rejection_propagate(setup_catalog):
    _, guard = setup_catalog
    router = SimpleNamespace(amatch=AsyncMock(return_value=[]))
    guard.side_effect = [identity(), RuntimeIdentityRevoked("revoked")]
    search = module.create_search_skills_tool(router)
    with pytest.raises(RuntimeIdentityRevoked):
        await search.ainvoke({"query": "空气"}, config=config())
    guard.side_effect = None
    router.amatch.side_effect = RuntimeIdentityRevoked("revoked before send")
    with pytest.raises(RuntimeIdentityRevoked):
        await search.ainvoke({"query": "空气"}, config=config())


@pytest.mark.anyio
async def test_real_router_receives_strict_flag_without_network(
    tmp_path, setup_catalog
):
    from common.skill_router import SkillSemanticRouter

    router = SkillSemanticRouter(tmp_path)
    router.amatch = AsyncMock(return_value=[])
    search = module.create_search_skills_tool(router)
    assert (await search.ainvoke({"query": "空气"}, config=config()))["status"] == "ok"
    router.amatch.assert_awaited_once_with("空气", strict=True)


@pytest.mark.anyio
async def test_large_server_catalog_search_returns_only_bounded_candidates(monkeypatch):
    items = [item(index) for index in range(90)]
    monkeypatch.setattr(module, "list_graph_skills", lambda _: items)
    monkeypatch.setattr(
        module, "guard_runtime_call", AsyncMock(return_value=identity())
    )
    router = SimpleNamespace(
        amatch=AsyncMock(
            return_value=[
                SimpleNamespace(name=entry.name, similarity_score=0.9)
                for entry in items
            ]
        )
    )
    search = module.create_search_skills_tool(router)
    result = await search.ainvoke({"query": "当前业务需求"}, config=config())
    assert len(result["candidates"]) == 5
    assert [entry["skill_id"] for entry in result["candidates"]] == [
        entry.id for entry in items[:5]
    ]
    assert all(
        set(entry)
        == {"skill_id", "name", "description", "material_version", "status", "score"}
        for entry in result["candidates"]
    )
    assert items[-1].id not in str(result)


@pytest.mark.anyio
async def test_material_change_during_search_not_disclosed(setup_catalog):
    items, _ = setup_catalog

    async def match(_query, *, strict):
        items[0] = replace(items[0], material_version="sha256:changed")
        return [SimpleNamespace(name="skill-0", similarity_score=0.9)]

    router = SimpleNamespace(amatch=AsyncMock(side_effect=match))
    search = module.create_search_skills_tool(router)
    assert (await search.ainvoke({"query": "空气"}, config=config()))[
        "candidates"
    ] == []


@pytest.mark.anyio
async def test_scope_errors_are_not_treated_as_search_fallback(setup_catalog):
    _, guard = setup_catalog
    router = SimpleNamespace(
        amatch=AsyncMock(side_effect=module.SkillCatalogScopeError("denied"))
    )
    search = module.create_search_skills_tool(router)
    with pytest.raises(module.SkillCatalogScopeError):
        await search.ainvoke({"query": "空气"}, config=config())
    router.amatch.side_effect = SkillSearchUnavailable("failure")
    guard.side_effect = [identity(), RuntimeIdentityRevoked("revoked")]
    with pytest.raises(RuntimeIdentityRevoked):
        await search.ainvoke({"query": "空气"}, config=config())
