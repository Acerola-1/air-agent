"""独立复验发现的回归：可信引用传递、同步/异步远程边界和严格协议。"""

import asyncio
import time

import httpx
import pytest
from tests.unit_tests.test_remote_identity_client import _identity_payload

from common.identity import remote, runtime
from common.identity.db import IdentityBackendUnavailable


def actor():
    return dict(
        identity="11111111-1111-4111-8111-111111111111",
        tenant_id="22222222-2222-4222-8222-222222222222",
        membership_id="33333333-3333-4333-8333-333333333333",
        session_id="44444444-4444-4444-8444-444444444444",
        verification_ref="55555555-5555-4555-8555-555555555555",
        user_auth_revision=0,
        tenant_auth_revision=1,
        membership_revision=1,
        session_ctx_version=2,
    )


@pytest.mark.parametrize("source", ["config", "server_info"])
def test_trusted_reference_reaches_runtime(source):
    user = actor()
    value = (
        runtime.identity_from_config({"configurable": {"langgraph_auth_user": user}})
        if source == "config"
        else runtime.identity_from_auth_user(user)
    )
    assert value.verification_ref == user["verification_ref"]
    assert runtime.identity_from_config({"metadata": user}) is None


def test_sync_remote_never_queries_legacy_identity(monkeypatch):
    import common.identity.db as db

    monkeypatch.setenv("AGENT_IDENTITY_BACKEND", "remote")
    calls = []
    monkeypatch.setattr(
        remote,
        "recheck_execution_context",
        lambda *a, **kw: calls.append(a) or (True, True),
    )

    def legacy(*a, **kw):
        pytest.fail("remote 发送不得查询本地会话")

    monkeypatch.setattr(db, "fetch_identity_state_sync", legacy)
    runtime.guard_runtime_call_sync({"configurable": {"langgraph_auth_user": actor()}})
    assert len(calls) == 1


@pytest.mark.anyio
async def test_async_iam_does_not_block_loop(monkeypatch):
    monkeypatch.setenv("AGENT_IDENTITY_BACKEND", "remote")

    def slow(*a, **kw):
        time.sleep(0.08)
        return True, True

    monkeypatch.setattr(remote, "recheck_execution_context", slow)
    task = asyncio.create_task(
        runtime.revalidate(runtime.identity_from_auth_user(actor()))
    )
    await asyncio.sleep(0.01)
    assert not task.done()
    await task


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["principal"].update(id="not-a-uuid"),
        lambda p: p["membership"].update(tenant_id="wrong"),
        lambda p: p["membership"].update(revision=99),
        lambda p: p["revisions"].update(user_auth_revision=-1),
        lambda p: p.update(context_usable=False),
        lambda p: p.update(needs_tenant_selection=True),
        lambda p: p["session"].update(expires_at="unknown"),
    ],
)
def test_malformed_identity_never_authorizes(mutation):
    payload = _identity_payload()
    mutation(payload)
    with pytest.raises(remote.RemoteProtocolError):
        remote.parse_human_identity(
            payload,
            expected_issuer="air-agent-iam",
            expected_audience="langgraph-runtime",
        )


def test_revision_reply_must_equal_requested_baseline(monkeypatch):
    revisions = dict(
        user_auth_revision=0,
        tenant_auth_revision=1,
        membership_revision=1,
        session_ctx_version=2,
    )
    monkeypatch.setattr(
        remote,
        "request_iam",
        lambda *a, **kw: httpx.Response(
            200,
            json={
                "active": True,
                "context_matches": True,
                "revisions": dict(revisions, session_ctx_version=999),
            },
        ),
    )
    with pytest.raises(remote.RemoteProtocolError):
        remote.recheck_execution_context(
            "ref",
            revisions,
            expected_audience="langgraph-runtime",
            config={"IAM_ISSUER": "iam"},
        )


def test_total_deadline_discards_late_success(monkeypatch):
    class SlowClient:
        def request(self, *a, **kw):
            time.sleep(0.15)
            return httpx.Response(200, json={"active": True})

    monkeypatch.setattr(remote, "_deadline", lambda: 0.03)
    monkeypatch.setattr(remote, "_shared_client", lambda cfg: SlowClient())
    started = time.monotonic()
    with pytest.raises(IdentityBackendUnavailable):
        remote.request_iam("POST", "/introspect", config={})
    assert time.monotonic() - started < 0.10
