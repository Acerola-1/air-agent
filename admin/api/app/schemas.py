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
    created_at: dt.datetime | None
    roles: list[RoleBrief]
    followed_region_codes: list[str]


class MeOut(UserOut):
    """当前登录用户：额外带权限码，供前端渲染菜单与按钮。"""

    permissions: list[str]


class LoginIn(BaseModel):
    account: str = Field(min_length=3, max_length=32, description="登录账号（ASCII）")
    password: str = Field(min_length=1, max_length=128)


# --------------------------------------------------------------------------- 企业会话
#
# /auth/me 与登录响应使用**固定类型**（任务 3.2）：user / session / current_tenant /
# membership / permissions / context_version。字段缺失用显式 null，不发散键集合。


class SessionOut(BaseModel):
    session_id: uuid.UUID
    active_tenant_id: uuid.UUID | None
    ctx_version: int
    created_at: dt.datetime
    expires_at: dt.datetime


class TenantBrief(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    status: str


class MembershipBrief(BaseModel):
    id: uuid.UUID | None
    tenant_id: uuid.UUID
    department_id: uuid.UUID | None
    status: str
    revision: int | None
    role_codes: list[str] = Field(default_factory=list)


class TenantListItem(BaseModel):
    tenant: TenantBrief
    membership: MembershipBrief


class SessionContextOut(BaseModel):
    """me/登录/切换共用的固定响应外壳。"""

    user: UserOut
    session: SessionOut
    current_tenant: TenantBrief | None
    membership: MembershipBrief | None
    permissions: list[str]
    platform_admin: bool
    context_version: int
    tenants: list[TenantListItem] = Field(
        default_factory=list, description="本人活跃企业列表（无其他企业信息）"
    )
    needs_tenant_selection: bool = False


class SwitchTenantIn(BaseModel):
    tenant_id: uuid.UUID
    expected_ctx_version: int = Field(description="客户端持有的上下文版本；不一致返回 409")


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class RuntimeTicketIn(BaseModel):
    # 非授权性的目标企业选择：必须与会话上下文一致，不匹配 409
    target_tenant_id: uuid.UUID | None = None
    # F05/R02：签发同样携带页面感知的上下文版本——me→签发之间被其他标签页
    # 切换（含 A→B→A）时版本必然前进，旧声明换不到新票据。
    expected_ctx_version: int | None = None


class RuntimeTicketOut(BaseModel):
    ticket: str
    expires_at: dt.datetime
    audience: str


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
    # 作用域与所属企业（任务 4.1：平台/企业角色分离）
    scope: str = "platform"
    tenant_id: uuid.UUID | None = None


class RoleDetail(RoleOut):
    permission_codes: list[str]


class RoleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    sort: int = 0
    # platform 仅平台管理员可用；tenant 默认落到当前企业
    scope: str = Field(default="tenant", pattern=r"^(platform|tenant)$")
    tenant_id: uuid.UUID | None = None


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
    actor_membership_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    platform_scope: bool = False
    action: str
    target_type: str | None
    target_id: str | None
    request_id: str | None = None
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


# --------------------------------------------------------------------------- 企业与成员


class TenantCreate(BaseModel):
    code: str = Field(min_length=2, max_length=63, pattern=r"^[a-z0-9][a-z0-9_-]{1,62}$")
    name: str = Field(min_length=1, max_length=64, description="企业中文名")


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    status: str | None = Field(default=None, pattern=r"^(active|suspended)$")


class TenantOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    status: str
    auth_revision: int
    created_at: dt.datetime


class MemberCreate(BaseModel):
    """平台授权关联：按已存在的全局账号 UUID 或账号名（精确匹配）。"""

    user_id: uuid.UUID | None = None
    account: str | None = Field(default=None, min_length=3, max_length=32)
    role_codes: list[str] = Field(default_factory=list)
    department_id: uuid.UUID | None = None


class MemberUpdate(BaseModel):
    department_id: uuid.UUID | None = None
    status: str | None = Field(default=None, pattern=r"^(active|disabled)$")


class MemberRolesIn(BaseModel):
    role_codes: list[str]


class MemberOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    account: str
    display_name: str
    user_is_active: bool
    department_id: uuid.UUID | None
    status: str
    revision: int
    role_codes: list[str]
    created_at: dt.datetime


class DepartmentCreate(BaseModel):
    dept_code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    parent_id: uuid.UUID | None = None


class DepartmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    parent_id: uuid.UUID | None = None


class DepartmentOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    dept_code: str
    name: str
    parent_id: uuid.UUID | None
    created_at: dt.datetime


# --------------------------------------------------------------------------- MCP 绑定


class BindingUpsert(BaseModel):
    tenant_id: uuid.UUID
    membership_id: uuid.UUID | None = None
    server_key: str = Field(min_length=1, max_length=64)
    external_subject: str = Field(min_length=1, max_length=128)
    credential_ref: str = Field(min_length=1, max_length=128)
    protocol_version: str = Field(min_length=1, max_length=32)
    status: str = Field(default="active", pattern=r"^(active|disabled)$")


class BindingOut(BaseModel):
    """脱敏输出：credential_ref 永不返回，只返回是否已配置。"""

    id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID | None
    server_key: str
    external_subject: str
    protocol_version: str
    auth_revision: int
    status: str
    credential_configured: bool
