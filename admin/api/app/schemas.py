"""Pydantic 请求/响应模型。

响应统一用显式构造函数（`user_out` 等）而不是 `from_attributes`：关注城市、权限码、
统计数这些字段来自不同来源（关联表、聚合查询），显式拼装比塞一堆 validator 更好懂。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.models import Permission, Region, Role, User


class Page[T](BaseModel):
    """统一分页响应。"""

    items: list[T]
    total: int
    page: int
    size: int


# --------------------------------------------------------------------------- 认证


class RoleBrief(BaseModel):
    id: uuid.UUID
    code: str
    name: str

    @classmethod
    def of(cls, role: Role) -> RoleBrief:
        return cls(id=role.id, code=role.code, name=role.name)


class UserOut(BaseModel):
    id: uuid.UUID
    account: str
    display_name: str
    is_active: bool
    last_login_at: dt.datetime | None
    created_at: dt.datetime
    roles: list[RoleBrief]
    followed_region_codes: list[str]


class MeOut(UserOut):
    """当前登录用户：额外带权限码，供前端渲染菜单与按钮。"""

    permissions: list[str]


class LoginIn(BaseModel):
    account: str = Field(min_length=3, max_length=32, description="登录账号（ASCII）")
    password: str = Field(min_length=1, max_length=128)


# --------------------------------------------------------------------------- 用户


class UserCreate(BaseModel):
    account: str = Field(
        min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$", description="登录账号（ASCII）"
    )
    display_name: str = Field(min_length=1, max_length=64, description="用户名/昵称（支持中文）")
    password: str = Field(min_length=6, max_length=128)
    is_active: bool = True


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    is_active: bool | None = None


class UserPasswordIn(BaseModel):
    password: str = Field(min_length=6, max_length=128)


class UserRolesIn(BaseModel):
    role_codes: list[str]


class FollowedRegionsIn(BaseModel):
    region_codes: list[str] = Field(
        default_factory=list, description="行政区划编码；最多 5 个，省/市/区县均可"
    )


# --------------------------------------------------------------------------- 角色


class RoleOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_system: bool
    sort: int
    created_at: dt.datetime
    permission_count: int = 0
    user_count: int = 0


class RoleDetail(RoleOut):
    permission_codes: list[str]


class RoleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    sort: int = 0


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    sort: int | None = None


class RolePermissionsIn(BaseModel):
    permission_codes: list[str]


# --------------------------------------------------------------------------- 权限


class PermissionOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    type: int
    path: str | None
    icon: str | None
    sort: int
    is_active: bool

    @classmethod
    def of(cls, permission: Permission) -> PermissionOut:
        return cls(
            id=permission.id,
            code=permission.code,
            name=permission.name,
            type=permission.type,
            path=permission.path,
            icon=permission.icon,
            sort=permission.sort,
            is_active=permission.is_active,
        )


class PermissionNode(PermissionOut):
    children: list[PermissionNode] = Field(default_factory=list)


# --------------------------------------------------------------------------- 行政区划


class RegionOut(BaseModel):
    code: str
    name: str
    level: int
    parent_code: str | None
    has_children: bool = False

    @classmethod
    def of(cls, region: Region, *, has_children: bool = False) -> RegionOut:
        return cls(
            code=region.code,
            name=region.name,
            level=region.level,
            parent_code=region.parent_code,
            has_children=has_children,
        )


# --------------------------------------------------------------------------- 审计


class AuditLogOut(BaseModel):
    id: int
    actor_id: uuid.UUID | None
    actor_account: str | None
    action: str
    target_type: str | None
    target_id: str | None
    detail: dict[str, Any]
    ip: str | None
    created_at: dt.datetime


# --------------------------------------------------------------------------- 构造工具


def user_out(
    user: User,
    *,
    roles: list[Role],
    followed_region_codes: list[str],
) -> UserOut:
    return UserOut(
        id=user.id,
        account=user.account,
        display_name=user.display_name,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        roles=[RoleBrief.of(r) for r in roles],
        followed_region_codes=followed_region_codes,
    )
