"""运营后台 v1 请求/响应 DTO（对应 api-contract.md）.

查询参数在路由层用 FastAPI 校验；这里集中定义写操作与共享枚举，避免前端
与后端对状态/动作字符串各自硬编码。
"""

from __future__ import annotations

import uuid
from typing import Generic, Literal, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, NonNegativeInt

WindowKind = Literal["today", "last_7_days", "last_30_days", "custom"]
FeedbackRating = Literal["positive", "negative", "none"]
FeedbackReason = Literal["answer_incorrect", "irrelevant", "incomplete", "slow", "other"]
CaseState = Literal["pending", "in_progress", "resolved"]
CaseAction = Literal["start", "resolve", "reopen"]
CapabilityGroup = Literal["business", "skill", "tool"]
CostGroup = Literal["model", "user", "business", "currency"]
PerformanceSource = Literal["all", "service", "browser", "model"]
HistoryKind = Literal["rating", "case"]
ContentKind = Literal["comment", "note"]


class FeedbackCaseActionIn(BaseModel):
    """反馈处理写操作（operation_id 做幂等；两个修订号都做 CAS）."""

    operation_id: str = Field(min_length=36, max_length=36)
    expected_revision: int = Field(strict=True, ge=0, le=1_000_000)
    expected_feedback_revision: int = Field(strict=True, ge=0, le=1_000_000)
    action: CaseAction
    note: str | None = Field(default=None, max_length=1000)


class FeedbackCaseActionOut(BaseModel):
    answer_id: uuid.UUID
    case_id: uuid.UUID
    operation_id: uuid.UUID
    action: CaseAction
    from_state: str | None
    state: CaseState
    revision: int
    feedback_revision: int
    reviewed_feedback_revision: int
    needs_review: bool
    handled_by: uuid.UUID
    handled_at: str
    note_ref: str | None
    replayed: bool


# 返回模型显式列出字段：防止查询重构时把正文或内部列顺手带入普通响应。


class ConsoleModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConsoleWindowOut(ConsoleModel):
    start: AwareDatetime
    end: AwareDatetime
    timezone: Literal["Asia/Shanghai"]
    kind: str
    partial_day: bool


class ConsoleScopeOut(ConsoleModel):
    tenant_id: uuid.UUID
    context_version: NonNegativeInt


class ConsoleMeta(ConsoleModel):
    schema_version: Literal["admin-console-v1"]
    metric_version: str
    as_of: AwareDatetime
    window: ConsoleWindowOut
    filters: dict[str, JsonValue]
    scope: ConsoleScopeOut
    projected_at: AwareDatetime | None
    data_lag_seconds: float | None
    measurement_source: str
    status: str
    limitations: list[str]


class ConsoleMetric(ConsoleModel):
    value: int | float | str | None
    status: Literal[
        "measured", "partial", "not_collected", "unknown", "estimated", "not_applicable"
    ]
    numerator: int | float | None = None
    denominator: int | float | None = None
    sample_count: NonNegativeInt | None = None
    missing_count: NonNegativeInt = 0
    measurement_source: str
    cohort: str
    note: str | None = None
    reason: str | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    quantization: str | None = None
    excluded_from_default_cohort: bool = False
    not_collected_count: NonNegativeInt = 0
    invalid_count: NonNegativeInt = 0


class ConsoleCostSummary(ConsoleModel):
    by_currency: dict[str, str]
    unpriced_calls: ConsoleMetric
    usage_incomplete_calls: ConsoleMetric
    complete_cost_known: bool
    measurement_source: str


class ConsoleOverview(ConsoleModel):
    meta: ConsoleMeta
    requests: dict[str, ConsoleMetric]
    attempts: dict[str, ConsoleMetric]
    feedback: dict[str, ConsoleMetric]
    content: dict[str, ConsoleMetric]
    usage: dict[str, ConsoleMetric]
    cost: ConsoleCostSummary
    performance: dict[str, ConsoleMetric]
    trace: dict[str, ConsoleMetric]


