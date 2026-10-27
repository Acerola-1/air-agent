"""Chat publication middleware for validated tracing reports.

The model never writes user-visible prose.  The lead's model output is blanked
and the report is published only from the frozen, allowlisted plan.  Sections
are rendered and validated one at a time during the report tool; ``after_agent``
reuses the same publication session and never resends the body.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelResponse,
)
from langchain_core.messages import AIMessage

from common.event_ledger.lifecycle import require_ledger_context
from common.event_ledger.store import fetch_attempt_lifecycle, thread_deletion_exists
from common.identity.runtime import guard_runtime_call
from common.output_streaming.controls import publish_control_message
from common.output_streaming.publisher import (
    OutputAborted,
    OutputPublisher,
    runtime_authorization_probe,
)
from intelligent_tracing.claims import build_claim_allowlist, fallback_plan
from intelligent_tracing.conversation import (
    ANALYSIS_TOOLS,
    capability_catalog,
    render_conversation,
)
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.gate import ReportGate
from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.process_assessment import assess_process
from intelligent_tracing.report_store import ReportStore
from intelligent_tracing.schemas import (
    KnowledgeBasisItem,
    PublicationSession,
    ReportPlan,
    RuntimeScope,
    TracingRequest,
    TurnDecision,
    TurnPublication,
    ValidatedReport,
)
from intelligent_tracing.section_publishing import (
    SectionPublicationError,
    publication_execution_key,
    publish_frozen_plan,
)
from intelligent_tracing.turn_routing import current_decision, turn_context

logger = logging.getLogger(__name__)

_CLARIFY_TEXT = (
    "请补充或确认站点代码/名称、分析时间范围（含时区）和调查半径；"
    "在请求冻结前不会发起数据查询。"
)
_INCOMPLETE_TEXT = "当前尚未完成必要分析，暂不能发布结论；请稍后重试或补充条件。"


class TracingPublishMiddleware(AgentMiddleware):
    """Mark model prose internal and publish only validated report sections."""

    def __init__(
        self,
        *,
        registry: EvidenceRegistry,
        gate: ReportGate,
        policy: AnalysisPolicy,
        report_store: ReportStore,
        lifecycle_probe: Callable[[dict[str, Any], RuntimeScope], Awaitable[bool]]
        | None = None,
        require_turn_decision: bool = False,
    ) -> None:
        """Bind the execution-layer publication dependencies."""
        super().__init__()
        self._registry = registry
        self._gate = gate
        self._policy = policy
        self._report_store = report_store
        self._lifecycle_probe = lifecycle_probe or self._ledger_probe
        self._published_ids: set[str] = set()
        self._require_turn_decision = require_turn_decision
        self._catalog = capability_catalog(policy, ANALYSIS_TOOLS)

    @staticmethod
    async def _ledger_probe(config: dict[str, Any], scope: RuntimeScope) -> bool:
        """Ask the event ledger whether publication is still allowed.

        Any missing context, tombstone, terminal/cancelled attempt, ownership or
        business mismatch suppresses publication; ledger outage fails closed.
        """
        try:
            await guard_runtime_call(config)
            ledger_scope, request_id, attempt_id = require_ledger_context(config)
            if await thread_deletion_exists(ledger_scope, thread_id=scope.thread_id):
                return False
            row = await fetch_attempt_lifecycle(
                ledger_scope, attempt_id=attempt_id, thread_id=scope.thread_id
            )
        except Exception:  # noqa: BLE001 - ledger outage must suppress output
            return False
        if row is None:
            return False
        if str(row.get("request_id") or "") != request_id:
            return False
        for key, expected in (
            ("business_code", scope.business_code),
            ("business_policy_version", scope.business_policy_version),
        ):
            current = str(row.get(key) or "")
            if current and current != expected:
                return False
        state = str(row.get("state") or "").lower()
        if state in {"cancelled", "canceled", "failed", "timed_out", "timeout"}:
            return False
        if bool(row.get("cancel_confirmed")):
            return False
        return True

    async def _publication_allowed(
        self, config: dict[str, Any], scope: RuntimeScope
    ) -> bool:
        """Return False unless the ledger probe explicitly allows publication."""
        try:
            return bool(await self._lifecycle_probe(config, scope))
        except Exception:  # noqa: BLE001 - probe failure must fail closed
            return False

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """Mark every model AIMessage non-visible before graph publication."""
        response = await handler(request)
        if isinstance(response, AIMessage):
            return self._invisible(response)
        if isinstance(response, ModelResponse):
            return ModelResponse(
                result=[
                    self._invisible_message(message) for message in response.result
                ],
                structured_response=response.structured_response,
            )
        if isinstance(response, ExtendedModelResponse):
            inner = response.model_response
            return ExtendedModelResponse(
                model_response=ModelResponse(
                    result=[
                        self._invisible_message(message) for message in inner.result
                    ],
                    structured_response=inner.structured_response,
                ),
                command=response.command,
            )
        return response

    @staticmethod
    def _invisible(message: AIMessage) -> AIMessage:
        return message.model_copy(
            update={
                "content": "",
                "additional_kwargs": {
                    **message.additional_kwargs,
                    "ui_visible": False,
                },
            }
        )

    def _invisible_message(self, message: Any) -> Any:
        return self._invisible(message) if isinstance(message, AIMessage) else message

    async def aafter_agent(
        self, state: Any, runtime: Any = None
    ) -> dict[str, Any] | None:
        """Confirm/reuse the publication session or publish a controlled fallback."""
        config = self._config(runtime)
        if self._require_turn_decision:
            decision = current_decision(state, config)
            if decision is None:
                if state.get("turn_decision") is not None:
                    return {}
                decision = TurnDecision(
                    context=turn_context(state, config),
                    proposed_intent="unknown",
                    route="fallback",
                    reason="missing_plan",
                )
            if decision.route != "analysis":
                return await self._publish_turn(state, config, decision)
            current_request = state.get("request")
            if (
                not decision.frozen
                or current_request is None
                or current_request.analysis_id != decision.analysis_id
                or current_request.revision != decision.revision
                or current_request.request_digest != decision.request_digest
            ):
                return await self._publish_text(config, _INCOMPLETE_TEXT, "incomplete")
        request = state.get("request") if isinstance(state, dict) else None
        scope = state.get("scope") if isinstance(state, dict) else None
        report = state.get("validated_report") if isinstance(state, dict) else None
        session = state.get("publication_session") if isinstance(state, dict) else None
        if request is None or scope is None:
            return await self._publish_text(config, _CLARIFY_TEXT, "clarify")
        if self._should_suppress(config, scope):
            return {}
        if not await self._publication_allowed(config, scope):
            return {}
        if (
            isinstance(session, PublicationSession)
            and session.failed_reason
            and session.execution_key == publication_execution_key(config, scope)
        ):
            # A report tool already terminated this publication. Never replace
            # its public prefix by constructing another report in after_agent.
            return {}
        if (
            isinstance(report, ValidatedReport)
            and isinstance(session, PublicationSession)
            and session.completed
            and session.report_id == report.report_id
        ):
            if report.report_id in self._published_ids:
                return None
            self._published_ids.add(report.report_id)
            return self._visible_update(report)
        current = await self._current_allowlist(state)
        if current is None:
            logger.warning("tracing publication: fallback allowlist missing")
            return await self._publish_text(config, _INCOMPLETE_TEXT, "incomplete")
        request_now, scope_now, allowlist_now = current
        run_id = str(config.get("configurable", {}).get("run_id") or "")
        plan = (
            ReportPlan(
                analysis_id=report.analysis_id,
                revision=report.revision,
                claim_ids=report.claim_ids,
                review_claim_ids=(),
                knowledge_reference_ids=tuple(
                    item.citation_id for item in report.knowledge_basis
                ),
            )
            if isinstance(report, ValidatedReport)
            and report.analysis_id == request_now.analysis_id
            and report.revision == request_now.revision
            else fallback_plan(allowlist_now)
        )
        knowledge_basis = await self._resolve_knowledge(
            plan, request_now, scope_now, allowlist_now, state, config
        )
        if knowledge_basis is None:
            return await self._publish_text(config, _INCOMPLETE_TEXT, "incomplete")
        if knowledge_basis and not plan.knowledge_reference_ids:
            plan = plan.model_copy(
                update={
                    "knowledge_reference_ids": tuple(
                        item.citation_id for item in knowledge_basis
                    )
                }
            )
        report_id = (
            report.report_id
            if isinstance(report, ValidatedReport)
            and report.analysis_id == request_now.analysis_id
            and report.revision == request_now.revision
            else f"rep_{uuid.uuid4().hex}"
        )
        frozen, errors = self._gate.precheck(
            plan,
            allowlist_now,
            request_now,
            scope_now,
            report_id=report_id,
            run_id=run_id,
            knowledge_basis=knowledge_basis,
        )
        if frozen is None:
            # 报告对应的发现已变化：用当前可信 allowlist 的确定性全量计划重建，
            # 不沿用旧报告中被删除/降级的 claim；报告身份必须随内容重新分配。
            report_id = f"rep_{uuid.uuid4().hex}"
            fallback = fallback_plan(allowlist_now).model_copy(
                update={
                    "knowledge_reference_ids": tuple(
                        item.citation_id for item in knowledge_basis
                    )
                }
            )
            frozen, errors = self._gate.precheck(
                fallback,
                allowlist_now,
                request_now,
                scope_now,
                report_id=report_id,
                run_id=run_id,
                knowledge_basis=knowledge_basis,
            )
            if frozen is None:
                logger.warning(
                    "tracing publication: fallback rejected errors=%s", errors
                )
                return await self._publish_text(config, _INCOMPLETE_TEXT, "incomplete")
        try:
            new_session, published_report = await publish_frozen_plan(
                gate=self._gate,
                report_store=self._report_store,
                frozen=frozen,
                config=config,
                writer=self._stream_writer(),
                session=session,
            )
        except SectionPublicationError as exc:
            logger.warning(
                "tracing publication: section publication failed reason=%s", exc.reason
            )
            if exc.session is not None:
                return {"publication_session": exc.session}
            return await self._publish_text(config, _INCOMPLETE_TEXT, "incomplete")
        self._published_ids.add(published_report.report_id)
        return {
            **self._visible_update(published_report),
            "publication_session": new_session,
        }

    async def _publish_turn(
        self, state: Any, config: dict[str, Any], decision: Any
    ) -> dict[str, Any]:
        """Publish one validated conversation plan, never the hidden lead prose."""
        scope = decision.context.scope
        if self._should_suppress(config, scope) or not await self._publication_allowed(
            config, scope
        ):
            return {}
        try:
            text = render_conversation(decision, self._catalog)
        except (ValueError, KeyError):
            decision = decision.model_copy(
                update={"route": "fallback", "reason": "catalog_changed"}
            )
            text = render_conversation(decision, self._catalog)
        digest = hashlib.sha256(text.encode()).hexdigest()
        record = state.get("turn_publication")
        if (
            isinstance(record, TurnPublication)
            and record.turn_key == decision.context.key
        ):
            if record.content_sha256 != digest or record.text != text:
                return {}
        else:
            identifier = uuid.uuid5(uuid.NAMESPACE_URL, decision.context.key).hex
            record = TurnPublication(
                turn_key=decision.context.key,
                output_id="out_" + identifier,
                message_id="msg_" + identifier,
                text=text,
                content_sha256=digest,
            )
        if (
            record.output_id not in self._published_ids
            and state.get("turn_publication") != record
        ):

            async def probe() -> bool:
                return await self._publication_allowed(config, scope)

            publisher = OutputPublisher(
                config,
                graph_id="intelligent-tracing",
                delivery_mode="single_shot",
                writer=self._stream_writer(),
                authorization_probe=probe,
                output_id=record.output_id,
                message_id=record.message_id,
            )
            publisher.start(control_code="tracing_" + decision.route)
            try:
                await publisher.finish(text)
            except OutputAborted:
                return {}
            self._published_ids.add(record.output_id)
            logger.info(
                "tracing publication kind=%s output=%s attempt=%s run=%s",
                decision.route,
                record.output_id,
                decision.context.attempt_id,
                decision.context.run_id,
            )
        return {
            "turn_decision": decision,
            "turn_publication": record,
            "messages": [
                AIMessage(
                    content=text,
                    id=record.message_id,
                    additional_kwargs={
                        "ui_visible": True,
                        "output_protocol_version": 1,
                        "output_id": record.output_id,
                        "delivery_mode": "single_shot",
                        "control_code": "tracing_" + decision.route,
                    },
                )
            ],
        }

    async def _current_allowlist(self, state: dict[str, Any]):
        """Rebuild the trusted allowlist from the findings present now."""
        request = state.get("request")
        scope = state.get("scope")
        if request is None or scope is None:
            return None
        from intelligent_tracing.subagents import (
            unavailable_meteo_findings,
            unavailable_spatial_findings,
            unavailable_station_findings,
        )

        station = state.get("station_findings") or unavailable_station_findings(request)
        spatial = state.get("grid_findings") or unavailable_spatial_findings(request)
        meteo = state.get("meteo_findings") or unavailable_meteo_findings(request)
        if any(
            item.analysis_id != request.analysis_id or item.revision != request.revision
            for item in (station, spatial, meteo)
        ):
            return None
        process = assess_process(
            request, {}, station, spatial, meteo, policy=self._policy
        )
        extra = ("budget_exhausted",) if state.get("budget_exhausted") else ()
        allowlist = build_claim_allowlist(
            request,
            station,
            spatial,
            meteo,
            process,
            policy=self._policy,
            extra_limitations=extra,
        )
        return request, scope, allowlist

    async def _resolve_knowledge(
        self,
        plan: ReportPlan,
        request: TracingRequest,
        scope: RuntimeScope,
        allowlist,
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> tuple[KnowledgeBasisItem, ...] | None:
        """Re-resolve citations; never silently drop previously verified sources."""
        from common.knowledge.policy import citation_ids_from_messages
        from intelligent_tracing.tools import _resolve_knowledge_basis

        previous = state.get("validated_report")
        ids = (
            tuple(item.citation_id for item in previous.knowledge_basis)
            if isinstance(previous, ValidatedReport)
            else ()
        )
        ids = (
            tuple(plan.knowledge_reference_ids)
            or ids
            or tuple(citation_ids_from_messages(state.get("messages", [])))
        )
        if not ids:
            return ()
        resolved_plan = plan.model_copy(update={"knowledge_reference_ids": ids})
        resolved = await _resolve_knowledge_basis(
            resolved_plan, scope=scope, config=config
        )
        return None if isinstance(resolved, dict) else resolved

    def _visible_update(self, report: ValidatedReport) -> dict[str, Any]:
        """Return the single visible message that backs history for this report."""
        return {
            "messages": [
                AIMessage(
                    content=report.body_markdown,
                    id=report.report_id,
                    additional_kwargs={
                        "ui_visible": True,
                        "validated_report_ref": report.report_id,
                        "content_sha256": report.content_sha256,
                        "output_protocol_version": 1,
                    },
                )
            ],
            "validated_report": report.model_copy(update={"published": True}),
        }

    async def _render_fallback(
        self,
        request: TracingRequest,
        scope: RuntimeScope,
        allowlist,
        *,
        run_id: str = "",
        knowledge_basis: tuple[KnowledgeBasisItem, ...] | None = (),
    ) -> ValidatedReport | None:
        """Compatibility helper: precheck + confirm a deterministic fallback report."""
        if knowledge_basis is None:
            return None
        plan = fallback_plan(allowlist).model_copy(
            update={
                "knowledge_reference_ids": tuple(
                    item.citation_id for item in knowledge_basis
                )
            }
        )
        frozen, _errors = self._gate.precheck(
            plan,
            allowlist,
            request,
            scope,
            report_id=f"rep_{uuid.uuid4().hex}",
            run_id=run_id,
            knowledge_basis=knowledge_basis,
        )
        if frozen is None:
            return None
        result = self._gate.confirm(frozen)
        return result.report if result.ok else None

    async def _publish_text(
        self, config: dict[str, Any], text: str, prefix: str
    ) -> dict[str, Any]:
        if self._require_turn_decision:
            from intelligent_tracing.runtime import runtime_scope_from_config

            scope = runtime_scope_from_config(config)
            if self._should_suppress(
                config, scope
            ) or not await self._publication_allowed(config, scope):
                return {}
        message = await publish_control_message(
            config,
            graph_id="intelligent-tracing",
            text=text,
            writer=self._stream_writer(),
            authorization_probe=runtime_authorization_probe(config),
            control_code=f"tracing_{prefix}",
        )
        if message.additional_kwargs.get("ui_visible") is False:
            return {}
        return {"messages": [message]}

    def _stream_writer(self) -> Any:
        try:
            from langgraph.config import get_stream_writer

            return get_stream_writer()
        except Exception:  # pragma: no cover - offline direct calls
            return None

    @staticmethod
    def _config(runtime: Any) -> dict[str, Any]:
        config = getattr(runtime, "config", None)
        if isinstance(config, dict):
            return config
        try:
            from langgraph.config import get_config

            return dict(get_config() or {})
        except Exception:  # pragma: no cover - offline direct calls
            return {}

    @staticmethod
    def _should_suppress(config: dict[str, Any], scope: RuntimeScope) -> bool:
        configurable = config.get("configurable") or {}
        metadata = config.get("metadata") or {}
        if configurable.get("tracing_deleted") or metadata.get("tracing_deleted"):
            return True
        if configurable.get("cancel_confirmed") or metadata.get("cancel_confirmed"):
            return True
        current_epoch = str(
            configurable.get("execution_epoch") or metadata.get("execution_epoch") or ""
        )
        if current_epoch and current_epoch != scope.epoch:
            return True
        return False
