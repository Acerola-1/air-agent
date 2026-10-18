"""remote 逐发送守卫（G2.2，A08 受控层）：主/辅助/摘要/子任务/MCP/retry 每发送复查引用.

- remote 模式 revalidate 走 execution_context 复查（受控 transport 注入）；
- 修订前进/主体撤销 → RuntimeIdentityRevoked，下一次真实发送增量 0；
- 引用缺失（remote 下）一律拒绝（不能从 config/metadata 自报建立）；
- local 模式原断言不变（test_per_call_outbound_guards.py 已覆盖）。
"""

from __future__ import annotations

import httpx
import pytest

from common.identity import remote as remote_mod
from common.identity import runtime as rt
from common.identity.runtime import RuntimeIdentity, RuntimeIdentityRevoked, revalidate

pytestmark = pytest.mark.anyio


CONFIG = {
    "IAM_BASE_URL": "http://127.0.0.1:50100",
    "IAM_ISSUER": "air-agent-iam",
    "IAM_INTERNAL_CLIENT_ID": "client",
    "IAM_INTERNAL_CLIENT_SECRET": "secret",
    "IAM_RUNTIME_AUDIENCE": "langgraph-runtime",
}


def _identity(ref: str | None = "ref-1") -> RuntimeIdentity:
    return RuntimeIdentity(
        user_id="u1",
        tenant_id="t1",
        membership_id="m1",
        session_id="s1",
        session_ctx_version=2,
        user_auth_revision=0,
        tenant_auth_revision=1,
        membership_revision=1,
        verification_ref=ref,
    )


def _inject(handler) -> None:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url=CONFIG["IAM_BASE_URL"])
    remote_mod._CLIENT = client  # noqa: SLF001
    remote_mod._CLIENT._iam_config_key = remote_mod._config_key(CONFIG)  # noqa: SLF001


@pytest.fixture(autouse=True)
def _remote_mode(monkeypatch):
    monkeypatch.setattr(remote_mod, "require_remote_config", lambda: CONFIG)
    monkeypatch.setattr(rt, "_remote_enabled", lambda: True)
    yield
    remote_mod.close_shared_client()


def _recheck(active: bool = True, matches: bool = True):
    revisions = {
        "user_auth_revision": 0,
        "tenant_auth_revision": 1,
        "membership_revision": 1,
        "session_ctx_version": 2,
    }
    seen = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["count"] += 1
        return httpx.Response(
            200,
            json={"active": active, "context_matches": matches, "revisions": revisions},
        )

    _inject(handler)
    return seen


async def test_remote_revalidate_happy_path():
    seen = _recheck()
    await revalidate(_identity())
    assert seen["count"] == 1


async def test_remote_revalidate_requires_ref():
    _recheck()
    with pytest.raises(RuntimeIdentityRevoked):
        await revalidate(_identity(ref=None))


async def test_remote_revoked_next_send_zero(monkeypatch):
    seen = _recheck(active=False)
    with pytest.raises(RuntimeIdentityRevoked):
        await revalidate(_identity())
    # 重试同样拒绝（不绕过）
    with pytest.raises(RuntimeIdentityRevoked):
        await revalidate(_identity())
    assert seen["count"] == 2  # 只有复查请求；无其他网络增量


async def test_remote_revision_mismatch(monkeypatch):
    seen = _recheck(matches=False)
    with pytest.raises(RuntimeIdentityRevoked):
        await revalidate(_identity())
    assert seen["count"] == 1


async def test_remote_transport_failure_fails_closed(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    _inject(handler)
    with pytest.raises(RuntimeIdentityRevoked):
        await revalidate(_identity())


async def test_local_mode_untouched(monkeypatch):
    """local 语义：不触发 IAM 请求，走 fetch_identity_state（monkeypatch 计数）。"""
    monkeypatch.setattr(rt, "_remote_enabled", lambda: False)
    calls = {"n": 0}

    async def fake_fetch(session_id, *, user_id, tenant_id):
        calls["n"] += 1
        from common.identity.db import IdentityState

        return IdentityState(
            session_active=True,
            session_unexpired=True,
            session_ctx_version=2,
            user_active=True,
            account="a",
            user_auth_revision=0,
            tenant_status="active",
            tenant_auth_revision=1,
            tenant_code="c",
            membership_status="active",
            membership_revision=1,
        )

    monkeypatch.setattr(rt, "fetch_identity_state", fake_fetch)
    await revalidate(_identity(ref=None))
    assert calls["n"] == 1