class ConsoleTrendDay(ConsoleModel):
    day: str
    requests: NonNegativeInt
    active_users: NonNegativeInt
    attempts: NonNegativeInt
    succeeded: NonNegativeInt
    failed: NonNegativeInt
    cancelled: NonNegativeInt
    confirmed_answers: NonNegativeInt
    captured_answers: NonNegativeInt
    positive: NonNegativeInt
    negative: NonNegativeInt
    withdrawn: NonNegativeInt
    model_calls: NonNegativeInt
    measured_calls: NonNegativeInt
    tokens: NonNegativeInt | None
    cost_by_currency: dict[str, str]
    unpriced_calls: NonNegativeInt


class ConsoleTrends(ConsoleModel):
    meta: ConsoleMeta
    metric: str
    bucket_timezone: Literal["Asia/Shanghai"]
    items: list[ConsoleTrendDay]
    cohorts: dict[str, str]


Item = TypeVar("Item")


class ConsoleList(ConsoleModel, Generic[Item]):  # noqa: UP046  Pydantic泛型与副本名称保持稳定
    meta: ConsoleMeta
    items: list[Item]
    next_cursor: str | None
    has_more: bool
    limit: int = Field(ge=1, le=200)


class ConsoleUserUsage(ConsoleModel):
    user_id: uuid.UUID
    display_name: str
    requests: NonNegativeInt
    threads: NonNegativeInt
    attempts: NonNegativeInt
    succeeded: NonNegativeInt
    unsuccessful: NonNegativeInt
    model_calls: NonNegativeInt
    logical_calls: NonNegativeInt
    total_tokens: NonNegativeInt | None
    usage_missing_calls: NonNegativeInt
    cost_by_currency: dict[str, str]
    unpriced_cost_calls: NonNegativeInt
    last_active_at: AwareDatetime | None
    measurement_source: str


ContentStatus = Literal["available", "truncated", "capture_failed", "not_collected", "expired"]


class ConsoleAnswerRef(ConsoleModel):
    answer_id: uuid.UUID
    content_version: int = Field(ge=1)
    confirmed: bool
    confirmed_at: AwareDatetime | None
    content_status: ContentStatus
    truncated: bool


class ConsoleRun(ConsoleModel):
    attempt_id: uuid.UUID
    request_id: uuid.UUID
    thread_id: str
    graph_id: str
    business_code: str
    user_id: uuid.UUID
    display_name: str
    state: str
    state_revision: NonNegativeInt
    evidence_source: str | None
    platform_run_id: str | None
    cancel_requested: bool
    cancel_origin: str | None
    cancel_confirmed: bool
    admitted_at: AwareDatetime
    started_at: AwareDatetime | None
    ended_at: AwareDatetime | None
    resume_count: NonNegativeInt
    answer: ConsoleAnswerRef | None
    content_included: Literal[False]


class ConsoleAttempt(ConsoleModel):
    attempt_id: uuid.UUID
    request_id: uuid.UUID
    thread_id: str
    user_message_id: str | None
    graph_id: str
    business_code: str
    operation_kind: str
    state: str
    state_revision: NonNegativeInt
    evidence_source: str | None
    platform_status: str | None
    platform_run_id: str | None
    cancel_requested: bool
    cancel_origin: str | None
    cancel_phase: str | None
    cancel_confirmed: bool
    transport_connected: bool | None
    waiting_kind: str | None
    content_digest: str | None
    admitted_at: AwareDatetime
    queued_at: AwareDatetime | None
    started_at: AwareDatetime | None
    ended_at: AwareDatetime | None
    last_progress_at: AwareDatetime
    updated_at: AwareDatetime
    user_id: uuid.UUID
    display_name: str
    department_snapshot: str | None
    role_snapshot: list[JsonValue]
    request_admitted_at: AwareDatetime


