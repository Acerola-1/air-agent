"""Top 3 search, incremental cache, independent activation and failure boundaries."""

from __future__ import annotations

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.tools import StructuredTool

from basic_qa.capabilities import mcp_catalog as catalog_module
from basic_qa.capabilities import mcp_index as index
from basic_qa.capabilities import mcp_search as search

pytestmark = pytest.mark.anyio


def doc(name, description=None, server="ipp"):
    async def query(region: str):
        """Read-only synthetic schema; not an actual service call."""
        return region

    return catalog_module.document_for(
        server,
        StructuredTool.from_function(
            coroutine=query, name=name, description=description or name
        ),
    )


def catalog(documents, *, scope="scope", version=None):
    return catalog_module.ToolCatalog(
        tuple(documents),
        scope,
        version
        or catalog_module.content_version(
            [(d.tool_id, d.schema_version) for d in documents]
        ),
        "tenant",
        "user",
        "membership",
    )


class MemoryStore:
    """Transactional test double; the PostgreSQL integration tests verify real RLS."""

    def __init__(self):
        self.documents = {}
        self.generations = {}
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self, cat, model):
        async with self.lock:
            scope = (cat.tenant_id, cat.scope_key, model)
            pending = copy.deepcopy(self.documents.get(scope, {}))

            async def existing():
                return dict(pending)

            async def publish(values, keys):
                pending.update({key: values[key] for key in keys.values()})

            yield SimpleNamespace(existing=existing, publish=publish)
            self.documents[scope] = pending
            self.generations[(scope, cat.catalog_version)] = True


class Encoder:
    name = "test-model-v1"
    openai_base_url = "https://example.invalid/v1"

    def __init__(self):
        self.calls = []

    async def acall(self, texts):
        self.calls.append(list(texts))
        await asyncio.sleep(0)
        return [[1.0, 1.0] for _ in texts]


@pytest.fixture(autouse=True)
def guard(monkeypatch):
    import common.identity.runtime as identity

    monkeypatch.setattr(
        identity, "guard_runtime_call", AsyncMock(return_value=object())
    )


async def test_restart_increment_singleflight_model_and_deletion():
    store, encoder = MemoryStore(), Encoder()
    original = catalog([doc("a"), doc("b"), doc("c"), doc("d")])
    results = await asyncio.gather(
        *(
            index.search_catalog("query", original, {}, store=store, encoder=encoder)
            for _ in range(4)
        )
    )
    assert sorted(r["embedding_counts"]["documents"] for r in results) == [0, 0, 0, 4]
    assert [c["tool_id"] for c in results[0]["candidates"]] == [
        "mcp:ipp:a",
        "mcp:ipp:b",
        "mcp:ipp:c",
    ]
    assert all(
        "schema" not in c and "properties" not in json.dumps(c)
        for c in results[0]["candidates"]
    )
    restarted_encoder = Encoder()
    warm = await index.search_catalog(
        "query", original, {}, store=store, encoder=restarted_encoder
    )
    assert warm["embedding_counts"] == {"documents": 0, "queries": 1}
    changed = catalog([doc("a", "new definition"), *original.documents[1:]])
    result = await index.search_catalog(
        "query", changed, {}, store=store, encoder=encoder
    )
    assert result["embedding_counts"]["documents"] == 1
    removed = catalog([changed.documents[0]])
    result = await index.search_catalog(
        "query", removed, {}, store=store, encoder=encoder
    )
    assert (
        len(result["candidates"]) == 1 and result["embedding_counts"]["documents"] == 0
    )
    encoder.name = "test-model-v2"
    assert (
        await index.search_catalog("query", changed, {}, store=store, encoder=encoder)
    )["embedding_counts"]["documents"] == 4


async def test_failed_or_revoked_build_does_not_publish():
    from common.identity.runtime import RuntimeIdentityRevoked

    store, encoder = MemoryStore(), Encoder()

    async def revoked():
        raise RuntimeIdentityRevoked("revoked")

    with pytest.raises(RuntimeIdentityRevoked):
        await index.search_catalog(
            "query",
            catalog([doc("a")]),
            {},
            store=store,
            encoder=encoder,
            verify=revoked,
        )
    assert store.documents == {} and store.generations == {}


