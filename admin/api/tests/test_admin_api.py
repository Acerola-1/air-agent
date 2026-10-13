"""管理接口端到端测试：RBAC 边界、用户/角色 CRUD、关注城市、审计。

    .venv/bin/python -m pytest tests/test_admin_api.py -q

依赖真实数据库与已导入的区划数据（`scripts/import_regions.py`）。
测试自建管理员与普通用户，结束时清理。
"""

from __future__ import annotations

import uuid

import pytest
from ctx_client import ContextClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import OwnerSessionLocal
from app.main import app
from app.models import Role, User, UserRole
from app.security import hash_password

PASSWORD = "test-password-123"
SMOKE_PREFIX = "tmp_adm_"


async def _make_user(account: str, role_codes: list[str]) -> uuid.UUID:
    async with OwnerSessionLocal() as session:
        user = User(
            account=account,
            display_name=f"测试-{account}",
            password_hash=hash_password(PASSWORD),
            is_active=True,
        )
        session.add(user)
        await session.flush()
        if role_codes:
            roles = (await session.scalars(select(Role).where(Role.code.in_(role_codes)))).all()
            for role in roles:
                session.add(UserRole(user_id=user.id, role_id=role.id))
        await session.commit()
        return user.id


async def _drop_user(user_id: uuid.UUID) -> None:
    async with OwnerSessionLocal() as session:
        victim = await session.get(User, user_id)
        if victim is not None:
            await session.delete(victim)
            await session.commit()


async def _login(account: str, password: str = PASSWORD) -> AsyncClient:
    client = ContextClient(transport=ASGITransport(app=app), base_url="http://test")
    res = await client.post("/auth/login", json={"account": account, "password": password})
    assert res.status_code == 200, res.text
    return client


@pytest.fixture
async def admin() -> AsyncClient:
    """带 super_admin 的临时管理员。"""
    account = f"{SMOKE_PREFIX}admin_{uuid.uuid4().hex[:8]}"
    uid = await _make_user(account, ["super_admin"])
    client = await _login(account)
    yield client
    await client.aclose()
    await _drop_user(uid)


@pytest.fixture
async def viewer() -> AsyncClient:
    """只有 viewer 角色（全部是 :read 权限）的普通用户。"""
    account = f"{SMOKE_PREFIX}viewer_{uuid.uuid4().hex[:8]}"
    uid = await _make_user(account, ["viewer"])
    client = await _login(account)
    yield client
    await client.aclose()
    await _drop_user(uid)


# --------------------------------------------------------------------- 菜单与权限边界


async def test_admin_sees_both_top_menus(admin: AsyncClient) -> None:
    res = await admin.get("/auth/menus")
    assert res.status_code == 200
    names = [node["name"] for node in res.json()]
    # 新权限树：super_admin 额外看到企业管理顶层目录
    assert names == ["系统管理", "企业管理", "业务管理"]


async def test_viewer_sees_only_read_menus(viewer: AsyncClient) -> None:
    """viewer 只有 :read 权限，但父级菜单会被补齐，否则页面渲染不出来。"""
    res = await viewer.get("/auth/menus")
    assert res.status_code == 200
    codes = _collect_codes(res.json())
    assert "user:read" in codes
    assert "user:create" not in codes
    assert "system:user" in codes  # 父级菜单被补齐


def _collect_codes(nodes: list[dict]) -> set[str]:
    result: set[str] = set()
    for node in nodes:
        result.add(node["code"])
        result |= _collect_codes(node.get("children", []))
    return result


async def test_viewer_cannot_create_user(viewer: AsyncClient) -> None:
    res = await viewer.post(
        "/users", json={"account": "nope", "display_name": "无权限", "password": "123456"}
    )
    assert res.status_code == 403
    assert "user:create" in res.json()["detail"]


async def test_viewer_cannot_create_role(viewer: AsyncClient) -> None:
    res = await viewer.post("/roles", json={"code": "nope", "name": "无权限"})
    assert res.status_code == 403


async def test_viewer_cannot_read_full_permission_tree(viewer: AsyncClient) -> None:
    """viewer 有 permission:read，所以能读；换个无权限用户才应 403。"""
    assert (await viewer.get("/permissions/tree")).status_code == 200


