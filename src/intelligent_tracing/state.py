"""Parent state and revision-safe reducers for the tracing graph."""

from __future__ import annotations

from typing import Annotated, Any, NotRequired

from deepagents.graph import DeepAgentState

from intelligent_tracing.schemas import (
    ChildExecution,
    ClaimAllowlist,
    EvidenceReview,
    MeteoFindings,
    PublicationSession,
    RuntimeScope,
    SpatialFindings,
    StationFindings,
    TracingRequest,
    TracingRequestDraft,
    TurnContext,
    TurnDecision,
    TurnPublication,
    ValidatedReport,
)


def latest_request_reducer(
    current: TracingRequest | None, incoming: TracingRequest | None
) -> TracingRequest | None:
    """Keep the newest revision; never let a late old request overwrite."""
    if incoming is None:
        return current
    if current is None:
        return incoming
    if (
        incoming.analysis_id == current.analysis_id
        and incoming.revision <= current.revision
    ):
        return current
    return incoming


def latest_finding_reducer(current: Any, incoming: Any) -> Any:
    """Keep a per-analysis latest revision; ``None`` explicitly resets."""
    if incoming is None:
        return None
    if current is None:
        return incoming
    if incoming.analysis_id != current.analysis_id:
        # A new analysis is allowed; staleness is checked before publication.
        return incoming
    if incoming.revision > current.revision:
        return incoming
    return current


def merge_child_executions(
    current: tuple[ChildExecution, ...], incoming: tuple[ChildExecution, ...]
) -> tuple[ChildExecution, ...]:
    """Accumulate child executions by id; latest record wins."""
    merged = {item.child_execution_id: item for item in current}
    for item in incoming:
        merged[item.child_execution_id] = item
    return tuple(merged.values())


def merge_publication_session(
    current: PublicationSession | None, incoming: PublicationSession | None
) -> PublicationSession | None:
    """Merge a publication; explicit None resets it for a newly frozen turn."""
    if incoming is None:
        return None
    if current is None:
        return incoming
    if (
        current.plan_digest == incoming.plan_digest
        and current.report_id == incoming.report_id
        and current.analysis_id == incoming.analysis_id
        and current.revision == incoming.revision
    ):
        published = tuple(
            dict.fromkeys(
                (*current.published_section_ids, *incoming.published_section_ids)
            )
        )
        return incoming.model_copy(
            update={
                "published_section_ids": published,
                "completed": current.completed or incoming.completed,
            }
        )
    return incoming


def latest_report_reducer(
    current: ValidatedReport | None, incoming: ValidatedReport | None
) -> ValidatedReport | None:
    """Keep the newest validated report; explicit reset clears it."""
    if incoming is None:
        return None
    if current is None:
        return incoming
    if (
        incoming.analysis_id == current.analysis_id
        and incoming.revision < current.revision
    ):
        return current
    return incoming


class TracingState(DeepAgentState):
    """DeepAgent state extended with frozen request and specialist results."""

    turn_context: NotRequired[TurnContext]
    turn_decision: NotRequired[TurnDecision | None]
    request_draft: NotRequired[TracingRequestDraft | None]
    turn_publication: NotRequired[TurnPublication | None]

    request: NotRequired[Annotated[TracingRequest | None, latest_request_reducer]]
    scope: NotRequired[RuntimeScope]
    station_findings: NotRequired[
        Annotated[StationFindings | None, latest_finding_reducer]
    ]
    grid_findings: NotRequired[
        Annotated[SpatialFindings | None, latest_finding_reducer]
    ]
    meteo_findings: NotRequired[Annotated[MeteoFindings | None, latest_finding_reducer]]
    evidence_review: NotRequired[
        Annotated[EvidenceReview | None, latest_finding_reducer]
    ]
    allowlist: NotRequired[Annotated[ClaimAllowlist | None, latest_finding_reducer]]
    validated_report: NotRequired[
        Annotated[ValidatedReport | None, latest_report_reducer]
    ]
    publication_session: NotRequired[
        Annotated[PublicationSession | None, merge_publication_session]
    ]
    budget_exhausted: NotRequired[bool]
    child_executions: NotRequired[
        Annotated[tuple[ChildExecution, ...], merge_child_executions]
    ]
