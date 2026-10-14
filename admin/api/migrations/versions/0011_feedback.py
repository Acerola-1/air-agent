"""回答反馈：当前评价、操作历史与意见正文（change add-chat-feedback-interactions）.

Revision ID: 0011_feedback
Revises: 0010
Create Date: 2026-10-13

设计要点（openspec/changes/add-chat-feedback-interactions design §1）：

- 三张表都带 `tenant_id`；反馈/意见通过**复合外键**指向 `agent_answers(id, tenant_id)`，
  数据库层拒绝跨租户关联；回答删除（线程级联）时反馈随删。
- `agent_feedback_current` 唯一键 (tenant, user, answer)：同一答案对同一用户只有一行
  当前评价（rating='none' 表示已撤销的当前状态）。
- `agent_feedback_operations` 的 operation_id 幂等唯一 (tenant, user, operation_id)；
  历史不可变（无 UPDATE 授权需求），revision 记录受理时的结果修订号。
- 意见正文独立存储 `agent_feedback_comments`（≤1000 字符约束），独立权限读取，
  不进入事件载荷/Trace/普通审计。
- RLS 复用 0007 模式：单一 AND 策略（租户 + 本人 OR 运营 GUC），失败关闭；
  意见正文表额外要求 `air_agent.feedback_content` GUC 才可见（原文权限同级语义）。
- 本迁移不触碰 0001–0010 的表与策略。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_feedback"
down_revision = "0010"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")

RUNTIME_ROLE = "air_agent_runtime"

RATINGS = ("positive", "negative", "none")
REASONS = ("answer_incorrect", "irrelevant", "incomplete", "slow", "other")


def _tenant_guc() -> str:
    return "NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"


def _user_guc() -> str:
    return "NULLIF(current_setting('air_agent.user_id', true), '')::uuid"


def _sql_in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    # ------------------------------------------------------------ 当前评价
    op.create_table(
        "agent_feedback_current",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("answer_id", UUID, nullable=False),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("rating", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text()),
        sa.Column("comment_id", UUID),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "answer_id", name="uq_agent_feedback_current_target"
        ),
        sa.CheckConstraint(_sql_in("rating", RATINGS), name="agent_feedback_current_rating_valid"),
        sa.CheckConstraint(
            "reason_code IS NULL OR " + _sql_in("reason_code", REASONS),
            name="agent_feedback_current_reason_valid",
        ),
        sa.Index("idx_agent_feedback_current_tenant_time", "tenant_id", "updated_at"),
    )

    # ------------------------------------------------------------ 操作历史
    op.create_table(
        "agent_feedback_operations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("membership_id", UUID, nullable=False),
        sa.Column("answer_id", UUID, nullable=False),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("operation_id", sa.Text(), nullable=False),
        sa.Column("rating", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text()),
        sa.Column("comment_id", UUID),
        sa.Column("accepted_via", sa.Text(), nullable=False, server_default="feedback_put"),
        sa.Column("expected_revision", sa.Integer()),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("client_occurred_at", sa.DateTime(timezone=True)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "operation_id", name="uq_agent_feedback_operations_id"
        ),
        sa.CheckConstraint(
            _sql_in("rating", RATINGS), name="agent_feedback_operations_rating_valid"
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR " + _sql_in("reason_code", REASONS),
            name="agent_feedback_operations_reason_valid",
        ),
        sa.CheckConstraint(
            _sql_in("accepted_via", ("feedback_put", "browser_event")),
            name="agent_feedback_operations_via_valid",
        ),
        sa.ForeignKeyConstraint(
            ["answer_id", "tenant_id"],
            ["agent_answers.id", "agent_answers.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_feedback_operations_answer_same_tenant",
        ),
        sa.Index("idx_agent_feedback_operations_tenant_time", "tenant_id", "received_at"),
        sa.Index("idx_agent_feedback_operations_answer", "tenant_id", "answer_id"),
    )

    # ------------------------------------------------------------ 意见正文
    op.create_table(
        "agent_feedback_comments",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("answer_id", UUID, nullable=False),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("comment_text", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint(
            "char_length(comment_text) <= 1000", name="agent_feedback_comments_len_valid"
        ),
        sa.ForeignKeyConstraint(
            ["answer_id", "tenant_id"],
            ["agent_answers.id", "agent_answers.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_feedback_comments_answer_same_tenant",
        ),
        sa.Index("idx_agent_feedback_comments_tenant", "tenant_id", "created_at"),
    )

    _apply_rls_and_grants()


def feedback_grant_statements(role_checker=None) -> list[str]:
    """反馈域授权与 RLS 策略语句（幂等；迁移与恢复流程共用）。

    意见正文表要求独立 GUC `air_agent.feedback_content='on'` 才可见
    （与台账原文 `agent:content:read` 同级的"另需原文权限"语义）。
    """
    check = role_checker or _role_exists
    if not check(RUNTIME_ROLE):
        raise RuntimeError(
            f"运行角色 {RUNTIME_ROLE} 不存在。请先执行 scripts/bootstrap_tenant_db.py"
        )
    statements: list[str] = []
    feedback_tables = [
        "agent_feedback_current",
        "agent_feedback_operations",
        "agent_feedback_comments",
    ]
    for table in feedback_tables:
        statements.append(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {RUNTIME_ROLE}")
    statements.append(f"GRANT SELECT, UPDATE ON agent_feedback_operations_id_seq TO {RUNTIME_ROLE}")

    # 当前评价与操作历史：租户 + (本人 OR 运营 GUC) 的单条 AND 策略（与 0007 同模式）
    expr = (
        f"tenant_id = {_tenant_guc()}"
        f" AND (user_id = {_user_guc()}"
        " OR current_setting('air_agent.agent_operations', true) = 'on')"
    )
    for table in ("agent_feedback_current", "agent_feedback_operations"):
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        statements.append(f"DROP POLICY IF EXISTS feedback_scope ON {table}")
        statements.append(
            f"CREATE POLICY feedback_scope ON {table} FOR ALL TO {RUNTIME_ROLE}"
            f" USING ({expr}) WITH CHECK ({expr})"
        )

    # 意见正文：租户 + 本人 OR（运营 GUC AND feedback_content GUC）
    comment_expr = (
        f"tenant_id = {_tenant_guc()}"
        " AND (user_id = "
        + _user_guc()
        + " OR (current_setting('air_agent.agent_operations', true) = 'on'"
        " AND current_setting('air_agent.feedback_content', true) = 'on'))"
    )
    statements.append("ALTER TABLE agent_feedback_comments ENABLE ROW LEVEL SECURITY")
    statements.append("DROP POLICY IF EXISTS feedback_comment_scope ON agent_feedback_comments")
    statements.append(
        f"CREATE POLICY feedback_comment_scope ON agent_feedback_comments FOR ALL"
        f" TO {RUNTIME_ROLE} USING ({comment_expr}) WITH CHECK ({comment_expr})"
    )
    return statements


def _role_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": name})
        .scalar()
    )


def _apply_rls_and_grants() -> None:
    for statement in feedback_grant_statements():
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError(
        "0011_feedback 不提供 downgrade：评价事实一旦受理，回退等同丢失用户操作历史；"
        "回退遵循受控备份恢复流程（0003–0010 同策略）"
    )
