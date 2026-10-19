"""业务许可快照字段（change simplify-agent-business-authorization 任务 2.3）.

- 台账请求/尝试固定受理时的稳定业务码与业务策略版本；旧记录保持 NULL，
  不按历史图名伪造新模型许可（旧运行需重新受理）。
- IAM 运行票据绑定增加入站内省冻结的精确权限集合（非秘密），供原生
  create_run 在服务端求交业务使用权限；逐发送仍以执行引用复查修订。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0021_business_authorization"
down_revision = "0020_i4_reference_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def columns(table: str) -> set[str]:
        return {column["name"] for column in inspector.get_columns(table)}

    request_columns = columns("agent_requests")
    if "business_code" not in request_columns:
        op.add_column("agent_requests", sa.Column("business_code", sa.String(64)))
    if "business_policy_version" not in request_columns:
        op.add_column("agent_requests", sa.Column("business_policy_version", sa.String(64)))

    attempt_columns = columns("agent_run_attempts")
    if "business_code" not in attempt_columns:
        op.add_column("agent_run_attempts", sa.Column("business_code", sa.String(64)))
    if "business_policy_version" not in attempt_columns:
        op.add_column("agent_run_attempts", sa.Column("business_policy_version", sa.String(64)))

    binding_columns = columns("agent_iam_ticket_bindings")
    if "permissions" not in binding_columns:
        op.add_column(
            "agent_iam_ticket_bindings",
            sa.Column(
                "permissions",
                JSONB,
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )

    # 只约束新写入的格式；历史 NULL 表示"无新模型许可快照"，不是授权事实。
    checks = {
        constraint["name"]
        for table in ("agent_requests", "agent_run_attempts")
        for constraint in inspector.get_check_constraints(table)
        if constraint.get("name")
    }
    if "agent_requests_business_code_format" not in checks:
        op.create_check_constraint(
            "agent_requests_business_code_format",
            "agent_requests",
            "business_code IS NULL OR business_code ~ '^[a-z0-9][a-z0-9-]{0,63}$'",
        )
    if "agent_run_attempts_business_code_format" not in checks:
        op.create_check_constraint(
            "agent_run_attempts_business_code_format",
            "agent_run_attempts",
            "business_code IS NULL OR business_code ~ '^[a-z0-9][a-z0-9-]{0,63}$'",
        )


def downgrade() -> None:
    op.drop_constraint(
        "agent_run_attempts_business_code_format",
        "agent_run_attempts",
        type_="check",
    )
    op.drop_constraint("agent_requests_business_code_format", "agent_requests", type_="check")
    op.drop_column("agent_iam_ticket_bindings", "permissions")
    op.drop_column("agent_run_attempts", "business_policy_version")
    op.drop_column("agent_run_attempts", "business_code")
    op.drop_column("agent_requests", "business_policy_version")
    op.drop_column("agent_requests", "business_code")
