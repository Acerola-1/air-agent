"""SQLAlchemy 模型：RBAC + 行政区划 + 审计 + 企业身份域。

表结构对应 `migrations/versions/0001_init.py`、`0002_audit_actor_account.py` 与
`0003_tenant_identity.py`；两边必须保持一致（迁移是权威，模型用于查询）。
约束尽量下沉到数据库，应用层绕不过去。

企业身份域的设计约束（openspec change add-tenant-identity-access）：
- 全局账号（users）与企业成员（tenant_memberships）职责分离；租户授权只走
  membership_roles，旧 user_roles 仅承载平台角色。
- 同租户复合外键：department / membership / role 关联都带 (id, tenant_id) 复合
  唯一目标，跨企业关联在数据库层就写不进去。
- 租户表启用 RLS（见 0003 迁移）；运行角色非 owner、非 BYPASSRLS，事务本地
  GUC（air_agent.tenant_id 等）决定可见范围，缺失即不可见。

两张关联表（user_roles / role_permissions）做成映射类而非裸 Table：既要携带
`granted_by`/`granted_at` 这类附加列，也便于用 `session.add` / `session.delete`
逐行操作授权关系，而不必每次都拼 Core 语句。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

UUIDColumn = UUID(as_uuid=True)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- 实体


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(r"account ~ '^[a-zA-Z0-9_-]{3,32}$'", name="users_account_ascii"),
        Index("idx_users_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, primary_key=True, server_default=func.gen_random_uuid()
    )
    # 登录账号：ASCII，唯一
    account: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # 用户名/昵称：支持中文，仅用于展示
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # 密码/账号状态修订号：改密码、停用时递增；会话保存签发时的值，不一致即失效
    auth_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # user_roles 有两个外键指向 users（user_id / granted_by），必须显式声明走哪条
    roles: Mapped[list[Role]] = relationship(
        secondary="user_roles",
        primaryjoin="User.id == UserRole.user_id",
        secondaryjoin="Role.id == UserRole.role_id",
        back_populates="users",
        lazy="selectin",
    )
    followed_regions: Mapped[list[UserFollowedRegion]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        # 作用域与 tenant_id 一致性：平台角色无租户，租户角色必须带租户
        CheckConstraint(
            "(scope = 'platform' AND tenant_id IS NULL)"
            " OR (scope = 'tenant' AND tenant_id IS NOT NULL)",
            name="roles_scope_tenant_consistent",
        ),
        # 复合唯一目标：membership_roles 用它保证角色与成员同企业
        UniqueConstraint("id", "tenant_id", name="uq_roles_id_tenant"),
        Index(
            "uq_roles_platform_code",
            "code",
            unique=True,
            postgresql_where=text("scope = 'platform'"),
        ),
        Index(
            "uq_roles_tenant_code",
            "tenant_id",
            "code",
            unique=True,
            postgresql_where=text("scope = 'tenant'"),
        ),
        Index("idx_roles_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, primary_key=True, server_default=func.gen_random_uuid()
    )
    # code 唯一性按作用域（0003 迁移去掉表级 unique，改为上面的部分唯一索引）
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # platform=平台角色（旧 super_admin/viewer 等，仅经 user_roles 授权）；
    # tenant=企业角色（仅经 membership_roles 授权）
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="platform")
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDColumn, ForeignKey("tenants.id", ondelete="CASCADE")
    )
    # 内置角色禁止删除/改 code
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    users: Mapped[list[User]] = relationship(
        secondary="user_roles",
        primaryjoin="Role.id == UserRole.role_id",
        secondaryjoin="User.id == UserRole.user_id",
        back_populates="roles",
        lazy="selectin",
    )
    permissions: Mapped[list[Permission]] = relationship(
        secondary="role_permissions", back_populates="roles", lazy="selectin"
    )


class Permission(Base):
    """权限 = 菜单 + 按钮，树形。

    type: 1=目录 2=菜单 3=按钮。`code` 形如 `user:create`，前端据此 gate 按钮；
    目录/菜单额外带 `path`/`icon` 用于渲染侧边栏。
    """

    __tablename__ = "permissions"
    __table_args__ = (
        CheckConstraint("type IN (1, 2, 3)", name="permissions_type_valid"),
        Index("idx_permissions_parent", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, primary_key=True, server_default=func.gen_random_uuid()
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDColumn, ForeignKey("permissions.id", ondelete="CASCADE")
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    path: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(Text)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # false=平台专属权限（租户角色不可授予）；租户管理只允许勾选 true 的子集
    grantable_to_tenants: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    roles: Mapped[list[Role]] = relationship(
        secondary="role_permissions", back_populates="permissions", lazy="selectin"
    )
    children: Mapped[list[Permission]] = relationship(
        back_populates="parent", cascade="all, delete-orphan", lazy="selectin"
    )
    parent: Mapped[Permission | None] = relationship(
        back_populates="children", remote_side="Permission.id"
    )


class UserRole(Base):
    """用户-角色关联（附带授权人与授权时间）。"""

    __tablename__ = "user_roles"
    __table_args__ = (Index("idx_user_roles_role", "role_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUIDColumn, ForeignKey("users.id", ondelete="SET NULL")
    )
    granted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RolePermission(Base):
    """角色-权限关联。"""

    __tablename__ = "role_permissions"
    __table_args__ = (Index("idx_role_permissions_perm", "permission_id"),)

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class Region(Base):
    """行政区划（省/市/区县三级）。关注城市的候选来源，不允许自由输入。"""

    __tablename__ = "regions"
    __table_args__ = (
        CheckConstraint("level IN (1, 2, 3)", name="regions_level_valid"),
        Index("idx_regions_parent", "parent_code"),
    )

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    short_name: Mapped[str | None] = mapped_column(Text)
    parent_code: Mapped[str | None] = mapped_column(
        Text, ForeignKey("regions.code", ondelete="CASCADE")
    )
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class UserFollowedRegion(Base):
    """用户关注的城市。

    用关联表而非数组，是为了对 `regions.code` 建外键——保证存进来的都是合法区划码。
    「最多 5 个」由服务层事务保证：锁定用户行后计数再写入
    （见 app/services/followed_regions.py）。
    """

    __tablename__ = "user_followed_regions"
    __table_args__ = (Index("idx_followed_user", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    region_code: Mapped[str] = mapped_column(Text, ForeignKey("regions.code"), primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="followed_regions")
    region: Mapped[Region] = relationship(lazy="joined")


class AuditLog(Base):
    """后台操作审计。真正的管理工作必需，任何写操作都应落一条。

    `actor_account` 冗余存操作人账号：`actor_id` 的外键是 `ON DELETE SET NULL`，
    用户被删除后若不冗余存储，历史记录会退化成"未知操作人"，审计轨迹就断了。

    0003 起审计带企业维度：`tenant_id` 为操作发生的企业上下文；平台操作置
    `platform_scope=true` 且 `tenant_id` 为空。迁移前的旧审计 `tenant_id` 为 NULL，
    属于受限历史域——只对平台上下文与操作人本人可见，不进入任何企业列表。
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_actor", "actor_id", "created_at"),
        Index("idx_audit_created", "created_at"),
        Index("idx_audit_tenant", "tenant_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDColumn, ForeignKey("users.id", ondelete="SET NULL")
    )
    actor_account: Mapped[str | None] = mapped_column(Text)
    actor_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDColumn, ForeignKey("tenant_memberships.id", ondelete="SET NULL")
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDColumn, ForeignKey("tenants.id", ondelete="SET NULL")
    )
    platform_scope: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --------------------------------------------------------------- 企业身份域（0003）


