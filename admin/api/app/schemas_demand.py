"""需求洞察 v1 DTO（对应 change 7 api-contract）.

全部响应显式列字段：metrics 响应不包含 user/message/request 原始 ID、原文、
向量或委托凭据；来源/未归类端点也只返回脱敏摘要与来源引用。
"""

from __future__ import annotations

import uuid
from typing import Generic, Literal, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, NonNegativeInt

Rank = Literal["intents", "users", "requests"]
JobKind = Literal["incremental", "backfill"]
JobAction = Literal["retry", "pause", "renew", "cancel"]
QuestionAction = Literal["rename", "assign", "merge", "split"]
WindowKind = Literal["today", "last_7_days", "last_30_days", "custom"]


class InsightsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InsightsWindowOut(InsightsModel):
    start: AwareDatetime
    end: AwareDatetime
    timezone: Literal["Asia/Shanghai"]
    kind: str
    partial_day: bool


class InsightsScopeOut(InsightsModel):
    tenant_id: uuid.UUID
    context_version: NonNegativeInt


class InsightsMeta(InsightsModel):
    schema_version: Literal["demand-insights-v1"]
    metric_version: str
    as_of: AwareDatetime
    window: InsightsWindowOut
    filters: dict[str, JsonValue]
    scope: InsightsScopeOut
    projected_at: AwareDatetime | None
    data_lag_seconds: float | None
    measurement_source: str
    status: str
    limitations: list[str]
    analysis_version: str
    assignment_revision: NonNegativeInt
    embedding_profile: str
    profile_id: uuid.UUID | None
    profile_state_revision: NonNegativeInt
    source_cutoff: AwareDatetime | None
    eligible_requests: NonNegativeInt
    processed_requests: NonNegativeInt
    pending_requests: NonNegativeInt
    failed_requests: NonNegativeInt
    missing_content_requests: NonNegativeInt
    pending_confirmation_requests: NonNegativeInt
    expired_or_deleted_requests: NonNegativeInt
    review_required_requests: NonNegativeInt
    classified_intents: NonNegativeInt
    unclassified_intents: NonNegativeInt
    total_intents: NonNegativeInt
    negative_feedback_requests: NonNegativeInt
    evaluated_requests: NonNegativeInt
    technical_failure_requests: NonNegativeInt
    stopped_requests: NonNegativeInt
    coverage: float | None
    feedback_coverage: float | None
    comparable: bool


class Share(InsightsModel):
    value: float | None
    numerator: int
    denominator: int | None


class FeedbackCoverage(InsightsModel):
    numerator: int
    denominator: int | None


class QuestionItem(InsightsModel):
    cluster_id: uuid.UUID
    canonical_name: str
    status: str
    topic_id: uuid.UUID | None
    topic_name: str | None
    atomic_intent_count: NonNegativeInt
    distinct_requests: NonNegativeInt
    distinct_users: NonNegativeInt
    share: Share
    previous_count: int | None
    growth_rate: float | None
    new: bool
    comparison_status: str
    request_level_negative_feedback: NonNegativeInt
    feedback_coverage: FeedbackCoverage


class TopicItem(InsightsModel):
    topic_id: uuid.UUID
    name: str
    status: str
    atomic_intent_count: NonNegativeInt
    distinct_requests: NonNegativeInt
    distinct_users: NonNegativeInt
    share: Share


Item = TypeVar("Item")


class InsightsList(InsightsModel, Generic[Item]):  # noqa: UP046 - Pydantic 泛型稳定
    items: list[Item]
    next_cursor: str | None
    has_more: bool
    limit: int
    sort: str | None = None
    filters: dict[str, JsonValue] | None = None


class QuestionsResponse(InsightsList[QuestionItem]):
    meta: InsightsMeta
    previous_comparable: bool


class TopicsResponse(InsightsList[TopicItem]):
    meta: InsightsMeta


class TrendPoint(InsightsModel):
    day: str
    intents: NonNegativeInt
    requests: NonNegativeInt
    users: NonNegativeInt


class QuestionStatistics(InsightsModel):
    atomic_intent_count: NonNegativeInt
    distinct_requests: NonNegativeInt
    distinct_users: NonNegativeInt
    share: Share
    request_level_negative_feedback: NonNegativeInt


class QuestionDetailResponse(InsightsModel):
    meta: InsightsMeta
    cluster_id: uuid.UUID
    canonical_name: str
    action: str
    object: str
    time_kind: str
    negation: bool
    constraints: dict[str, JsonValue]
    status: str
    revision: NonNegativeInt
    topic_id: uuid.UUID | None
    topic_name: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    statistics: QuestionStatistics | None
    summary: str | None
    summary_source: str | None
    trend: list[TrendPoint]
    coverage: dict[str, JsonValue]
    merged_into: uuid.UUID | None = None