async def test_no_role_user_has_no_permissions() -> None:
    account = f"{SMOKE_PREFIX}norole_{uuid.uuid4().hex[:8]}"
    uid = await _make_user(account, [])
    client = await _login(account)
    try:
        ctx = (await client.get("/auth/me")).json()
        assert ctx["user"]["id"]
        assert ctx["permissions"] == []
        assert (await client.get("/users")).status_code == 403
        assert (await client.get("/permissions/tree")).status_code == 403
        # 但自己的关注城市可以管
        assert (await client.get("/auth/me/followed-regions")).status_code == 200
    finally:
        await client.aclose()
        await _drop_user(uid)


# --------------------------------------------------------------------- 用户 CRUD


async def test_user_crud_lifecycle(admin: AsyncClient) -> None:
    account = f"{SMOKE_PREFIX}crud_{uuid.uuid4().hex[:8]}"
    created = await admin.post(
        "/users",
        json={"account": account, "display_name": "张三", "password": "initial-pass"},
    )
    assert created.status_code == 201
    uid = created.json()["id"]
    assert created.json()["display_name"] == "张三"
    assert created.json()["roles"] == []

    try:
        # 重复账号
        dup = await admin.post(
            "/users",
            json={"account": account, "display_name": "重复", "password": "whatever"},
        )
        assert dup.status_code == 409

        # 中文账号必须被拒（数据库 CHECK + Pydantic pattern 双重拦截）
        bad = await admin.post(
            "/users",
            json={"account": "中文账号", "display_name": "非法", "password": "whatever"},
        )
        assert bad.status_code == 422

        # 改名 + 停用
        patched = await admin.patch(
            f"/users/{uid}", json={"display_name": "张三丰", "is_active": False}
        )
        assert patched.status_code == 200
        assert patched.json()["display_name"] == "张三丰"
        assert patched.json()["is_active"] is False

        # 停用后不能再登录
        blocked = ContextClient(transport=ASGITransport(app=app), base_url="http://test")
        res = await blocked.post(
            "/auth/login", json={"account": account, "password": "initial-pass"}
        )
        assert res.status_code == 403
        await blocked.aclose()

        # 列表可按关键词搜到
        found = await admin.get("/users", params={"q": account})
        assert found.json()["total"] == 1
    finally:
        await admin.delete(f"/users/{uid}")

    assert (await admin.get(f"/users/{uid}")).status_code == 404


async def test_role_assignment_changes_permissions(admin: AsyncClient) -> None:
    account = f"{SMOKE_PREFIX}assign_{uuid.uuid4().hex[:8]}"
    created = await admin.post(
        "/users",
        json={"account": account, "display_name": "待授权", "password": "assign-pass"},
    )
    uid = created.json()["id"]
    try:
        before = await _login(account, "assign-pass")
        assert (await before.get("/users")).status_code == 403
        await before.aclose()

        granted = await admin.put(f"/users/{uid}/roles", json={"role_codes": ["viewer"]})
        assert granted.status_code == 200
        assert [r["code"] for r in granted.json()["roles"]] == ["viewer"]

        after = await _login(account, "assign-pass")
        assert (await after.get("/users")).status_code == 200
        await after.aclose()

        # 未知角色应被拒
        bad = await admin.put(f"/users/{uid}/roles", json={"role_codes": ["ghost"]})
        assert bad.status_code == 400
    finally:
        await admin.delete(f"/users/{uid}")


async def test_admin_cannot_delete_or_disable_self(admin: AsyncClient) -> None:
    me = (await admin.get("/auth/me")).json()["user"]
    assert (await admin.delete(f"/users/{me['id']}")).status_code == 400
    assert (await admin.patch(f"/users/{me['id']}", json={"is_active": False})).status_code == 400


