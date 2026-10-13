"""R04 回归（轮次01 修复）：运行中撤权必须比较修订号，且覆盖切 A→B→A。

修复前断言：`RuntimeIdentity` 不携带任何可比较的修订值，`revalidate` 在
session ctx_version、user/tenant/membership 修订全部变化但状态均 active 时
仍然放行（评审 runtime_probe 反例 `revision_changed_runtime_denied=false`）。
"""

from __future__ import annotations

from typing import Any

import pytest

from common.identity.db import IdentityState
from common.identity.runtime import (
    RuntimeIdentity,
    RuntimeIdentityRevoked,
    identity_from_config,
    revalidate,
)

pytestmark = pytest.mark.anyio


def _state(**overrides: Any) -> IdentityState:
    base = {
        "session_active": True,
        "session_unexpired": True,
        "session_ctx_version": 1,
        "user_active": True,
        "account": "alice",
        "user_auth_revision": 0,
        "tenant_status": "active",
        "tenant_auth_revision": 1,
        "tenant_code": "a",
        "membership_status": "active",
        "membership_revision": 1,
    }
    base.update(overrides)
    return IdentityState(**base)


def _identity(**overrides: Any) -> RuntimeIdentity:
    base: dict[str, Any] = {
        "user_id": "u-1",
        "tenant_id": "t-1",
        "membership_id": "m-1",
        "session_id": "s-1",
        "session_ctx_version": 1,
        "user_auth_revision": 0,
        "tenant_auth_revision": 1,
        "membership_revision": 1,
    }
    base.update(overrides)
    return RuntimeIdentity(**base)


async def test_membership_revision_bump_denied_even_while_active(monkeypatch) -> None:
    """角色撤销使成员修订前进但状态仍 active → 下一次调用必须拒绝。"""
    import common.identity.runtime as rt

    async def fake_state(
        session_id: str, *, user_id: str, tenant_id: str
    ) -> IdentityState:
        return _state(membership_revision=99)

    monkeypatch.setattr(rt, "fetch_identity_state", fake_state)
    with pytest.raises(RuntimeIdentityRevoked):
        await revalidate(_identity())


async def test_session_ctx_version_change_denied(monkeypatch) -> None:
    """切 A→B→A 两次都递增 ctx_version；旧运行上下文必须以拒绝收敛。"""
    import common.identity.runtime as rt

    async def fake_state(
        session_id: str, *, user_id: str, tenant_id: str
    ) -> IdentityState:
        return _state(session_ctx_version=3)

    monkeypatch.setattr(rt, "fetch_identity_state", fake_state)
    with pytest.raises(RuntimeIdentityRevoked):
        await revalidate(_identity(session_ctx_version=1))


async def test_user_auth_revision_change_denied(monkeypatch) -> None:
    import common.identity.runtime as rt

    async def fake_state(
        session_id: str, *, user_id: str, tenant_id: str
    ) -> IdentityState:
        return _state(user_auth_revision=7)

    monkeypatch.setattr(rt, "fetch_identity_state", fake_state)
    with pytest.raises(RuntimeIdentityRevoked):
        await revalidate(_identity(user_auth_revision=0))


async def test_tenant_auth_revision_change_denied(monkeypatch) -> None:
    import common.identity.runtime as rt

    async def fake_state(
        session_id: str, *, user_id: str, tenant_id: str
    ) -> IdentityState:
        return _state(tenant_auth_revision=5)

    monkeypatch.setattr(rt, "fetch_identity_state", fake_state)
    with pytest.raises(RuntimeIdentityRevoked):
        await revalidate(_identity(tenant_auth_revision=1))


async def test_matching_revisions_pass(monkeypatch) -> None:
    import common.identity.runtime as rt

    async def fake_state(
        session_id: str, *, user_id: str, tenant_id: str
    ) -> IdentityState:
        return _state()

    monkeypatch.setattr(rt, "fetch_identity_state", fake_state)
    await revalidate(_identity())  # 不抛错即通过


def test_identity_from_config_carries_revisions() -> None:
    """authenticate 注入的可信上下文必须携带修订值供运行中比较。"""
    user = {
        "identity": "u-1",
        "tenant_id": "t-1",
        "membership_id": "m-1",
        "session_id": "s-1",
        "session_ctx_version": 2,
        "user_auth_revision": 3,
        "tenant_auth_revision": 4,
        "membership_revision": 5,
    }
    identity = identity_from_config({"configurable": {"langgraph_auth_user": user}})
    assert identity is not None
    assert identity.session_ctx_version == 2
    assert identity.user_auth_revision == 3
    assert identity.tenant_auth_revision == 4
    assert identity.membership_revision == 5
