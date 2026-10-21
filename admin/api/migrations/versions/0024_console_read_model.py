"""运营后台读模型：受控回答正文快照与轻量反馈处理单（change add-agent-admin-console）.

Revision ID: 0024_console_read_model
Revises: 0023_knowledge_citations
Create Date: 2026-10-20

设计（openspec/changes/add-agent-admin-console/design.md D4/D5）：

- `agent_answer_contents`：确认终答的受控正文快照。以
  (tenant_id, answer_id, content_version) 唯一；只保存可见文本，不保存推理/
  工具调用/完整 state。复合外键保证同企业回答；FORCE RLS + 删除墓碑。
- `agent_feedback_cases`：每 (tenant, answer) 一条运营处理状态；
  用户评价修订（agent_feedback_current.revision）与处理修订（case.revision）
  分开，用户后来改评不覆盖处理历史。
- `agent_feedback_case_actions`：操作 ID 幂等历史；不含意见/说明正文。
- `agent_feedback_case_notes`：处理说明正文，独立 GUC 才可被运营读取，
  长度 ≤1000，随会话删除失效。

权限分级（与既有台账/反馈一致）：
- 本人：tenant + user GUC；
- 运营读：额外 `air_agent.agent_operations='on'`；
- 正文读：再额外 `air_agent.answer_content='on'`（回答）或
  `air_agent.feedback_content='on'`（处理说明）；
- 删除清理：`air_agent.deletion_cleanup='on'` 放行墓碑守卫；
- 运行角色非 owner；平台名称不构成旁路。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024_console_read_model"
down_revision = "0023_knowledge_citations"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
NOW = sa.text("now()")
RUNTIME_ROLE = "air_agent_runtime"

CASE_STATES = ("pending", "in_progress", "resolved")
CASE_ACTIONS = ("start", "resolve", "reopen")


def _tenant_guc() -> str:
    return "NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"


def _user_guc() -> str:
    return "NULLIF(current_setting('air_agent.user_id', true), '')::uuid"


def _sql_in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    # ------------------------------------------------------ 回答确认正文快照
    op.create_table(
        "agent_answer_contents",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("answer_id", UUID, nullable=False),
        sa.Column("message_id", sa.Text()),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("content_text", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("total_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("capture_status", sa.Text(), nullable=False, server_default="captured"),
        sa.Column("captured_at", sa.DateTime(timezone=True)),
        sa.Column("retention_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "tenant_id",
            "answer_id",
            "content_version",
            name="uq_agent_answer_contents_version",
        ),
        sa.CheckConstraint(
            _sql_in("capture_status", ("captured", "capture_failed")),
            name="agent_answer_contents_status_valid",
        ),
        sa.CheckConstraint("total_chars >= 0", name="agent_answer_contents_total_chars_valid"),
        sa.ForeignKeyConstraint(
            ["answer_id", "tenant_id"],
            ["agent_answers.id", "agent_answers.tenant_id"],
            ondelete="CASCADE",
            name="fk_answer_contents_answer_same_tenant",
        ),
        sa.Index(
            "idx_agent_answer_contents_request",
            "tenant_id",
            "request_id",
            "content_version",
        ),
        sa.Index("idx_agent_answer_contents_thread", "tenant_id", "thread_id"),
    )

    # ------------------------------------------------------ 反馈处理单
    op.create_table(
        "agent_feedback_cases",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("answer_id", UUID, nullable=False),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "reviewed_feedback_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("handled_by", UUID),
        sa.Column("handled_at", sa.DateTime(timezone=True)),
        sa.Column("last_note_id", UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_feedback_cases_id_tenant"),
        sa.UniqueConstraint("tenant_id", "answer_id", name="uq_agent_feedback_cases_answer"),
        sa.CheckConstraint(_sql_in("state", CASE_STATES), name="agent_feedback_cases_state_valid"),
        sa.ForeignKeyConstraint(
            ["answer_id", "tenant_id"],
            ["agent_answers.id", "agent_answers.tenant_id"],
            ondelete="CASCADE",
            name="fk_feedback_cases_answer_same_tenant",
        ),
        sa.Index("idx_agent_feedback_cases_tenant_state", "tenant_id", "state", "updated_at"),
    )

    op.create_table(
        "agent_feedback_case_actions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("case_id", UUID, nullable=False),
        sa.Column("answer_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("operation_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("from_state", sa.Text()),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", UUID, nullable=False),
        sa.Column("actor_account", sa.Text(), nullable=False),
        sa.Column("payload_digest", sa.Text(), nullable=False),
        sa.Column("note_id", UUID),
        sa.Column("result_snapshot", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "tenant_id", "operation_id", name="uq_agent_feedback_case_actions_operation"
        ),
        sa.CheckConstraint(
            _sql_in("action", CASE_ACTIONS), name="agent_feedback_case_actions_action_valid"
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["agent_feedback_cases.id", "agent_feedback_cases.tenant_id"],
            ondelete="CASCADE",
            name="fk_feedback_case_actions_case_same_tenant",
        ),
        sa.Index(
            "idx_agent_feedback_case_actions_case",
            "tenant_id",
            "case_id",
            "created_at",
        ),
    )

    op.create_table(
        "agent_feedback_case_notes",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("case_id", UUID, nullable=False),
        sa.Column("answer_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("actor_user_id", UUID, nullable=False),
        sa.Column("note_text", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("retention_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "char_length(note_text) <= 1000", name="agent_feedback_case_notes_len_valid"
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["agent_feedback_cases.id", "agent_feedback_cases.tenant_id"],
            ondelete="CASCADE",
            name="fk_feedback_case_notes_case_same_tenant",
        ),
        sa.Index("idx_agent_feedback_case_notes_case", "tenant_id", "case_id", "created_at"),
    )

    _apply_rls_and_grants()


def console_grant_statements(role_checker=None) -> list[str]:
    """本域授权与 RLS（幂等；迁移与恢复流程共用）."""
    check = role_checker or _role_exists
    if not check(RUNTIME_ROLE):
        raise RuntimeError(
            f"运行角色 {RUNTIME_ROLE} 不存在。请先执行 scripts/bootstrap_tenant_db.py"
        )
    statements: list[str] = []
    tables = [
        "agent_answer_contents",
        "agent_feedback_cases",
        "agent_feedback_case_actions",
        "agent_feedback_case_notes",
    ]
    for table in tables:
        statements.append(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {RUNTIME_ROLE}")

    tenant = _tenant_guc()
    user = _user_guc()
    answer_expr = (
        f"tenant_id = {tenant}"
        f" AND (user_id = {user}"
        " OR (current_setting('air_agent.agent_operations', true) = 'on'"
        " AND current_setting('air_agent.answer_content', true) = 'on'))"
    )
    note_expr = (
        f"tenant_id = {tenant}"
        " AND (user_id = "
        + user
        + " OR (current_setting('air_agent.agent_operations', true) = 'on'"
        " AND current_setting('air_agent.feedback_content', true) = 'on'))"
    )
    # 处理单/操作历史没有 user_id 列：本人仍按回答归属人存于 cases.user_id，
    # 读策略以 cases.user_id 为准；操作历史通过存在性子查询继承。
    case_expr = (
        f"tenant_id = {tenant}"
        f" AND (user_id = {user}"
        " OR current_setting('air_agent.agent_operations', true) = 'on')"
    )
    action_expr = (
        f"tenant_id = {tenant}"
        " AND (EXISTS (SELECT 1 FROM agent_feedback_cases c"
        " WHERE c.id = agent_feedback_case_actions.case_id"
        " AND c.tenant_id = agent_feedback_case_actions.tenant_id"
        f" AND (c.user_id = {user}"
        " OR current_setting('air_agent.agent_operations', true) = 'on'))"
        " OR current_setting('air_agent.deletion_cleanup', true) = 'on')"
    )

    policies = {
        "agent_answer_contents": ("answer_content_scope", answer_expr),
        "agent_feedback_cases": ("feedback_case_scope", case_expr),
        "agent_feedback_case_actions": ("feedback_case_action_scope", action_expr),
        "agent_feedback_case_notes": ("feedback_case_note_scope", note_expr),
    }
    for table, (policy, expr) in policies.items():
        statements += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS {policy} ON {table}",
            f"CREATE POLICY {policy} ON {table} FOR ALL TO {RUNTIME_ROLE}"
            f" USING ({expr}) WITH CHECK ({expr})",
            f"DROP POLICY IF EXISTS console_deleted_guard ON {table}",
            (
                f"CREATE POLICY console_deleted_guard ON {table} AS RESTRICTIVE FOR ALL TO "
                f"{RUNTIME_ROLE} USING ((NOT EXISTS (SELECT 1 FROM agent_thread_deletions d"
                f" WHERE d.tenant_id = {table}.tenant_id AND d.thread_id = {table}.thread_id))"
                " OR current_setting('air_agent.deletion_cleanup', true) = 'on')"
                " WITH CHECK ((NOT EXISTS (SELECT 1 FROM agent_thread_deletions d"
                f" WHERE d.tenant_id = {table}.tenant_id AND d.thread_id = {table}.thread_id))"
                " OR current_setting('air_agent.deletion_cleanup', true) = 'on')"
            ),
        ]
    # 删除清理 worker 需要能按墓碑删除本域行：清理 GUC 已在应用层显式设置。
    statements.append(f"GRANT USAGE ON SCHEMA public TO {RUNTIME_ROLE}")
    return statements


def _role_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": name})
        .scalar()
    )


def _apply_rls_and_grants() -> None:
    for statement in console_grant_statements():
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError(
        "0024_console_read_model 不提供 downgrade：回答快照与处理审计是业务事实；"
        "回退使用应用版本与采集开关，不自动丢弃已确认内容。"
    )
