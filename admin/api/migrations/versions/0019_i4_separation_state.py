"""I4 前向准备：部署形态标记与最小身份引用复合键。

此迁移对 local 数据库只增加元数据，不替换旧表、外键或权限；remote-only
物理转换由显式 I4 CLI 执行，并在全部门禁通过后把标记设为 remote/ready。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0019_i4_separation_state"
down_revision = "0018_iam_scope_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "i4_separation_state",
        sa.Column("singleton", sa.SmallInteger(), primary_key=True),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_fingerprint", JSONB(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("singleton = 1", name="ck_i4_separation_singleton"),
        sa.CheckConstraint("mode IN ('local', 'remote')", name="ck_i4_separation_mode"),
        sa.CheckConstraint(
            "state IN ('local', 'preparing', 'frozen', 'ready', 'blocked', 'recovering')",
            name="ck_i4_separation_state",
        ),
    )
    op.execute(
        "INSERT INTO i4_separation_state (singleton, mode, state) VALUES (1, 'local', 'local')"
    )
    op.create_unique_constraint(
        "uq_iam_reference_membership_id_tenant",
        "iam_reference_memberships",
        ["id", "tenant_id"],
    )
    op.create_unique_constraint(
        "uq_iam_reference_membership_id_tenant_user",
        "iam_reference_memberships",
        ["id", "tenant_id", "user_id"],
    )
    op.execute("REVOKE ALL ON i4_separation_state FROM PUBLIC")
    op.execute("GRANT SELECT ON i4_separation_state TO air_agent_runtime")


def downgrade() -> None:
    op.drop_constraint(
        "uq_iam_reference_membership_id_tenant_user",
        "iam_reference_memberships",
        type_="unique",
    )
    op.drop_constraint(
        "uq_iam_reference_membership_id_tenant",
        "iam_reference_memberships",
        type_="unique",
    )
    op.drop_table("i4_separation_state")