class Tenant(Base):
    """企业（租户）。共用业务平台下的独立授权身份边界。"""

    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(r"code ~ '^[a-z0-9][a-z0-9_-]{1,62}$'", name="tenants_code_ascii"),
        CheckConstraint("status IN ('active', 'suspended')", name="tenants_status_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, primary_key=True, server_default=func.gen_random_uuid()
    )
    # code 是 ASCII 短码（进入票据/审计引用）；name 支持中文
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    # 授权修订号：角色/绑定等平台级授权变化时递增，运行票据携带比对
    auth_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    memberships: Mapped[list[TenantMembership]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan", lazy="selectin"
    )


class Department(Base):
    """部门。父子关系必须同企业：复合外键 (parent_id, tenant_id) → (id, tenant_id)。

    无环校验在服务层事务内用递归 CTE 完成（数据库循环检测需要触发器，刻意避免）。
    """

    __tablename__ = "departments"
    __table_args__ = (
        # 复合 FK (parent_id, tenant_id)→(id, tenant_id)：父部门必须同企业（数据库级）
        ForeignKeyConstraint(
            ["parent_id", "tenant_id"],
            ["departments.id", "departments.tenant_id"],
            ondelete="RESTRICT",
            name="fk_departments_parent_same_tenant",
        ),
        UniqueConstraint("id", "tenant_id", name="uq_departments_id_tenant"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="departments_no_self_parent"),
        UniqueConstraint("tenant_id", "dept_code", name="uq_departments_tenant_code"),
        Index("idx_departments_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    dept_code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TenantMembership(Base):
    """用户在某企业的有效成员身份。同一用户可属于多家企业，互不影响。"""

    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
        UniqueConstraint("id", "tenant_id", name="uq_memberships_id_tenant"),
        # 复合 FK：成员部门必须与企业一致（数据库级）
        ForeignKeyConstraint(
            ["department_id", "tenant_id"],
            ["departments.id", "departments.tenant_id"],
            ondelete="RESTRICT",
            name="fk_memberships_department_same_tenant",
        ),
        CheckConstraint("status IN ('active', 'disabled')", name="memberships_status_valid"),
        Index("idx_memberships_user", "user_id"),
        Index("idx_memberships_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 同企业部门（复合 FK，应用层绕不过）
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    # 成员授权修订：角色/状态/部门变化时递增；票据与缓存比对
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(lazy="selectin")
    roles: Mapped[list[MembershipRole]] = relationship(
        back_populates="membership",
        cascade="all, delete-orphan",
        lazy="selectin",
        foreign_keys="[MembershipRole.membership_id]",
    )
    followed_regions: Mapped[list[MembershipFollowedRegion]] = relationship(
        back_populates="membership",
        cascade="all, delete-orphan",
        lazy="selectin",
        foreign_keys="[MembershipFollowedRegion.membership_id]",
    )


class MembershipRole(Base):
    """成员-企业角色关联。

    两条复合外键把“成员、角色、关联行同企业”下沉到数据库：
    (membership_id, tenant_id) 必须命中某成员的 (id, tenant_id)，
    (role_id, tenant_id) 必须命中某角色的 (id, tenant_id)——平台角色
    tenant_id 为 NULL，复合外键永不匹配，因此**成员角色只接受企业角色**。
    """

    __tablename__ = "membership_roles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["tenant_memberships.id", "tenant_memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_membership_roles_membership_same_tenant",
        ),
        ForeignKeyConstraint(
            ["role_id", "tenant_id"],
            ["roles.id", "roles.tenant_id"],
            ondelete="CASCADE",
            name="fk_membership_roles_role_same_tenant",
        ),
        Index("idx_membership_roles_role", "role_id"),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("tenant_memberships.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUIDColumn, ForeignKey("users.id", ondelete="SET NULL")
    )
    granted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    membership: Mapped[TenantMembership] = relationship(
        back_populates="roles", foreign_keys=[membership_id]
    )
    role: Mapped[Role] = relationship(lazy="selectin", foreign_keys=[role_id])


class MembershipFollowedRegion(Base):
    """成员作用域的关注城市：同一账号在不同企业的偏好分开保存。"""

    __tablename__ = "membership_followed_regions"
    __table_args__ = (
        # 复合 FK：关注城市行与企业一致（数据库级保证偏好不跨企业）
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["tenant_memberships.id", "tenant_memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_membership_followed_same_tenant",
        ),
        Index("idx_membership_followed_membership", "membership_id"),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("tenant_memberships.id", ondelete="CASCADE"), primary_key=True
    )
    region_code: Mapped[str] = mapped_column(Text, ForeignKey("regions.code"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    membership: Mapped[TenantMembership] = relationship(
        back_populates="followed_regions", foreign_keys=[membership_id]
    )
    region: Mapped[Region] = relationship(lazy="joined")


class AuthSession(Base):
    """服务端可撤销会话。只存高熵随机 token 的 SHA-256 摘要，原始 token 不落库。"""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("idx_sessions_user", "user_id"),
        Index("idx_sessions_expiry", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # 当前企业上下文；多企业未选择时为 NULL
    active_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDColumn, ForeignKey("tenants.id", ondelete="SET NULL")
    )
    # 上下文版本：切换企业递增；旧页面携带旧版本请求 → 409
    ctx_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # 签发时的 users.auth_revision；改密/停用递增后旧会话失效
    user_auth_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class McpIdentityBinding(Base):
    """企业/成员 → 统一业务平台外部授权主体的绑定。凭据只存引用名，不落真值。"""

    __tablename__ = "mcp_identity_bindings"
    __table_args__ = (
        # 成员覆盖绑定必须同企业（复合 FK；企业默认绑定 membership_id NULL 时约束自动放行）
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["tenant_memberships.id", "tenant_memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_bindings_membership_same_tenant",
        ),
        CheckConstraint("status IN ('active', 'disabled')", name="bindings_status_valid"),
        Index("idx_bindings_tenant", "tenant_id", "server_key"),
        # 同一企业同一服务至多一条默认绑定；成员覆盖按 (tenant, server, member) 唯一
        Index(
            "ux_bindings_tenant_server_default",
            "tenant_id",
            "server_key",
            unique=True,
            postgresql_where=text("membership_id IS NULL"),
        ),
        Index(
            "ux_bindings_tenant_server_member",
            "tenant_id",
            "server_key",
            "membership_id",
            unique=True,
            postgresql_where=text("membership_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, primary_key=True, server_default=func.gen_random_uuid()
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    # NULL=企业默认绑定；非 NULL=成员覆盖绑定（必须同企业）
    membership_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    server_key: Mapped[str] = mapped_column(Text, nullable=False)
    external_subject: Mapped[str] = mapped_column(Text, nullable=False)
    # 凭据引用（例如环境变量名 / 受保护存储键），真实凭据仅在发送边界解析
    credential_ref: Mapped[str] = mapped_column(Text, nullable=False)
    protocol_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="unverified")
    # 授权修订号：轮换/变更递增；缓存与进行中调用比对
    auth_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ThreadOwnership(Base):
    """线程可信归属。与 LangGraph 原生 metadata 一致，作为自有归档/历史核对索引。"""

    __tablename__ = "thread_ownership"
    __table_args__ = (
        CheckConstraint("source IN ('native', 'migrated')", name="thread_ownership_source_valid"),
        Index("idx_thread_ownership_tenant", "tenant_id", "user_id"),
    )

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="native")
    migration_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MigrationLedger(Base):
    """迁移台账：清单指纹 + 实体映射 + 状态。不含密码/哈希/原文。"""

    __tablename__ = "migration_ledger"
    __table_args__ = (
        UniqueConstraint(
            "manifest_fingerprint", "entity_type", "source_key", name="uq_ledger_fingerprint_entity"
        ),
        CheckConstraint(
            "status IN ('pending', 'applied', 'skipped', 'conflict', 'quarantined')",
            name="ledger_status_valid",
        ),
        Index("idx_ledger_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    manifest_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    target_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    conflict_code: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    actor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# 台账新域（change add-agent-event-ledger，迁移 0007）：独立模块，末尾导入完成
# metadata 注册，避免把所有新表堆叠进本文件（设计 §8）。
# 回答反馈域（change add-chat-feedback-interactions，迁移 0011_feedback）：同策略。
from app import models_feedback as _models_feedback  # noqa: E402,F401  (注册 Base.metadata)
from app import models_ledger as _models_ledger  # noqa: E402,F401  (注册 Base.metadata)