async def test_bad_embedding_never_publishes():
    store, encoder = MemoryStore(), Encoder()
    encoder.acall = AsyncMock(return_value=[[float("nan")]])
    with pytest.raises(ValueError):
        await index.search_catalog(
            "query", catalog([doc("a")]), {}, store=store, encoder=encoder
        )
    assert not store.generations


@pytest.mark.parametrize("n", [0, 1, 2, 3, 5])
async def test_top3_from_eligible_pool_without_loading(monkeypatch, n):
    cat = catalog([doc(str(i)) for i in range(n)])
    monkeypatch.setattr(search, "resolve_tool_catalog", AsyncMock(return_value=cat))

    async def rank(query, catalog, config, *, verify):
        return await index.search_catalog(
            query,
            catalog,
            config,
            encoder=Encoder(),
            store=MemoryStore(),
            verify=verify,
        )

    monkeypatch.setattr(search, "search_catalog", rank)
    result = await search.create_search_mcp_tool().ainvoke({"query": "坐标行政区"}, {})
    assert result["status"] == "ok" and len(result["candidates"]) == min(n, 3)
    assert all(
        set(c)
        == {"tool_id", "name", "description", "schema_version", "status", "score"}
        for c in result["candidates"]
    )


@pytest.mark.parametrize("query", ["", " ", "x" * 501])
async def test_invalid_query_does_not_discover(monkeypatch, query):
    discovery = AsyncMock(side_effect=AssertionError("no discovery"))
    monkeypatch.setattr(search, "resolve_tool_catalog", discovery)
    assert (await search.create_search_mcp_tool().ainvoke({"query": query}))[
        "status"
    ] == "invalid_query"
    discovery.assert_not_awaited()


async def test_timeout_unavailable_and_revision_change_are_distinct(monkeypatch):
    async def slow(_):
        await asyncio.sleep(1)

    monkeypatch.setattr(search, "resolve_tool_catalog", slow)
    assert (await search.create_search_mcp_tool(timeout=0.001).ainvoke({"query": "x"}))[
        "status"
    ] == "search_timeout"
    monkeypatch.setattr(
        search,
        "resolve_tool_catalog",
        AsyncMock(side_effect=RuntimeError("private address secret")),
    )
    result = await search.create_search_mcp_tool().ainvoke({"query": "x"})
    assert result == {"status": "index_unavailable", "candidates": []}
    cat = catalog([doc("a")])
    monkeypatch.setattr(
        search,
        "resolve_tool_catalog",
        AsyncMock(side_effect=[cat, catalog([doc("b")])]),
    )

    async def rank(query, cat, cfg, *, verify):
        await verify()

    monkeypatch.setattr(search, "search_catalog", rank)
    assert (await search.create_search_mcp_tool().ainvoke({"query": "x"}))[
        "status"
    ] == "index_unavailable"


