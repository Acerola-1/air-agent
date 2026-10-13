"""企业身份域：同租户复合约束、RLS 边界与连接池隔离（任务 2.2/2.3/2.5）。

依赖专用隔离测试库 `air_agent_tenant_test`（勿指向业务库）：

    export TENANT_TEST_OWNER_URI=...      # owner（迁移角色）
    export TENANT_TEST_RUNTIME_URI=...    # air_agent_runtime（非 owner、非 BYPASSRLS）

    .venv/bin/python -m pytest tests/test_tenant_schema.py -q

owner 连接负责造基座数据；所有越权断言都发生在 runtime 连接上——RLS 与列
权限的真实效果只有非 owner 角色才能证明。GUC 一律 `set_config(..., true)`
事务本地：提交/回滚即失效，连接回池后不得把作用域带给下一个请求。
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _async_uri(uri: str) -> str:
    if uri.startswith("postgresql://"):
        return "postgresql+psycopg://" + uri[len("postgresql://") :]
    return uri


OWNER_URI = _async_uri(os.environ.get("TENANT_TEST_OWNER_URI", ""))
RUNTIME_URI = _async_uri(os.environ.get("TENANT_TEST_RUNTIME_URI", ""))

pytestmark = pytest.mark.skipif(
    not OWNER_URI or not RUNTIME_URI, reason="需要 TENANT_TEST_OWNER_URI / TENANT_TEST_RUNTIME_URI"
)


async def _guc(session, **values: str | None) -> None:
    """在当前事务内设置上下文（事务本地，随提交/回滚失效）。"""
    for key, value in values.items():
        await session.execute(
            text("SELECT set_config('air_agent." + key + "', :v, true)"), {"v": value or ""}
        )


@pytest.fixture
def owner_factory():
    engine = create_async_engine(OWNER_URI)
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def runtime_factory():
    engine = create_async_engine(RUNTIME_URI, pool_size=4, max_overflow=2)
    yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def seeded(owner_factory):
    """测试库专用：清空企业域后重建 A/B 基座数据。"""
    async with owner_factory() as session:
        await session.execute(
            text(
                "TRUNCATE membership_roles, membership_followed_regions, mcp_identity_bindings,"
                " tenant_memberships, departments, roles, tenants CASCADE"
            )
        )
        await session.execute(text("DELETE FROM users WHERE password_hash = 'x'"))
        await session.execute(text("DELETE FROM thread_ownership"))
        await session.commit()

    ids = {
        name: str(uuid.uuid4())
        for name in (
            "tenant_a",
            "tenant_b",
            "dept_a",
            "dept_b",
            "dept_a_child",
            "user_a",
            "user_b",
            "user_both",
            "member_a",
            "member_b",
            "member_both_a",
            "member_both_b",
            "role_admin_a",
            "role_platform",
        )
    }
    async with owner_factory() as session:
        for tid, code in ((ids["tenant_a"], "t_a_00"), (ids["tenant_b"], "t_b_00")):
            await session.execute(
                text("INSERT INTO tenants (id, code, name) VALUES (:id, :code, :name)"),
                {"id": tid, "code": code, "name": f"测试企业{code}"},
            )
        for key, acc in (("user_a", "ta"), ("user_b", "tb"), ("user_both", "tb2")):
            await session.execute(
                text(
                    "INSERT INTO users (id, account, display_name, password_hash)"
                    " VALUES (:id, :acc, :dn, 'x')"
                ),
                {"id": ids[key], "acc": f"{acc}_{uuid.uuid4().hex[:8]}", "dn": "测试用户"},
            )
        for did, tid, parent in (
            (ids["dept_a"], ids["tenant_a"], None),
            (ids["dept_b"], ids["tenant_b"], None),
            (ids["dept_a_child"], ids["tenant_a"], ids["dept_a"]),
        ):
            await session.execute(
                text(
                    "INSERT INTO departments (id, tenant_id, dept_code, name, parent_id)"
                    " VALUES (:id, :tid, :code, :name, :parent)"
                ),
                {
                    "id": did,
                    "tid": tid,
                    "code": f"d_{did[:8]}",
                    "name": "部门",
                    "parent": parent,
                },
            )
        for mid, tid, uid in (
            (ids["member_a"], ids["tenant_a"], ids["user_a"]),
            (ids["member_b"], ids["tenant_b"], ids["user_b"]),
            (ids["member_both_a"], ids["tenant_a"], ids["user_both"]),
            (ids["member_both_b"], ids["tenant_b"], ids["user_both"]),
        ):
            await session.execute(
                text(
                    "INSERT INTO tenant_memberships (id, tenant_id, user_id)"
                    " VALUES (:id, :tid, :uid)"
                ),
                {"id": mid, "tid": tid, "uid": uid},
            )
        await session.execute(
            text(
                "INSERT INTO roles (id, code, name, scope, tenant_id, is_system)"
                " VALUES (:id, 'tenant_admin', '企业管理员', 'tenant', :tid, true)"
            ),
            {"id": ids["role_admin_a"], "tid": ids["tenant_a"]},
        )
        await session.execute(
            text(
                "INSERT INTO roles (id, code, name, scope, tenant_id, is_system)"
                " VALUES (:id, 'platform_op', '平台角色', 'platform', NULL, true)"
            ),
            {"id": ids["role_platform"]},
        )
        await session.commit()
    return ids


async def test_runtime_role_is_not_bypassrls(runtime_factory):
    """运行角色必须非超级、非 BYPASSRLS——否则一切 RLS 断言无意义。"""
    async with runtime_factory() as session:
        row = (
            await session.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).fetchone()
    assert row is not None
    assert not row[0] and not row[1]


async def test_rls_missing_context_sees_nothing(runtime_factory, seeded):
    """无 GUC 的查询看不见任何企业行；遗漏 tenant 条件也越不了权。"""
    async with runtime_factory() as session:
        res = await session.execute(text("SELECT count(*) FROM tenant_memberships"))
        assert res.scalar() == 0
        res = await session.execute(text("SELECT count(*) FROM audit_logs"))
        assert res.scalar() == 0
        res = await session.execute(
            text("SELECT count(*) FROM tenant_memberships WHERE user_id = :uid"),
            {"uid": seeded["user_b"]},
        )
        assert res.scalar() == 0


async def test_self_membership_rows_visible_without_tenant(runtime_factory, seeded):
    """带 user GUC 但无 tenant GUC：仅本人成员行可见（企业切换列表路径）。"""
    async with runtime_factory() as session:
        await _guc(session, user_id=seeded["user_both"])
        res = await session.execute(text("SELECT count(*) FROM tenant_memberships"))
        assert res.scalar() == 2  # 本人在 A/B 的两条
        res = await session.execute(
            text("SELECT count(*) FROM tenant_memberships WHERE user_id = :uid"),
            {"uid": seeded["user_a"]},
        )
        assert res.scalar() == 0  # 他人成员行不可见
        res = await session.execute(text("SELECT count(*) FROM departments"))
        assert res.scalar() == 0  # 部门仍需 tenant GUC


async def test_rls_scoped_to_current_tenant(runtime_factory, seeded):
    """GUC=A 只见 A 的 2 条成员行与 2 个部门；B 的成员行不可见。"""
    async with runtime_factory() as session:
        await _guc(session, tenant_id=seeded["tenant_a"], user_id=seeded["user_a"])
        res = await session.execute(text("SELECT count(*) FROM tenant_memberships"))
        assert res.scalar() == 2
        res = await session.execute(
            text("SELECT count(*) FROM tenant_memberships WHERE tenant_id = :b"),
            {"b": seeded["tenant_b"]},
        )
        assert res.scalar() == 0
        res = await session.execute(text("SELECT count(*) FROM departments"))
        assert res.scalar() == 2


async def test_cross_tenant_association_rejected(runtime_factory, seeded):
    """同租户复合外键：A 成员挂 B 部门、授予平台角色都必须失败。"""
    async with runtime_factory() as session:
        await _guc(session, tenant_id=seeded["tenant_a"], user_id=seeded["user_a"])
        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE tenant_memberships SET department_id = :d WHERE id = :m"),
                {"d": seeded["dept_b"], "m": seeded["member_a"]},
            )
    async with runtime_factory() as session:
        await _guc(session, tenant_id=seeded["tenant_a"], user_id=seeded["user_a"])
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO membership_roles (membership_id, role_id, tenant_id)"
                    " VALUES (:m, :r, :t)"
                ),
                {"m": seeded["member_a"], "r": seeded["role_platform"], "t": seeded["tenant_a"]},
            )


async def test_duplicate_membership_rejected(runtime_factory, seeded):
    async with runtime_factory() as session:
        await _guc(session, platform="on")
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO tenant_memberships (id, tenant_id, user_id) VALUES (:id, :t, :u)"
                ),
                {"id": str(uuid.uuid4()), "t": seeded["tenant_a"], "u": seeded["user_a"]},
            )


async def test_department_cycle_rejected(runtime_factory, owner_factory, seeded):
    """部门成环（A→child→A）必须由延迟约束触发器在提交时发现。"""
    async with runtime_factory() as session:
        await _guc(session, platform="on")
        await session.execute(
            text("UPDATE departments SET parent_id = :child WHERE id = :parent"),
            {"child": seeded["dept_a_child"], "parent": seeded["dept_a"]},
        )
        # PL/pgSQL RAISE EXCEPTION → psycopg RaiseException → SQLAlchemy ProgrammingError；
        # 重点是提交被拒绝且数据未写入（下方断言）
        with pytest.raises(ProgrammingError):
            await session.commit()
    async with owner_factory() as verify:
        res = await verify.execute(
            text("SELECT parent_id FROM departments WHERE id = :p"), {"p": seeded["dept_a"]}
        )
        assert res.scalar() is None


async def test_self_parent_rejected(runtime_factory, seeded):
    async with runtime_factory() as session:
        await _guc(session, platform="on")
        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE departments SET parent_id = id WHERE id = :d"), {"d": seeded["dept_a"]}
            )


async def test_binding_uniqueness(runtime_factory, seeded):
    """企业默认绑定每 (tenant, server) 至多一条。"""
    async with runtime_factory() as session:
        await _guc(session, platform="on")
        await session.execute(
            text(
                "INSERT INTO mcp_identity_bindings"
                " (id, tenant_id, server_key, external_subject, credential_ref)"
                " VALUES (:id, :t, 'ipp', 'subj_a', 'env:X')"
            ),
            {"id": str(uuid.uuid4()), "t": seeded["tenant_a"]},
        )
        await session.commit()
    async with runtime_factory() as session:
        await _guc(session, platform="on")
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "INSERT INTO mcp_identity_bindings"
                    " (id, tenant_id, server_key, external_subject, credential_ref)"
                    " VALUES (:id, :t, 'ipp', 'subj_a2', 'env:Y')"
                ),
                {"id": str(uuid.uuid4()), "t": seeded["tenant_a"]},
            )


async def test_thread_ownership_rls_blocks_cross_read(runtime_factory, seeded):
    """线程归属：B 上下文写入成功；A 上下文按 thread_id 直查不可见。"""
    tid = "thread-cross-rls-" + uuid.uuid4().hex[:8]
    async with runtime_factory() as session:
        await _guc(session, tenant_id=seeded["tenant_b"], user_id=seeded["user_b"])
        await session.execute(
            text(
                "INSERT INTO thread_ownership (thread_id, tenant_id, user_id) VALUES (:tid, :t, :u)"
            ),
            {"tid": tid, "t": seeded["tenant_b"], "u": seeded["user_b"]},
        )
        await session.commit()
    async with runtime_factory() as session:
        await _guc(session, tenant_id=seeded["tenant_a"], user_id=seeded["user_a"])
        res = await session.execute(
            text("SELECT count(*) FROM thread_ownership WHERE thread_id = :tid"), {"tid": tid}
        )
        assert res.scalar() == 0


async def test_tenant_context_cannot_insert_global_user(runtime_factory, seeded):
    """租户上下文不能创建全局账号：运行角色无 users INSERT 权限 + RLS WITH CHECK 双保险。"""
    async with runtime_factory() as session:
        await _guc(session, tenant_id=seeded["tenant_a"], user_id=seeded["user_a"])
        with pytest.raises(ProgrammingError):
            await session.execute(
                text(
                    "INSERT INTO users (id, account, display_name, password_hash)"
                    " VALUES (:id, :acc, 'x', 'x')"
                ),
                {"id": str(uuid.uuid4()), "acc": f"evil_{uuid.uuid4().hex[:6]}"},
            )


async def test_tenant_context_cannot_enumerate_global_users(runtime_factory, owner_factory, seeded):
    """2.4：租户上下文只见本企业成员；平台上下文才能看全局清单。"""
    stranger = str(uuid.uuid4())
    async with owner_factory() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, account, display_name, password_hash)"
                " VALUES (:id, :acc, :dn, 'x')"
            ),
            {"id": stranger, "acc": f"str_{uuid.uuid4().hex[:8]}", "dn": "无关用户"},
        )
        await session.commit()
    async with runtime_factory() as session:
        await _guc(session, tenant_id=seeded["tenant_a"], user_id=seeded["user_a"])
        res = await session.execute(
            text("SELECT count(*) FROM users WHERE id = :s"), {"s": stranger}
        )
        assert res.scalar() == 0
        res = await session.execute(text("SELECT count(*) FROM users"))
        assert res.scalar() == 2  # user_a + user_both（A 的有效成员）
    async with runtime_factory() as session:
        await _guc(session, platform="on")
        res = await session.execute(
            text("SELECT count(*) FROM users WHERE id = :s"), {"s": stranger}
        )
        assert res.scalar() == 1


async def test_permission_definitions_read_only_for_tenants(runtime_factory, owner_factory, seeded):
    """2.4：权限定义是全局目录（可读不可改）；“企业角色只能取 grantable 子集”
    由角色授权接口强制（test_tenant_api 覆盖），数据库强制定义写仅平台。"""
    perm = str(uuid.uuid4())
    async with owner_factory() as session:
        await session.execute(
            text(
                "INSERT INTO permissions (id, code, name, type, is_active, grantable_to_tenants)"
                " VALUES (:id, :code, '平台管理', 2, true, false)"
            ),
            {"id": perm, "code": f"platform:admin_{perm[:8]}"},
        )
        await session.commit()
    # 租户上下文可读目录
    async with runtime_factory() as session:
        await _guc(session, tenant_id=seeded["tenant_a"], user_id=seeded["user_a"])
        res = await session.execute(
            text("SELECT count(*) FROM permissions WHERE id = :p"), {"p": perm}
        )
        assert res.scalar() == 1
        # 但不可修改定义（RLS 写仅平台）
        with pytest.raises(ProgrammingError):
            await session.execute(
                text("UPDATE permissions SET grantable_to_tenants = true WHERE id = :p"),
                {"p": perm},
            )
    async with owner_factory() as session:
        await session.execute(text("DELETE FROM permissions WHERE id = :p"), {"p": perm})
        await session.commit()


async def test_public_dictionary_readable_without_tenant_context(
    runtime_factory, owner_factory, seeded
):
    """2.4：公开行政区字典无租户边界，可读即可（数据无关断言）。"""
    probe = f"T{uuid.uuid4().hex[:8].upper()}"
    async with owner_factory() as session:
        await session.execute(
            text(
                "INSERT INTO regions(code,name,level,parent_code,sort) VALUES (:c,:n,1,NULL,9999)"
            ),
            {"c": probe, "n": "字典探针"},
        )
        await session.commit()
    try:
        async with runtime_factory() as session:
            res = await session.execute(
                text("SELECT count(*) FROM regions WHERE code = :c"), {"c": probe}
            )
            assert res.scalar() == 1  # 无租户 GUC 也可读，无 RLS 报错
    finally:
        async with owner_factory() as session:
            await session.execute(text("DELETE FROM regions WHERE code = :c"), {"c": probe})
            await session.commit()


async def test_connection_pool_reuse_clears_scope(runtime_factory, seeded):
    """异常回滚后连接回池；新事务不继承 A 作用域。"""

    async def request_a(should_fail: bool):
        async with runtime_factory() as session:
            await _guc(session, tenant_id=seeded["tenant_a"], user_id=seeded["user_a"])
            res = await session.execute(text("SELECT count(*) FROM tenant_memberships"))
            visible = res.scalar()
            if should_fail:
                await session.execute(text("SELECT 1/0"))
            else:
                await session.commit()
            return visible

    assert await request_a(False) >= 1
    with pytest.raises(DataError):
        await request_a(True)

    async with runtime_factory() as session:
        res = await session.execute(text("SELECT count(*) FROM tenant_memberships"))
        assert res.scalar() == 0
        res = await session.execute(text("SELECT current_setting('air_agent.tenant_id', true)"))
        assert res.scalar() in ("", None)


async def test_concurrent_ab_transactions(runtime_factory, seeded):
    """A/B 并发交错使用不同 session：各自只见自己企业的成员行。"""
    results: dict[str, int] = {}

    async def worker(label: str, tenant: str, user: str):
        async with runtime_factory() as session:
            await _guc(session, tenant_id=tenant, user_id=user)
            await asyncio.sleep(0.05)  # 制造交错窗口
            res = await session.execute(text("SELECT count(*) FROM tenant_memberships"))
            results[label] = res.scalar()
            await session.commit()

    await asyncio.gather(
        worker("a", seeded["tenant_a"], seeded["user_a"]),
        worker("b", seeded["tenant_b"], seeded["user_b"]),
        worker("both_a", seeded["tenant_a"], seeded["user_both"]),
        worker("both_b", seeded["tenant_b"], seeded["user_both"]),
    )
    # 本人成员行按 user_id 分支可见（企业列表需要），企业行按 tenant 分支可见
    assert results["a"] == 2
    assert results["b"] == 2
    assert results["both_a"] == 3
    assert results["both_b"] == 3
