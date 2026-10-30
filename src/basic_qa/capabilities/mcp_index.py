"""Incremental MCP embeddings in the existing tenant-scoped business PostgreSQL."""

from __future__ import annotations

import math
from contextlib import asynccontextmanager
from typing import Any

from psycopg.types.json import Jsonb

from basic_qa.capabilities.mcp_catalog import ToolCatalog, content_version


def vector(values: Any) -> list[float]:
    """Reject incomplete or nonfinite embeddings rather than publishing a generation."""
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("invalid embedding")
    result = [float(v) for v in values]
    if any(not math.isfinite(v) for v in result) or not any(result):
        raise ValueError("invalid embedding")
    return result


def cosine(left: list[float], right: list[float]) -> float:
    """Compute exact cosine for this small eligible candidate pool."""
    if len(left) != len(right):
        raise ValueError("embedding dimension changed")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        math.sqrt(sum(v * v for v in left)) * math.sqrt(sum(v * v for v in right))
    )


def relevance_score(query: str, document, similarity: float) -> float:
    """Prefer an explicitly named object, then semantic similarity within it.

    All entries already passed the same business/connection/selection filter.
    This only ranks relevance: unmatched or unclassified entries stay eligible,
    and loading/execution authorization never consumes this hint.
    """
    facets = {
        key
        for key, words in {
            "station": ("站点", "监测站"),
            "grid": ("网格",),
            "city": ("城市", "行政区"),
            "weather": ("天气", "气象"),
        }.items()
        if any(word in query for word in words)
    }
    return similarity + (2.0 if document.subject in facets else 0.0)


class PostgresIndexStore:
    """Use the runtime pool/GUC and a cross-worker transactional build lock."""

    @asynccontextmanager
    async def transaction(self, catalog: ToolCatalog, model_key: str):
        """Serialize one scope/model build; cancelled builds roll back completely."""
        from common.event_ledger.store import LedgerScope, _tx

        scope = LedgerScope(catalog.tenant_id, catalog.user_id, catalog.membership_id)
        async with _tx(scope) as conn:
            # A process lock alone would duplicate embedding across API workers.
            lock = int(
                content_version(
                    [catalog.tenant_id, catalog.scope_key, model_key]
                ).split(":")[1][:15],
                16,
            )
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
            yield _PostgresBuild(conn, catalog, model_key)

    async def prune(self, catalog: ToolCatalog, *, days: int = 30) -> None:
        """Reclaim only this tenant's expired tool-cache generations and orphans."""
        from common.event_ledger.store import LedgerScope, _tx

        if type(days) is not int or days < 1:
            raise ValueError("cache retention must be positive")
        async with _tx(
            LedgerScope(catalog.tenant_id, catalog.user_id, catalog.membership_id)
        ) as conn:
            await conn.execute(
                "DELETE FROM bqa_mcp_index_generations WHERE tenant_id=%s AND published_at < now() - %s * interval '1 day'",
                (catalog.tenant_id, days),
            )
            await conn.execute(
                "DELETE FROM bqa_mcp_index_documents d WHERE d.tenant_id=%s AND NOT EXISTS(SELECT 1 FROM bqa_mcp_index_generations g WHERE g.tenant_id=d.tenant_id AND g.scope_key=d.scope_key AND g.model_key=d.model_key AND g.documents ? d.document_key)",
                (catalog.tenant_id,),
            )


class _PostgresBuild:
    def __init__(self, conn, catalog, model_key):
        self.conn, self.catalog, self.model_key = conn, catalog, model_key

    def _scope(self):
        return (self.catalog.tenant_id, self.catalog.scope_key, self.model_key)

    async def existing(self):
        cursor = await self.conn.execute(
            "SELECT document_key, embedding FROM bqa_mcp_index_documents WHERE tenant_id=%s AND scope_key=%s AND model_key=%s",
            self._scope(),
        )
        return {r["document_key"]: r["embedding"] for r in await cursor.fetchall()}

    async def publish(self, embeddings, keys):
        for document in self.catalog.documents:
            key = keys[document.tool_id]
            await self.conn.execute(
                "INSERT INTO bqa_mcp_index_documents(tenant_id,scope_key,model_key,document_key,tool_id,schema_version,embedding) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,scope_key,model_key,document_key) DO UPDATE SET schema_version=EXCLUDED.schema_version",
                (
                    *self._scope(),
                    key,
                    document.tool_id,
                    document.schema_version,
                    embeddings[key],
                ),
            )
        await self.conn.execute(
            "INSERT INTO bqa_mcp_index_generations(tenant_id,scope_key,model_key,catalog_version,documents) VALUES(%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,scope_key,model_key,catalog_version) DO UPDATE SET published_at=now()",
            (
                *self._scope(),
                self.catalog.catalog_version,
                Jsonb(sorted(keys.values())),
            ),
        )


def configured_encoder():
    """Instantiate the same guarded embedding channel as Skill search, lazily."""
    from common.config import config
    from common.skill_router import SiliconFlowEmbeddingEncoder

    return SiliconFlowEmbeddingEncoder(
        name=config.SILICONFLOW_EMBEDDING_MODEL,
        openai_base_url=config.SILICONFLOW_BASE_URL,
        openai_api_key=config.SILICONFLOW_API_KEY,
    )


def embedding_model_key(encoder) -> str:
    """Separate model/endpoint versions without storing endpoints in the cache."""
    return content_version(
        {
            "profile_version": 1,
            "model": encoder.name,
            "endpoint": encoder.openai_base_url,
        }
    )


async def search_catalog(
    query: str, catalog: ToolCatalog, config, *, store=None, encoder=None, verify=None
):
    """Embed only missing documents, publish atomically, then rank the legal pool."""
    from common.identity.runtime import guard_runtime_call

    encoder = encoder or configured_encoder()
    store = store or PostgresIndexStore()
    model_key = embedding_model_key(encoder)
    keys = {doc.tool_id: content_version(doc.text) for doc in catalog.documents}
    embedded = 0
    async with store.transaction(catalog, model_key) as build:
        existing = await build.existing()
        missing = [
            doc for doc in catalog.documents if keys[doc.tool_id] not in existing
        ]
        if missing:
            await guard_runtime_call(config)
            values = await encoder.acall([doc.text for doc in missing])
            if len(values) != len(missing):
                raise ValueError("incomplete document embeddings")
            for doc, value in zip(missing, values, strict=True):
                existing[keys[doc.tool_id]] = vector(value)
            embedded = len(missing)
        if verify:
            await verify()
        await guard_runtime_call(config)
        # Only publish an entire current generation, never a partial build.
        await build.publish(existing, keys)
    await guard_runtime_call(config)
    q = await encoder.acall([query])
    if len(q) != 1:
        raise ValueError("incomplete query embedding")
    q = vector(q[0])
    scored = [
        (
            doc,
            relevance_score(query, doc, cosine(q, vector(existing[keys[doc.tool_id]]))),
        )
        for doc in catalog.documents
    ]
    scored.sort(key=lambda item: (-item[1], item[0].tool_id))
    return {
        "status": "ok",
        "catalog_version": catalog.catalog_version,
        "candidates": [doc.candidate(score) for doc, score in scored[:3]],
        "embedding_counts": {"documents": embedded, "queries": 1},
    }
