"""权限读取接口。

`/permissions/tree` 返回**完整**权限树，供角色授权界面勾选，需要 `permission:read`。
当前用户自己可见的菜单走 `GET /auth/menus`（无需额外权限）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import SessionDep, require_permission
from app.models import Permission
from app.schemas import PermissionNode, PermissionOut
from app.services.identity import Identity
from app.services.permission_tree import build_tree

router = APIRouter(tags=["permissions"])

ReadPermission = Annotated[Identity, Depends(require_permission("permission:read"))]
GrantPermission = Annotated[Identity, Depends(require_permission("role:assign_permission"))]


async def load_permissions(session: AsyncSession) -> list[Permission]:
    """按 (sort, code) 取全部权限。导出给 auth 路由复用。"""
    return list(
        (await session.scalars(select(Permission).order_by(Permission.sort, Permission.code))).all()
    )


@router.get("", response_model=list[PermissionOut], summary="权限列表（扁平）")
async def list_permissions(session: SessionDep, _user: ReadPermission) -> list[PermissionOut]:
    return [PermissionOut.of(p) for p in await load_permissions(session)]


@router.get("/tree", response_model=list[PermissionNode], summary="权限树（完整）")
async def permission_tree(session: SessionDep, _user: ReadPermission) -> list[PermissionNode]:
    return build_tree(await load_permissions(session))


@router.get(
    "/grantable-tree",
    response_model=list[PermissionNode],
    summary="权限树（当前操作者可授予范围）",
)
async def permission_grantable_tree(
    session: SessionDep, actor: GrantPermission
) -> list[PermissionNode]:
    """R11：企业管理员配置企业角色所需的最小目录。

    只返回 `grantable_to_tenants` 的权限定义；非平台管理员再收窄为其本人实际
    持有的权限码（不能授予自己没有的权限——与角色写接口服务端校验一致）。
    """
    permissions = await load_permissions(session)
    allowed = [
        p
        for p in permissions
        if p.grantable_to_tenants and (actor.platform_admin or p.code in actor.permissions)
    ]
    return build_tree(allowed)
