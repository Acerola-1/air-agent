"""Checkpoint validated sections; never retry a partially sent report.

The subgraph inherits the official server's saver. A paused tool resumes the
pending section with its frozen evidence, output identity and sequence.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph

from common.output_streaming.contract import (
    ABORT_REASONS,
    DELIVERY_MODE_VALIDATED_SECTION,
)
from common.output_streaming.publisher import (
    OutputAborted,
    OutputPublisher,
    runtime_authorization_probe,
)
from intelligent_tracing.evidence import canonical_digest
from intelligent_tracing.gate import FrozenPublicationPlan, ReportGate
from intelligent_tracing.report_store import ReportStore
from intelligent_tracing.schemas import (
    PublicationSession,
    RuntimeScope,
    ValidatedReport,
)


class SectionPublicationError(RuntimeError):
    """Carry terminal progress back into the parent tool's checkpoint."""

    def __init__(
        self,
        reason: str,
        *,
        errors: tuple[str, ...] = (),
        session: PublicationSession | None = None,
    ) -> None:
        """Retain only public failure codes and trusted checkpoint progress."""
        super().__init__(reason)
        self.reason = reason
        self.errors = errors
        self.session = session


class PublicationState(TypedDict, total=False):
    """Private subgraph state, never a model tool argument."""

    frozen: FrozenPublicationPlan | None
    session: PublicationSession | None
    report: ValidatedReport | None
    errors: tuple[str, ...]


def publication_execution_key(config: dict[str, Any], scope: RuntimeScope) -> str:
    """Bind a publication to the trusted scope and admitted execution."""
    cfg = config.get("configurable") or {}
    metadata = config.get("metadata") or {}
    return canonical_digest(
        {
            "scope": scope.model_dump(mode="json"),
            "epoch": cfg.get("execution_epoch")
            or metadata.get("execution_epoch")
            or scope.epoch,
            "attempt": cfg.get("ledger_attempt_id")
            or metadata.get("run_id")
            or cfg.get("run_id")
            or cfg.get("langgraph_run_id")
            or "controlled",
        }
    )


