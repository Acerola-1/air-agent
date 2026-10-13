"""R06 回归（轮次01 修复）：mt-v1 迁移票据的窄授权路径（原生 Auth 侧）。

修复前：迁移脚本直接 UPDATE 平台私有 thread 表。修复后：脚本经受限受支持
API（PATCH /threads/{id} + mt-v1 短时效单目的票据），本文件验证钩子只放行
"票据绑定线程 + 台账已登记 migrated 归属 + 仅可信 metadata 键"这一条路径，
其余资源操作一律拒绝。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from langgraph_sdk import Auth

from common.identity import langgraph_auth as lgauth
from common.identity.ticket import (
    TicketError,
    parse_keys,
    verify_migration_ticket,
)

pytestmark = pytest.mark.anyio

_TID = "11111111-2222-4333-8444-555555555555"
_KEYS = [("k1", "unit-test-secret")]


def _sign_mt(
    thread_id: str,
    tenant_id: str,
    user_id: str,
    *,
    secret: str = "unit-test-secret",
    kid: str = "k1",
    audience: str = "langgraph-migration",
    iat: int | None = None,
    exp: int | None = None,
) -> str:
    payload = {
        "typ": "mt-v1",
        "thread": thread_id,
        "tenant": tenant_id,
        "user": user_id,
        "aud": audience,
        "iat": iat if iat is not None else int(time.time()),
        "exp": exp if exp is not None else int(time.time()) + 30,
    }
    body = (
        base64.urlsafe_b64encode(
            json.dumps({"kid": kid, "p": payload}, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        .decode("ascii")
        .rstrip("=")
    )
    sig = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{body}.{base64.urlsafe_b64encode(sig).decode('ascii').rstrip('=')}"


class _Ctx:
    def __init__(self, user: dict[str, Any]) -> None:
        self.user = user


def _migration_user(thread_id: str = _TID) -> dict[str, Any]:
    return {
        "identity": "u-1",
        "tenant_id": "t-1",
        "membership_id": "",
        "session_id": "",
        "is_migration": True,
        "migration_thread_id": thread_id,
    }


# ------------------------------------------------------------------ 票据层


def test_mt_ticket_roundtrip_and_rejections() -> None:
    keys = parse_keys(json.dumps({"k1": "unit-test-secret"}))
    claims = verify_migration_ticket(
        _sign_mt(_TID, "t-1", "u-1"), keys, expected_audience="langgraph-migration"
    )
    assert claims.thread_id == _TID

    with pytest.raises(TicketError):
        verify_migration_ticket(
            _sign_mt(_TID, "t-1", "u-1", audience="langgraph-runtime"),
            keys,
            expected_audience="langgraph-migration",
        )
    with pytest.raises(TicketError):
        verify_migration_ticket(
            _sign_mt(_TID, "t-1", "u-1", exp=int(time.time()) - 1),
            keys,
            expected_audience="langgraph-migration",
        )
    # rt-v1 会话票据不能被 mt 验证器接受（typ 不符）
    with pytest.raises(TicketError):
        verify_migration_ticket(
            "not.a.token", keys, expected_audience="langgraph-migration"
        )


# ------------------------------------------------------------------ 钩子层


async def test_migration_ctx_denied_on_read_search_create_run(monkeypatch) -> None:
    async def fake_owner(thread_id: str, *, tenant_id: str, user_id: str):
        return {"tenant_id": "t-1", "user_id": "u-1", "source": "migrated"}

    monkeypatch.setattr(lgauth, "fetch_thread_owner", fake_owner)
    ctx = _Ctx(_migration_user())
    for hook, value in [
        (lgauth.on_thread_read, {"thread_id": _TID}),
        (lgauth.on_thread_delete, {"thread_id": _TID}),
        (lgauth.on_thread_create_run, {"thread_id": _TID}),
        (lgauth.on_thread_search, {"metadata": {}}),
        (lgauth.on_thread_create, {"thread_id": _TID, "metadata": {}}),
    ]:
        with pytest.raises(Auth.exceptions.HTTPException) as exc:
            await hook(ctx, value)
        assert exc.value.status_code == 403


async def test_migration_update_requires_bound_thread(monkeypatch) -> None:
    async def fake_owner(thread_id: str, *, tenant_id: str, user_id: str):
        return {"tenant_id": "t-1", "user_id": "u-1", "source": "migrated"}

    monkeypatch.setattr(lgauth, "fetch_thread_owner", fake_owner)
    ctx = _Ctx(_migration_user(_TID))
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await lgauth.on_thread_update(
            ctx, {"thread_id": "99999999-8888-4777-8666-555555555555", "metadata": {}}
        )
    assert exc.value.status_code == 403


async def test_migration_update_requires_migrated_ownership(monkeypatch) -> None:
    sources = {"value": "native"}

    async def fake_owner(thread_id: str, *, tenant_id: str, user_id: str):
        if sources["value"] is None:
            return None
        return {"tenant_id": "t-1", "user_id": "u-1", "source": sources["value"]}

    monkeypatch.setattr(lgauth, "fetch_thread_owner", fake_owner)
    ctx = _Ctx(_migration_user())
    # 无归属行（未知历史）：拒绝
    sources["value"] = None
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await lgauth.on_thread_update(ctx, {"thread_id": _TID, "metadata": {}})
    assert exc.value.status_code in (403, 404)
    # native（本人创建线程）不允许走迁移路径
    sources["value"] = "native"
    with pytest.raises(Auth.exceptions.HTTPException):
        await lgauth.on_thread_update(ctx, {"thread_id": _TID, "metadata": {}})


async def test_migration_update_forces_trusted_keys(monkeypatch) -> None:
    async def fake_owner(thread_id: str, *, tenant_id: str, user_id: str):
        return {"tenant_id": "t-1", "user_id": "u-1", "source": "migrated"}

    monkeypatch.setattr(lgauth, "fetch_thread_owner", fake_owner)
    ctx = _Ctx(_migration_user())
    value: dict[str, Any] = {
        "thread_id": _TID,
        "metadata": {"user_id": "spoof", "tenant_id": "other", "graph_id": "basic-qa"},
    }
    await lgauth.on_thread_update(ctx, value)
    assert value["metadata"]["user_id"] == "u-1"
    assert value["metadata"]["tenant_id"] == "t-1"
    assert value["metadata"]["graph_id"] == "basic-qa"  # 业务 metadata 保留
