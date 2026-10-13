"""统一认证接口的端到端测试（真实 HTTP 层 + 真实数据库运行角色连接）。

    .venv/bin/python -m pytest tests/test_auth_api.py -q

覆盖任务 3.1/3.2 的协议行为：不透明会话签发、me 固定形状、登出撤销、
账号停用、旧 HMAC cookie 拒绝、cookie 属性。fixture 直接建数走 owner
连接（平台引导语义）；请求路径走 runtime 角色（RLS 真实生效）。
"""

from __future__ import annotations

import uuid

import pytest
from ctx_client import ContextClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import OwnerSessionLocal
from app.main import app
from app.models import User
from app.security import SESSION_COOKIE, hash_password

ACCOUNT_PREFIX = "tmp_api_"
PASSWORD = "tmp-password"


@pytest.fixture
async def api_user() -> dict[str, str]:
    """建一个带随机账号的测试用户，用完删掉。"""
    account = f"{ACCOUNT_PREFIX}{uuid.uuid4().hex[:10]}"
    async with OwnerSessionLocal() as session:
        user = User(
            account=account,
            display_name="接口测试用户",
            password_hash=hash_password(PASSWORD),
            is_active=True,
        )
        session.add(user)
        await session.commit()
        uid = user.id

    yield {"account": account, "password": PASSWORD, "id": str(uid)}

    async with OwnerSessionLocal() as session:
        victim = await session.get(User, uid)
        if victim is not None:
            await session.delete(victim)
            await session.commit()


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with ContextClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client: AsyncClient) -> None:
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


