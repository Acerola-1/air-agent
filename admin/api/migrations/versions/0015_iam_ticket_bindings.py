"""I2 票据摘要→执行引用绑定存储（change switch-agent-to-remote-iam，G2.1）.

agent_iam_ticket_bindings：Agent 服务端专用存储，登记 rt-v1 票据 SHA-256 摘要 →
IAM 执行引用（verification_ref）+ 已验签票据的身份/四修订 + 入口到期。原始票据/
人类 subject_token 不落库；浏览器不能写本表（登记只允许可信服务端）。BFF 与
native 进程共享（同一业务库）以支撑跨进程复核（A06）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0015_iam_ticket_bindings"
down_revision = "0014_iam_remote_bridge"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "air_agent_runtime"
NOW = sa.text("now()")
TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "agent_iam_ticket_bindings",
        sa.Column("ticket_hash", sa.String(64), primary_key=True),
        sa.Column("verification_ref", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("membership_id", UUID, nullable=False),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("audience", sa.String(64), nullable=False),
        sa.Column("iam_issuer", sa.String(128), nullable=False),
        sa.Column("user_auth_revision", sa.Integer(), nullable=False),
        sa.Column("tenant_auth_revision", sa.Integer(), nullable=False),
        sa.Column("membership_revision", sa.Integer(), nullable=False),
        sa.Column("session_ctx_version", sa.Integer(), nullable=False),
        # 入口到期（票据 exp，≤60s）；引用自身 valid_until 由 IAM 决定（更长）
        sa.Column("entry_expires_at", TZ, nullable=False),
        # 引用复查基线（与票据四修订一致；竞态不一致即不登记）
        sa.Column("ref_revisions", sa.String(256), nullable=False),
        sa.Column("created_at", TZ, nullable=False, server_default=NOW),
    )
    op.create_index(
        "ix_iam_ticket_bindings_entry", "agent_iam_ticket_bindings", ["entry_expires_at"]
    )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON agent_iam_ticket_bindings TO {RUNTIME_ROLE}")


def downgrade() -> None:
    op.drop_table("agent_iam_ticket_bindings")
