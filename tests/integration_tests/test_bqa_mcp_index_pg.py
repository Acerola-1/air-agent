"""Real PostgreSQL cache persistence and tenant isolation in an owned empty database."""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock
from urllib.parse import urlparse

import pytest

from basic_qa.capabilities.mcp_catalog import ToolCatalog, ToolDocument
from basic_qa.capabilities.mcp_index import PostgresIndexStore, search_catalog

pytestmark = pytest.mark.anyio


class Encoder:
    name = "isolated-index-v1"
    openai_base_url = "https://example.invalid/v1"

    def __init__(self):
        self.count = 0

    async def acall(self, texts):
        self.count += len(texts)
        await asyncio.sleep(0.01)
        return [[1.0, 2.0] for _ in texts]


@pytest.fixture
async def env(monkeypatch):
    import common.identity.runtime as identity
    from common.event_ledger import store

    uri = os.environ.get("BQA_INDEX_TEST_RUNTIME_URI", "")
    if not uri:
        pytest.skip("requires an explicitly owned BQA_INDEX_TEST_RUNTIME_URI")
    assert urlparse(uri).path.startswith("/bqa_caps_")
    assert urlparse(uri).port != 15432
    await store.close_pool()
    monkeypatch.setenv("RUNTIME_DATABASE_URI", uri)
    monkeypatch.setattr(
        identity, "guard_runtime_call", AsyncMock(return_value=object())
    )
    d = ToolDocument(
        "mcp:ipp:synthetic",
        "synthetic",
        "synthetic query",
        {"type": "object", "properties": {}},
        "schema-v1",
    )
    a = ToolCatalog(
        (d,),
        "scope",
        "catalog-v1",
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        str(uuid.uuid4()),
    )
    b = replace(a, tenant_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()))
    yield a, b
    await store.close_pool()


async def test_real_build_singleflight_restart_increment_and_model(env):
    from common.event_ledger import store

    a, _ = env
    encoder = Encoder()
    results = await asyncio.gather(
        *(search_catalog("query", a, {}, encoder=encoder) for _ in range(4))
    )
    assert sum(r["embedding_counts"]["documents"] for r in results) == 1
    await store.close_pool()
    restarted = Encoder()
    r = await search_catalog("query", a, {}, encoder=restarted)
    assert r["embedding_counts"] == {"documents": 0, "queries": 1}
    changed = replace(
        a,
        documents=(
            replace(a.documents[0], description="changed", schema_version="schema-v2"),
        ),
        catalog_version="catalog-v2",
    )
    assert (await search_catalog("query", changed, {}, encoder=restarted))[
        "embedding_counts"
    ]["documents"] == 1
    restarted.name = "isolated-index-v2"
    assert (await search_catalog("query", changed, {}, encoder=restarted))[
        "embedding_counts"
    ]["documents"] == 1


async def test_force_rls_positive_controls_and_cross_tenant_read_write(env):
    from common.event_ledger.store import LedgerBackendUnavailable, LedgerScope, _tx

    a, b = env
    for cat in (a, b):
        await search_catalog("query", cat, {}, encoder=Encoder())
    scope = LedgerScope(a.tenant_id, a.user_id, a.membership_id)
    async with _tx(scope) as conn:
        role = await (
            await conn.execute(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
        ).fetchone()
        assert not role["rolsuper"] and not role["rolbypassrls"]
        for table in ("bqa_mcp_index_generations", "bqa_mcp_index_documents"):
            flags = await (
                await conn.execute(
                    "SELECT relrowsecurity,relforcerowsecurity,pg_get_userbyid(relowner) AS owner FROM pg_class WHERE relname=%s",
                    (table,),
                )
            ).fetchone()
            assert (
                flags["relrowsecurity"]
                and flags["relforcerowsecurity"]
                and flags["owner"] != "air_agent_runtime"
            )
            rows = await (
                await conn.execute(f"SELECT tenant_id FROM {table}")
            ).fetchall()
            assert rows and {str(row["tenant_id"]) for row in rows} == {a.tenant_id}
    with pytest.raises(LedgerBackendUnavailable):
        async with _tx(scope) as conn:
            await conn.execute(
                "INSERT INTO bqa_mcp_index_documents(tenant_id,scope_key,model_key,document_key,tool_id,schema_version,embedding) VALUES(%s,'x','x','x','x','x',ARRAY[1.0])",
                (b.tenant_id,),
            )
    # Rejection did not poison a reused connection and B's positive data remains.
    assert (await search_catalog("query", b, {}, encoder=Encoder()))[
        "embedding_counts"
    ]["documents"] == 0


async def test_failed_publish_rollback_and_scoped_cache_reclamation(env):
    from common.event_ledger.store import LedgerScope, _tx

    a, b = env

    async def stale():
        raise RuntimeError("connection revision changed")

    with pytest.raises(RuntimeError):
        await search_catalog("query", a, {}, encoder=Encoder(), verify=stale)
    assert (await search_catalog("query", a, {}, encoder=Encoder()))[
        "embedding_counts"
    ]["documents"] == 1
    await search_catalog("query", b, {}, encoder=Encoder())
    async with _tx(LedgerScope(a.tenant_id, a.user_id, a.membership_id)) as conn:
        await conn.execute(
            "UPDATE bqa_mcp_index_generations SET published_at=now()-interval '40 days'"
        )
    await PostgresIndexStore().prune(a, days=30)
    assert (await search_catalog("query", a, {}, encoder=Encoder()))[
        "embedding_counts"
    ]["documents"] == 1
    assert (await search_catalog("query", b, {}, encoder=Encoder()))[
        "embedding_counts"
    ]["documents"] == 0
