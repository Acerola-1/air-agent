"""检索隐私与实际发送守卫，不依赖真实 embedding 服务."""

from types import SimpleNamespace

import httpx
import pytest
from loguru import logger

from common.identity.outbound import TenantOutboundAuth
from common.identity.runtime import RuntimeIdentityError
from common.skill_router import (
    SiliconFlowEmbeddingEncoder,
    SkillSearchUnavailable,
    SkillSemanticRouter,
)


@pytest.mark.asyncio
async def test_queries_are_not_logged_or_cached(tmp_path):
    router = SkillSemanticRouter(tmp_path)
    calls = []

    async def acall(query, **kwargs):
        calls.append(query)
        return SimpleNamespace(name=None)

    router._router = SimpleNamespace(acall=acall)
    logs = []
    sink = logger.add(lambda message: logs.append(str(message)))
    try:
        await router.amatch("private-marker-tenant-a", strict=True)
        await router.amatch("private-marker-tenant-a", strict=True)
    finally:
        logger.remove(sink)
    assert len(calls) == 2
    assert not hasattr(router, "_match_cache")
    assert "private-marker" not in "".join(logs)


@pytest.mark.asyncio
async def test_strict_failure_is_not_empty_match_and_denial_is_not_swallowed(tmp_path):
    router = SkillSemanticRouter(tmp_path)

    async def fail(*args, **kwargs):
        raise ValueError("private-marker")

    router._router = SimpleNamespace(acall=fail)
    with pytest.raises(SkillSearchUnavailable, match="skill search failed"):
        await router.amatch("query", strict=True)

    async def denied(*args, **kwargs):
        raise RuntimeIdentityError("denied")

    router._router = SimpleNamespace(acall=denied)
    with pytest.raises(RuntimeIdentityError):
        await router.amatch("query", strict=True)


def test_embedding_clients_use_both_transport_guards():
    encoder = SiliconFlowEmbeddingEncoder(
        name="test", openai_base_url="https://example.invalid", openai_api_key="test"
    )
    assert isinstance(encoder._client._client.auth, TenantOutboundAuth)
    assert isinstance(encoder._aclient._client.auth, TenantOutboundAuth)
    encoder._client.close()


def test_sync_transport_denial_sends_zero_requests(monkeypatch):
    import common.identity.outbound as outbound

    sent = []

    def deny(config):
        raise RuntimeIdentityError("revoked")

    monkeypatch.setattr(outbound, "guard_runtime_call_sync", deny)
    with httpx.Client(
        auth=TenantOutboundAuth(),
        transport=httpx.MockTransport(lambda request: sent.append(request)),
    ) as client:
        with pytest.raises(RuntimeIdentityError):
            client.post("https://example.invalid/embeddings")
    assert sent == []


@pytest.mark.asyncio
async def test_sdk_wrapped_identity_denial_propagates(tmp_path):
    import openai

    router = SkillSemanticRouter(tmp_path)

    async def denied(*args, **kwargs):
        try:
            raise RuntimeIdentityError("revoked")
        except RuntimeIdentityError as exc:
            raise openai.APIConnectionError(
                request=httpx.Request("POST", "https://example.invalid")
            ) from exc

    router._router = SimpleNamespace(acall=denied)
    with pytest.raises(RuntimeIdentityError):
        await router.amatch("query", strict=True)
