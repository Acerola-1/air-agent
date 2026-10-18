"""应用会话行保存 IAM subject_token（受控服务端会话；design：原始 token 留在受控服务端应用会话）.

安全边界：仅服务端存储（与业务库同权限模型）；不进浏览器 JS/URL/日志/
checkpoint/state/Trace；登出即随行撤销。用于服务端代表用户执行 IAM 企业
切换等需原会话凭据的受控操作。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_iam_session_token"
down_revision = "0016_iam_session_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_iam_sessions", sa.Column("subject_token", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_iam_sessions", "subject_token")
