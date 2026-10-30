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


def config(*, mode="auto", skill_id=None, version=None):
    return {
        "configurable": {
            "capability_selection_id": "selection",
            "capability_selection_digest": "digest",
            "capability_skill_mode": mode,
            "capability_skill_id": skill_id,
            "capability_material_version": version,
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


@pytest.mark.anyio
async def test_catalog_auto_explicit_and_stale_scope(setup_catalog):
    items, guard = setup_catalog
    auto = await module.basic_qa_skill_catalog(config())
    assert len(auto) == 8
    assert set(auto[0]) == {
        "skill_id",
        "name",
        "description",
        "material_version",
        "status",
    }
    assert "secret-tool" not in str(auto)
    explicit = config(
        mode="explicit", skill_id=items[1].id, version=items[1].material_version
    )
    assert [
        entry["skill_id"] for entry in await module.basic_qa_skill_catalog(explicit)
    ] == [items[1].id]
    explicit["configurable"]["capability_material_version"] = "sha256:old"
    with pytest.raises(module.SkillCatalogScopeError):
        await module.basic_qa_skill_catalog(explicit)
    assert guard.await_count == 3


@pytest.mark.anyio
async def test_scope_denies_missing_or_invalid_authority(monkeypatch, setup_catalog):
    _, guard = setup_catalog
    guard.return_value = None
    with pytest.raises(RuntimeIdentityError):
        await module.basic_qa_skill_catalog(config())
    guard.return_value = identity(permitted=False)
    with pytest.raises(BusinessAccessError):
        await module.basic_qa_skill_catalog(config())
    guard.return_value = identity()
    missing = config()
    del missing["configurable"]["capability_selection_digest"]
    with pytest.raises(module.SkillCatalogScopeError):
        await module.basic_qa_skill_catalog(missing)
    forged = config(
        mode="explicit", skill_id="skill:data-analysis:skill-0", version="sha256:0"
    )
    with pytest.raises(module.SkillCatalogScopeError):
        await module.basic_qa_skill_catalog(forged)


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
    result = await search.ainvoke(
        {"query": query},
        config=config(
            mode="explicit", skill_id=items[6].id, version=items[6].material_version
        ),
    )
    assert [candidate["skill_id"] for candidate in result["candidates"]] == [
        items[6].id
    ]


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
async def test_actual_catalog_exposes_only_metadata(monkeypatch):
    monkeypatch.setattr(
        module, "guard_runtime_call", AsyncMock(return_value=identity())
    )
    catalog = await module.basic_qa_skill_catalog(config())
    assert len(catalog) == 9
    assert all(entry["skill_id"].startswith("skill:basic-qa:") for entry in catalog)
    assert all(
        set(entry) == {"skill_id", "name", "description", "material_version", "status"}
        for entry in catalog
    )
    assert all(entry["material_version"].startswith("sha256:") for entry in catalog)


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
