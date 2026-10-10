"""初始化 RBAC 数据：权限树、内置角色、root 账号。幂等，可反复执行。

    python scripts/seed.py

幂等策略：全部按唯一 `code` / `account` upsert，重复执行只会把描述、
排序、角色授权关系刷成当前定义，不会产生重复行，也不会重置已改过的密码。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Permission, Role, RolePermission, User, UserRole  # noqa: E402
from app.security import hash_password  # noqa: E402

DIRECTORY, MENU, BUTTON = 1, 2, 3

# (code, name, type, parent_code, path, icon, sort)
PERMISSION_TREE: list[tuple[str, str, int, str | None, str | None, str | None, int]] = [
    ("system", "系统管理", DIRECTORY, None, None, "settings", 10),
    ("system:user", "用户管理", MENU, "system", "/system/users", "users", 10),
    ("user:read", "查看用户", BUTTON, "system:user", None, None, 10),
    ("user:create", "新建用户", BUTTON, "system:user", None, None, 20),
    ("user:update", "编辑用户", BUTTON, "system:user", None, None, 30),
    ("user:delete", "删除用户", BUTTON, "system:user", None, None, 40),
    ("user:assign_role", "分配角色", BUTTON, "system:user", None, None, 50),
    ("user:reset_password", "重置密码", BUTTON, "system:user", None, None, 60),
    ("system:role", "角色管理", MENU, "system", "/system/roles", "shield", 20),
    ("role:read", "查看角色", BUTTON, "system:role", None, None, 10),
    ("role:create", "新建角色", BUTTON, "system:role", None, None, 20),
    ("role:update", "编辑角色", BUTTON, "system:role", None, None, 30),
    ("role:delete", "删除角色", BUTTON, "system:role", None, None, 40),
    ("role:assign_permission", "分配权限", BUTTON, "system:role", None, None, 50),
    ("system:permission", "权限管理", MENU, "system", "/system/permissions", "key", 30),
    ("permission:read", "查看权限", BUTTON, "system:permission", None, None, 10),
    ("system:audit", "审计日志", MENU, "system", "/system/audit", "history", 40),
    ("audit:read", "查看审计", BUTTON, "system:audit", None, None, 10),
    ("business", "业务管理", DIRECTORY, None, None, "database", 20),
    ("business:region", "行政区划", MENU, "business", "/business/regions", "map", 10),
    ("region:read", "查看区划", BUTTON, "business:region", None, None, 10),
    ("region:manage", "维护区划", BUTTON, "business:region", None, None, 20),
    ("business:followed", "用户关注城市", MENU, "business", "/business/followed", "star", 20),
    ("followed:read", "查看关注", BUTTON, "business:followed", None, None, 10),
    ("followed:manage", "维护关注", BUTTON, "business:followed", None, None, 20),
]

# (code, name, description, sort)
ROLES: list[tuple[str, str, str, int]] = [
    ("super_admin", "超级管理员", "拥有全部权限，不可删除", 10),
    ("admin", "管理员", "拥有全部权限", 20),
    ("operator", "运营", "业务管理读写 + 用户查看", 30),
    ("viewer", "访客", "只读", 40),
]

# 角色 -> 按钮权限码；父级菜单会自动补齐
ROLE_GRANTS: dict[str, list[str]] = {
    "operator": [
        "region:read",
        "region:manage",
        "followed:read",
        "followed:manage",
        "user:read",
    ],
    "viewer": [
        "user:read",
        "role:read",
        "permission:read",
        "audit:read",
        "region:read",
        "followed:read",
    ],
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
    for code, name, type_, parent_code, path, icon, sort in PERMISSION_TREE:
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
            )
            session.add(existing)
        else:
            existing.name = name
            existing.type = type_
            existing.parent_id = parent_id
            existing.path = path
            existing.icon = icon
            existing.sort = sort
            existing.is_active = True
        await session.flush()
        by_code[code] = existing
    return by_code


async def seed_roles(session, permissions: dict[str, Permission]) -> dict[str, Role]:  # type: ignore[no-untyped-def]
    by_code: dict[str, Role] = {}
    all_codes = set(permissions)

    for code, name, description, sort in ROLES:
        role = await session.scalar(select(Role).where(Role.code == code))
        if role is None:
            role = Role(code=code, name=name, description=description, is_system=True, sort=sort)
            session.add(role)
        else:
            role.name = name
            role.description = description
            role.is_system = True
            role.sort = sort
        await session.flush()
        by_code[code] = role

    for code, role in by_code.items():
        granted = all_codes if code in {"super_admin", "admin"} else _expand_with_ancestors(
            ROLE_GRANTS.get(code, [])
        )
        unknown = granted - all_codes
        if unknown:
            raise SystemExit(f"角色 {code} 引用了不存在的权限码: {sorted(unknown)}")

        # 角色授权关系整体重建，保证与上面的定义一致
        for row in (
            await session.scalars(
                select(RolePermission).where(RolePermission.role_id == role.id)
            )
        ).all():
            await session.delete(row)
        await session.flush()
        for permission_code in sorted(granted):
            session.add(
                RolePermission(
                    role_id=role.id, permission_id=permissions[permission_code].id
                )
            )
    await session.flush()
    return by_code


async def seed_root(session, super_admin: Role) -> None:  # type: ignore[arg-type]
    settings = get_settings()
    account = settings.root_account

    user = await session.scalar(select(User).where(User.account == account))
    if user is None:
        user = User(
            account=account,
            display_name=settings.root_display_name,
            password_hash=hash_password(settings.root_password),
            is_active=True,
        )
        session.add(user)
        await session.flush()
        print(f"  已创建 root 账号: {account}")
    else:
        user.display_name = settings.root_display_name
        user.is_active = True
        print(f"  root 账号已存在，保留其现有密码: {account}")

    already = await session.scalar(
        select(UserRole.role_id).where(
            UserRole.user_id == user.id, UserRole.role_id == super_admin.id
        )
    )
    if already is None:
        session.add(UserRole(user_id=user.id, role_id=super_admin.id))
        await session.flush()


async def seed() -> None:
    settings = get_settings()
    if not settings.auth_secret:
        raise SystemExit("AUTH_SECRET 未设置：请确认仓库根目录 .env 存在（session token 依赖它）")

    async with SessionLocal() as session:
        permissions = await seed_permissions(session)
        print(f"  权限: {len(permissions)} 条")
        roles = await seed_roles(session, permissions)
        print(f"  角色: {len(roles)} 个")
        await seed_root(session, roles["super_admin"])
        await session.commit()
    print("种子数据完成")


if __name__ == "__main__":
    asyncio.run(seed())
