"""运营后台读模型 ORM（迁移 `0024_console_read_model`）.

只声明查询/写入所需形状；表结构以迁移为权威。分域模块避免继续膨胀
`models.py`，注册方式与 `models_ledger` / `models_feedback` 一致。
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

UUIDColumn = UUID(as_uuid=True)

CASE_STATES = ("pending", "in_progress", "resolved")
CASE_ACTIONS = ("start", "resolve", "reopen")
NOTE_MAX_CHARS = 1000
ANSWER_MAX_CHARS = 65536


class AgentAnswerContent(Base):
    """确认回答的受控正文快照（只含用户可见文本）."""

    __tablename__ = "agent_answer_contents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "answer_id", "content_version", name="uq_agent_answer_contents_version"
        ),
        CheckConstraint(
            "capture_status IN ('captured','capture_failed')",
            name="agent_answer_contents_status_valid",
        ),
        CheckConstraint("total_chars >= 0", name="agent_answer_contents_total_chars_valid"),
        ForeignKeyConstraint(
            ["answer_id", "tenant_id"],
            ["agent_answers.id", "agent_answers.tenant_id"],
            ondelete="CASCADE",
            name="fk_answer_contents_answer_same_tenant",
        ),
        Index(
            "idx_agent_answer_contents_request", "tenant_id", "request_id", "content_version"
        ),
        Index("idx_agent_answer_contents_thread", "tenant_id", "thread_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    answer_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    message_id: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    content_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    total_chars: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    capture_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="captured")
    captured_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    retention_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentFeedbackCase(Base):
    """每答案一条运营处理状态（与用户评价修订独立）."""

    __tablename__ = "agent_feedback_cases"
    __table_args__ = (
        UniqueConstraint("id", "tenant_id", name="uq_agent_feedback_cases_id_tenant"),
        UniqueConstraint("tenant_id", "answer_id", name="uq_agent_feedback_cases_answer"),
        CheckConstraint(
            "state IN ('pending','in_progress','resolved')",
            name="agent_feedback_cases_state_valid",
        ),
        ForeignKeyConstraint(
            ["answer_id", "tenant_id"],
            ["agent_answers.id", "agent_answers.tenant_id"],
            ondelete="CASCADE",
            name="fk_feedback_cases_answer_same_tenant",
        ),
        Index("idx_agent_feedback_cases_tenant_state", "tenant_id", "state", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    answer_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    reviewed_feedback_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    handled_by: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    handled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_note_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentFeedbackCaseAction(Base):
    """处理操作幂等历史（无正文）."""

    __tablename__ = "agent_feedback_case_actions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "operation_id", name="uq_agent_feedback_case_actions_operation"
        ),
        CheckConstraint(
            "action IN ('start','resolve','reopen')",
            name="agent_feedback_case_actions_action_valid",
        ),
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["agent_feedback_cases.id", "agent_feedback_cases.tenant_id"],
            ondelete="CASCADE",
            name="fk_feedback_case_actions_case_same_tenant",
        ),
        Index("idx_agent_feedback_case_actions_case", "tenant_id", "case_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    answer_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    operation_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    from_state: Mapped[str | None] = mapped_column(Text)
    to_state: Mapped[str] = mapped_column(Text, nullable=False)
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    result_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    actor_account: Mapped[str] = mapped_column(Text, nullable=False)
    payload_digest: Mapped[str] = mapped_column(Text, nullable=False)
    note_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    result_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentFeedbackCaseNote(Base):
    """处理说明正文（独立授权读取；不进普通事件/日志）."""

    __tablename__ = "agent_feedback_case_notes"
    __table_args__ = (
        CheckConstraint(
            f"char_length(note_text) <= {NOTE_MAX_CHARS}",
            name="agent_feedback_case_notes_len_valid",
        ),
        ForeignKeyConstraint(
            ["case_id", "tenant_id"],
            ["agent_feedback_cases.id", "agent_feedback_cases.tenant_id"],
            ondelete="CASCADE",
            name="fk_feedback_case_notes_case_same_tenant",
        ),
        Index("idx_agent_feedback_case_notes_case", "tenant_id", "case_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    answer_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    note_text: Mapped[str] = mapped_column(Text, nullable=False)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    retention_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
