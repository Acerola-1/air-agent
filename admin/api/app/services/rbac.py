"""RBAC 权限解析。

权限集合从数据库现算（用户 → 角色 → 权限码），不信任 token 里的任何授权信息：
token 只承载身份（user_id + account），授权状态每次查库，这样后台改权限能立刻生效，
不需要用户重新登录。

`super_admin` 不做特殊旁路——种子数据已经把它显式授予了全部权限码，
统一走权限码校验可以让"谁能做什么"只有一个真相来源。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Permission, Role, RolePermission, UserRole


async def get_permission_codes(session: AsyncSession, user_id: uuid.UUID) -> set[str]:
    """该用户当前拥有的全部权限码（含菜单与按钮）。"""
    rows = await session.scalars(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id, Permission.is_active.is_(True))
        .distinct()
    )
    return set(rows.all())


async def get_roles(session: AsyncSession, user_id: uuid.UUID) -> list[Role]:
    rows = await session.scalars(
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
        .order_by(Role.sort, Role.code)
    )
    return list(rows.all())
