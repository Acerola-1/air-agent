"""台账请求/响应模型（独立域模块）。"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

OperationKind = Literal["initial", "retry", "regenerate", "resume"]
CancelOrigin = Literal[
    "user_stop",
    "navigation",
    "tenant_switch",
    "transport_disconnect",
    "permission_revocation",
    "unknown",
]
CancelPhase = Literal["queued", "processing", "output", "unknown"]
EvidenceSource = Literal["platform_read", "runtime_callback", "browser_observed"]


class AdmitRequestIn(BaseModel):
    """受理请求。`content` 只用于计算规范摘要与（可选）受控原文快照，不进入事件。"""

    thread_id: str = Field(min_length=1, max_length=128)
    submission_key: str = Field(min_length=8, max_length=128)
    graph_id: str = Field(min_length=1, max_length=64)
    operation_kind: OperationKind = "initial"
    user_message_id: str | None = Field(default=None, max_length=128)
    content: str = Field(default="", max_length=20000)
    source_attempt_id: uuid.UUID | None = None
    store_content: bool = False
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class AdmissionResultOut(BaseModel):
    request_id: str
    attempt_id: str
    submission_id: str
    admission_status: str
    operation_kind: str
    reused: bool
    thread_id: str
    graph_id: str
    ledger_marker: str


class StartClaimIn(BaseModel):
    """受控入口的启动权裁决请求（R01）：一次业务提交只能启动一次平台运行。"""

    submission_id: uuid.UUID
    attempt_id: uuid.UUID
    thread_id: str = Field(min_length=1, max_length=128)
    graph_id: str = Field(min_length=1, max_length=64)
    operation_kind: OperationKind = "initial"
    content: str = Field(default="", max_length=20000)


class StartClaimOut(BaseModel):
    status: str
    start_claim_token: str | None = None
    platform_run_id: str | None = None
    state: str | None = None
    detail: str | None = None
    reserved_graph_id: str | None = None
    reserved_operation_kind: str | None = None


class AdmissionConfirmIn(BaseModel):
    request_id: uuid.UUID
    attempt_id: uuid.UUID
    # F02：确认必须落到**本次提交**；恢复与初次共享 attempt 时，按 attempt 批量
    # 改写会把别的操作一起改掉。缺省仅为兼容旧调用（按 attempt 回退）。
    submission_id: uuid.UUID | None = None
    outcome: Literal["confirmed", "admission_unknown"]
    platform_run_id: str | None = Field(default=None, max_length=128)
    reason: str | None = Field(default=None, max_length=200)


class StopIntentIn(BaseModel):
    attempt_id: uuid.UUID | None = None
    thread_id: str | None = Field(default=None, max_length=128)
    origin: CancelOrigin
    phase: CancelPhase
    platform_run_id: str | None = Field(default=None, max_length=128)


class StateObservedIn(BaseModel):
    state: str = Field(min_length=1, max_length=32)
    evidence_source: EvidenceSource
    platform_status: str | None = Field(default=None, max_length=64)
    state_revision: int | None = None


class ResumeIn(BaseModel):
    platform_run_id: str = Field(min_length=1, max_length=128)


class AnswerIn(BaseModel):
    message_id: str | None = Field(default=None, max_length=128)
    content_version: int = Field(default=1, ge=1)
    confirmed: bool = True


class TraceLinkIn(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    trace_id: str = Field(min_length=1, max_length=256)
    project_scope: str = Field(default="default", max_length=128)
    primary: bool = False
    link_source: str = Field(default="runtime_callback", max_length=64)


class BrowserEventIn(BaseModel):
    """浏览器事件（逐项结果）；v1 预留给第 3 项，当前仅类型白名单会全部拒绝。"""

    events: list[dict[str, Any]] = Field(default_factory=list)


class BrowserEventOut(BaseModel):
    accepted: bool
    batch_reason: str
    items: list[dict[str, Any]]
    written: int = 0


class ThreadDeletionIn(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)


class RunListOut(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None
    limit: int
    content_included: bool = False


class ReconcileTicketIn(BaseModel):
    """F05：服务级只读校准票据申请（内部端点，绑定租户 + 可选线程收窄）."""

    tenant_id: uuid.UUID
    thread_ids: list[str] = Field(default_factory=list, max_length=50)
    ttl_seconds: int = Field(default=600, ge=60, le=3600)


class ReconcileTicketOut(BaseModel):
    ticket: str
    scope: str
    tenant_id: str
    expires_in: int
    thread_ids: list[str]
