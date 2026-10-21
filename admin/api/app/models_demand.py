"""需求洞察域 ORM（迁移 `0028_demand_insights`）.

表结构以迁移为权威。物理上把“可聚合元数据”和“受限内容/向量”分表：

- `agent_question_intents` / `agent_question_clusters`：不含原句与向量，统计权限
  可读聚合所需字段；
- `agent_question_intent_contents` / `agent_question_cluster_vectors`：仅
  ops+content 可读，worker 写入；
- 这样 metrics 响应不需要在应用层“假装隐藏”原文/向量。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

UUIDColumn = UUID(as_uuid=True)
RealArray = ARRAY(REAL)

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


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class AgentInsightProfile(Base):
    __tablename__ = "agent_insight_profiles"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_insight_profiles_id_tenant"),
        UniqueConstraint(
            "tenant_id", "profile_key", "profile_version", name="uq_agent_insight_profiles_key"
        ),
        CheckConstraint(_in("state", PROFILE_STATES), name="agent_insight_profiles_state_valid"),
        CheckConstraint(
            "embedding_dimension >= 1 AND embedding_dimension <= 4096",
            name="agent_insight_profiles_dimension_valid",
        ),
        Index("idx_agent_insight_profiles_tenant", "tenant_id", "state", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    profile_key: Mapped[str] = mapped_column(Text, nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    normalizer_version: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_version: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_profile: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_profile: Mapped[str | None] = mapped_column(Text)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    calibration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class AgentInsightProfileState(Base):
    __tablename__ = "agent_insight_profile_state"
    __table_args__ = (
        ForeignKeyConstraint(
            ["active_profile_id", "tenant_id"],
            ["agent_insight_profiles.id", "agent_insight_profiles.tenant_id"],
            ondelete="RESTRICT",
            name="fk_insight_profile_state_active_same_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    active_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    assignment_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentInsightJob(Base):
    __tablename__ = "agent_insight_jobs"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_insight_jobs_id_tenant"),
        CheckConstraint(_in("job_kind", JOB_KINDS), name="agent_insight_jobs_kind_valid"),
        CheckConstraint(_in("state", JOB_STATES), name="agent_insight_jobs_state_valid"),
        ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["agent_insight_profiles.id", "agent_insight_profiles.tenant_id"],
            ondelete="RESTRICT",
            name="fk_insight_jobs_profile_same_tenant",
        ),
        ForeignKeyConstraint(
            ["delegation_id", "tenant_id"],
            ["agent_insight_delegations.id", "agent_insight_delegations.tenant_id"],
            ondelete="RESTRICT",
            name="fk_insight_jobs_delegation_same_tenant",
        ),
        Index("idx_agent_insight_jobs_tenant", "tenant_id", "created_at"),
        Index("idx_agent_insight_jobs_claim", "state", "lease_deadline", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    delegation_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    job_kind: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    window_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    as_of: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    max_sources: Mapped[int] = mapped_column(Integer, nullable=False, server_default="500")
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="200000")
    authorization_expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    processed_sources: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    classified_intents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    unclassified_intents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failed_sources: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    estimated_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    actual_tokens: Mapped[int | None] = mapped_column(BigInteger)
    usage_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_deadline: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    fence: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    cursor_admitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    cursor_request_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error_code: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    continuous: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    predecessor_job_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class AgentInsightDelegation(Base):
    __tablename__ = "agent_insight_delegations"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_insight_delegations_id_tenant"),
        CheckConstraint(
            _in("status", ("active", "expired", "revoked")),
            name="agent_insight_delegations_status_valid",
        ),
        CheckConstraint(
            "expires_at <= issued_at + interval '12 hours'",
            name="agent_insight_delegations_max_window",
        ),
        Index("idx_agent_insight_delegations_expiry", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    session_handle_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ctx_version: Mapped[int] = mapped_column(Integer, nullable=False)
    user_auth_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_auth_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    membership_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    issued_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class AgentQuestionSource(Base):
    __tablename__ = "agent_question_sources"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_question_sources_id_tenant"),
        UniqueConstraint(
            "tenant_id",
            "request_id",
            "source_content_version",
            "analysis_version",
            name="uq_agent_question_sources_version",
        ),
        CheckConstraint(_in("status", SOURCE_STATUSES), name="agent_question_sources_status_valid"),
        ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["agent_requests.id", "agent_requests.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_sources_request_same_tenant",
        ),
        ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["agent_insight_profiles.id", "agent_insight_profiles.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_sources_profile_same_tenant",
        ),
        Index("idx_agent_question_sources_window", "tenant_id", "admitted_at", "request_id"),
        Index("idx_agent_question_sources_status", "tenant_id", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    graph_id: Mapped[str] = mapped_column(Text, nullable=False)
    business_code: Mapped[str | None] = mapped_column(Text)
    admitted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_message_id: Mapped[str | None] = mapped_column(Text)
    source_content_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    retention_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="processing")
    failure_code: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_version: Mapped[str] = mapped_column(Text, nullable=False)
    split_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentQuestionSourceGap(Base):
    __tablename__ = "agent_question_source_gaps"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "request_id", "analysis_version", name="uq_agent_question_gaps_request"
        ),
        CheckConstraint(_in("reason", GAP_REASONS), name="agent_question_gaps_reason_valid"),
        ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["agent_requests.id", "agent_requests.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_gaps_request_same_tenant",
        ),
        ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["agent_insight_profiles.id", "agent_insight_profiles.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_gaps_profile_same_tenant",
        ),
        Index("idx_agent_question_gaps_reason", "tenant_id", "reason", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    admitted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentQuestionIntent(Base):
    __tablename__ = "agent_question_intents"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_question_intents_id_tenant"),
        UniqueConstraint(
            "source_id", "split_index", "analysis_version", name="uq_agent_question_intents_split"
        ),
        CheckConstraint(
            "split_index >= 0 AND split_index <= 4", name="agent_question_intents_index"
        ),
        CheckConstraint(
            "embedding_dimension >= 1 AND embedding_dimension <= 4096",
            name="agent_question_intents_dimension",
        ),
        ForeignKeyConstraint(
            ["source_id", "tenant_id"],
            ["agent_question_sources.id", "agent_question_sources.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_intents_source_same_tenant",
        ),
        ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["agent_requests.id", "agent_requests.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_intents_request_same_tenant",
        ),
        Index("idx_agent_question_intents_source", "tenant_id", "source_id"),
        Index("idx_agent_question_intents_window", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    split_index: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    object: Mapped[str] = mapped_column(Text, nullable=False)
    time_kind: Mapped[str] = mapped_column(Text, nullable=False)
    negation: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    slots: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    normalizer_version: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_version: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_profile: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    content_expiry: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentQuestionIntentContent(Base):
    __tablename__ = "agent_question_intent_contents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["intent_id", "tenant_id"],
            ["agent_question_intents.id", "agent_question_intents.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_intent_contents_intent_same_tenant",
        ),
        CheckConstraint(
            "array_length(embedding, 1) = embedding_dimension",
            name="agent_question_intent_contents_dimension",
        ),
    )

    intent_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(RealArray, nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentQuestionTopic(Base):
    __tablename__ = "agent_question_topics"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_question_topics_id_tenant"),
        UniqueConstraint("tenant_id", "profile_id", "code", name="uq_agent_question_topics_code"),
        CheckConstraint(
            _in("status", ("active", "retired")), name="agent_question_topics_status_valid"
        ),
        ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["agent_insight_profiles.id", "agent_insight_profiles.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_topics_profile_same_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name_zh: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentQuestionCluster(Base):
    __tablename__ = "agent_question_clusters"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_question_clusters_id_tenant"),
        CheckConstraint(
            _in("status", CLUSTER_STATUSES), name="agent_question_clusters_status_valid"
        ),
        ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["agent_insight_profiles.id", "agent_insight_profiles.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_clusters_profile_same_tenant",
        ),
        ForeignKeyConstraint(
            ["topic_id", "tenant_id"],
            ["agent_question_topics.id", "agent_question_topics.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_clusters_topic_same_tenant",
        ),
        ForeignKeyConstraint(
            ["merged_into", "tenant_id"],
            ["agent_question_clusters.id", "agent_question_clusters.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_clusters_merge_same_tenant",
        ),
        Index(
            "idx_agent_question_clusters_list", "tenant_id", "profile_id", "status", "created_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    object: Mapped[str] = mapped_column(Text, nullable=False)
    time_kind: Mapped[str] = mapped_column(Text, nullable=False)
    negation: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="candidate")
    merged_into: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    representative_intent_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentQuestionClusterVector(Base):
    __tablename__ = "agent_question_cluster_vectors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["cluster_id", "tenant_id"],
            ["agent_question_clusters.id", "agent_question_clusters.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_cluster_vectors_cluster_same_tenant",
        ),
        CheckConstraint(
            "array_length(prototype, 1) = dimension",
            name="agent_question_cluster_vectors_dimension",
        ),
    )

    cluster_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    prototype: Mapped[list[float]] = mapped_column(RealArray, nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentQuestionAssignment(Base):
    __tablename__ = "agent_question_assignments"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_question_assignments_id_tenant"),
        CheckConstraint(
            "(cluster_id IS NOT NULL) <> (unclassified_reason IS NOT NULL)",
            name="agent_question_assignments_target_valid",
        ),
        CheckConstraint(
            _in("method", ASSIGNMENT_METHODS), name="agent_question_assignments_method_valid"
        ),
        CheckConstraint(
            _in("status", ASSIGNMENT_STATUSES), name="agent_question_assignments_status_valid"
        ),
        ForeignKeyConstraint(
            ["intent_id", "tenant_id"],
            ["agent_question_intents.id", "agent_question_intents.tenant_id"],
            ondelete="CASCADE",
            name="fk_question_assignments_intent_same_tenant",
        ),
        ForeignKeyConstraint(
            ["cluster_id", "tenant_id"],
            ["agent_question_clusters.id", "agent_question_clusters.tenant_id"],
            ondelete="RESTRICT",
            name="fk_question_assignments_cluster_same_tenant",
        ),
        Index(
            "uq_agent_question_assignments_active",
            "tenant_id",
            "intent_id",
            "profile_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "idx_agent_question_assignments_cluster",
            "tenant_id",
            "profile_id",
            "cluster_id",
            "assignment_revision",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    intent_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    unclassified_reason: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    assignment_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentInsightOperation(Base):
    __tablename__ = "agent_insight_operations"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_insight_operations_id_tenant"),
        UniqueConstraint("tenant_id", "operation_id", name="uq_agent_insight_operations_operation"),
        Index("idx_agent_insight_operations_target", "tenant_id", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    operation_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    result_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_digest: Mapped[str] = mapped_column(Text, nullable=False)
    result_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    actor_account: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
