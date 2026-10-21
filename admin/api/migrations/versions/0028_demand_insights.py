"""需求洞察：来源、委托、profile、问题簇、归属与人工操作（change 7）.

Revision ID: 0028_demand_insights
Revises: 0027_console_paging_index

边界（design D1–D7）：

- 来源只锚定“同企业已确认受理 request + 精确 content_version”，不保存原句副本；
- 标准化文本/向量与可聚合元数据分表，metrics 只能读去标识聚合字段；
- 运行角色 `air_agent_runtime` 按 tenant + 域 GUC 最小读写；独立 worker 角色
  `air_agent_insights_worker` 才能跨企业领取作业/读取委托，并在每个任务内重设
  tenant GUC；
- 委托表只有 worker 能读；浏览器/API 只得到 job_id；
- 所有企业表 FORCE RLS，复合外键保证同 tenant；删除墓碑由本域读取策略与
  清理 hook 共同保证。

角色不存在时迁移仍可完成表结构，但 worker 策略不创建；部署必须先执行
`scripts/bootstrap_tenant_db.py` 并提供 `TENANT_INSIGHTS_WORKER_DB_PASSWORD`。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028_demand_insights"
down_revision = "0027_console_paging_index"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
REAL_ARRAY = postgresql.ARRAY(sa.REAL())
NOW = sa.text("now()")
RUNTIME_ROLE = "air_agent_runtime"
WORKER_ROLE = "air_agent_insights_worker"

PROFILE_STATES = ("draft", "ready", "active", "retired", "failed")
JOB_KINDS = ("incremental", "backfill", "rebuild")
JOB_STATES = (
    "pending",
    "running",
    "paused_user",
    "paused_authorization",
    "paused_budget",
    "paused_failure",
    "completed",
    "cancelled",
    "failed",
)
SOURCE_STATUSES = ("processing", "processed", "failed", "skipped")
GAP_REASONS = (
    "pending_confirmation",
    "missing_content",
    "expired_content",
    "deleted",
    "too_long",
    "sensitive_content",
    "unresolved_context",
    "capacity_review",
)
CLUSTER_STATUSES = ("candidate", "active", "merged", "retired")
ASSIGNMENT_METHODS = ("vector", "vector_new", "llm", "manual", "rule")
ASSIGNMENT_STATUSES = ("active", "superseded")


def _sql_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _role_exists(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": name})
        .scalar()
    )


def upgrade() -> None:
    op.create_table(
        "agent_insight_profiles",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("profile_key", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("normalizer_version", sa.Text(), nullable=False),
        sa.Column("analysis_version", sa.Text(), nullable=False),
        sa.Column("embedding_profile", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("llm_profile", sa.Text()),
        sa.Column("thresholds", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("policy", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("calibration", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_insight_profiles_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "profile_key", "profile_version", name="uq_agent_insight_profiles_key"
        ),
        sa.CheckConstraint(
            _sql_in("state", PROFILE_STATES), name="agent_insight_profiles_state_valid"
        ),
        sa.CheckConstraint(
            "embedding_dimension >= 1 AND embedding_dimension <= 4096",
            name="agent_insight_profiles_dimension_valid",
        ),
        sa.Index("idx_agent_insight_profiles_tenant", "tenant_id", "state", "created_at"),
    )

    op.create_table(
        "agent_insight_profile_state",
        sa.Column("tenant_id", UUID, primary_key=True),
        sa.Column("active_profile_id", UUID),
        sa.Column("assignment_revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["active_profile_id", "tenant_id"],
            ["agent_insight_profiles.id", "agent_insight_profiles.tenant_id"],
            ondelete="RESTRICT",
            name="fk_insight_profile_state_active_same_tenant",
        ),
    )

    op.create_table(
        "agent_insight_delegations",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("membership_id", UUID, nullable=False),
        sa.Column("session_handle_hash", sa.String(64), nullable=False),
        sa.Column("ctx_version", sa.Integer(), nullable=False),
        sa.Column("user_auth_revision", sa.Integer(), nullable=False),
        sa.Column("tenant_auth_revision", sa.Integer(), nullable=False),
        sa.Column("membership_revision", sa.Integer(), nullable=False),
        sa.Column("profile_id", UUID, nullable=False),
        sa.Column("scope", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_insight_delegations_id_tenant"),
        sa.CheckConstraint(
            _sql_in("status", ("active", "expired", "revoked")),
            name="agent_insight_delegations_status_valid",
        ),
        sa.CheckConstraint(
            "expires_at <= issued_at + interval '12 hours'",
            name="agent_insight_delegations_max_window",
        ),
        sa.Index("idx_agent_insight_delegations_expiry", "status", "expires_at"),
    )

    op.create_table(
        "agent_insight_jobs",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("profile_id", UUID, nullable=False),
        sa.Column("delegation_id", UUID, nullable=False),
        sa.Column("requested_by", UUID, nullable=False),
        sa.Column("job_kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True)),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("max_sources", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="200000"),
        sa.Column("authorization_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("classified_intents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unclassified_intents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actual_tokens", sa.BigInteger()),
        sa.Column("usage_missing", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("lease_owner", sa.Text()),
        sa.Column("lease_deadline", sa.DateTime(timezone=True)),
        sa.Column("fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cursor_admitted_at", sa.DateTime(timezone=True)),
        sa.Column("cursor_request_id", UUID),
        sa.Column("state_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.Text()),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("continuous", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("predecessor_job_id", UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("paused_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_insight_jobs_id_tenant"),
        sa.CheckConstraint(_sql_in("job_kind", JOB_KINDS), name="agent_insight_jobs_kind_valid"),
        sa.CheckConstraint(_sql_in("state", JOB_STATES), name="agent_insight_jobs_state_valid"),
        sa.ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["agent_insight_profiles.id", "agent_insight_profiles.tenant_id"],
            ondelete="RESTRICT",
            name="fk_insight_jobs_profile_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["delegation_id", "tenant_id"],
            ["agent_insight_delegations.id", "agent_insight_delegations.tenant_id"],
            ondelete="RESTRICT",
            name="fk_insight_jobs_delegation_same_tenant",
        ),
        sa.Index("idx_agent_insight_jobs_tenant", "tenant_id", "created_at"),
        sa.Index("idx_agent_insight_jobs_claim", "state", "lease_deadline", "created_at"),
    )

    op.create_table(
        "agent_question_sources",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("profile_id", UUID, nullable=False),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("membership_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("graph_id", sa.Text(), nullable=False),
        sa.Column("business_code", sa.Text()),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_message_id", sa.Text()),
        sa.Column("source_content_version", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("retention_until", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False, server_default="processing"),
        sa.Column("failure_code", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("analysis_version", sa.Text(), nullable=False),
        sa.Column("split_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_question_sources_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id",
            "request_id",
            "source_content_version",
            "analysis_version",
            name="uq_agent_question_sources_version",
        ),
        sa.CheckConstraint(
            _sql_in("status", SOURCE_STATUSES), name="agent_question_sources_status_valid"
        ),
        sa.ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["agent_requests.id", "agent_requests.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_sources_request_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["agent_insight_profiles.id", "agent_insight_profiles.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_sources_profile_same_tenant",
        ),
        sa.Index("idx_agent_question_sources_window", "tenant_id", "admitted_at", "request_id"),
        sa.Index("idx_agent_question_sources_status", "tenant_id", "status", "updated_at"),
    )

    op.create_table(
        "agent_question_source_gaps",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("profile_id", UUID, nullable=False),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("analysis_version", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint(
            "tenant_id", "request_id", "analysis_version", name="uq_agent_question_gaps_request"
        ),
        sa.CheckConstraint(_sql_in("reason", GAP_REASONS), name="agent_question_gaps_reason_valid"),
        sa.ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["agent_requests.id", "agent_requests.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_gaps_request_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["agent_insight_profiles.id", "agent_insight_profiles.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_gaps_profile_same_tenant",
        ),
        sa.Index("idx_agent_question_gaps_reason", "tenant_id", "reason", "updated_at"),
    )

    op.create_table(
        "agent_question_intents",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("source_id", UUID, nullable=False),
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("split_index", sa.Integer(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("object", sa.Text(), nullable=False),
        sa.Column("time_kind", sa.Text(), nullable=False),
        sa.Column("negation", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("slots", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("normalizer_version", sa.Text(), nullable=False),
        sa.Column("analysis_version", sa.Text(), nullable=False),
        sa.Column("embedding_profile", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("content_expiry", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_question_intents_id_tenant"),
        sa.UniqueConstraint(
            "source_id", "split_index", "analysis_version", name="uq_agent_question_intents_split"
        ),
        sa.CheckConstraint(
            "split_index >= 0 AND split_index <= 4", name="agent_question_intents_index"
        ),
        sa.CheckConstraint(
            "embedding_dimension >= 1 AND embedding_dimension <= 4096",
            name="agent_question_intents_dimension",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "tenant_id"],
            ["agent_question_sources.id", "agent_question_sources.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_intents_source_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["agent_requests.id", "agent_requests.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_intents_request_same_tenant",
        ),
        sa.Index("idx_agent_question_intents_source", "tenant_id", "source_id"),
        sa.Index("idx_agent_question_intents_window", "tenant_id", "created_at"),
    )

    op.create_table(
        "agent_question_intent_contents",
        sa.Column("intent_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("embedding", REAL_ARRAY, nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("intent_id", "tenant_id", name="pk_agent_question_intent_contents"),
        sa.ForeignKeyConstraint(
            ["intent_id", "tenant_id"],
            ["agent_question_intents.id", "agent_question_intents.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_intent_contents_intent_same_tenant",
        ),
        sa.CheckConstraint(
            "array_length(embedding, 1) = embedding_dimension",
            name="agent_question_intent_contents_dimension",
        ),
    )

    op.create_table(
        "agent_question_topics",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("profile_id", UUID, nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name_zh", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_question_topics_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "profile_id", "code", name="uq_agent_question_topics_code"
        ),
        sa.CheckConstraint(
            _sql_in("status", ("active", "retired")), name="agent_question_topics_status_valid"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["agent_insight_profiles.id", "agent_insight_profiles.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_topics_profile_same_tenant",
        ),
    )

    op.create_table(
        "agent_question_clusters",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("profile_id", UUID, nullable=False),
        sa.Column("topic_id", UUID),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("object", sa.Text(), nullable=False),
        sa.Column("time_kind", sa.Text(), nullable=False),
        sa.Column("negation", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("constraints", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.Text(), nullable=False, server_default="candidate"),
        sa.Column("merged_into", UUID),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("representative_intent_id", UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_question_clusters_id_tenant"),
        sa.CheckConstraint(
            _sql_in("status", CLUSTER_STATUSES), name="agent_question_clusters_status_valid"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["agent_insight_profiles.id", "agent_insight_profiles.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_clusters_profile_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id", "tenant_id"],
            ["agent_question_topics.id", "agent_question_topics.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_clusters_topic_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["merged_into", "tenant_id"],
            ["agent_question_clusters.id", "agent_question_clusters.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_clusters_merge_same_tenant",
        ),
        sa.Index(
            "idx_agent_question_clusters_list", "tenant_id", "profile_id", "status", "created_at"
        ),
    )

    op.create_table(
        "agent_question_cluster_vectors",
        sa.Column("cluster_id", UUID, nullable=False),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("prototype", REAL_ARRAY, nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint(
            "cluster_id", "tenant_id", name="pk_agent_question_cluster_vectors"
        ),
        sa.ForeignKeyConstraint(
            ["cluster_id", "tenant_id"],
            ["agent_question_clusters.id", "agent_question_clusters.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_cluster_vectors_cluster_same_tenant",
        ),
        sa.CheckConstraint(
            "array_length(prototype, 1) = dimension",
            name="agent_question_cluster_vectors_dimension",
        ),
    )

    op.create_table(
        "agent_question_assignments",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("profile_id", UUID, nullable=False),
        sa.Column("intent_id", UUID, nullable=False),
        sa.Column("cluster_id", UUID),
        sa.Column("unclassified_reason", sa.Text()),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("assignment_revision", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_question_assignments_id_tenant"),
        sa.CheckConstraint(
            "(cluster_id IS NOT NULL) <> (unclassified_reason IS NOT NULL)",
            name="agent_question_assignments_target_valid",
        ),
        sa.CheckConstraint(
            _sql_in("method", ASSIGNMENT_METHODS), name="agent_question_assignments_method_valid"
        ),
        sa.CheckConstraint(
            _sql_in("status", ASSIGNMENT_STATUSES), name="agent_question_assignments_status_valid"
        ),
        sa.ForeignKeyConstraint(
            ["intent_id", "tenant_id"],
            ["agent_question_intents.id", "agent_question_intents.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_assignments_intent_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["cluster_id", "tenant_id"],
            ["agent_question_clusters.id", "agent_question_clusters.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_assignments_cluster_same_tenant",
        ),
        sa.Index(
            "uq_agent_question_assignments_active",
            "tenant_id",
            "intent_id",
            "profile_id",
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        ),
        sa.Index(
            "idx_agent_question_assignments_cluster",
            "tenant_id",
            "profile_id",
            "cluster_id",
            "assignment_revision",
        ),
    )

    op.create_table(
        "agent_insight_operations",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID, nullable=False),
        sa.Column("operation_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", UUID, nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("payload_digest", sa.Text(), nullable=False),
        sa.Column("result_snapshot", JSONB),
        sa.Column("actor_user_id", UUID, nullable=False),
        sa.Column("actor_account", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("id", "tenant_id", name="uq_agent_insight_operations_id_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "operation_id", name="uq_agent_insight_operations_operation"
        ),
        sa.Index("idx_agent_insight_operations_target", "tenant_id", "target_type", "target_id"),
    )

    for statement in insight_grant_statements():
        op.execute(statement)


def insight_grant_statements(role_checker=None) -> list[str]:  # noqa: ANN001
    """本域 RLS/授权语句（幂等；迁移与恢复脚本共用同一事实源）."""
    check = role_checker or _role_exists
    if not check(RUNTIME_ROLE):
        raise RuntimeError(
            f"运行角色 {RUNTIME_ROLE} 不存在。请先执行 scripts/bootstrap_tenant_db.py"
        )
    tenant = "tenant_id = NULLIF(current_setting('air_agent.tenant_id', true), '')::uuid"
    metrics = "current_setting('air_agent.insight_metrics', true) = 'on'"
    ops = "current_setting('air_agent.insight_operations', true) = 'on'"
    content = "current_setting('air_agent.insight_content', true) = 'on'"
    manage = "current_setting('air_agent.insight_manage', true) = 'on'"
    worker = "current_setting('air_agent.insight_worker', true) = 'on'"
    statements: list[str] = []

    runtime_grants = {
        "agent_insight_profiles": "SELECT, INSERT, UPDATE",
        "agent_insight_profile_state": "SELECT, INSERT, UPDATE",
        "agent_insight_jobs": "SELECT, INSERT, UPDATE",
        "agent_insight_delegations": "INSERT",
        "agent_question_sources": "SELECT",
        "agent_question_source_gaps": "SELECT",
        "agent_question_intents": "SELECT",
        "agent_question_intent_contents": "SELECT",
        "agent_question_topics": "SELECT, INSERT",
        "agent_question_clusters": "SELECT, INSERT, UPDATE",
        "agent_question_cluster_vectors": "SELECT, INSERT, UPDATE, DELETE",
        "agent_question_assignments": "SELECT, INSERT, UPDATE",
        "agent_insight_operations": "SELECT, INSERT",
    }
    for table, privileges in runtime_grants.items():
        statements.append(f"GRANT {privileges} ON {table} TO {RUNTIME_ROLE}")

    policies = [
        (
            "agent_insight_profiles",
            f"{tenant} AND ({metrics} OR {ops} OR {manage})",
            f"{tenant} AND {manage}",
        ),
        (
            "agent_insight_profile_state",
            f"{tenant} AND ({metrics} OR {ops} OR {manage})",
            f"{tenant} AND {manage}",
        ),
        (
            "agent_insight_jobs",
            f"{tenant} AND {ops}",
            f"{tenant} AND {manage} AND {ops} AND {content}",
        ),
        (
            "agent_insight_delegations",
            f"{tenant} AND {manage} AND {ops} AND {content}",
            f"{tenant} AND {manage} AND {ops} AND {content}",
        ),
        (
            "agent_question_sources",
            f"{tenant} AND ({metrics} OR ({ops} AND {content}))",
            "false",
        ),
        (
            "agent_question_source_gaps",
            f"{tenant} AND ({metrics} OR ({ops} AND {content}))",
            "false",
        ),
        (
            "agent_question_intents",
            f"{tenant} AND ({metrics} OR ({ops} AND {content}))",
            "false",
        ),
        (
            "agent_question_intent_contents",
            f"{tenant} AND {ops} AND {content}",
            "false",
        ),
        (
            "agent_question_topics",
            f"{tenant} AND ({metrics} OR {ops} OR {manage})",
            f"{tenant} AND {manage}",
        ),
        (
            "agent_question_clusters",
            f"{tenant} AND ({metrics} OR {ops} OR {manage})",
            f"{tenant} AND {manage}",
        ),
        (
            "agent_question_cluster_vectors",
            f"{tenant} AND {ops} AND {content}",
            f"{tenant} AND {manage}",
        ),
        (
            "agent_question_assignments",
            f"{tenant} AND ({metrics} OR {ops} OR {manage})",
            f"{tenant} AND {manage}",
        ),
        (
            "agent_insight_operations",
            f"{tenant} AND {ops}",
            f"{tenant} AND {ops} AND {manage} AND {content}",
        ),
    ]
    # 读策略与写策略分开：同一 FOR ALL 策略会让 metrics-only 的 UPDATE 通过
    # USING 后被 WITH CHECK 再判一次；分命令策略更清楚。
    for table, read_expr, write_expr in policies:
        statements += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS insight_read ON {table}",
            f"CREATE POLICY insight_read ON {table} FOR SELECT TO {RUNTIME_ROLE}"
            f" USING ({read_expr})",
        ]
        if write_expr != "false":
            if table == "agent_question_cluster_vectors":
                # 内容表不能用 FOR ALL 写策略（会顺带放行 SELECT 向量）；
                # 维护操作只允许 INSERT/UPDATE/DELETE。
                for command in ("INSERT", "UPDATE", "DELETE"):
                    clause = (
                        f"WITH CHECK ({write_expr})"
                        if command == "INSERT"
                        else f"USING ({write_expr})"
                        if command == "DELETE"
                        else f"USING ({write_expr}) WITH CHECK ({write_expr})"
                    )
                    statements += [
                        f"DROP POLICY IF EXISTS insight_write_{command.lower()} ON {table}",
                        f"CREATE POLICY insight_write_{command.lower()} ON {table}"
                        f" FOR {command} TO {RUNTIME_ROLE} {clause}",
                    ]
            else:
                statements += [
                    f"DROP POLICY IF EXISTS insight_write ON {table}",
                    f"CREATE POLICY insight_write ON {table} FOR ALL TO {RUNTIME_ROLE}"
                    f" USING ({write_expr}) WITH CHECK ({write_expr})",
                ]

    if check(WORKER_ROLE):
        for table in runtime_grants:
            statements.append(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {WORKER_ROLE}")
        for table in (
            "agent_insight_jobs",
            "agent_insight_delegations",
        ):
            statements += [
                f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
                f"DROP POLICY IF EXISTS insight_worker ON {table}",
                f"CREATE POLICY insight_worker ON {table} FOR ALL TO {WORKER_ROLE}"
                f" USING ({worker}) WITH CHECK ({worker})",
            ]
        for table in (
            "agent_insight_profiles",
            "agent_insight_profile_state",
            "agent_question_sources",
            "agent_question_source_gaps",
            "agent_question_intents",
            "agent_question_intent_contents",
            "agent_question_topics",
            "agent_question_clusters",
            "agent_question_cluster_vectors",
            "agent_question_assignments",
        ):
            statements += [
                f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
                f"DROP POLICY IF EXISTS insight_worker ON {table}",
                f"CREATE POLICY insight_worker ON {table} FOR ALL TO {WORKER_ROLE}"
                f" USING ({tenant} AND {worker}) WITH CHECK ({tenant} AND {worker})",
            ]
        # 来源事实只读：worker 不修改台账；策略仍要求 tenant（任务内已设置）。
        for table in (
            "agent_requests",
            "agent_submissions",
            "agent_content_refs",
            "agent_thread_deletions",
            "agent_feedback_current",
        ):
            statements += [
                f"GRANT SELECT ON {table} TO {WORKER_ROLE}",
                f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
                f"DROP POLICY IF EXISTS insight_worker_read ON {table}",
                f"CREATE POLICY insight_worker_read ON {table} FOR SELECT TO {WORKER_ROLE}"
                f" USING ({tenant} AND {worker})",
            ]
        statements += [
            f"GRANT SELECT ON agent_iam_sessions TO {WORKER_ROLE}",
            "ALTER TABLE agent_iam_sessions ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE agent_iam_sessions FORCE ROW LEVEL SECURITY",
            "DROP POLICY IF EXISTS insight_worker_session ON agent_iam_sessions",
            "CREATE POLICY insight_worker_session ON agent_iam_sessions FOR SELECT"
            f" TO {WORKER_ROLE} USING (handle_hash ="
            " current_setting('air_agent.iam_handle_hash', true))",
            f"GRANT USAGE ON SCHEMA public TO {WORKER_ROLE}",
        ]
    return statements


def downgrade() -> None:
    raise NotImplementedError(
        "0028_demand_insights 不提供 downgrade：来源/簇/委托是审计事实；"
        "回退使用应用开关与 worker 停止，不自动丢弃已确认分析。"
    )
