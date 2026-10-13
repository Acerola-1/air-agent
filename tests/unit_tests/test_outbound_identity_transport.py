"""发送守卫覆盖辅助模型调用，隔离同一传输中的并发身份。"""

from contextvars import ContextVar

import httpx
import pytest

from common.identity import outbound
from common.identity.db import IdentityState
from common.identity.runtime import RuntimeIdentityError

pytestmark = pytest.mark.anyio


def config(tenant="a"):
    return {
        "configurable": {
            "langgraph_auth_user": {
                "identity": "u",
                "tenant_id": tenant,
                "membership_id": "m",
                "session_id": "s",
                "session_ctx_version": 1,
                "user_auth_revision": 0,
                "tenant_auth_revision": 1,
                "membership_revision": 1,
            }
        }
    }


def active(revision=1):
    return IdentityState(
        True, True, 1, True, "synthetic", 0, "active", 1, "a", "active", revision
    )


async def test_transport_refuses_missing_and_revoked_identity(monkeypatch):
    import common.identity.runtime as rt

    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    cfg = {"value": {}}
    revision = {"value": 1}
    sent = []
    monkeypatch.setattr(outbound, "get_config", lambda: cfg["value"])

    async def fetch(*args, **kwargs):
        return active(revision["value"])

    async def send(request):
        sent.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(rt, "fetch_identity_state", fetch)
    async with httpx.AsyncClient(
        auth=outbound.TenantOutboundAuth(), transport=httpx.MockTransport(send)
    ) as client:
        with pytest.raises(RuntimeIdentityError):
            await client.get("https://synthetic.invalid/summary")
        assert not sent
        cfg["value"] = config()
        await client.get("https://synthetic.invalid/summary")
        revision["value"] = 2
        with pytest.raises(RuntimeIdentityError):
            await client.get("https://synthetic.invalid/child-agent")
        assert sent == ["/summary"]


async def test_shared_transport_does_not_cache_tenant_context(monkeypatch):
    import asyncio

    import common.identity.runtime as rt

    monkeypatch.setenv("TENANT_ENFORCE_RUNTIME_IDENTITY", "true")
    current = ContextVar("synthetic_current")
    checked, sent = [], []
    monkeypatch.setattr(outbound, "get_config", lambda: current.get())

    async def fetch(session_id, *, tenant_id, user_id):
        checked.append(tenant_id)
        await asyncio.sleep(0)
        return active()

    async def send(request):
        sent.append(current.get()["configurable"]["langgraph_auth_user"]["tenant_id"])
        return httpx.Response(200)

    monkeypatch.setattr(rt, "fetch_identity_state", fetch)
    async with httpx.AsyncClient(
        auth=outbound.TenantOutboundAuth(), transport=httpx.MockTransport(send)
    ) as client:

        async def call(tenant):
            token = current.set(config(tenant))
            try:
                await client.get("https://synthetic.invalid/model")
            finally:
                current.reset(token)

        await asyncio.gather(call("a"), call("b"))
    assert sorted(checked) == ["a", "b"]
    assert sorted(sent) == ["a", "b"]
