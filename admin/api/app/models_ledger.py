"""Agent 事件台账 ORM 模型（独立域模块，对应迁移 `0007`）。

设计 §8：新域用独立模块，不把全部新表堆进 `models.py`。`Base` 复用 `app.models`
的同一 declarative base（同一 metadata），由 `app.models` 末尾导入本模块完成注册。

表结构以迁移 0007 为权威；这里只声明查询/写入所需的列与约束形状。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

UUIDColumn = UUID(as_uuid=True)

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
TERMINAL_STATES = ("succeeded", "failed", "cancelled", "timed_out")
CANCEL_ORIGINS = (
    "user_stop",
    "navigation",
    "tenant_switch",
    "transport_disconnect",
    "permission_revocation",
    "unknown",
)
OPERATION_KINDS = ("initial", "retry", "regenerate", "resume")


class AgentRequest(Base):
    """逻辑提问（可信受理的锚点；不含正文）。"""

    __tablename__ = "agent_requests"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_requests_id_tenant"),
        CheckConstraint(
            "operation_kind IN " + str(OPERATION_KINDS).replace("[", "(").replace("]", ")"),
            name="agent_requests_kind_valid",
        ),
        Index("idx_agent_requests_tenant_user", "tenant_id", "user_id", "admitted_at"),
        Index("idx_agent_requests_thread", "tenant_id", "thread_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_message_id: Mapped[str | None] = mapped_column(Text)
    graph_id: Mapped[str] = mapped_column(Text, nullable=False)
    operation_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="initial")
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    department_snapshot: Mapped[str | None] = mapped_column(Text)
    role_snapshot: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    identity_revision: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    admitted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentSubmission(Base):
    """提交幂等锚点：同成员+线程+提交键唯一。"""

    __tablename__ = "agent_submissions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "thread_id", "submission_key", name="uq_agent_submissions_key"
        ),
        CheckConstraint(
            "operation_kind IN " + str(OPERATION_KINDS).replace("[", "(").replace("]", ")"),
            name="agent_submissions_kind_valid",
        ),
        CheckConstraint(
            "admission_status IN ('admitting','admission_unknown','confirmed')",
            name="agent_submissions_status_valid",
        ),
        ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["agent_requests.id", "agent_requests.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_submissions_request_same_tenant",
        ),
        Index("idx_agent_submissions_thread", "tenant_id", "user_id", "thread_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    submission_key: Mapped[str] = mapped_column(Text, nullable=False)
    operation_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="initial")
    content_digest: Mapped[str] = mapped_column(Text, nullable=False)
    admission_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="admitting")
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    # R03：提交稳定绑定"自己那次操作的尝试"（重放不漂到同请求下最新尝试）
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    capability_selection_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    digest_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # R01：启动权按**提交**裁决（resume 复用同一尝试但每次是新提交，各有独立槽位）
    start_claimed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    start_claim_token: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    start_claim_source: Mapped[str | None] = mapped_column(Text)
    platform_run_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRunAttempt(Base):
    """运行尝试：状态、证据、修订与停止事实。"""

    __tablename__ = "agent_run_attempts"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_attempts_id_tenant"),
        UniqueConstraint("request_id", "attempt_index", name="uq_agent_attempts_index"),
        CheckConstraint(
            "state IN " + str(ATTEMPT_STATES).replace("[", "(").replace("]", ")"),
            name="agent_attempts_state_valid",
        ),
        ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["agent_requests.id", "agent_requests.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_attempts_request_same_tenant",
        ),
        Index(
            "uq_agent_attempts_platform_run",
            "tenant_id",
            "platform_run_id",
            unique=True,
            postgresql_where=text("platform_run_id IS NOT NULL"),
        ),
        Index("idx_agent_attempts_tenant_state", "tenant_id", "state", "admitted_at"),
        Index("idx_agent_attempts_reconcile", "state", "last_progress_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    attempt_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    source_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    capability_selection_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    graph_id: Mapped[str] = mapped_column(Text, nullable=False)
    # R01：预留比对依据（真实图在 graph_id；规范内容摘要 + 操作类型在此）
    operation_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="initial")
    content_digest: Mapped[str | None] = mapped_column(Text)
    start_claimed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    start_claim_token: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    start_claim_source: Mapped[str | None] = mapped_column(Text)
    platform_run_id: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="admitting")
    state_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    evidence_source: Mapped[str | None] = mapped_column(Text)
    platform_status: Mapped[str | None] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    cancel_origin: Mapped[str | None] = mapped_column(Text)
    cancel_phase: Mapped[str | None] = mapped_column(Text)
    cancel_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    transport_connected: Mapped[bool | None] = mapped_column(Boolean)
    waiting_kind: Mapped[str | None] = mapped_column(Text)
    admitted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    queued_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_progress_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRunResume(Base):
    """人工恢复记录：不增加提问量。"""

    __tablename__ = "agent_run_resumes"
    __table_args__ = (
        UniqueConstraint("attempt_id", "resume_index", name="uq_agent_resumes_index"),
        ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["agent_run_attempts.id", "agent_run_attempts.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_resumes_attempt_same_tenant",
        ),
        Index("idx_agent_resumes_tenant", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    resume_index: Mapped[int] = mapped_column(Integer, nullable=False)
    original_run_id: Mapped[str | None] = mapped_column(Text)
    platform_run_id: Mapped[str] = mapped_column(Text, nullable=False)
    input_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentAnswer(Base):
    """用户可见回答版本（草稿/推理/工具输出不入表）。"""

    __tablename__ = "agent_answers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["agent_run_attempts.id", "agent_run_attempts.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_answers_attempt_same_tenant",
        ),
        Index(
            "uq_agent_answers_message_version",
            "tenant_id",
            "attempt_id",
            "message_id",
            "content_version",
            unique=True,
            postgresql_where=text("message_id IS NOT NULL"),
        ),
        Index("idx_agent_answers_attempt", "tenant_id", "attempt_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    message_id: Mapped[str | None] = mapped_column(Text)
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    content_ref_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    partial: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentTraceLink(Base):
    """Trace 多重映射（非秘密逻辑标识）。"""

    __tablename__ = "agent_trace_links"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "project_scope",
            "trace_id",
            "attempt_id",
            name="uq_agent_trace_links",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["agent_run_attempts.id", "agent_run_attempts.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_trace_links_attempt_same_tenant",
        ),
        Index("idx_agent_trace_links_attempt", "tenant_id", "attempt_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    project_scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    link_source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="runtime_callback"
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentEvent(Base):
    """事件信封（服务端身份覆盖后的事实）。"""

    __tablename__ = "agent_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", name="uq_agent_events_tenant_event"),
        CheckConstraint(
            "source IN ('runtime','server','system','browser')", name="agent_events_source_valid"
        ),
        Index("idx_agent_events_tenant_time", "tenant_id", "occurred_at"),
        Index("idx_agent_events_attempt", "tenant_id", "attempt_id"),
        Index("idx_agent_events_type", "tenant_id", "event_type", "occurred_at"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="1.0")
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    thread_id: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    run_id: Mapped[str | None] = mapped_column(Text)
    answer_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    message_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    payload_digest: Mapped[str] = mapped_column(Text, nullable=False)
    state_revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentOutbox(Base):
    """事务 outbox（与业务事实同事务写入）。"""

    __tablename__ = "agent_outbox"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", "consumer", name="uq_agent_outbox_target"),
        CheckConstraint(
            "status IN ('pending','leased','done','dead_letter')", name="agent_outbox_status_valid"
        ),
        ForeignKeyConstraint(
            ["event_id", "tenant_id"],
            ["agent_events.event_id", "agent_events.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_outbox_event_same_tenant",
        ),
        Index("idx_agent_outbox_ready", "status", "available_at"),
        Index("idx_agent_outbox_tenant", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    consumer: Mapped[str] = mapped_column(Text, nullable=False)
    thread_id: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_deadline: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_class: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentConsumerReceipt(Base):
    """消费幂等 receipt（与投影更新同事务）。"""

    __tablename__ = "agent_consumer_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "consumer", "consumer_version", "event_id", name="uq_agent_receipts_target"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    consumer: Mapped[str] = mapped_column(Text, nullable=False)
    consumer_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    event_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    applied_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentContentRef(Base):
    """原问题受控快照（独立授权 + 保留期；不进入事件载荷）。"""

    __tablename__ = "agent_content_refs"
    __table_args__ = (
        Index("idx_agent_content_refs_tenant", "tenant_id", "created_at"),
        Index("idx_agent_content_refs_retention", "retention_until"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    # 交付缺口：原文按**请求**精确读取（不可漂移引用），不只靠可变 thread 指针
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    message_id: Mapped[str | None] = mapped_column(Text)
    content_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    content_snapshot: Mapped[str | None] = mapped_column(Text)
    retention_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentThreadDeletion(Base):
    """会话删除标记（立即禁读 + 异步可重试清理）。"""

    __tablename__ = "agent_thread_deletions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "thread_id", name="uq_agent_deletions_thread"),
        CheckConstraint(
            "state IN ('requested','in_progress','completed','failed')",
            name="agent_deletions_state_valid",
        ),
        Index("idx_agent_deletions_open", "state", "requested_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    # R05：线程归属人（删除归属校验 + 最小权限 worker 的受限清理作用域）
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="requested")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error_class: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class AgentProjectionState(Base):
    """投影/口径版本状态（summary 的 as_of / projected_at / data_lag 来源）。"""

    __tablename__ = "agent_projection_state"
    __table_args__ = ()

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    consumer: Mapped[str] = mapped_column(Text, primary_key=True)
    consumer_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    metric_version: Mapped[str] = mapped_column(Text, nullable=False, server_default="v1")
    last_event_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    projected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentCapabilitySelection(Base):
    """用户授权能力选择快照（Change 4 任务 3.2）：在受理时冻结，不可篡改。"""

    __tablename__ = "agent_capability_selections"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_selections_id_tenant"),
        UniqueConstraint(
            "tenant_id", "submission_id", name="uq_agent_selections_tenant_submission"
        ),
        CheckConstraint(
            "skill_mode IN ('auto', 'explicit')",
            name="ck_agent_selections_skill_mode",
        ),
        CheckConstraint(
            "mcp_mode IN ('auto', 'allowlist')",
            name="ck_agent_selections_mcp_mode",
        ),
        CheckConstraint(
            "selection_source IN ('user_explicit', 'automatic')",
            name="ck_agent_selections_source",
        ),
        ForeignKeyConstraint(
            ["submission_id", "tenant_id"],
            ["agent_submissions.id", "agent_submissions.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_selections_submission_same_tenant",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "tenant_id"],
            ["agent_run_attempts.id", "agent_run_attempts.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_selections_attempt_same_tenant",
        ),
        ForeignKeyConstraint(
            ["request_id", "tenant_id"],
            ["agent_requests.id", "agent_requests.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_selections_request_same_tenant",
        ),
        ForeignKeyConstraint(
            ["membership_id", "tenant_id"],
            ["tenant_memberships.id", "tenant_memberships.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_selections_membership_same_tenant",
        ),
        Index("idx_agent_selections_tenant_user", "tenant_id", "user_id"),
        Index("idx_agent_selections_attempt", "tenant_id", "attempt_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    submission_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    graph_id: Mapped[str] = mapped_column(Text, nullable=False)
    skill_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="auto")
    skill_id: Mapped[str | None] = mapped_column(Text)
    mcp_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="auto")
    tool_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    catalog_version: Mapped[str | None] = mapped_column(Text)
    material_version: Mapped[str] = mapped_column(Text, nullable=False)
    selection_digest: Mapped[str] = mapped_column(Text, nullable=False)
    digest_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2")
    selection_source: Mapped[str] = mapped_column(Text, nullable=False, server_default="automatic")
    validation_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
