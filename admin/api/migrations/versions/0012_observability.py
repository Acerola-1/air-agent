"""观测域表：逐调用观测、价格版本、计费修订、消费投影（change complete-agent-observability）

Revision ID: 0012_observability
Revises: 0010
Create Date: 2026-10-13

要点（对齐 change design §1/§6 与 0007 的台账保护语义）：

- 仅新增观测域表；不改写 0001–0010，不触碰 LangGraph 平台内部表与台账受理表。
- 观测表按 tenant 强制 RLS；运行角色 `air_agent_runtime`（非 owner、非
  BYPASSRLS）策略引用事务本地 GUC，缺失即不可见（失败关闭）。
- 父子/attempt/submission 关联全部带 tenant 复合约束，跨企业父子在数据库层拒绝。
- 价格记录不可变版本（半开区间 [effective_from, effective_to)），重叠拒绝由
  排他约束实现；计费修订可重算但保留原依据。
- 观测队列/回读不需要新角色：写入走运行角色的运行事务；管理端查询走 admin
  API 的运行角色 + 应用层授权（本人/运营/统计分级在服务层实现）。
- 迁移后不假造任何价格：表从空开始，费用 unknown 由应用语义表达。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_observability"
down_revision = "0010"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)

JSONB = postgresql.JSONB
NOW = sa.text("now()")

RUNTIME_ROLE = "air_agent_runtime"

# 观测状态字典（spec: started/succeeded/failed/cancelled/timed_out/unknown）
OBSERVATION_STATUSES = (
    "started",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "unknown",
)
# 工具结果分类（spec agent-capability-observability）
TOOL_RESULT_STATUSES = (
    "succeeded",
    "empty_result",
    "validation_rejected",
    "permission_denied",
    "technical_error",
    "timed_out",
    "cancelled",
    "unknown",
)
# 模型角色（spec agent-model-observability）
MODEL_ROLES = ("main", "normalizer", "summarizer", "subagent", "cleanup", "other")
# usage 测量状态
MEASUREMENT_STATUSES = (
    "measured",
    "usage_missing",
    "invalid_usage",
    "estimated",
    "not_collected",
)


def _check(enum_name: str, column: str, values: tuple[str, ...]) -> sa.CheckConstraint:
    rendered = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({rendered})", name=enum_name)


def upgrade() -> None:
    """创建观测域六表 + 价格两表 + 投影状态，随后启用 FORCE RLS."""
    # 1) agent_model_observations：每次实际模型尝试一行
    op.create_table(
        "agent_model_observations",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("submission_id", UUID),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text()),
        sa.Column("platform_run_id", sa.Text()),
        sa.Column("graph_id", sa.Text(), nullable=False),
        sa.Column("observation_id", UUID, nullable=False),
        sa.Column("logical_call_id", UUID, nullable=False),
        sa.Column("parent_observation_id", UUID),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("ttft_ms", sa.Integer()),
        sa.Column("first_piece_kind", sa.Text()),
        # usage：受限规范化字段；缺 usage 保持 NULL（measurement_status 说明）
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("cache_read_tokens", sa.Integer()),
        sa.Column("cache_write_tokens", sa.Integer()),
        sa.Column("reasoning_tokens", sa.Integer()),
        sa.Column("measurement_status", sa.Text(), nullable=False),
        sa.Column("usage_source", sa.Text()),
        sa.Column("retry_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["agent_run_attempts.id", "agent_run_attempts.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["agent_requests.id", "agent_requests.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "(total_tokens IS NULL) OR (total_tokens >= 0)", name="ck_model_obs_total_nonneg"
        ),
        _check("ck_model_obs_status", "status", OBSERVATION_STATUSES),
        _check("ck_model_obs_role", "role", MODEL_ROLES),
        _check("ck_model_obs_measurement", "measurement_status", MEASUREMENT_STATUSES),
        sa.UniqueConstraint("tenant_id", "observation_id", name="uq_model_obs_observation"),
    )
    op.create_index("ix_model_obs_attempt", "agent_model_observations", ["tenant_id", "attempt_id"])
    op.create_index(
        "ix_model_obs_logical",
        "agent_model_observations",
        ["tenant_id", "logical_call_id"],
    )

    # 2) agent_capability_spans：Skill 阶段与通用 span
    op.create_table(
        "agent_capability_spans",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("observation_id", UUID, nullable=False),
        sa.Column("parent_observation_id", UUID),
        sa.Column("span_kind", sa.Text(), nullable=False),
        sa.Column("span_name", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("skill_id", sa.Text()),
        sa.Column("skill_version", sa.Text()),
        sa.Column("selection_source", sa.Text()),
        sa.Column("model_observation_id", UUID),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["agent_run_attempts.id", "agent_run_attempts.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "observation_id", name="uq_cap_span_observation"),
        _check(
            "ck_cap_span_stage",
            "stage",
            (
                "matched",
                "selected",
                "loaded",
                "executed",
                "not_executed",
                "other",
            ),
        ),
    )
    op.create_index("ix_cap_span_attempt", "agent_capability_spans", ["tenant_id", "attempt_id"])

    # 3) agent_tool_calls：工具逻辑调用（每逻辑调用一行）
    op.create_table(
        "agent_tool_calls",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("logical_call_id", UUID, nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("service_key", sa.Text()),
        sa.Column("skill_id", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["agent_run_attempts.id", "agent_run_attempts.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "logical_call_id", name="uq_tool_call_logical"),
    )
    op.create_index("ix_tool_calls_attempt", "agent_tool_calls", ["tenant_id", "attempt_id"])

    # 4) agent_tool_call_attempts：每次物理尝试（重试/降级各一行）
    op.create_table(
        "agent_tool_call_attempts",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("observation_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("logical_call_id", UUID, nullable=False),
        sa.Column("retry_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("fallback_kind", sa.Text()),
        sa.Column("error_category", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["agent_run_attempts.id", "agent_run_attempts.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "observation_id", name="uq_tool_attempt_observation"),
        _check("ck_tool_attempt_status", "status", TOOL_RESULT_STATUSES),
    )
    op.create_index(
        "ix_tool_attempt_logical",
        "agent_tool_call_attempts",
        ["tenant_id", "logical_call_id"],
    )

    # 5) agent_model_prices：不可变价格版本（半开区间，重叠拒绝）
    op.create_table(
        "agent_model_prices",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("price_ref", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False, server_default="1m_tokens"),
        sa.Column("input_per_unit", sa.Numeric(18, 10), nullable=False),
        sa.Column("output_per_unit", sa.Numeric(18, 10), nullable=False),
        sa.Column("cache_read_per_unit", sa.Numeric(18, 10)),
        sa.Column("cache_write_per_unit", sa.Numeric(18, 10)),
        # 推理计价模式：included_in_output（随输出价，不重复收费）或独立费率
        sa.Column("reasoning_mode", sa.Text(), nullable=False, server_default="included_in_output"),
        sa.Column("reasoning_per_unit", sa.Numeric(18, 10)),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("created_by", UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("tenant_id", "price_ref", name="uq_model_prices_ref"),
        _check(
            "ck_model_prices_reasoning_mode",
            "reasoning_mode",
            ("included_in_output", "independent"),
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_model_prices_interval",
        ),
    )
    op.create_index(
        "ix_model_prices_lookup",
        "agent_model_prices",
        ["tenant_id", "provider", "model", "effective_from"],
    )
    # Postgres 排他约束拒绝同 (provider, model) 区间重叠
    op.execute(
        """
        CREATE EXTENSION IF NOT EXISTS btree_gist;
        ALTER TABLE agent_model_prices ADD CONSTRAINT ex_model_prices_no_overlap
        EXCLUDE USING gist (
            provider WITH =,
            model WITH =,
            tenant_id WITH =,
            tstzrange(effective_from, COALESCE(effective_to, 'infinity'::timestamptz), '[)') WITH &&
        )
        """
    )

    # 6) agent_cost_revisions：显式计费修订（幂等重算审计）
    op.create_table(
        "agent_cost_revisions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("observation_id", UUID, nullable=False),
        sa.Column("attempt_id", UUID, nullable=False),
        sa.Column("calculation_revision", sa.Integer(), nullable=False),
        sa.Column("price_ref", sa.Text()),
        sa.Column("amount", sa.Numeric(18, 10)),
        sa.Column("currency", sa.Text()),
        sa.Column("measurement_status", sa.Text(), nullable=False),
        sa.Column("basis", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_by", UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "tenant_id", "observation_id", "calculation_revision", name="uq_cost_revision_once"
        ),
        _check("ck_cost_revision_status", "measurement_status", MEASUREMENT_STATUSES),
    )

    # 7) agent_observability_projection：消费者投影状态（独立 consumer_version）
    op.create_table(
        "agent_observability_projection",
        sa.Column("tenant_id", UUID, primary_key=True),
        sa.Column("consumer", sa.Text(), primary_key=True),
        sa.Column("consumer_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("metric_version", sa.Text(), nullable=False, server_default="v1"),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
        sa.Column("projected_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("tenant_id", "consumer"),
    )

    _apply_rls_and_grants()


def _apply_rls_and_grants() -> None:
    """观测表 RLS + 运行角色授权（幂等语句，恢复流程可重跑）."""
    statements: list[str] = []
    obs_tables = [
        "agent_model_observations",
        "agent_capability_spans",
        "agent_tool_calls",
        "agent_tool_call_attempts",
        "agent_cost_revisions",
    ]
    for table in obs_tables:
        statements.append(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {RUNTIME_ROLE}")
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        statements.append(f"DROP POLICY IF EXISTS observability_scope ON {table}")
        statements.append(
            f"CREATE POLICY observability_scope ON {table} FOR ALL TO {RUNTIME_ROLE}"
            " USING (tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"
            " AND (user_id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid"
            " OR current_setting('air_agent.agent_operations', true) = 'on'))"
            " WITH CHECK (tenant_id ="
            " NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"
            " AND (user_id = NULLIF(current_setting('air_agent.user_id', true), '')::uuid"
            " OR current_setting('air_agent.agent_operations', true) = 'on'))"
        )
    # 价格与投影：租户级。价格管理经 admin API 的 agent:pricing:manage 权限 +
    # 审计（services/observability），运行角色需要 INSERT；策略仍限租户 GUC。
    statements.append(f"GRANT SELECT, INSERT, UPDATE ON agent_model_prices TO {RUNTIME_ROLE}")
    statements.append("ALTER TABLE agent_model_prices ENABLE ROW LEVEL SECURITY")
    statements.append("ALTER TABLE agent_model_prices FORCE ROW LEVEL SECURITY")
    statements.append("DROP POLICY IF EXISTS observability_tenant ON agent_model_prices")
    statements.append(
        "CREATE POLICY observability_tenant ON agent_model_prices FOR ALL TO "
        f"{RUNTIME_ROLE}"
        " USING (tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid)"
    )
    statements.append(
        f"GRANT SELECT, INSERT, UPDATE ON agent_observability_projection TO {RUNTIME_ROLE}"
    )
    statements.append("ALTER TABLE agent_observability_projection ENABLE ROW LEVEL SECURITY")
    statements.append("ALTER TABLE agent_observability_projection FORCE ROW LEVEL SECURITY")
    statements.append(
        "DROP POLICY IF EXISTS observability_tenant ON agent_observability_projection"
    )
    statements.append(
        "CREATE POLICY observability_tenant ON agent_observability_projection FOR ALL TO "
        f"{RUNTIME_ROLE}"
        " USING (tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid)"
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    """拒绝自动丢弃观测事实；回退需显式运维决策."""
    raise RuntimeError("0012_observability 保存逐调用观测事实，不支持破坏性自动回退")
