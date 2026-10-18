"""I2 remote IAM 桥接：服务端应用会话存储 + 最小业务引用投影（change switch-agent-to-remote-iam）.

- agent_iam_sessions：Agent 自己的 HttpOnly 应用会话 handle → IAM 派生 token 摘要、
  身份快照标识与四修订。原始 IAM subject_token 绝不入库——只存 SHA-256 摘要，
  兑换/内省在内存完成。两个进程（BFF/native）共享该表以支撑跨进程票据绑定复核。
  迁移/引导角色建表；请求角色按会话键读写（RLS 强制）。
- iam_reference_tenants / iam_reference_users / iam_reference_memberships：
  与 IAM **同 UUID** 的最小业务引用投影——只服务业务 FK/RLS/展示，不存任何
  有效会话、角色或口令认证材料，不构成第二授权源。口令哈希列如旧表不可空，
  投影表不建该列；本地登录在 remote 部署由服务端开关关闭。

owner（迁移）建表与授权；请求角色 air_agent_runtime 仅获得带作用域的最小 DML。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

NOW = sa.text("now()")
TZ = sa.DateTime(timezone=True)

revision = "0014_iam_remote_bridge"
down_revision = "0013s_capability_selection"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "air_agent_runtime"


def upgrade() -> None:
    # ---- 1. 服务端应用会话（IAM 派生凭据的摘要存储；跨进程共享） ----
    op.create_table(
        "agent_iam_sessions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        # handle：浏览器 HttpOnly cookie 值的 SHA-256（原始 handle 不入库）
        sa.Column("handle_hash", sa.String(64), nullable=False, unique=True),
        # IAM access_token 的 SHA-256（原始 token 只存在于浏览器→BFF 短链路与进程内存）
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=True),
        sa.Column("membership_id", UUID, nullable=True),
        sa.Column("ctx_version", sa.Integer(), nullable=False),
        sa.Column("user_auth_revision", sa.Integer(), nullable=False),
        sa.Column("tenant_auth_revision", sa.Integer(), nullable=True),
        sa.Column("membership_revision", sa.Integer(), nullable=True),
        sa.Column("audience", sa.String(64), nullable=False),
        sa.Column("iam_issuer", sa.String(128), nullable=False),
        # G2 票据绑定：该会话经 include_execution_context=true 取得的执行引用
        sa.Column("verification_ref", UUID, nullable=True),
        # 最近一次 IAM 内省的非秘密快照（展示/投影同步用；不含 token/口令）
        sa.Column("account", sa.String(64), nullable=False, server_default=""),
        sa.Column("tenant_code", sa.String(64), nullable=True),
        sa.Column("tenant_name", sa.String(128), nullable=True),
        sa.Column("permissions", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", TZ, nullable=False, server_default=NOW),
        sa.Column("last_seen_at", TZ, nullable=False, server_default=NOW),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_iam_sessions_user", "agent_iam_sessions", ["user_id"])

    # ---- 2. 最小业务引用投影（同 UUID；不授权） ----
    op.create_table(
        "iam_reference_tenants",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("auth_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("synced_at", TZ, nullable=False, server_default=NOW),
    )
    op.create_table(
        "iam_reference_users",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("account", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("auth_revision", sa.Integer(), nullable=False, server_default="0"),
        # 技术占位：remote 投影绝不存可用口令；此列固定放无法通过 bcrypt 校验的哨兵值
        sa.Column(
            "password_unusable",
            sa.String(128),
            nullable=False,
            server_default="!iam-remote-no-local-login",
        ),
        sa.Column("synced_at", TZ, nullable=False, server_default=NOW),
    )
    op.create_table(
        "iam_reference_memberships",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, sa.ForeignKey("iam_reference_tenants.id"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("iam_reference_users.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("synced_at", TZ, nullable=False, server_default=NOW),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_iam_reference_membership"),
    )

    # ---- 3. 请求角色最小授权 ----
    # 会话存储：runtime 角色按主键/会话键读写；投影表：只读（由服务端同步写入 owner 面）。
    for stmt in (
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON agent_iam_sessions TO {RUNTIME_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON iam_reference_tenants TO {RUNTIME_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON iam_reference_users TO {RUNTIME_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON iam_reference_memberships TO {RUNTIME_ROLE}",
    ):
        op.execute(stmt)


def downgrade() -> None:
    op.drop_table("iam_reference_memberships")
    op.drop_table("iam_reference_users")
    op.drop_table("iam_reference_tenants")
    op.drop_table("agent_iam_sessions")
