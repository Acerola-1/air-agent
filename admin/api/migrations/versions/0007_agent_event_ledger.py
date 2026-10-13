"""Agent 事件台账：可信提问/尝试/回答/Trace 关联、事件与事务 outbox、删除与投影

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-13

对应 openspec change `add-agent-event-ledger`（设计 §2/§5/§6/§7）。要点：

- 所有台账对象带 `tenant_id`；跨租户关联用**复合外键**在数据库层拒绝
  （request↔attempt、attempt↔resume/answer/trace 都带 (id, tenant_id) 唯一目标）。
- RLS 只对运行角色 `air_agent_runtime`（非 owner、非 BYPASSRLS）生效；策略引用
  事务本地 GUC，缺失即不可见（失败关闭）。
- 明细/事件/尝试/回答/映射：单一 AND 策略，条件为
  `tenant_id = GUC(tenant) AND (user_id = GUC(user) OR GUC(agent_operations)='on')`。
  普通成员只能读**本人**对象；企业运营在权限校验后由应用层显式开启
  `air_agent.agent_operations`（防"忘记加 user 过滤"的越权读取）。
  注意：PostgreSQL 多条 permissive 策略是 OR 合并，因此必须写成一条 AND 策略。
- `agent_outbox` 的跨租户领取由**受限调度角色** `air_agent_ledger_worker`
  （bootstrap 可选创建，非 BYPASSRLS、无正文列权限）在 GUC
  `air_agent.ledger_dispatch='on'` 下执行；它看不到 payload/正文。
- 原问题正文只进 `agent_content_refs`（独立授权 + 保留期），普通事件/outbox/
  死信禁止携带全文（见 event 契约 forbidden_payload_key_patterns）。
- 本迁移不触碰 LangGraph 平台内部表（checkpoints 等）。

`0006` 之后的第一个 revision；不改写已执行 revision。
"""

from __future__ import annotations

from collections.abc import Callable

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")

RUNTIME_ROLE = "air_agent_runtime"
WORKER_ROLE = "air_agent_ledger_worker"

# 运行主状态（与 docs/contracts/agent-events-v1/states.json 一致）
ATTEMPT_STATES = (
    "admitting",
    "admission_unknown",
    "queued",
    "running",
    "waiting_input",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "status_unknown",
)
CANCEL_ORIGINS = (
    "user_stop",
    "navigation",
    "tenant_switch",
    "transport_disconnect",
    "permission_revocation",
    "unknown",
)
OPERATION_KINDS = ("initial", "retry", "regenerate", "resume")


def _tenant_guc() -> str:
    return "NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"


def _user_guc() -> str:
    return "NULLIF(current_setting('air_agent.user_id', true), '')::uuid"


def _scope_expr(user_column: str = "user_id") -> str:
    """单条租户+用户作用域条件（合并成一条策略，避免 OR 合并旁路）。"""
    return (
        f"tenant_id = {_tenant_guc()}"
        f" AND ({user_column} = {_user_guc()}"
        " OR current_setting('air_agent.agent_operations', true) = 'on')"
    )


def _role_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": name})
        .scalar()
    )