class ConsoleAnswerMetadata(ConsoleModel):
    answer_id: uuid.UUID
    message_id: str | None
    content_version: int = Field(ge=1)
    partial: bool
    confirmed: bool
    confirmed_at: AwareDatetime | None
    created_at: AwareDatetime
    snapshot_id: uuid.UUID | None
    content_sha256: str | None
    total_chars: NonNegativeInt | None
    truncated: bool | None
    capture_status: str | None
    captured_at: AwareDatetime | None
    retention_until: AwareDatetime | None
    availability: ContentStatus
    content_included: Literal[False]


class ConsoleResume(ConsoleModel):
    resume_index: int
    platform_run_id: str | None
    original_run_id: str | None
    created_at: AwareDatetime
    user_id: uuid.UUID


class ConsoleTraceRef(ConsoleModel):
    provider: str
    project_scope: str
    trace_id: str
    is_primary: bool
    link_source: str


class ConsoleSelection(ConsoleModel):
    selection_status: str | None = None
    selection_id: uuid.UUID | None = None
    skill_mode: str | None = None
    skill_id: str | None = None
    mcp_mode: str | None = None
    tool_ids: list[str] | None = None
    material_version: str | None = None
    catalog_version: str | None = None
    selection_digest: str | None = None
    digest_version: int | None = None
    selection_source: str | None = None
    validation_time: AwareDatetime | None = None


class ConsoleObservationSummary(ConsoleModel):
    observations: NonNegativeInt
    logical_calls: NonNegativeInt
    measured: NonNegativeInt
    usage_missing: NonNegativeInt
    total_tokens: NonNegativeInt | None
    failed: NonNegativeInt
    ttft_samples: NonNegativeInt
    details_url: str
    content_included: Literal[False]


class ConsoleRunDetail(ConsoleModel):
    meta: ConsoleMeta
    attempt: ConsoleAttempt
    answers: list[ConsoleAnswerMetadata]
    resumes: list[ConsoleResume]
    traces: list[ConsoleTraceRef]
    capability_selection: ConsoleSelection
    observations: ConsoleObservationSummary


class ConsoleContentRef(ConsoleModel):
    ref: str
    created_at: AwareDatetime | None
    actor_user_id: uuid.UUID
    deleted: bool
    content_included: Literal[False]


class ConsoleContentPage(ConsoleModel):
    status: ContentStatus
    offset: NonNegativeInt
    next_offset: NonNegativeInt | None
    total_chars: NonNegativeInt
    text: str | None
    content_type: Literal["text/plain"] = "text/plain"
    cache_control: Literal["no-store"]
    content_version: int | None = None
    content_sha256: str | None = None
    summary: str | None = None
    truncated: bool | None = None
    capture_status: str | None = None
    message: str | None = None
    kind: ContentKind | None = None
    ref: str | None = None
    actor_user_id: uuid.UUID | None = None
    items: list[ConsoleContentRef] | None = None
    has_more: bool | None = None
    request_id: uuid.UUID | None = None
    answer_id: uuid.UUID | None = None
    attempt_id: uuid.UUID | None = None
    thread_id: str | None = None
    graph_id: str | None = None
    business_code: str | None = None
    message_id: str | None = None
    admitted_at: AwareDatetime | None = None
    confirmed_at: AwareDatetime | None = None
    created_at: AwareDatetime | None = None
    captured_at: AwareDatetime | None = None
    retention_until: AwareDatetime | None = None


class ConsoleFeedback(ConsoleModel):
    answer_id: uuid.UUID
    request_id: uuid.UUID
    thread_id: str
    user_id: uuid.UUID
    business_code: str
    rating: FeedbackRating
    reason_code: str | None
    feedback_revision: NonNegativeInt
    feedback_updated_at: AwareDatetime | None
    has_comment: bool
    comment_included: Literal[False]
    message_id: str | None
    content_version: int
    confirmed_at: AwareDatetime
    case_id: uuid.UUID | None
    case_state: CaseState
    case_revision: NonNegativeInt
    reviewed_feedback_revision: NonNegativeInt
    needs_review: bool
    handled_at: AwareDatetime | None