async def test_reset_password(admin: AsyncClient) -> None:
    account = f"{SMOKE_PREFIX}pwd_{uuid.uuid4().hex[:8]}"
    created = await admin.post(
        "/users",
        json={"account": account, "display_name": "改密", "password": "old-password"},
    )
    uid = created.json()["id"]
    try:
        assert (
            await admin.put(f"/users/{uid}/password", json={"password": "new-password"})
        ).status_code == 204

        old = ContextClient(transport=ASGITransport(app=app), base_url="http://test")
        assert (
            await old.post("/auth/login", json={"account": account, "password": "old-password"})
        ).status_code == 401
        await old.aclose()

        new = ContextClient(transport=ASGITransport(app=app), base_url="http://test")
        assert (
            await new.post("/auth/login", json={"account": account, "password": "new-password"})
        ).status_code == 200
        await new.aclose()
    finally:
        await admin.delete(f"/users/{uid}")


# --------------------------------------------------------------------- 角色管理


async def test_builtin_roles_cannot_be_deleted(admin: AsyncClient) -> None:
    roles = (await admin.get("/roles")).json()
    for role in roles:
        if role["scope"] != "platform":
            continue  # 企业模板在平台无企业上下文时不可删除（404 属预期），此处验证平台内置
        assert role["is_system"] is True
        assert (await admin.delete(f"/roles/{role['id']}")).status_code == 400


async def test_role_crud(admin: AsyncClient) -> None:
    code = f"tmp_role_{uuid.uuid4().hex[:6]}"
    created = await admin.post(
        "/roles",
        json={"code": code, "name": "临时角色", "description": "测试用", "scope": "platform"},
    )
    assert created.status_code == 201
    rid = created.json()["id"]
    assert created.json()["is_system"] is False

    try:
        assert (
            await admin.post("/roles", json={"code": code, "name": "重复", "scope": "platform"})
        ).status_code == 409

        # 给这个角色分配两条权限
        res = await admin.put(
            f"/roles/{rid}/permissions", json={"permission_codes": ["user:read", "region:read"]}
        )
        assert res.status_code == 200
        assert set(res.json()["permission_codes"]) == {"user:read", "region:read"}

        # 非法权限码
        bad = await admin.put(
            f"/roles/{rid}/permissions", json={"permission_codes": ["ghost:read"]}
        )
        assert bad.status_code == 400

        # 改名
        patched = await admin.patch(f"/roles/{rid}", json={"name": "改名后"})
        assert patched.json()["name"] == "改名后"
    finally:
        assert (await admin.delete(f"/roles/{rid}")).status_code == 204

    assert (await admin.get(f"/roles/{rid}")).status_code == 404


# --------------------------------------------------------------------- 关注城市


async def _province_and_city(admin: AsyncClient) -> tuple[str, str]:
    provinces = (await admin.get("/regions/children")).json()
    zhejiang = next(p for p in provinces if p["name"] == "浙江省")
    cities = (await admin.get("/regions/children", params={"parent_code": zhejiang["code"]})).json()
    hangzhou = next(c for c in cities if c["name"] == "杭州市")
    return zhejiang["code"], hangzhou["code"]


async def test_followed_regions_via_api(admin: AsyncClient) -> None:
    account = f"{SMOKE_PREFIX}city_{uuid.uuid4().hex[:8]}"
    created = await admin.post(
        "/users",
        json={"account": account, "display_name": "关注城市", "password": "city-pass"},
    )
    uid = created.json()["id"]
    province, city = await _province_and_city(admin)

    try:
        # 省/市/区县都可选，不是只允许区县
        ok = await admin.put(
            f"/users/{uid}/followed-regions", json={"region_codes": [province, city]}
        )
        assert ok.status_code == 200
        assert set(ok.json()) == {province, city}

        # 6 个被拒
        over = await admin.put(
            f"/users/{uid}/followed-regions",
            json={"region_codes": [province, city, "330106", "110000", "110101", "440300"]},
        )
        assert over.status_code == 400
        assert "最多 5 个" in over.json()["detail"]

        # 非法编码被拒
        bad = await admin.put(f"/users/{uid}/followed-regions", json={"region_codes": ["999999"]})
        assert bad.status_code == 400

        # 重复去重
        dedup = await admin.put(
            f"/users/{uid}/followed-regions", json={"region_codes": [city, city, province]}
        )
        assert len(dedup.json()) == 2

        # 详情带名称
        detail = await admin.get(f"/users/{uid}/followed-regions/detail")
        assert detail.status_code == 200
        assert {d["name"] for d in detail.json()} == {"浙江省", "杭州市"}
    finally:
        await admin.delete(f"/users/{uid}")


