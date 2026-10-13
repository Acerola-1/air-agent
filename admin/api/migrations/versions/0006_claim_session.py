"""线程归属认领会话绑定列（轮次复验 F01）

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-12

pending 认领行记录建立它的会话（`claim_session_id`）；确认端点只升级同一已验
运行票据会话的 pending 行——"内部凭据 + 身份匹配 + pending 存在"三者叠加也
不能替代创建事实，会话绑定的认领→确认链路把可信创建事实锚定到当次操作。
幂等 ADD COLUMN IF NOT EXISTS；不触碰既有行（其 claim_session_id 为 NULL，
NULL 行永不匹配确认条件，等价保持 pending 不可见）。
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE thread_ownership ADD COLUMN IF NOT EXISTS claim_session_id uuid")


def downgrade() -> None:
    raise NotImplementedError("0006 不提供 downgrade（与 0003/0004/0005 同策略）")
