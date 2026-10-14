"""回答反馈 ORM 模型（独立域模块，对应迁移 `0011_feedback`）。

设计（change add-chat-feedback-interactions §1）：
- `agent_feedback_current`：同一 (tenant, user, answer) 唯一当前评价；
- `agent_feedback_operations`：操作 ID 幂等的不可变历史；
- `agent_feedback_comments`：意见正文独立受限存储（不进事件/Trace/审计）。

表结构以迁移 0011_feedback 为权威；这里只声明查询/写入所需的列与约束形状。
`Base` 复用 `app.models` 的同一 declarative base，由 `app.models` 末尾导入本模块完成注册。
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

UUIDColumn = UUID(as_uuid=True)

RATINGS = ("positive", "negative", "none")
REASON_CODES = ("answer_incorrect", "irrelevant", "incomplete", "slow", "other")
COMMENT_MAX_CHARS = 1000


class AgentFeedbackCurrent(Base):
    """当前有效评价（每答案一行；rating='none' 表示已撤销）。"""

    __tablename__ = "agent_feedback_current"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "answer_id", name="uq_agent_feedback_current_target"
        ),
        CheckConstraint(
            "rating IN ('positive','negative','none')", name="agent_feedback_current_rating_valid"
        ),
        CheckConstraint(
            "reason_code IS NULL OR reason_code IN"
            " ('answer_incorrect','irrelevant','incomplete','slow','other')",
            name="agent_feedback_current_reason_valid",
        ),
        ForeignKeyConstraint(
            ["answer_id", "tenant_id"],
            ["agent_answers.id", "agent_answers.tenant_id"],
            ondelete="CASCADE",
            name="fk_feedback_current_answer",
        ),
        Index("idx_agent_feedback_current_tenant_time", "tenant_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    answer_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(Text)
    comment_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # 反馈随回答删除级联删除（复合外键保证同租户）。
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentFeedbackOperation(Base):
    """已受理反馈操作的不可变历史（operation_id 幂等锚点）。"""

    __tablename__ = "agent_feedback_operations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "operation_id", name="uq_agent_feedback_operations_id"
        ),
        CheckConstraint(
            "rating IN ('positive','negative','none')",
            name="agent_feedback_operations_rating_valid",
        ),
        CheckConstraint(
            "reason_code IS NULL OR reason_code IN"
            " ('answer_incorrect','irrelevant','incomplete','slow','other')",
            name="agent_feedback_operations_reason_valid",
        ),
        ForeignKeyConstraint(
            ["answer_id", "tenant_id"],
            ["agent_answers.id", "agent_answers.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_feedback_operations_answer_same_tenant",
        ),
        Index("idx_agent_feedback_operations_tenant_time", "tenant_id", "received_at"),
        Index("idx_agent_feedback_operations_answer", "tenant_id", "answer_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    answer_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    operation_id: Mapped[str] = mapped_column(Text, nullable=False)
    intent_digest: Mapped[str | None] = mapped_column(Text)
    result_snapshot: Mapped[dict | None] = mapped_column(JSON)
    rating: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(Text)
    comment_id: Mapped[uuid.UUID | None] = mapped_column(UUIDColumn)
    # 受理途径：feedback_put（专用端点）或 browser_event（通用浏览器端点）
    accepted_via: Mapped[str] = mapped_column(Text, nullable=False, server_default="feedback_put")
    expected_revision: Mapped[int | None] = mapped_column(Integer)
    result_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    # 浏览器低信任操作时间；受理/修订一律用服务端时间
    client_occurred_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentFeedbackComment(Base):
    """意见正文（独立授权读取；不进入事件/Trace/普通审计）。"""

    __tablename__ = "agent_feedback_comments"
    __table_args__ = (
        CheckConstraint(
            f"char_length(comment_text) <= {COMMENT_MAX_CHARS}",
            name="agent_feedback_comments_len_valid",
        ),
        ForeignKeyConstraint(
            ["answer_id", "tenant_id"],
            ["agent_answers.id", "agent_answers.tenant_id"],
            ondelete="CASCADE",
            name="fk_agent_feedback_comments_answer_same_tenant",
        ),
        Index("idx_agent_feedback_comments_tenant", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    answer_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUIDColumn, nullable=False)
    thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    comment_text: Mapped[str] = mapped_column(Text, nullable=False)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
