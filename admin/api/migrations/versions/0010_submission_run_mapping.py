"""协调者补修：按提交保存不可漂移的平台运行映射.

Revision ID: 0010
Revises: 0009

resume 共享 attempt，其当前 run 会变化，不能作为历史 submission 的幂等结果。
只回填无恢复历史且唯一确认提交的既有映射；歧义记录保持 NULL，等待可信校准。
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增提交级映射，保留已执行迁移及历史歧义事实."""
    op.add_column("agent_submissions", sa.Column("platform_run_id", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "uq_agent_submission_platform_run", "agent_submissions", ["tenant_id", "platform_run_id"]
    )
    op.execute("""
        UPDATE agent_submissions s SET platform_run_id = a.platform_run_id
        FROM agent_run_attempts a
        WHERE s.attempt_id = a.id AND s.tenant_id = a.tenant_id
          AND s.admission_status = 'confirmed' AND s.operation_kind <> 'resume'
          AND a.platform_run_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM agent_run_resumes r
                          WHERE r.attempt_id = a.id AND r.tenant_id = a.tenant_id)
          AND 1 = (SELECT count(*) FROM agent_submissions x
                   WHERE x.attempt_id = a.id AND x.tenant_id = a.tenant_id
                     AND x.admission_status = 'confirmed')
    """)


def downgrade() -> None:
    """拒绝自动丢失提交映射，回退使用前向修复."""
    raise RuntimeError("0010 保存不可漂移的运行事实，不支持破坏性自动回退")
