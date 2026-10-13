"""内置企业角色的实例化（任务 4.5）。

平台角色（super_admin/admin/...）全局一份；**企业管理员模板**（tenant_admin）
按企业实例化：code 同为 `tenant_admin`，作用域唯一性按 (tenant_id, code)。

授予范围严格限定在 `grantable_to_tenants=true` 的权限码子集：企业管理员拿不到
任何平台专属能力（user:*/tenant:*/member:link/mcp:binding:manage/permission:read
都不在其中）——“内置模板不构成授权旁路”。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Permission, Role, RolePermission

TENANT_ADMIN_ROLE_CODE = "tenant_admin"
TENANT_ADMIN_ROLE_NAME = "企业管理员"

# 企业管理员模板权限（按钮码；菜单父级自动补齐由调用方处理）
TENANT_ADMIN_GRANTS: list[str] = [
    "member:read",
    "member:manage",
    "member:remove",
    "department:read",
    "department:manage",
    "role:read",
    "role:create",
    "role:update",
    "role:assign_permission",
    "audit:read",
    "followed:read",
    "followed:manage",
    "mcp:binding:read",
    "region:read",
    # 运行台账：企业管理员获得运营明细与统计；**不含** agent:content:read
    # （原文读取需单独授予）与 agent:replay:manage（平台专属运维）。
    "agent:operations:read",
    "agent:metrics:read",
]


async def ensure_tenant_admin_role(session: AsyncSession, tenant_id: uuid.UUID) -> Role:
    """幂等创建/刷新企业的内置管理员角色。调用上下文必须是平台或迁移角色。"""
    role = await session.scalar(
        select(Role).where(Role.code == TENANT_ADMIN_ROLE_CODE, Role.tenant_id == tenant_id)
    )
    if role is None:
        role = Role(
            code=TENANT_ADMIN_ROLE_CODE,
            name=TENANT_ADMIN_ROLE_NAME,
            description="企业内置管理员：仅本企业成员/部门/角色/审计范围",
            is_system=True,
            sort=10,
            scope="tenant",
            tenant_id=tenant_id,
        )
        session.add(role)
        await session.flush()

    rows = (
        await session.execute(
            select(Permission.id, Permission.code, Permission.parent_id).where(
                Permission.code.in_(TENANT_ADMIN_GRANTS)
            )
        )
    ).all()
    if len(rows) != len(TENANT_ADMIN_GRANTS):
        found = {row[1] for row in rows}
        missing = sorted(set(TENANT_ADMIN_GRANTS) - found)
        raise RuntimeError(f"种子缺少企业权限码: {missing}")
    by_id = {row[0]: row for row in rows}
    parent_of = {row[1]: row[2] for row in rows}

    # 补齐父级菜单/目录（侧边栏渲染需要）——只补企业侧祖先链，
    # 平台专属菜单不在授予集合的祖先里，天然不会被带进来。
    selected: set[uuid.UUID] = set(by_id)
    for _pid, _code, parent_id in rows:
        current = parent_id
        while current is not None:
            perm = await session.get(Permission, current)
            if perm is None or not perm.grantable_to_tenants:
                break
            selected.add(perm.id)
            parent_of[perm.code] = perm.parent_id
            current = perm.parent_id

    for permission_id in selected:
        exists = await session.scalar(
            select(RolePermission).where(
                RolePermission.role_id == role.id,
                RolePermission.permission_id == permission_id,
            )
        )
        if exists is None:
            session.add(RolePermission(role_id=role.id, permission_id=permission_id))
    await session.flush()
    return role
