"""Chat publication middleware for validated tracing reports."""

from __future__ import annotations

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
from common.runtime_events import emit_event
from intelligent_tracing.claims import build_claim_allowlist, fallback_plan
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.gate import ReportGate
from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.process_assessment import assess_process
from intelligent_tracing.report_store import ReportStore
from intelligent_tracing.schemas import (
    ReportValidationResult,
    RuntimeScope,
    TracingRequest,
    ValidatedReport,
)

_CLARIFY_TEXT = (
    "请补充或确认站点代码/名称、分析时间范围（含时区）和调查半径；"
    "在请求冻结前不会发起数据查询。"
)
_INCOMPLETE_TEXT = "当前尚未完成必要分析，暂不能发布结论；请稍后重试或补充条件。"


class TracingPublishMiddleware(AgentMiddleware):
    """Mark model prose internal and publish only a validated report."""

    def __init__(
        self,
        *,
        registry: EvidenceRegistry,
        gate: ReportGate,
        policy: AnalysisPolicy,
        report_store: ReportStore,
        lifecycle_probe: Callable[[dict[str, Any], RuntimeScope], Awaitable[bool]]
        | None = None,
    ) -> None:
        """Bind the execution-layer publication dependencies."""
        super().__init__()
        self._registry = registry
        self._gate = gate
        self._policy = policy
        self._report_store = report_store
        self._lifecycle_probe = lifecycle_probe or self._ledger_probe
        self._published_ids: set[str] = set()

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
                "additional_kwargs": {
                    **message.additional_kwargs,
                    "ui_visible": False,
                }
            }
        )

    def _invisible_message(self, message: Any) -> Any:
        return self._invisible(message) if isinstance(message, AIMessage) else message

    async def aafter_agent(
        self, state: Any, runtime: Any = None
    ) -> dict[str, Any] | None:
        """Publish a validated report or a controlled clarification."""
        config = self._config(runtime)
        request = state.get("request") if isinstance(state, dict) else None
        scope = state.get("scope") if isinstance(state, dict) else None
        report = state.get("validated_report") if isinstance(state, dict) else None
        if request is None or scope is None:
            return self._publish_text(config, _CLARIFY_TEXT, "clarify")
        if self._should_suppress(config, scope):
            return {}
        if not await self._publication_allowed(config, scope):
            return {}
        current = await self._current_allowlist(state)
        run_id = str(config.get("configurable", {}).get("run_id") or "")
        if report is None:
            report = await self._build_fallback(state, run_id=run_id)
        elif current is not None:
            request_now, scope_now, allowlist_now = current
            if not await self._report_is_current(
                report, request_now, scope_now, allowlist_now, state
            ):
                report = await self._render_fallback(
                    request_now, scope_now, allowlist_now, run_id=run_id
                )
        if report is None:
            return self._publish_text(config, _INCOMPLETE_TEXT, "incomplete")
        if (
            report.analysis_id != request.analysis_id
            or report.revision != request.revision
            or report.scope != scope
        ):
            return self._publish_text(config, _INCOMPLETE_TEXT, "stale")
        if report.report_id in self._published_ids:
            return None
        published = report.model_copy(update={"published": True})
        self._published_ids.add(report.report_id)
        emit_event(
            config,
            "final_output_done",
            message=published.body_markdown,
            message_id=published.report_id,
        )
        visible = AIMessage(
            content=published.body_markdown,
            id=published.report_id,
            additional_kwargs={
                "ui_visible": True,
                "validated_report_ref": published.report_id,
                "content_sha256": published.content_sha256,
            },
        )
        return {"messages": [visible], "validated_report": published}

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

    async def _render_fallback(
        self,
        request: TracingRequest,
        scope: RuntimeScope,
        allowlist,
        *,
        run_id: str = "",
    ) -> ValidatedReport | None:
        result: ReportValidationResult = self._gate.validate_and_render(
            fallback_plan(allowlist),
            allowlist,
            request,
            scope,
            run_id=run_id,
        )
        if not result.ok or result.report is None:
            return None
        await self._report_store.save(result.report)
        return result.report

    async def _build_fallback(
        self, state: dict[str, Any], *, run_id: str = ""
    ) -> ValidatedReport | None:
        current = await self._current_allowlist(state)
        if current is None:
            return None
        request, scope, allowlist = current
        return await self._render_fallback(request, scope, allowlist, run_id=run_id)

    async def _report_is_current(
        self,
        report: ValidatedReport,
        request: TracingRequest,
        scope: RuntimeScope,
        allowlist,
        state: dict[str, Any],
    ) -> bool:
        """Re-check digest, allowlist and evidence before publication."""
        if (
            report.analysis_id != request.analysis_id
            or report.revision != request.revision
            or report.scope != scope
            or report.content_sha256 != report.compute_digest()
        ):
            return False
        stored = state.get("allowlist")
        if stored is None or stored.digest != allowlist.digest:
            return False
        try:
            self._registry.get_many(
                report.evidence_ids,
                scope=scope,
                analysis_id=request.analysis_id,
                revision=request.revision,
            )
        except Exception:  # noqa: BLE001 - missing evidence means re-render
            return False
        return True

    def _publish_text(
        self, config: dict[str, Any], text: str, prefix: str
    ) -> dict[str, Any]:
        message_id = f"{prefix}_{uuid.uuid4().hex}"
        emit_event(config, "final_output_done", message=text, message_id=message_id)
        return {
            "messages": [
                AIMessage(
                    content=text,
                    id=message_id,
                    additional_kwargs={"ui_visible": True, "tracing_status": prefix},
                )
            ]
        }

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