class ConsoleRatingHistory(ConsoleModel):
    id: int
    operation_id: uuid.UUID
    rating: FeedbackRating
    reason_code: str | None
    accepted_via: str
    expected_revision: NonNegativeInt
    result_revision: NonNegativeInt
    received_at: AwareDatetime
    has_comment: bool


class ConsoleCaseHistory(ConsoleModel):
    id: uuid.UUID
    case_id: uuid.UUID
    operation_id: uuid.UUID
    action: CaseAction
    from_state: str | None
    to_state: CaseState
    expected_revision: NonNegativeInt
    result_revision: NonNegativeInt
    actor_user_id: uuid.UUID
    actor_account: str
    has_note: bool
    created_at: AwareDatetime


class ConsoleHistory(ConsoleList[ConsoleRatingHistory | ConsoleCaseHistory]):
    content_included: Literal[False]


class ConsoleCapability(ConsoleModel):
    group_by: CapabilityGroup
    group: str
    measurement_source: str
    # 指标名由business/skill/tool三种维度决定；值仅允许安全计数与毫秒量。
    __pydantic_extra__: dict[str, int | float | None] = Field(init=False)
    model_config = ConfigDict(extra="allow")


class ConsoleCapabilities(ConsoleList[ConsoleCapability]):
    group_by: CapabilityGroup


class ConsoleCapabilityDetail(ConsoleModel):
    id: uuid.UUID
    started_at: AwareDatetime
    attempt_id: uuid.UUID
    user_id: uuid.UUID
    span_kind: str | None = None
    span_name: str | None = None
    stage: str | None = None
    status: str | None
    duration_ms: int | None
    skill_id: str | None
    skill_version: str | None = None
    selection_source: str | None = None
    logical_call_id: uuid.UUID | None = None
    tool_name: str | None = None
    service_key: str | None = None
    retry_index: int | None = None
    fallback_kind: str | None = None
    error_category: str | None = None
    network_attempted: bool | None = None


class ConsoleCapabilityDetails(ConsoleList[ConsoleCapabilityDetail]):
    kind: Literal["skill_span", "tool_call"]


class ConsoleDisplayTiming(ConsoleModel):
    sample_count: NonNegativeInt
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    measurement_source: str


class ConsoleModelTiming(ConsoleModel):
    model: str
    provider: str | None
    calls: NonNegativeInt
    ttft_samples: NonNegativeInt
    p50_ms: float | None
    p95_ms: float | None


class ConsolePerformancePartition(ConsoleMetric):
    queue_wait_ms: ConsoleMetric | None = None
    display_complete: ConsoleDisplayTiming | None = None
    by_model: list[ConsoleModelTiming] | None = None
    by_model_truncated: bool | None = None


class ConsolePerformance(ConsoleModel):
    meta: ConsoleMeta
    partitions: dict[str, ConsolePerformancePartition]
    not_applicable: ConsoleMetric


class ConsoleCostItem(ConsoleModel):
    group_by: CostGroup
    group: str
    currency: str
    subtotal: str | None
    priced_calls: NonNegativeInt
    unknown_calls: NonNegativeInt
    measurement_status: str
    measurement_source: str


class ConsoleCostTotals(ConsoleModel):
    observations: NonNegativeInt
    unpriced_calls: NonNegativeInt
    usage_incomplete_calls: NonNegativeInt
    complete_cost_known: bool


class ConsoleCosts(ConsoleList[ConsoleCostItem]):
    totals: ConsoleCostTotals


class ConsoleErrorDetail(ConsoleModel):
    code: str
    error: str


class ConsoleErrorResponse(ConsoleModel):
    detail: ConsoleErrorDetail | str | list[JsonValue]