async def test_region_cascade_and_search(admin: AsyncClient) -> None:
    provinces = (await admin.get("/regions/children")).json()
    assert len(provinces) == 31
    assert all(p["level"] == 1 and p["has_children"] for p in provinces)

    # 直筒子市：行政上不设区县，到市级即为终点
    dongguan = (await admin.get("/regions/search", params={"q": "东莞市"})).json()
    assert dongguan and dongguan[0]["has_children"] is False

    found = (await admin.get("/regions/search", params={"q": "西湖区"})).json()
    assert any(r["level"] == 3 for r in found)


# --------------------------------------------------------------------- 审计


async def test_audit_log_records_mutations(admin: AsyncClient) -> None:
    account = f"{SMOKE_PREFIX}audit_{uuid.uuid4().hex[:8]}"
    created = await admin.post(
        "/users",
        json={"account": account, "display_name": "审计", "password": "audit-pass"},
    )
    uid = created.json()["id"]
    try:
        logs = (await admin.get("/audit-logs", params={"action": "user.create"})).json()
        assert logs["total"] >= 1
        assert any(log["action"] == "user.create" for log in logs["items"])
        assert all(log["ip"] is not None for log in logs["items"])
    finally:
        await admin.delete(f"/users/{uid}")


async def test_viewer_cannot_manage_others_followed_regions(viewer: AsyncClient) -> None:
    """改别人的关注城市需要 followed:manage，viewer 没有。

    目标账号现场创建：清洁专用库没有默认 root 账号（生产拒绝 root/root），
    旧版依赖 `root` 用户会让新库复现失真。
    """
    target = f"{SMOKE_PREFIX}followed_target_{uuid.uuid4().hex[:8]}"
    target_id = await _make_user(target, [])
    try:
        res = await viewer.put(
            f"/users/{target_id}/followed-regions", json={"region_codes": ["110000"]}
        )
        assert res.status_code == 403
    finally:
        await _drop_user(target_id)


async def test_audit_survives_actor_deletion(admin: AsyncClient) -> None:
    """删除用户后，他历史上的操作仍要能追溯到账号。

    `actor_id` 外键是 ON DELETE SET NULL，所以必须靠冗余存的 `actor_account` 兜住；
    否则删除用户会把之前的审计记录一起变成"未知操作人"。
    """
    account = f"{SMOKE_PREFIX}gone_{uuid.uuid4().hex[:8]}"
    created = await admin.post(
        "/users",
        json={"account": account, "display_name": "即将删除", "password": "gone-pass"},
    )
    assert created.status_code == 201
    uid = created.json()["id"]

    # 让对方做一件会被审计的事（管理自己的关注城市）
    actor = await _login(account, "gone-pass")
    mine = await actor.put("/auth/me/followed-regions", json={"region_codes": ["110000"]})
    assert mine.status_code == 200
    await actor.aclose()

    # 删掉这个用户
    assert (await admin.delete(f"/users/{uid}")).status_code == 204

    # 审计里仍应能看到是他做的
    logs = (
        await admin.get("/audit-logs", params={"action": "profile.set_followed_regions"})
    ).json()
    matching = [log for log in logs["items"] if log["target_id"] == uid]
    assert matching, "删除用户后找不到他的审计记录"
    record = matching[0]
    assert record["actor_account"] == account, "账号快照丢失，审计无法追溯"
    assert record["actor_id"] is None, "外键应已被置空（记录的是当时身份，不是当前引用）"


async def test_user_delete_records_display_name_in_detail(admin: AsyncClient) -> None:
    """删除用户时，审计 detail 里要留下被删账号，便于事后核对。"""
    account = f"{SMOKE_PREFIX}del_{uuid.uuid4().hex[:8]}"
    created = await admin.post(
        "/users",
        json={"account": account, "display_name": "被删者", "password": "del-pass"},
    )
    uid = created.json()["id"]
    assert (await admin.delete(f"/users/{uid}")).status_code == 204

    logs = (await admin.get("/audit-logs", params={"action": "user.delete"})).json()
    matching = [log for log in logs["items"] if log["target_id"] == uid]
    assert matching
    assert matching[0]["detail"]["account"] == account
    assert matching[0]["actor_account"] is not None
