"""R01 回归（轮次01 修复）：历史线程认领越权的原生侧协议。

修复前断言：`on_thread_create` 无条件调用 `record_thread_ownership`
（`ON CONFLICT DO NOTHING` 静默吞并），客户端可携带 `if_exists=do_nothing`
对未知历史归属的线程 ID 发起"创建"，把平台既有线程登记为本人并读取。
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph_sdk import Auth

from common.identity import langgraph_auth as lgauth
from common.identity.db import IdentityBackendUnavailable

pytestmark = pytest.mark.anyio

_TID = "0f1bab7a-0000-4000-8000-0000000000aa"
_IDS = {
    "identity": "u-1",
    "tenant_id": "t-1",
    "membership_id": "m-1",
    "session_id": "s-1",
}


class _Ctx:
    class user:  # noqa: N801
        identity = "u-1"
        tenant_id = "t-1"
        membership_id = "m-1"
        session_id = "s-1"


def _patch_claim(monkeypatch: Any, status: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_claim(
        thread_id: str, *, tenant_id: str, user_id: str, session_id: str = ""
    ):
        calls.append(
            {"thread_id": thread_id, "tenant_id": tenant_id, "user_id": user_id}
        )
        return status

    monkeypatch.setattr(lgauth, "claim_thread_ownership", fake_claim)
    return calls


async def test_do_nothing_creation_rejected(monkeypatch) -> None:
    """do_nothing/update 语义允许"吸收"平台既有线程，原生侧必须拒绝非严格创建。"""
    _patch_claim(monkeypatch, "claimed")
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await lgauth.on_thread_create(
            _Ctx(),
            {
                "thread_id": _TID,
                "if_exists": "do_nothing",
                "metadata": {},
            },
        )
    assert exc.value.status_code == 403


async def test_update_if_exists_rejected(monkeypatch) -> None:
    _patch_claim(monkeypatch, "claimed")
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await lgauth.on_thread_create(
            _Ctx(),
            {
                "thread_id": _TID,
                "if_exists": "update",
                "metadata": {},
            },
        )
    assert exc.value.status_code in (403, 409)


async def test_ownership_conflict_denies_claim(monkeypatch) -> None:
    """认领发现归属行已存在（他企业/他人/未知历史）：不得静默吞并。"""
    calls = _patch_claim(monkeypatch, "conflict")
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await lgauth.on_thread_create(
            _Ctx(),
            {
                "thread_id": _TID,
                "metadata": {},
            },
        )
    assert exc.value.status_code in (403, 404, 409)
    assert calls  # 协议走认领而非无条件登记


async def test_fresh_claim_is_pending_not_confirmed(monkeypatch) -> None:
    """新认领必须处于待确认（创建成功前不可读）——修复前的 record_ 无条件确认。"""

    async def fake_claim(
        thread_id: str, *, tenant_id: str, user_id: str, session_id: str = ""
    ):
        return "claimed"

    monkeypatch.setattr(lgauth, "claim_thread_ownership", fake_claim)
    await lgauth.on_thread_create(_Ctx(), {"thread_id": _TID, "metadata": {}})

    # 读取侧：pending 归属必须拒绝
    async def fake_owner(thread_id: str, *, tenant_id: str, user_id: str):
        return {"tenant_id": "t-1", "user_id": "u-1", "source": "native_pending"}

    monkeypatch.setattr(lgauth, "fetch_thread_owner", fake_owner)
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await lgauth.on_thread_read(_Ctx(), {"thread_id": _TID})
    assert exc.value.status_code in (404, 403)


async def test_confirmed_owner_still_readable(monkeypatch) -> None:
    async def fake_owner(thread_id: str, *, tenant_id: str, user_id: str):
        return {"tenant_id": "t-1", "user_id": "u-1", "source": "native"}

    monkeypatch.setattr(lgauth, "fetch_thread_owner", fake_owner)
    await lgauth.on_thread_read(_Ctx(), {"thread_id": _TID})  # 不抛错


async def test_migrated_owner_readable(monkeypatch) -> None:
    async def fake_owner(thread_id: str, *, tenant_id: str, user_id: str):
        return {"tenant_id": "t-1", "user_id": "u-1", "source": "migrated"}

    monkeypatch.setattr(lgauth, "fetch_thread_owner", fake_owner)
    await lgauth.on_thread_read(_Ctx(), {"thread_id": _TID})


async def test_claim_backend_failure_fails_closed(monkeypatch) -> None:
    async def fake_claim(
        thread_id: str, *, tenant_id: str, user_id: str, session_id: str = ""
    ):
        raise IdentityBackendUnavailable("down")

    monkeypatch.setattr(lgauth, "claim_thread_ownership", fake_claim)
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await lgauth.on_thread_create(_Ctx(), {"thread_id": _TID, "metadata": {}})
    assert exc.value.status_code == 503
