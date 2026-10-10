"""auth 接口的端到端测试（走真实 HTTP 层 + 真实数据库）。

    .venv/bin/python -m pytest tests/test_auth_api.py -q

覆盖：登录成功/失败、账号停用、cookie 属性、会话解析、登出、权限码返回。
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import User
from app.security import SESSION_COOKIE, hash_password, verify_session

ACCOUNT_PREFIX = "tmp_api_"
PASSWORD = "tmp-password"


@pytest.fixture
async def api_user() -> dict[str, str]:
    """建一个带随机账号的测试用户，用完删掉。"""
    account = f"{ACCOUNT_PREFIX}{uuid.uuid4().hex[:10]}"
    async with SessionLocal() as session:
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

    async with SessionLocal() as session:
        victim = await session.get(User, uid)
        if victim is not None:
            await session.delete(victim)
            await session.commit()


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
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
    assert body["account"] == api_user["account"]
    assert body["display_name"] == "接口测试用户"
    assert body["permissions"] == []  # 未分配角色，无任何权限
    assert SESSION_COOKIE in res.cookies

    # cookie 必须是 httpOnly，前端 JS 不可读
    raw = res.headers.get("set-cookie", "")
    assert "HttpOnly" in raw
    assert "SameSite=lax" in raw


async def test_login_cookie_is_verifiable_and_matches_user(
    client: AsyncClient, api_user: dict[str, str]
) -> None:
    """签发的 token 必须能被验签，且载荷里是 user_id + account。"""
    res = await client.post(
        "/auth/login",
        json={"account": api_user["account"], "password": api_user["password"]},
    )
    token = res.cookies[SESSION_COOKIE]
    payload = verify_session(token, get_settings().auth_secret)

    assert payload is not None
    assert payload.user_id == api_user["id"]
    assert payload.account == api_user["account"]


async def test_login_wrong_password(client: AsyncClient, api_user: dict[str, str]) -> None:
    res = await client.post(
        "/auth/login", json={"account": api_user["account"], "password": "wrong"}
    )
    assert res.status_code == 401


async def test_login_unknown_account_uses_same_message(client: AsyncClient) -> None:
    """不泄露账号是否存在：提示必须与密码错误完全一致。"""
    res = await client.post(
        "/auth/login", json={"account": "no_such_user_xyz", "password": "whatever"}
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "账号或密码错误"


async def test_login_disabled_account_forbidden(
    client: AsyncClient, api_user: dict[str, str]
) -> None:
    async with SessionLocal() as session:
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
    assert res.json()["account"] == api_user["account"]


async def test_me_rejects_forged_cookie(client: AsyncClient) -> None:
    client.cookies.set(SESSION_COOKIE, "ZmFrZQ.ZmFrZQ")
    assert (await client.get("/auth/me")).status_code == 401


async def test_me_rejects_valid_token_for_deleted_user(client: AsyncClient) -> None:
    """token 合法但用户已被删除 → 401，不能因为签名对就放行。"""
    async with SessionLocal() as session:
        account = f"{ACCOUNT_PREFIX}{uuid.uuid4().hex[:10]}"
        user = User(
            account=account,
            display_name="将被删除",
            password_hash=hash_password(PASSWORD),
            is_active=True,
        )
        session.add(user)
        await session.commit()

    await client.post("/auth/login", json={"account": account, "password": PASSWORD})
    assert (await client.get("/auth/me")).status_code == 200

    async with SessionLocal() as session:
        victim = await session.scalar(select(User).where(User.account == account))
        assert victim is not None
        await session.delete(victim)
        await session.commit()

    assert (await client.get("/auth/me")).status_code == 401


async def test_logout_clears_cookie(client: AsyncClient, api_user: dict[str, str]) -> None:
    await client.post(
        "/auth/login",
        json={"account": api_user["account"], "password": api_user["password"]},
    )
    res = await client.post("/auth/logout")
    assert res.status_code == 204
    # delete_cookie 会下发一个空值 + Max-Age=0
    raw = res.headers.get("set-cookie", "")
    assert "Max-Age=0" in raw or "max-age=0" in raw


async def test_login_updates_last_login_at(
    client: AsyncClient, api_user: dict[str, str]
) -> None:
    res = await client.post(
        "/auth/login",
        json={"account": api_user["account"], "password": api_user["password"]},
    )
    assert res.json()["last_login_at"] is not None
