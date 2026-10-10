"""审计日志冗余存操作人账号

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11

背景：`audit_logs.actor_id` 是外键且 `ON DELETE SET NULL`。用户一旦被删除，
他历史上的所有操作在审计里就退化成"未知操作人"，审计轨迹无法追溯——这与
"删除用户"这个操作本身一样是管理员需要留痕的行为。

修法：写审计时把操作人账号一并冗余存下。账号是登录标识、不会变，即使账号后来
被改名，历史记录保留当时的取值也是审计应有的语义（记录"当时是谁做的"）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("actor_account", sa.Text()))

    # 回填仍能对应上的历史记录；已被删除的用户无从还原，保持 NULL
    op.execute(
        """
        UPDATE audit_logs AS a
        SET actor_account = u.account
        FROM users AS u
        WHERE a.actor_id = u.id AND a.actor_account IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("audit_logs", "actor_account")
