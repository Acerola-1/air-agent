"""应用会话行补充 IAM 会话 id（展示/me 外壳需要；非授权语义）."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0016_iam_session_id"
down_revision = "0015_iam_ticket_bindings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_iam_sessions", sa.Column("iam_session_id", UUID, nullable=True))


def downgrade() -> None:
    op.drop_column("agent_iam_sessions", "iam_session_id")
