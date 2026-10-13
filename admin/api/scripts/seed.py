"""初始化 RBAC 与企业权限数据：权限树、平台角色、内置企业管理员角色、平台操作账号。幂等。

    .venv/bin/python scripts/seed.py   # 迁移/引导角色连接（owner）

安全约束（任务 4.5）：
- 生产（未显式 ALLOW_DEFAULT_ROOT_BOOTSTRAP=true）拒绝默认 root/root；平台操作
  账号必须由 ADMIN_BOOTSTRAP_ACCOUNT / ADMIN_BOOTSTRAP_PASSWORD 显式授权；
- 平台管理员**不自动成为任何企业的管理员**，也不自动为所有企业授予管理员；
- 种子走 owner 连接：授权定义初始化是平台引导职责，不经过租户 RLS。

幂等策略：全部按唯一 `code` upsert，重复执行只会把定义刷成当前版本，不产生
重复行，也不重置已改过的密码。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import owner_engine  # noqa: E402
from app.models import Permission, Role, RolePermission, Tenant, User, UserRole  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services.tenant_seed import ensure_tenant_admin_role  # noqa: E402

DIRECTORY, MENU, BUTTON = 1, 2, 3

# (code, name, type, parent_code, path, icon, sort, grantable_to_tenants)
# platform-only 的码 grantable=False：企业角色无法授予，杜绝提权旁路。
PERMISSION_TREE: list[tuple[str, str, int, str | None, str | None, str | None, int, bool]] = [
    ("system", "系统管理", DIRECTORY, None, None, "settings", 10, True),
    ("system:user", "平台账号", MENU, "system", "/system/users", "users", 10, False),
    ("user:read", "查看账号", BUTTON, "system:user", None, None, 10, False),
    ("user:create", "新建账号", BUTTON, "system:user", None, None, 20, False),
    ("user:update", "编辑账号", BUTTON, "system:user", None, None, 30, False),
    ("user:delete", "删除账号", BUTTON, "system:user", None, None, 40, False),
    ("user:assign_role", "分配平台角色", BUTTON, "system:user", None, None, 50, False),
    ("user:reset_password", "重置密码", BUTTON, "system:user", None, None, 60, False),
    ("member:link", "授权关联成员", BUTTON, "system:user", None, None, 70, False),
    ("system:tenant", "企业列表", MENU, "system", "/platform/tenants", "building", 15, False),
    ("tenant:read", "查看企业", BUTTON, "system:tenant", None, None, 10, False),
    ("tenant:create", "创建企业", BUTTON, "system:tenant", None, None, 20, False),
    ("tenant:update", "停用/修改企业", BUTTON, "system:tenant", None, None, 30, False),
    ("system:role", "角色管理", MENU, "system", "/system/roles", "shield", 20, True),
    ("role:read", "查看角色", BUTTON, "system:role", None, None, 10, True),
    ("role:create", "新建角色", BUTTON, "system:role", None, None, 20, True),
    ("role:update", "编辑角色", BUTTON, "system:role", None, None, 30, True),
    ("role:delete", "删除角色", BUTTON, "system:role", None, None, 40, True),
    ("role:assign_permission", "分配权限", BUTTON, "system:role", None, None, 50, True),
    ("system:permission", "权限定义", MENU, "system", "/system/permissions", "key", 30, False),
    ("permission:read", "查看权限定义", BUTTON, "system:permission", None, None, 10, False),
    ("system:audit", "审计日志", MENU, "system", "/system/audit", "history", 40, True),
    ("audit:read", "查看审计", BUTTON, "system:audit", None, None, 10, True),
    ("enterprise", "企业管理", DIRECTORY, None, None, "building", 15, True),
    ("enterprise:member", "成员管理", MENU, "enterprise", "/enterprise/members", "users", 10, True),
    ("member:read", "查看成员", BUTTON, "enterprise:member", None, None, 10, True),
    ("member:manage", "成员管理", BUTTON, "enterprise:member", None, None, 20, True),
    ("member:remove", "移出成员", BUTTON, "enterprise:member", None, None, 30, True),
    (
        "enterprise:department",
        "部门管理",
        MENU,
        "enterprise",
        "/enterprise/departments",
        "sitemap",
        20,
        True,
    ),
    ("department:read", "查看部门", BUTTON, "enterprise:department", None, None, 10, True),
    ("department:manage", "管理部门", BUTTON, "enterprise:department", None, None, 20, True),
    ("enterprise:role", "企业角色", MENU, "enterprise", "/enterprise/roles", "shield", 30, True),
    ("enterprise:mcp", "MCP 授权", MENU, "enterprise", "/enterprise/mcp", "api", 40, True),
    ("mcp:binding:read", "查看授权状态", BUTTON, "enterprise:mcp", None, None, 10, True),
    ("mcp:binding:manage", "管理授权绑定", BUTTON, "enterprise:mcp", None, None, 20, False),
    ("business", "业务管理", DIRECTORY, None, None, "database", 20, True),
    ("business:region", "行政区划", MENU, "business", "/business/regions", "map", 10, True),
    ("region:read", "查看区划", BUTTON, "business:region", None, None, 10, True),
    ("region:manage", "维护区划", BUTTON, "business:region", None, None, 20, False),
    ("business:followed", "用户关注城市", MENU, "business", "/business/followed", "star", 20, True),
    ("followed:read", "查看关注", BUTTON, "business:followed", None, None, 10, True),
    ("followed:manage", "维护关注", BUTTON, "business:followed", None, None, 20, True),
    # 运行台账（change add-agent-event-ledger）：运营元数据与原文**分开**授权；
    # 普通运营账号不自动获得原文读取。重放/清理为平台专属运维能力。
    ("business:runs", "运行台账", MENU, "business", "/business/runs", "activity", 30, True),
    ("agent:operations:read", "查看企业运行明细", BUTTON, "business:runs", None, None, 10, True),
    ("agent:metrics:read", "查看运行统计", BUTTON, "business:runs", None, None, 20, True),
    ("agent:content:read", "读取问题原文", BUTTON, "business:runs", None, None, 30, True),
    ("agent:replay:manage", "重放与清理运维", BUTTON, "business:runs", None, None, 40, False),
    # 删除**他人**线程的独立管理权限：默认平台专属（企业管理员不含），
    # 即默认只有线程本人可以申请删除自己的会话（R05）。
    ("agent:thread:delete", "删除任意会话", BUTTON, "business:runs", None, None, 50, False),
]

# 平台内置角色（scope=platform）。企业管理员模板 tenant_admin 按企业实例化，
# 不在此列（见 app/services/tenant_seed.py），也不自动授予任何账号。
ROLES: list[tuple[str, str, str, int]] = [
    ("super_admin", "超级管理员", "拥有全部权限（平台域），不可删除", 10),
]

ROLE_GRANTS: dict[str, list[str]] = {
    # 兼容保留：viewer 等平台角色如需重建，在这里显式声明；租户授权一律走成员角色
}


def _expand_with_ancestors(codes: list[str]) -> set[str]:
    """把按钮权限码补上其父级菜单/目录 —— 否则后台侧边栏无法渲染出这些页面。"""
    parent_of = {code: parent for code, _n, _t, parent, *_rest in PERMISSION_TREE}
    result = set(codes)
    for code in codes:
        current = parent_of.get(code)
        while current:
            result.add(current)
            current = parent_of.get(current)
    return result


async def seed_permissions(session) -> dict[str, Permission]:  # type: ignore[no-untyped-def]
    by_code: dict[str, Permission] = {}
    for code, name, type_, parent_code, path, icon, sort, grantable in PERMISSION_TREE:
        existing = await session.scalar(select(Permission).where(Permission.code == code))
        parent_id = by_code[parent_code].id if parent_code else None
        if existing is None:
            existing = Permission(
                code=code,
                name=name,
                type=type_,
                parent_id=parent_id,
                path=path,
                icon=icon,
                sort=sort,
                grantable_to_tenants=grantable,
            )
            session.add(existing)
        else:
            existing.name = name
            existing.type = type_
            existing.parent_id = parent_id
            existing.path = path
            existing.icon = icon
            existing.sort = sort
            existing.grantable_to_tenants = grantable
            existing.is_active = True
        await session.flush()
        by_code[code] = existing
    return by_code


async def seed_platform_roles(session, permissions: dict[str, Permission]) -> dict[str, Role]:
    """平台内置角色。super_admin 显式授予全部权限码（含平台专属码）。"""
    by_code: dict[str, Role] = {}
    all_codes = set(permissions)

    for code, name, description, sort in ROLES:
        role = await session.scalar(
            select(Role).where(
                Role.code == code, Role.scope == "platform", Role.tenant_id.is_(None)
            )
        )
        if role is None:
            role = Role(
                code=code,
                name=name,
                description=description,
                is_system=True,
                sort=sort,
                scope="platform",
            )
            session.add(role)
        else:
            role.name = name
            role.description = description
            role.is_system = True
            role.sort = sort
        await session.flush()
        by_code[code] = role

    for code, role in by_code.items():
        granted = (
            all_codes
            if code == "super_admin"
            else _expand_with_ancestors(ROLE_GRANTS.get(code, []))
        )
        unknown = granted - all_codes
        if unknown:
            raise SystemExit(f"角色 {code} 引用了不存在的权限码: {sorted(unknown)}")

        for row in (
            await session.scalars(select(RolePermission).where(RolePermission.role_id == role.id))
        ).all():
            await session.delete(row)
        await session.flush()
        for permission_code in sorted(granted):
            session.add(
                RolePermission(role_id=role.id, permission_id=permissions[permission_code].id)
            )
    await session.flush()
    return by_code


async def seed_tenant_admin_templates(session) -> None:  # type: ignore[no-untyped-def]
    """为已存在企业补齐内置 tenant_admin 角色（不自动授予任何账号）。"""
    for tenant_id in (await session.scalars(select(Tenant.id))).all():
        await ensure_tenant_admin_role(session, tenant_id)


async def seed_platform_operator(session) -> None:  # type: ignore[no-untyped-def]
    """显式授权平台身份。

    - 生产拒绝默认 root/root：需要 ALLOW_DEFAULT_ROOT_BOOTSTRAP=true 明确声明本地
      场景，或提供 ADMIN_BOOTSTRAP_ACCOUNT/ADMIN_BOOTSTRAP_PASSWORD；
    - 已存在的平台账号不重置密码，只保证有效并持有 super_admin（幂等）。
    """
    settings = get_settings()
    account = os.environ.get("ADMIN_BOOTSTRAP_ACCOUNT") or settings.root_account
    password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD") or settings.root_password
    using_default_pair = (
        account == settings.root_account and settings.root_password == "root" and password == "root"
    )
    if using_default_pair and not settings.allow_default_root_bootstrap:
        raise SystemExit(
            "拒绝默认 root/root 平台账号：生产请设置 ADMIN_BOOTSTRAP_ACCOUNT / "
            "ADMIN_BOOTSTRAP_PASSWORD（本地确需默认口令时显式 ALLOW_DEFAULT_ROOT_BOOTSTRAP=true）"
        )

    user = await session.scalar(select(User).where(User.account == account))
    if user is None:
        user = User(
            account=account,
            display_name=settings.root_display_name,
            password_hash=hash_password(password),
            is_active=True,
        )
        session.add(user)
        await session.flush()
        print(f"  已创建平台账号: {account}")
    else:
        user.is_active = True
        print(f"  平台账号已存在，保留其现有密码: {account}")

    super_admin = await session.scalar(
        select(Role).where(Role.code == "super_admin", Role.scope == "platform")
    )
    if super_admin is None:
        raise SystemExit("super_admin 平台角色缺失：先执行 seed_platform_roles")
    already = await session.scalar(
        select(UserRole.role_id).where(
            UserRole.user_id == user.id, UserRole.role_id == super_admin.id
        )
    )
    if already is None:
        session.add(UserRole(user_id=user.id, role_id=super_admin.id))
        await session.flush()


async def seed() -> None:
    from app.db import OwnerSessionLocal

    async with OwnerSessionLocal() as session:
        permissions = await seed_permissions(session)
        print(f"  权限: {len(permissions)} 条")
        roles = await seed_platform_roles(session, permissions)
        print(f"  平台角色: {len(roles)} 个")
        await seed_tenant_admin_templates(session)
        await seed_platform_operator(session)
        await session.commit()
    # owner_engine 生命周期由脚本持有者负责（uvicorn lifespan 管理 runtime_engine）
    await owner_engine.dispose()
    print("种子数据完成")


if __name__ == "__main__":
    asyncio.run(seed())
