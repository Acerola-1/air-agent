"""补齐反馈幂等、父子和永久删除边界。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011f_integrity"
down_revision = "0013_merge"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_feedback_operations", sa.Column("intent_digest", sa.Text()))
    op.add_column("agent_feedback_operations", sa.Column("result_snapshot", JSONB()))
    op.execute(
        "DELETE FROM agent_feedback_current c WHERE NOT EXISTS (SELECT 1 FROM "
        "agent_answers a WHERE a.id=c.answer_id AND a.tenant_id=c.tenant_id)"
    )
    op.create_foreign_key(
        "fk_feedback_current_answer",
        "agent_feedback_current",
        "agent_answers",
        ["answer_id", "tenant_id"],
        ["id", "tenant_id"],
        ondelete="CASCADE",
    )
    for table in ("agent_feedback_current", "agent_feedback_operations", "agent_feedback_comments"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY feedback_deleted_guard ON {table} AS RESTRICTIVE FOR ALL TO "
            f"air_agent_runtime USING (NOT EXISTS (SELECT 1 FROM agent_thread_deletions "
            f"d WHERE d.tenant_id={table}.tenant_id AND d.thread_id={table}.thread_id)) "
            f"WITH CHECK (NOT EXISTS (SELECT 1 FROM agent_thread_deletions d WHERE "
            f"d.tenant_id={table}.tenant_id AND d.thread_id={table}.thread_id))"
        )


def downgrade():
    raise RuntimeError("不自动丢弃幂等与删除事实")