async def test_login_returns_user_and_sets_cookie(
    client: AsyncClient, api_user: dict[str, str]
) -> None:
    res = await client.post(
        "/auth/login",
        json={"account": api_user["account"], "password": api_user["password"]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["user"]["account"] == api_user["account"]
    assert body["user"]["display_name"] == "接口测试用户"
    assert body["permissions"] == []  # 未分配角色，无任何权限
    # 固定响应外壳（任务 3.2）
    for key in (
        "user",
        "session",
        "current_tenant",
        "membership",
        "permissions",
        "context_version",
    ):
        assert key in body
    assert SESSION_COOKIE in res.cookies

    raw = res.headers.get("set-cookie", "")
    assert "HttpOnly" in raw
    assert "SameSite=lax" in raw


async def test_login_cookie_is_opaque_and_session_scoped(
    client: AsyncClient, api_user: dict[str, str]
) -> None:
    """cookie = "<uuid>.<opaque>"；服务端不存原始 token。"""
    res = await client.post(
        "/auth/login",
        json={"account": api_user["account"], "password": api_user["password"]},
    )
    token = res.cookies[SESSION_COOKIE]
    session_id, _, raw = token.partition(".")
    uuid.UUID(session_id)  # 合法 UUID 段
    assert len(raw) >= 32
    async with OwnerSessionLocal() as session:
        row = (
            await session.execute(select(User).where(User.id == uuid.UUID(api_user["id"])))
        ).scalar_one()
        assert row is not None
        # 会话行数与摘要长度（64 hex）证明只落摘要
        from sqlalchemy import text

        count = await session.scalar(
            text("SELECT count(*) FROM auth_sessions WHERE user_id = :u"),
            {"u": api_user["id"]},
        )
        assert count == 1
        hash_len = await session.scalar(
            text("SELECT length(token_hash) FROM auth_sessions WHERE user_id = :u"),
            {"u": api_user["id"]},
        )
        assert hash_len == 64


async def test_login_wrong_password(client: AsyncClient, api_user: dict[str, str]) -> None:
    res = await client.post(
        "/auth/login", json={"account": api_user["account"], "password": "wrong"}
    )
    assert res.status_code == 401


async def test_login_unknown_account_uses_same_message(client: AsyncClient) -> None:
    res = await client.post(
        "/auth/login", json={"account": "no_such_user_xyz", "password": "whatever"}
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "账号或密码错误"


async def test_login_disabled_account_forbidden(
    client: AsyncClient, api_user: dict[str, str]
) -> None:
    async with OwnerSessionLocal() as session:
        user = await session.scalar(select(User).where(User.account == api_user["account"]))
        assert user is not None
        user.is_active = False
        await session.commit()

    res = await client.post(
        "/auth/login",
        json={"account": api_user["account"], "password": api_user["password"]},
    )
    assert res.status_code == 403


async def test_me_requires_session(client: AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401


async def test_me_with_cookie(client: AsyncClient, api_user: dict[str, str]) -> None:
    await client.post(
        "/auth/login",
        json={"account": api_user["account"], "password": api_user["password"]},
    )
    res = await client.get("/auth/me")
    assert res.status_code == 200
    assert res.json()["user"]["account"] == api_user["account"]


async def test_me_rejects_forged_cookie(client: AsyncClient) -> None:
    client.cookies.set(SESSION_COOKIE, "ZmFrZQ.ZmFrZQ")
    assert (await client.get("/auth/me")).status_code == 401


async def test_me_rejects_legacy_hmac_cookie(client: AsyncClient) -> None:
    """旧 HMAC cookie（两段但首段非 UUID）不再授予访问。"""
    import base64
    import hashlib
    import hmac as _hmac
    import time

    from app.config import get_settings

    secret = get_settings().auth_secret or "legacy-secret"
    body = f"{uuid.uuid4()}.someuser.{int(time.time()) + 3600}"
    payload = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    sig = (
        base64.urlsafe_b64encode(_hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        .decode()
        .rstrip("=")
    )
    client.cookies.set(SESSION_COOKIE, f"{payload}.{sig}")
    res = await client.get("/auth/me")
    assert res.status_code == 401


async def test_me_rejects_valid_session_for_disabled_user(
    client: AsyncClient, api_user: dict[str, str]
) -> None:
    """会话行还在，但账号被停用 → 后续请求 403。"""
    await client.post(
        "/auth/login",
        json={"account": api_user["account"], "password": api_user["password"]},
    )
    assert (await client.get("/auth/me")).status_code == 200

    async with OwnerSessionLocal() as session:
        user = await session.scalar(select(User).where(User.account == api_user["account"]))
        assert user is not None
        user.is_active = False
        await session.commit()

    res = await client.get("/auth/me")
    assert res.status_code == 403


async def test_logout_revokes_server_side(client: AsyncClient, api_user: dict[str, str]) -> None:
    """登出后重放 cookie：即使原始 token 没变，服务端撤销必须生效。"""
    res = await client.post(
        "/auth/login",
        json={"account": api_user["account"], "password": api_user["password"]},
    )
    cookie = res.cookies[SESSION_COOKIE]

    assert (await client.post("/auth/logout")).status_code == 204

    replay = ContextClient(transport=ASGITransport(app=app), base_url="http://test")
    replay.cookies.set(SESSION_COOKIE, cookie)
    assert (await replay.get("/auth/me")).status_code == 401
    await replay.aclose()


async def test_logout_is_idempotent(client: AsyncClient) -> None:
    assert (await client.post("/auth/logout")).status_code == 204


async def test_login_updates_last_login_at(client: AsyncClient, api_user: dict[str, str]) -> None:
    res = await client.post(
        "/auth/login",
        json={"account": api_user["account"], "password": api_user["password"]},
    )
    assert res.json()["user"]["last_login_at"] is not None


async def test_change_password_revokes_all_sessions(
    client: AsyncClient, api_user: dict[str, str]
) -> None:
    """改密后：当前页面后续请求不再成功（任务 3.1 会话策略）。"""
    res = await client.post(
        "/auth/login",
        json={"account": api_user["account"], "password": api_user["password"]},
    )
    old_cookie = res.cookies[SESSION_COOKIE]

    res = await client.post(
        "/auth/change-password",
        json={"old_password": PASSWORD, "new_password": "new-password-123"},
    )
    assert res.status_code == 204

    replay = ContextClient(transport=ASGITransport(app=app), base_url="http://test")
    replay.cookies.set(SESSION_COOKIE, old_cookie)
    assert (await replay.get("/auth/me")).status_code == 401
    await replay.aclose()

    # 新密码可登录
    fresh = ContextClient(transport=ASGITransport(app=app), base_url="http://test")
    res = await fresh.post(
        "/auth/login",
        json={"account": api_user["account"], "password": "new-password-123"},
    )
    assert res.status_code == 200
    await fresh.aclose()