async def publish_frozen_plan(
    *,
    gate: ReportGate,
    report_store: ReportStore,
    config: dict[str, Any],
    frozen: FrozenPublicationPlan | None = None,
    prepare: Callable[[], Awaitable[FrozenPublicationPlan]] | None = None,
    expected_scope: RuntimeScope | None = None,
    writer: Any = None,
    session: PublicationSession | None = None,
    section_barrier: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[PublicationSession, ValidatedReport]:
    """Persist progress before the next section and before final confirmation.

    ``prepare`` runs inside the initial node, so a tool restart does not repeat
    preflight against an empty process-local evidence registry.
    """
    scope = expected_scope or (frozen.scope if frozen else None)
    if scope is None:
        raise SectionPublicationError("missing_publication_scope")
    execution_key = publication_execution_key(config, scope)
    # A new accepted attempt starts a new publication, never reuses the prior
    # turn's report. A pending subgraph still checks its stored key on resume.
    if session is not None and session.execution_key != execution_key:
        session = None
    probe = runtime_authorization_probe(config)

    def publisher_for(current: PublicationSession) -> OutputPublisher:
        if current.scope != scope or current.execution_key != execution_key:
            raise SectionPublicationError("publication_scope_changed", session=current)
        publisher = OutputPublisher(
            config,
            graph_id="intelligent-tracing",
            delivery_mode=DELIVERY_MODE_VALIDATED_SECTION,
            writer=writer,
            authorization_probe=probe,
            output_id=current.output_id,
            message_id=current.message_id,
        )
        if current.last_seq >= 0:
            publisher.restore(
                text=current.published_text,
                seq=current.last_seq,
                sections=current.published_section_ids,
            )
        return publisher

    def checkpoint(
        current: PublicationSession, publisher: OutputPublisher, **updates: Any
    ):
        return current.model_copy(
            update={
                "published_text": publisher.emitted_text,
                "last_seq": publisher.sequence,
                **updates,
            }
        )

    async def fail(
        current: PublicationSession, publisher: OutputPublisher, exc: Exception
    ):
        reason = (
            exc.reason_code
            if isinstance(exc, OutputAborted)
            else exc.reason
            if isinstance(exc, SectionPublicationError)
            else "generation_failed"
        )
        await publisher.abort(
            reason if reason in ABORT_REASONS else "generation_failed"
        )
        return {
            "session": checkpoint(current, publisher, failed_reason=reason),
            "errors": exc.errors
            if isinstance(exc, SectionPublicationError)
            else (type(exc).__name__,),
            "report": None,
        }

    async def initialize(state: PublicationState):
        plan = state.get("frozen")
        if plan is None:
            if prepare is None:
                raise SectionPublicationError("missing_frozen_plan")
            plan = await prepare()
        if plan.scope != scope:
            raise SectionPublicationError("publication_scope_changed")
        current = state.get("session")
        if current is not None:
            if (
                current.plan_digest != plan.plan_digest
                or current.report_id != plan.report_id
            ):
                raise SectionPublicationError("publication_plan_changed")
            publisher_for(current)
            return {"frozen": plan}
        current = PublicationSession(
            report_id=plan.report_id,
            plan_digest=plan.plan_digest,
            analysis_id=plan.request.analysis_id,
            revision=plan.request.revision,
            scope=plan.scope,
            section_ids=plan.section_ids,
            output_id=f"out_{uuid.uuid4().hex}",
            message_id=plan.report_id,
            execution_key=execution_key,
        )
        # Commit the IDs before sending even the start frame, so replay after
        # a crash at this boundary cannot invent another output identity.
        return {"frozen": plan, "session": current}

    async def start(state: PublicationState):
        plan, current = state["frozen"], state["session"]
        assert plan is not None and current is not None
        publisher = publisher_for(current)
        publisher.start(
            plan_digest=plan.plan_digest,
            report_id=plan.report_id,
            section_count=len(plan.sections),
        )
        return {"session": checkpoint(current, publisher)}

    async def publish_section(state: PublicationState):
        plan, current = state["frozen"], state["session"]
        assert plan is not None and current is not None
        publisher = publisher_for(current)
        pending = [
            item
            for item in plan.sections
            if item.section_id not in current.published_section_ids
        ]
        section_plan = pending[0]
        try:
            if section_barrier is not None:
                await section_barrier(section_plan.section_id)
            validator = gate.for_checkpoint(plan)
            section = validator.render_section(plan, section_plan)
            errors = validator.validate_section(plan, section)
            if errors:
                raise SectionPublicationError(
                    "section_validation_failed", errors=tuple(errors)
                )
            if section.text.strip():
                text = ("\n\n" if publisher.total_chars else "") + section.text
                await publisher.publish(
                    text, section_id=section.section_id, plan_digest=plan.plan_digest
                )
            return {
                "session": checkpoint(
                    current,
                    publisher,
                    published_section_ids=(
                        *current.published_section_ids,
                        section_plan.section_id,
                    ),
                )
            }
        except GraphBubbleUp:
            # A pause is not an aborted answer. The previous checkpoint retains
            # the last delivered section and sequence when this node restarts.
            raise
        except asyncio.CancelledError:
            await publisher.abort("cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - terminal graph state, not a retry
            return await fail(current, publisher, exc)

    async def confirm(state: PublicationState):
        plan, current = state["frozen"], state["session"]
        assert plan is not None and current is not None
        publisher = publisher_for(current)
        try:
            await publisher.ensure_authorized()
            result = gate.for_checkpoint(plan).confirm(plan)
            if not result.ok or result.report is None:
                raise SectionPublicationError(
                    "final_confirmation_failed", errors=tuple(result.errors)
                )
            report = result.report
            if report.body_markdown != publisher.emitted_text:
                raise SectionPublicationError("final_content_drift")
            await report_store.save(report)
            await publisher.finish(
                report.body_markdown,
                report_id=report.report_id,
                revision=report.revision,
                plan_digest=plan.plan_digest,
                report_sha256=report.content_sha256,
            )
            return {
                "session": checkpoint(
                    current,
                    publisher,
                    completed=True,
                    body_sha256=report.content_sha256,
                ),
                "report": report,
            }
        except GraphBubbleUp:
            raise
        except asyncio.CancelledError:
            await publisher.abort("cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - no unsaved report becomes visible
            return await fail(current, publisher, exc)

    def route(state: PublicationState):
        current = state["session"]
        assert current is not None
        if current.failed_reason or current.completed:
            return END
        if current.last_seq < 0:
            return "start"
        return (
            "section"
            if len(current.published_section_ids) < len(current.section_ids)
            else "confirm"
        )

    if session and session.completed:
        await publisher_for(session).ensure_authorized()
        report = await report_store.get(session.report_id, scope=scope)
        if report is None or report.content_sha256 != session.body_sha256:
            raise SectionPublicationError("report_reread_failed", session=session)
        return session, report

    builder = StateGraph(PublicationState)
    builder.add_node("initialize", initialize)
    builder.add_node("start", start)
    builder.add_node("section", publish_section)
    builder.add_node("confirm", confirm)
    builder.add_edge(START, "initialize")
    builder.add_conditional_edges("initialize", route)
    builder.add_conditional_edges("start", route)
    builder.add_conditional_edges("section", route)
    builder.add_edge("confirm", END)
    # Inherit the parent's saver and invocation namespace, never a local saver.
    graph = builder.compile(name="tracing_publication")
    result = await graph.ainvoke({"frozen": frozen, "session": session}, config=config)
    current = result.get("session")
    if current is None or not current.completed or result.get("report") is None:
        raise SectionPublicationError(
            current.failed_reason if current else "publication_incomplete",
            errors=tuple(result.get("errors") or ()),
            session=current,
        )
    return current, result["report"]