class SourceItem(InsightsModel):
    intent_id: uuid.UUID
    request_id: uuid.UUID
    split_index: NonNegativeInt
    intent_summary: str
    action: str
    object: str
    time_kind: str
    negation: bool
    slots: dict[str, JsonValue]
    confidence: float
    admitted_at: AwareDatetime
    content_version: NonNegativeInt
    graph_id: str
    business_code: str | None
    attempt_id: uuid.UUID | None
    content_path: str


class SourcesResponse(InsightsModel):
    meta: InsightsMeta
    items: list[SourceItem]
    has_more: bool
    limit: int
    offset: NonNegativeInt


class UnclassifiedItem(InsightsModel):
    intent_id: uuid.UUID
    request_id: uuid.UUID
    split_index: NonNegativeInt
    intent_summary: str
    action: str
    object: str
    time_kind: str
    negation: bool
    slots: dict[str, JsonValue]
    confidence: float
    unclassified_reason: str
    admitted_at: AwareDatetime
    content_version: NonNegativeInt
    graph_id: str
    business_code: str | None
    attempt_id: uuid.UUID | None
    content_path: str


class GapItem(InsightsModel):
    request_id: uuid.UUID
    reason: str
    detail: str | None
    attempts: NonNegativeInt
    admitted_at: AwareDatetime


class UnclassifiedResponse(InsightsModel):
    meta: InsightsMeta
    items: list[UnclassifiedItem]
    gaps: list[GapItem]
    has_more: bool
    limit: int
    offset: NonNegativeInt


class JobItem(InsightsModel):
    job_id: uuid.UUID
    job_kind: str
    state: str
    window_start: AwareDatetime
    window_end: AwareDatetime | None
    as_of: AwareDatetime | None = None
    batch_size: NonNegativeInt | None = None
    lease_deadline: AwareDatetime | None = None
    processed_sources: NonNegativeInt
    failed_sources: NonNegativeInt
    classified_intents: NonNegativeInt
    unclassified_intents: NonNegativeInt
    actual_tokens: int | None
    usage_missing: bool
    state_revision: NonNegativeInt
    last_error_code: str | None
    created_at: AwareDatetime
    authorization_expires_at: AwareDatetime
    started_at: AwareDatetime | None = None
    paused_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    estimated_tokens: NonNegativeInt | None = None
    max_sources: NonNegativeInt | None = None
    max_tokens: NonNegativeInt | None = None
    continuous: bool | None = None
    cancel_requested: bool | None = None


class JobsResponse(InsightsModel):
    meta: InsightsMeta
    items: list[JobItem]


class OverviewTopic(InsightsModel):
    topic_id: uuid.UUID
    name: str
    atomic_intent_count: NonNegativeInt
    distinct_requests: NonNegativeInt
    distinct_users: NonNegativeInt


class OverviewReason(InsightsModel):
    reason: str
    count: NonNegativeInt


class OverviewResponse(InsightsModel):
    meta: InsightsMeta
    counts: dict[str, JsonValue]
    topics: list[OverviewTopic]
    unclassified_reasons: list[OverviewReason]


class JobCreateIn(InsightsModel):
    operation_id: str = Field(min_length=36, max_length=36)
    job_kind: JobKind = "incremental"
    window_start: AwareDatetime | None = None
    window_end: AwareDatetime | None = None
    continuous: bool = False
    max_sources: int = Field(default=500, ge=1, le=5000)
    max_tokens: int = Field(default=200_000, ge=1000, le=10_000_000)
    acknowledge_backfill: bool = False
    delegation_ttl_hours: int = Field(default=12, ge=1, le=12)


class JobActionIn(InsightsModel):
    operation_id: str = Field(min_length=36, max_length=36)
    expected_revision: int = Field(strict=True, ge=0, le=1_000_000)
    action: JobAction
    renew_ttl_hours: int = Field(default=12, ge=1, le=12)


class SplitGroupIn(InsightsModel):
    name: str = Field(min_length=1, max_length=80)
    intent_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class QuestionActionIn(InsightsModel):
    operation_id: str = Field(min_length=36, max_length=36)
    expected_revision: int = Field(strict=True, ge=0, le=1_000_000)
    action: QuestionAction
    preview: bool = False
    new_name: str | None = Field(default=None, max_length=80)
    target_cluster_id: uuid.UUID | None = None
    intent_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    new_cluster_name: str | None = Field(default=None, max_length=80)
    groups: list[SplitGroupIn] = Field(default_factory=list, max_length=10)
    preview_token: str | None = Field(default=None, max_length=4096)


class ProfileActionIn(InsightsModel):
    operation_id: str = Field(min_length=36, max_length=36)
    expected_revision: int = Field(strict=True, ge=0, le=1_000_000)


class InsightsErrorResponse(InsightsModel):
    detail: dict[str, str]


class InsightsActionResponse(InsightsModel):
    result: dict[str, JsonValue]
    replayed: bool = False
