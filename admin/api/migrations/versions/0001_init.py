"""初始化 RBAC、行政区划、关注城市与审计表

Revision ID: 0001
Revises:
Create Date: 2026-09-11

说明：「关注城市最多 5 个」的并发安全由服务层事务保证（锁定用户行后计数），
不在数据库层用触发器实现，详见 `app/services/followed_regions.py`。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
NOW = sa.text("now()")


def upgrade() -> None:
    # ------------------------------------------------------------------ 用户
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("account", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        # 账号必须是 ASCII：中文一旦进了 session token，JS 侧 btoa 会直接抛异常
        sa.CheckConstraint(r"account ~ '^[a-zA-Z0-9_-]{3,32}$'", name="users_account_ascii"),
    )
    op.create_index("idx_users_created_at", "users", ["created_at"])

    # ------------------------------------------------------------------ 角色
    op.create_table(
        "roles",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )

    # ------------------------------------------------------------------ 权限（菜单 + 按钮树）
    op.create_table(
        "permissions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("parent_id", UUID, sa.ForeignKey("permissions.id", ondelete="CASCADE")),
        sa.Column("code", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.SmallInteger(), nullable=False),
        sa.Column("path", sa.Text()),
        sa.Column("icon", sa.Text()),
        sa.Column("sort", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("type IN (1, 2, 3)", name="permissions_type_valid"),
    )
    op.create_index("idx_permissions_parent", "permissions", ["parent_id"])

    # ------------------------------------------------------------------ 用户-角色
    op.create_table(
        "user_roles",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", UUID, sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("granted_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("idx_user_roles_role", "user_roles", ["role_id"])

    # ------------------------------------------------------------------ 角色-权限
    op.create_table(
        "role_permissions",
        sa.Column("role_id", UUID, sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "permission_id",
            UUID,
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("idx_role_permissions_perm", "role_permissions", ["permission_id"])

    # ------------------------------------------------------------------ 行政区划（省/市/区县）
    op.create_table(
        "regions",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("short_name", sa.Text()),
        sa.Column("parent_code", sa.Text(), sa.ForeignKey("regions.code", ondelete="CASCADE")),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("level IN (1, 2, 3)", name="regions_level_valid"),
    )
    op.create_index("idx_regions_parent", "regions", ["parent_code"])

    # ------------------------------------------------------------------ 关注城市
    op.create_table(
        "user_followed_regions",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("region_code", sa.Text(), sa.ForeignKey("regions.code"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("idx_followed_user", "user_followed_regions", ["user_id"])

    # ------------------------------------------------------------------ 审计
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text()),
        sa.Column("target_id", sa.Text()),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("ip", postgresql.INET()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_index("idx_audit_actor", "audit_logs", ["actor_id", "created_at"])
    op.create_index("idx_audit_created", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("user_followed_regions")
    op.drop_table("regions")
    op.drop_table("role_permissions")
    op.drop_table("user_roles")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("users")
