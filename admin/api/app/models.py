"""SQLAlchemy 模型：RBAC 五表 + 行政区划 + 关注城市 + 审计。

表结构对应 `migrations/versions/0001_init.py`；两边必须保持一致（迁移是权威，
模型用于查询）。约束尽量下沉到数据库，应用层绕不过去。

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
    Index,
    Integer,
    SmallInteger,
    Text,
    func,
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

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDColumn, primary_key=True, server_default=func.gen_random_uuid()
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
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
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_actor", "actor_id", "created_at"),
        Index("idx_audit_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDColumn, ForeignKey("users.id", ondelete="SET NULL")
    )
    actor_account: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