def _sql_in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    # ------------------------------------------------------------ 逻辑提问
    op.create_table(
        "agent_requests",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("membership_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("user_message_id", sa.Text()),
        sa.Column("graph_id", sa.Text(), nullable=False),
        sa.Column("operation_kind", sa.Text(), nullable=False, server_default="initial"),
        sa.Column("department_id", UUID),
        sa.Column("department_snapshot", sa.Text()),
        sa.Column("role_snapshot", JSONB, nullable=False, server_default="[]"),
        sa.Column("identity_revision", JSONB, nullable=False, server_default="{}"),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_requests_id_tenant"),
        sa.CheckConstraint(
            _sql_in("operation_kind", OPERATION_KINDS), name="agent_requests_kind_valid"
        ),
        sa.Index("idx_agent_requests_tenant_user", "tenant_id", "user_id", "admitted_at"),
        sa.Index("idx_agent_requests_thread", "tenant_id", "thread_id"),
    )

    # ------------------------------------------------------------ 提交幂等
    op.create_table(
        "agent_submissions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("membership_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("submission_key", sa.Text(), nullable=False),
        sa.Column("operation_kind", sa.Text(), nullable=False, server_default="initial"),
        sa.Column("content_digest", sa.Text(), nullable=False),
        sa.Column("admission_status", sa.Text(), nullable=False, server_default="admitting"),
        sa.Column("request_id", UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        # 同成员同线程同提交键唯一（幂等锚点）
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "thread_id",
            "submission_key",
            name="uq_agent_submissions_key",
        ),
        sa.CheckConstraint(
            _sql_in("operation_kind", OPERATION_KINDS), name="agent_submissions_kind_valid"
        ),
        sa.CheckConstraint(
            _sql_in("admission_status", ("admitting", "admission_unknown", "confirmed")),
            name="agent_submissions_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["agent_requests.id", "agent_requests.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_submissions_request_same_tenant",
        ),
        sa.Index("idx_agent_submissions_thread", "tenant_id", "user_id", "thread_id"),
    )

    # ------------------------------------------------------------ 运行尝试
    op.create_table(
        "agent_run_attempts",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("membership_id", UUID, nullable=False),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("attempt_index", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_attempt_id", UUID),
        sa.Column("graph_id", sa.Text(), nullable=False),
        sa.Column("platform_run_id", sa.Text()),
        sa.Column("state", sa.Text(), nullable=False, server_default="admitting"),
        sa.Column("state_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_source", sa.Text()),
        sa.Column("platform_status", sa.Text()),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cancel_origin", sa.Text()),
        sa.Column("cancel_phase", sa.Text()),
        sa.Column("cancel_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("transport_connected", sa.Boolean()),
        sa.Column("waiting_kind", sa.Text()),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column(
            "last_progress_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_attempts_id_tenant"),
        sa.UniqueConstraint("request_id", "attempt_index", name="uq_agent_attempts_index"),
        sa.CheckConstraint(_sql_in("state", ATTEMPT_STATES), name="agent_attempts_state_valid"),
        sa.CheckConstraint(
            "cancel_origin IS NULL OR " + _sql_in("cancel_origin", CANCEL_ORIGINS),
            name="agent_attempts_origin_valid",
        ),
        sa.ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["agent_requests.id", "agent_requests.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_attempts_request_same_tenant",
        ),
        # 非空平台运行映射在租户内唯一（同一平台 run 不得挂到两个尝试）
        sa.Index(
            "uq_agent_attempts_platform_run",
            "tenant_id",
            "platform_run_id",
            unique=True,
            postgresql_where=sa.text("platform_run_id IS NOT NULL"),
        ),
        sa.Index("idx_agent_attempts_tenant_state", "tenant_id", "state", "admitted_at"),
        sa.Index("idx_agent_attempts_reconcile", "state", "last_progress_at"),
    )

    # ------------------------------------------------------------ 人工恢复
    op.create_table(
        "agent_run_resumes",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("resume_index", sa.Integer(), nullable=False),
        sa.Column("original_run_id", sa.Text()),
        sa.Column("platform_run_id", sa.Text(), nullable=False),
        sa.Column("input_ref", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("attempt_id", "resume_index", name="uq_agent_resumes_index"),
        sa.ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["agent_run_attempts.id", "agent_run_attempts.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_resumes_attempt_same_tenant",
        ),
        sa.Index("idx_agent_resumes_tenant", "tenant_id", "created_at"),
    )

    # ------------------------------------------------------------ 回答版本
    op.create_table(
        "agent_answers",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("message_id", sa.Text()),
        sa.Column("content_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_ref_id", UUID),
        sa.Column("partial", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["agent_run_attempts.id", "agent_run_attempts.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_answers_attempt_same_tenant",
        ),
        # 消息来源版本唯一：同尝试同消息同内容版本只允许一行
        sa.Index(
            "uq_agent_answers_message_version",
            "tenant_id",
            "attempt_id",
            "message_id",
            "content_version",
            unique=True,
            postgresql_where=sa.text("message_id IS NOT NULL"),
        ),
        sa.Index("idx_agent_answers_attempt", "tenant_id", "attempt_id"),
    )

    # ------------------------------------------------------------ Trace 映射
    op.create_table(
        "agent_trace_links",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("project_scope", sa.Text(), nullable=False, server_default="default"),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("link_source", sa.Text(), nullable=False, server_default="runtime_callback"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "project_scope",
            "trace_id",
            "attempt_id",
            name="uq_agent_trace_links",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["agent_run_attempts.id", "agent_run_attempts.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_trace_links_attempt_same_tenant",
        ),
        sa.Index("idx_agent_trace_links_attempt", "tenant_id", "attempt_id"),
    )

    # ------------------------------------------------------------ 事件信封
    op.create_table(
        "agent_events",
        sa.Column("event_id", UUID, primary_key=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("membership_id", UUID, nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default="1.0"),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("thread_id", sa.Text()),
        sa.Column("request_id", UUID),
        sa.Column("attempt_id", UUID),
        sa.Column("run_id", sa.Text()),
        sa.Column("answer_id", UUID),
        sa.Column("message_id", sa.Text()),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("payload_digest", sa.Text(), nullable=False),
        sa.Column("state_revision", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("tenant_id", "event_id", name="uq_agent_events_tenant_event"),
        sa.CheckConstraint(
            _sql_in("source", ("runtime", "server", "system", "browser")),
            name="agent_events_source_valid",
        ),
        sa.Index("idx_agent_events_tenant_time", "tenant_id", "occurred_at"),
        sa.Index("idx_agent_events_attempt", "tenant_id", "attempt_id"),
        sa.Index("idx_agent_events_type", "tenant_id", "event_type", "occurred_at"),
    )

    # ------------------------------------------------------------ 事务 outbox
    op.create_table(
        "agent_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("consumer", sa.Text(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("lease_owner", sa.Text()),
        sa.Column("lease_deadline", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_class", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("tenant_id", "event_id", "consumer", name="uq_agent_outbox_target"),
        sa.CheckConstraint(
            _sql_in("status", ("pending", "leased", "done", "dead_letter")),
            name="agent_outbox_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["event_id", "tenant_id"],
            ["agent_events.event_id", "agent_events.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_outbox_event_same_tenant",
        ),
        sa.Index("idx_agent_outbox_ready", "status", "available_at"),
        sa.Index("idx_agent_outbox_tenant", "tenant_id", "status"),
    )

    # ------------------------------------------------------ 消费 receipt（幂等）
    op.create_table(
        "agent_consumer_receipts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("consumer", sa.Text(), nullable=False),
        sa.Column("consumer_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "tenant_id",
            "consumer",
            "consumer_version",
            "event_id",
            name="uq_agent_receipts_target",
        ),
    )

    # ------------------------------------------------------------ 原问题快照
    op.create_table(
        "agent_content_refs",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("membership_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Text()),
        sa.Column("content_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_snapshot", sa.Text()),
        sa.Column("retention_until", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Index("idx_agent_content_refs_tenant", "tenant_id", "created_at"),
        sa.Index("idx_agent_content_refs_retention", "retention_until"),
    )

    # ------------------------------------------------------ 会话删除标记
    op.create_table(
        "agent_thread_deletions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("requested_by", UUID),
        sa.Column("state", sa.Text(), nullable=False, server_default="requested"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_class", sa.Text()),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "thread_id", name="uq_agent_deletions_thread"),
        sa.CheckConstraint(
            _sql_in("state", ("requested", "in_progress", "completed", "failed")),
            name="agent_deletions_state_valid",
        ),
        sa.Index("idx_agent_deletions_open", "state", "requested_at"),
    )

    # ------------------------------------------------------ 投影/口径状态
    op.create_table(
        "agent_projection_state",
        sa.Column("tenant_id", UUID, nullable=False, primary_key=True),
        sa.Column("consumer", sa.Text(), nullable=False, primary_key=True),
        sa.Column("consumer_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("metric_version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
        sa.Column("projected_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )

    _apply_rls_and_grants()


def ledger_grant_statements(role_checker: Callable[[str], bool] | None = None) -> list[str]:
    """台账授权与 RLS 策略语句（幂等）。

    迁移与**恢复流程**共用同一份语句：备份恢复若丢失 ACL（例如
    `pg_dump --no-privileges`），必须在**开放读取前**重跑本函数，否则运行角色
    会被拒绝（服务 503）。语句全部幂等（GRANT/`DROP POLICY IF EXISTS`）。
    """
    check = role_checker or _role_exists
    if not check(RUNTIME_ROLE):
        raise RuntimeError(
            f"运行角色 {RUNTIME_ROLE} 不存在。请先执行 scripts/bootstrap_tenant_db.py"
        )
    statements: list[str] = []
    ledger_tables = [
        "agent_requests",
        "agent_submissions",
        "agent_run_attempts",
        "agent_run_resumes",
        "agent_answers",
        "agent_trace_links",
        "agent_events",
        "agent_outbox",
        "agent_consumer_receipts",
        "agent_content_refs",
        "agent_thread_deletions",
        "agent_projection_state",
    ]
    for table in ledger_tables:
        statements.append(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {RUNTIME_ROLE}")
    statements.append(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {RUNTIME_ROLE}")

    user_scoped = [
        "agent_requests",
        "agent_submissions",
        "agent_run_attempts",
        "agent_run_resumes",
        "agent_answers",
        "agent_trace_links",
        "agent_events",
        "agent_content_refs",
    ]
    expr = _scope_expr()
    for table in user_scoped:
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        statements.append(f"DROP POLICY IF EXISTS ledger_scope ON {table}")
        statements.append(
            f"CREATE POLICY ledger_scope ON {table} FOR ALL TO {RUNTIME_ROLE}"
            f" USING ({expr}) WITH CHECK ({expr})"
        )

    tenant_only = (
        "agent_thread_deletions",
        "agent_projection_state",
        "agent_outbox",
        "agent_consumer_receipts",
    )
    tenant_expr = f"tenant_id = {_tenant_guc()}"
    for table in tenant_only:
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        statements.append(f"DROP POLICY IF EXISTS ledger_tenant ON {table}")
        statements.append(
            f"CREATE POLICY ledger_tenant ON {table} FOR ALL TO {RUNTIME_ROLE}"
            f" USING ({tenant_expr}) WITH CHECK ({tenant_expr})"
        )

    if check(WORKER_ROLE):
        statements += [
            f"GRANT SELECT (id, tenant_id, event_id, consumer, available_at, lease_owner,"
            f" lease_deadline, attempt_count, error_class, status, created_at, updated_at)"
            f" ON agent_outbox TO {WORKER_ROLE}",
            f"GRANT UPDATE (lease_owner, lease_deadline, attempt_count, error_class,"
            f" status, available_at, updated_at) ON agent_outbox TO {WORKER_ROLE}",
            f"GRANT SELECT (event_id, tenant_id, event_type, source, occurred_at, thread_id)"
            f" ON agent_events TO {WORKER_ROLE}",
            f"GRANT SELECT (id, tenant_id, thread_id, state, requested_at, completed_at)"
            f" ON agent_thread_deletions TO {WORKER_ROLE}",
            "ALTER TABLE agent_outbox ENABLE ROW LEVEL SECURITY",
            "DROP POLICY IF EXISTS ledger_dispatch ON agent_outbox",
            f"CREATE POLICY ledger_dispatch ON agent_outbox FOR ALL TO {WORKER_ROLE}"
            " USING (current_setting('air_agent.ledger_dispatch', true) = 'on')"
            " WITH CHECK (current_setting('air_agent.ledger_dispatch', true) = 'on')",
            "DROP POLICY IF EXISTS ledger_dispatch_read ON agent_events",
            f"CREATE POLICY ledger_dispatch_read ON agent_events FOR SELECT TO {WORKER_ROLE}"
            " USING (current_setting('air_agent.ledger_dispatch', true) = 'on')",
            "DROP POLICY IF EXISTS ledger_dispatch_read ON agent_thread_deletions",
            f"CREATE POLICY ledger_dispatch_read ON agent_thread_deletions FOR SELECT"
            f" TO {WORKER_ROLE}"
            " USING (current_setting('air_agent.ledger_dispatch', true) = 'on')",
        ]
    return statements


def _apply_rls_and_grants() -> None:
    """迁移路径：执行共用授权/策略语句（幂等）。"""
    for statement in ledger_grant_statements():
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError(
        "0007 不提供 downgrade：台账事实一旦写入，回退等同丢失已确认事件；"
        "回退遵循受控备份恢复流程（0003–0006 同策略）"
    )
