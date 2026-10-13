"""台账第二轮纠正迁移：启动权释放状态、服务级只读校准能力与有界校准运行台账

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-13

针对首轮修复独立复验的 F01/F04/F05。**不改写 0007/0008**（均已执行，含真实业务库）：
本迁移只做增量纠正，全部语句幂等（`IF EXISTS` / 存在性判断）。

1. F01 启动权释放：`agent_submissions.admission_status` 增加 `released` 取值。
   响应丢失时启动权可能被消费但平台并无运行；有界补偿在**确认无平台运行**且超过宽限期后
   释放未确认的启动权（`released`），使提交不再永久卡在"待确认"。原 CHECK 只允许
   admitting/admission_unknown/confirmed，故必须扩展取值域。
2. F05 服务级只读校准：新增 `agent_ledger_reconcile_runs`，记录每次校准的**有界运行**
   （作用域、游标、批次、计数、结果）。它让"有界执行/续跑路径"有可审计的落点：
   单次运行受 `max_batches` 限制，游标可续跑，不依赖任何用户会话。
3. 授权：运行角色按租户读写自己的校准运行记录（RLS 策略同其它台账表）；
   受限调度角色只读租户行的最小列（运维可见性），不含正文/载荷。
"""

from __future__ import annotations

from collections.abc import Callable

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "air_agent_runtime"
WORKER_ROLE = "air_agent_ledger_worker"
SUBMISSION_STATUSES = ("admitting", "admission_unknown", "confirmed", "released")


def _tenant_guc() -> str:
    """租户 GUC 表达式（事务本地，缺失即空集合）."""
    return "NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"


def upgrade() -> None:
    """扩展提交状态取值域并建立服务级校准运行台账."""
    # ---------------------------------------------------------------- F01
    op.execute(
        "ALTER TABLE agent_submissions DROP CONSTRAINT IF EXISTS agent_submissions_status_valid"
    )
    values = ", ".join(f"'{value}'" for value in SUBMISSION_STATUSES)
    op.execute(
        "ALTER TABLE agent_submissions"
        f" ADD CONSTRAINT agent_submissions_status_valid"
        f" CHECK (admission_status IN ({values}))"
    )

    # ---------------------------------------------------------------- F05
    op.create_table(
        "agent_ledger_reconcile_runs",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("auth_kind", sa.String(length=32), nullable=False, server_default="service"),
        sa.Column("scope_kind", sa.String(length=16), nullable=False, server_default="tenant"),
        sa.Column("scope_value", sa.String(length=128), nullable=True),
        sa.Column("cursor", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("batches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calibrated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kept_unknown", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("released", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("detail", sa.String(length=200), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'bounded_stop', 'completed', 'failed')",
            name="agent_reconcile_status_valid",
        ),
    )
    op.create_index(
        "ix_agent_reconcile_runs_tenant_started",
        "agent_ledger_reconcile_runs",
        ["tenant_id", "started_at"],
    )

    for statement in ledger_grant_statements(_role_exists):
        op.execute(statement)


def _role_exists(role: str) -> bool:
    """角色是否存在（授权语句只在角色存在时执行，保持与 0007/0008 同形幂等）."""
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": role})
        .scalar()
    )


def ledger_grant_statements(role_checker: Callable[[str], bool]) -> list[str]:
    """本迁移新增授权语句（与 0007/0008 同形）：隔离环境可用自己的受限角色复现.

    `docs/reviews/evidence/*/setup*.py` 会以随机非 owner 角色名调用本函数，
    使"专用角色能否真的读/写这些新表"在验收环境可复现。
    """
    runtime, worker = RUNTIME_ROLE, WORKER_ROLE
    statements = [
        f"GRANT SELECT, INSERT, UPDATE ON agent_ledger_reconcile_runs TO {runtime}",
        "ALTER TABLE agent_ledger_reconcile_runs ENABLE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS ledger_tenant ON agent_ledger_reconcile_runs",
        f"CREATE POLICY ledger_tenant ON agent_ledger_reconcile_runs FOR ALL"
        f" TO {runtime} USING ({_tenant_expr()}) WITH CHECK ({_tenant_expr()})",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {runtime}",
    ]
    if role_checker(worker):
        statements += [
            f"GRANT SELECT (tenant_id, actor, status, batches, candidates, calibrated,"
            f" kept_unknown, released, started_at, finished_at)"
            f" ON agent_ledger_reconcile_runs TO {worker}",
            "DROP POLICY IF EXISTS ledger_dispatch_read ON agent_ledger_reconcile_runs",
            f"CREATE POLICY ledger_dispatch_read ON agent_ledger_reconcile_runs"
            f" FOR SELECT TO {worker}"
            " USING (current_setting('air_agent.ledger_dispatch', true) = 'on')",
        ]
    return statements


def _tenant_expr() -> str:
    """租户 GUC 表达式（与其它台账表的租户策略同形）."""
    return f"tenant_id = {_tenant_guc()}"


def downgrade() -> None:
    raise NotImplementedError(
        "0009 不提供 downgrade：回退会重新允许'启动权永久滞留'与'无服务级校准凭据'，"
        "等同重新引入已复验缺陷；回退遵循受控备份恢复流程（0003–0008 同策略）"
    )
