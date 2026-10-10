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
from app.models import Permission, User
from app.schemas import PermissionNode, PermissionOut
from app.services.permission_tree import build_tree

router = APIRouter(tags=["permissions"])

ReadPermission = Annotated[User, Depends(require_permission("permission:read"))]


async def load_permissions(session: AsyncSession) -> list[Permission]:
    """按 (sort, code) 取全部权限。导出给 auth 路由复用。"""
    return list(
        (
            await session.scalars(select(Permission).order_by(Permission.sort, Permission.code))
        ).all()
    )


@router.get("", response_model=list[PermissionOut], summary="权限列表（扁平）")
async def list_permissions(session: SessionDep, _user: ReadPermission) -> list[PermissionOut]:
    return [PermissionOut.of(p) for p in await load_permissions(session)]


@router.get("/tree", response_model=list[PermissionNode], summary="权限树（完整）")
async def permission_tree(session: SessionDep, _user: ReadPermission) -> list[PermissionNode]:
    return build_tree(await load_permissions(session))
