"""用户授权能力选择快照与复合关联（Change 4 任务 3.2）.

在 0012o_delivery 之后追加：
- agent_submissions.capability_selection_id 与 digest_version (v1/v2)
- agent_run_attempts.capability_selection_id
- agent_capability_selections 表（含租户/用户/成员、request/attempt/submission 复合关联）
- FORCE ROW LEVEL SECURITY 与非 owner 最小权限策略
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0013s_capability_selection"
down_revision = "0012o_delivery"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "air_agent_runtime"
WORKER_ROLE = "air_agent_ledger_worker"
NOW = sa.text("now()")


def upgrade() -> None:
    # 1. 给 agent_submissions 增加选择快照关联与摘要版本
    op.add_column("agent_submissions", sa.Column("capability_selection_id", UUID, nullable=True))
    op.add_column(
        "agent_submissions",
        sa.Column("digest_version", sa.Integer(), nullable=False, server_default="1"),
    )

    # 2. 给 agent_run_attempts 增加选择快照关联
    op.add_column("agent_run_attempts", sa.Column("capability_selection_id", UUID, nullable=True))

    # 3. 创建 agent_capability_selections 表
    op.create_table(
        "agent_capability_selections",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("membership_id", UUID, nullable=False),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("submission_id", UUID, nullable=False),
        sa.Column("graph_id", sa.Text(), nullable=False),
        sa.Column("skill_mode", sa.Text(), nullable=False, server_default="auto"),
        sa.Column("skill_id", sa.Text(), nullable=True),
        sa.Column("mcp_mode", sa.Text(), nullable=False, server_default="auto"),
        sa.Column("tool_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("catalog_version", sa.Text(), nullable=True),
        sa.Column("material_version", sa.Text(), nullable=False),
        sa.Column("selection_digest", sa.Text(), nullable=False),
        sa.Column("digest_version", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("selection_source", sa.Text(), nullable=False, server_default="automatic"),
        sa.Column(
            "validation_time", sa.DateTime(timezone=True), nullable=False, server_default=NOW
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_selections_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "submission_id", name="uq_agent_selections_tenant_submission"
        ),
        sa.CheckConstraint(
            "skill_mode IN ('auto', 'explicit')", name="ck_agent_selections_skill_mode"
        ),
        sa.CheckConstraint(
            "mcp_mode IN ('auto', 'allowlist')", name="ck_agent_selections_mcp_mode"
        ),
        sa.CheckConstraint(
            "selection_source IN ('user_explicit', 'automatic')",
            name="ck_agent_selections_source",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id", "tenant_id"],
            ["agent_submissions.id", "agent_submissions.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_selections_submission_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["agent_run_attempts.id", "agent_run_attempts.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_selections_attempt_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["agent_requests.id", "agent_requests.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_selections_request_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["tenant_memberships.id", "tenant_memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_selections_membership_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_agent_selections_user",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_agent_selections_tenant",
        ),
    )
    op.create_index(
        "idx_agent_selections_tenant_user",
        "agent_capability_selections",
        ["tenant_id", "user_id"],
    )
    op.create_index(
        "idx_agent_selections_attempt",
        "agent_capability_selections",
        ["tenant_id", "attempt_id"],
    )

    # 4. 施加 RLS 策略与权限
    _apply_rls_and_grants()


def _apply_rls_and_grants() -> None:
    for statement in selection_grant_statements():
        op.execute(statement)


def selection_grant_statements() -> list[str]:
    """迁移与无 ACL 备份恢复共用的选择表授权和 RLS 规则."""
    table = "agent_capability_selections"
    statements = [
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {RUNTIME_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE ON {table} TO {WORKER_ROLE}",
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS selection_scope ON {table}",
        (
            f"CREATE POLICY selection_scope ON {table} FOR ALL TO {RUNTIME_ROLE}"
            " USING (tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"
            " AND (user_id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid"
            " OR current_setting('air_agent.agent_operations', true) = 'on'))"
            " WITH CHECK ("
            "tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"
            " AND (user_id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid"
            " OR current_setting('air_agent.agent_operations', true) = 'on'))"
        ),
        f"DROP POLICY IF EXISTS selection_worker_scope ON {table}",
        (
            f"CREATE POLICY selection_worker_scope ON {table} FOR ALL TO {WORKER_ROLE}"
            " USING (tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid)"
            " WITH CHECK ("
            "tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid)"
        ),
    ]
    guard = (
        f"NOT EXISTS (SELECT 1 FROM agent_run_attempts a JOIN agent_requests r "
        f"ON r.id = a.request_id AND r.tenant_id = a.tenant_id JOIN agent_thread_deletions d "
        f"ON d.thread_id = r.thread_id AND d.tenant_id = r.tenant_id "
        f"WHERE a.id = {table}.attempt_id AND a.tenant_id = {table}.tenant_id)"
    )
    statements += [
        f"DROP POLICY IF EXISTS selection_deleted_guard ON {table}",
        (
            f"CREATE POLICY selection_deleted_guard ON {table} AS RESTRICTIVE FOR ALL TO "
            f"{RUNTIME_ROLE} USING ("
            f"({guard}) OR current_setting('air_agent.deletion_cleanup', true) = 'on') "
            f"WITH CHECK ({guard})"
        ),
    ]
    return statements


def downgrade() -> None:
    raise RuntimeError("0013s_capability_selection 不支持破坏性自动回退")