@pytest.mark.parametrize("operation", ["search", "load"])
async def test_cancelled_control_does_not_continue_or_activate(monkeypatch, operation):
    from basic_qa.capabilities import runtime as basic_qa_runtime

    started = asyncio.Event()
    returned = []

    async def discovery(_):
        started.set()
        await asyncio.Event().wait()
        returned.append(True)

    rank = AsyncMock(side_effect=AssertionError("cancelled search must not embed"))
    monkeypatch.setattr(search, "resolve_tool_catalog", discovery)
    monkeypatch.setattr(search, "search_catalog", rank)
    state = {
        "bqa_turn_key": "attempt",
        "messages": [],
        "bqa_capabilities": {"prior": "preserved"},
    }
    original = copy.deepcopy(state)
    if operation == "search":
        invocation = search.create_search_mcp_tool().ainvoke(
            {"query": "坐标行政区"}, {}
        )
    else:
        monkeypatch.setattr(basic_qa_runtime, "trusted_turn_key", lambda _: "attempt")
        monkeypatch.setattr(
            basic_qa_runtime,
            "session_state",
            AsyncMock(return_value=copy.deepcopy(state["bqa_capabilities"])),
        )
        invocation = search.create_load_mcp_tool().coroutine(
            tool_id="mcp:ipp:target",
            expected_schema_version="sha256:" + "a" * 64,
            runtime=SimpleNamespace(state=state, tool_call_id="cancelled"),
            config={},
        )
    pending = asyncio.create_task(invocation)
    await asyncio.wait_for(started.wait(), timeout=1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    rank.assert_not_awaited()
    assert not returned and state == original


async def test_definition_version_includes_descriptions_parameters_and_service_identity():
    a, b = doc("same", "first"), doc("same", "second")
    assert a.schema_version != b.schema_version
    assert doc("same", server="other").tool_id != a.tool_id
    assert a.schema_version == doc("same", "first").schema_version


async def test_explicit_object_is_a_relevance_prior_not_permission_or_execution():
    station = doc("mcp_station_common_air_real_data")
    city = doc("mcp_city_common_get_air_quality_realtime_stat")
    unknown = doc("generic")
    assert doc("mcp_grid_base_grid_by_station_proximity").subject == "grid"
    assert (
        station.subject == "station"
        and city.subject == "city"
        and unknown.subject is None
    )
    assert index.relevance_score(
        "站点实时空气质量", station, 0.6
    ) > index.relevance_score("站点实时空气质量", city, 0.95)
    assert index.relevance_score("站点实时空气质量", unknown, 0.9) == 0.9
    assert index.relevance_score("实时空气质量", station, 0.6) == 0.6
    assert index.relevance_score("站点气象数据", station, 0.6) > 0


async def test_scoped_catalog_filters_before_embedding_and_honors_mcp_selection(
    monkeypatch,
):
    import common.capabilities as capabilities
    import common.identity.runtime as identity
    import common.mcp_connections as connections
    import common.mcp_scoped as mcp
    from basic_qa.capabilities import skill_search

    target, excluded = (
        "mcp_city_base_get_district_by_lnglat",
        "mcp_grid_common_air_data",
    )
    user = SimpleNamespace(
        tenant_id="tenant", user_id="user", membership_id="membership"
    )
    monkeypatch.setattr(identity, "guard_runtime_call", AsyncMock(return_value=user))
    monkeypatch.setattr(
        skill_search,
        "_scoped_catalog",
        lambda *_: [SimpleNamespace(required_tool_ids=[f"mcp:ipp:{target}"])],
    )
    conn = SimpleNamespace(
        revision=1,
        config_revision=2,
        credential_digest="digest",
        allows_tool=lambda name: name in {target, excluded},
    )
    config_obj = SimpleNamespace(resolve=lambda server, tenant: conn)
    monkeypatch.setattr(connections, "load_connections", lambda: config_obj)
    monkeypatch.setattr(
        capabilities,
        "discover_authorized_mcp_catalog",
        AsyncMock(
            return_value=(
                [SimpleNamespace(status="available")],
                [
                    SimpleNamespace(id=f"mcp:ipp:{n}", status="available")
                    for n in (target, excluded, "unregistered_new_operation")
                ],
            )
        ),
    )

    def tool_for(name):
        async def query(region: str):
            return "not executed"

        return StructuredTool.from_function(
            coroutine=query, name=name, description=name
        )

    monkeypatch.setattr(
        mcp.get_factory(),
        "tools_for",
        AsyncMock(
            return_value=[
                tool_for(n) for n in (target, excluded, "unregistered_new_operation")
            ]
        ),
    )
    cfg = {
        "configurable": {
            "capability_mcp_mode": "allowlist",
            "capability_tool_ids": [f"mcp:ipp:{target}"],
        }
    }
    result = await catalog_module.resolve_tool_catalog(cfg)
    assert [d.tool_id for d in result.documents] == [f"mcp:ipp:{target}"]
    cfg["configurable"].update(capability_mcp_mode="auto")
    assert [
        d.tool_id for d in (await catalog_module.resolve_tool_catalog(cfg)).documents
    ] == [f"mcp:ipp:{target}", f"mcp:ipp:{excluded}"]
    cfg["configurable"].update(capability_mcp_mode="allowlist", capability_tool_ids=[])
    mcp.get_factory().tools_for.reset_mock()
    assert (await catalog_module.resolve_tool_catalog(cfg)).documents == ()
    mcp.get_factory().tools_for.assert_not_awaited()
