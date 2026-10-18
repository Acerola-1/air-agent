"""移除远程身份引用表中无用途的不可登录口令哨兵列。"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_i4_reference_cleanup"
down_revision = "0019_i4_separation_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("iam_reference_users")
    }
    if "password_unusable" in columns:
        op.drop_column("iam_reference_users", "password_unusable")


def downgrade() -> None:
    op.add_column(
        "iam_reference_users",
        sa.Column(
            "password_unusable",
            sa.String(128),
            nullable=False,
            server_default="!iam-remote-no-local-login",
        ),
    )
