"""Per-turn routing and dispatch guards for the tracing lead.

The model proposes an intent and quoted slots. Only this middleware can mint
the current execution binding; neither a saved report nor a model label is a
tool permission. Conversation content is a plan of registered facts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ExtendedModelResponse,
    ModelResponse,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from pydantic import ValidationError

from common.observability.context import current_observation_config
from intelligent_tracing.conversation import (
    ANALYSIS_TOOLS,
    PLAN_TOOL,
    capability_catalog,
)
from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.runtime import TracingScopeError, runtime_scope_from_config
from intelligent_tracing.schemas import (
    RuntimeScope,
    StrictModel,
    TimeWindow,
    TracingRequestDraft,
    TracingTurnPlan,
    TurnContext,
    TurnDecision,
)

logger = logging.getLogger(__name__)


class SubmitTurnInput(StrictModel):
    """Strict model-visible envelope, without trusted identity fields."""

    plan: TracingTurnPlan


def build_turn_tool() -> StructuredTool:
    """Build the only tool available for the initial lead decision."""

    async def _submit(plan: TracingTurnPlan) -> str:
        raise RuntimeError("turn dispatcher required")

    return StructuredTool.from_function(
        coroutine=_submit,
        name=PLAN_TOOL,
        args_schema=SubmitTurnInput,
        description="每轮首先提交意图与回复计划。普通问候/能力说明使用conversation及已登记block；真实分析或补充条件使用analysis，slots注明本轮原文source_text。不得提交自由正文。",
    )


def scope_binding(scope: RuntimeScope) -> dict[str, Any]:
    """Return the identity binding allowed to share a conditions draft."""
    return scope.model_dump(exclude={"epoch"})


def turn_context(state: Any, config: dict[str, Any]) -> TurnContext:
    """Derive turn identity from authenticated config and the actual input."""
    scope = runtime_scope_from_config(config)
    cfg = config.get("configurable") or {}
    meta = config.get("metadata") or {}
    run_id = str(
        cfg.get("run_id") or cfg.get("langgraph_run_id") or meta.get("run_id") or ""
    )
    attempt = str(cfg.get("ledger_attempt_id") or meta.get("ledger_attempt_id") or "")
    if not run_id or not attempt:
        raise TracingScopeError("缺少可信运行或受理标识")
    human = next(
        (m for m in reversed(state.get("messages", [])) if isinstance(m, HumanMessage)),
        None,
    )
    if human is None or not isinstance(human.content, str) or not human.content.strip():
        raise TracingScopeError("缺少本轮用户输入")
    digest = hashlib.sha256(human.content.encode()).hexdigest()
    message_id = str(human.id or digest)
    raw = json.dumps(
        [scope.model_dump(), run_id, attempt, message_id, digest], sort_keys=True
    )
    return TurnContext(
        key=hashlib.sha256(raw.encode()).hexdigest(),
        run_id=run_id,
        attempt_id=attempt,
        message_id=message_id,
        input_digest=digest,
        scope=scope,
    )


def current_decision(state: Any, config: dict[str, Any]) -> TurnDecision | None:
    """Never accept a checkpoint's routing decision for a different execution."""
    decision = state.get("turn_decision")
    if not isinstance(decision, TurnDecision):
        return None
    try:
        return decision if decision.context == turn_context(state, config) else None
    except TracingScopeError:
        return None


def _date(value: str, timezone: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        result.replace(tzinfo=ZoneInfo(timezone))
        if result.tzinfo is None
        else result.astimezone(ZoneInfo(timezone))
    )


def _quoted_value(field: str, value: str, quote: str) -> bool:
    """Reject fabricated extractions, including numeric scope broadening."""
    if field == "neighbor_radius_km":
        digits = dict(zip("一二三四五六七八九", range(1, 10), strict=True))
        chinese = {**digits, "两": 2, "十": 10}
        chinese.update({"十" + digit: 10 + number for digit, number in digits.items()})
        for tens in range(2, 6):
            prefix = "二三四五"[tens - 2] + "十"
            chinese[prefix] = tens * 10
            chinese.update(
                {prefix + digit: tens * 10 + number for digit, number in digits.items()}
            )
        numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", quote)]
        numbers += [
            chinese[token]
            for token in re.findall(r"[一二两三四五六七八九十百]+", quote)
            if token in chinese
        ]
        try:
            return float(value) in numbers
        except ValueError:
            return False
    if field in {"start", "end", "baseline_start", "baseline_end"}:
        try:
            dt = _date(value, "Asia/Shanghai")
        except ValueError:
            return False
        # A quote can contain both endpoints; verify the complete timestamp,
        # including minutes/offset, rather than using only its first digits.
        pattern = r"\d{4}-\d{1,2}-\d{1,2}[T ]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?"
        for match in re.finditer(pattern, quote):
            try:
                if _date(match.group(), "Asia/Shanghai") == dt:
                    return True
            except ValueError:
                continue
        return False
    if field == "pollutant":

        def normalize(s: str) -> str:
            return s.lower().replace("₂", "2").replace("₅", "5").replace(" ", "")

        return normalize(value) in normalize(quote)
    if field == "timezone" and value == "Asia/Shanghai":
        return "北京时间" in quote or "Asia/Shanghai" in quote
    return value.lower() in quote.lower()


def merge_draft(
    plan: TracingTurnPlan,
    context: TurnContext,
    text: str,
    previous: TracingRequestDraft | None,
) -> TracingRequestDraft:
    """Merge exact user-backed extractions within one identity context."""
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    if (
        plan.inherit_previous
        and isinstance(previous, TracingRequestDraft)
        and scope_binding(previous.scope) == scope_binding(context.scope)
    ):
        values.update(previous.values)
        sources.update(previous.sources)
    for key in plan.reset_fields:
        values.pop(key, None)
        sources.pop(key, None)
    seen = set()
    for slot in plan.slots:
        if (
            slot.field in seen
            or slot.source_text not in text
            or not _quoted_value(slot.field, slot.value, slot.source_text)
        ):
            raise ValueError("unbacked or conflicting slot")
        seen.add(slot.field)
        values[slot.field] = slot.value.strip()
        sources[slot.field] = context.message_id
    return TracingRequestDraft(scope=context.scope, values=values, sources=sources)


def draft_issues(
    draft: TracingRequestDraft, policy: AnalysisPolicy
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Check required and conflicting conditions without any network access."""
    values = draft.values
    missing = []
    invalid = []
    if not (values.get("station_code") or values.get("station_name")):
        missing.append("target")
    missing.extend(
        k
        for k in ("region_name", "start", "end", "neighbor_radius_km")
        if not values.get(k)
    )
    try:
        if (
            values.get("neighbor_radius_km")
            and not 1 <= float(values["neighbor_radius_km"]) <= 50
        ):
            invalid.append("neighbor_radius_km")
    except ValueError:
        invalid.append("neighbor_radius_km")
    timezone = values.get("timezone", "Asia/Shanghai")
    if timezone != "Asia/Shanghai":
        invalid.append("timezone")
    try:
        policy.require_supported(values.get("pollutant", "pm2.5").lower())
    except ValueError:
        invalid.append("pollutant")
    if values.get("start") and values.get("end"):
        try:
            window = TimeWindow(
                start=_date(values["start"], timezone),
                end=_date(values["end"], timezone),
            )
        except (ValueError, KeyError):
            invalid.append("time_window")
        else:
            if values.get("baseline_start") and values.get("baseline_end"):
                try:
                    baseline = TimeWindow(
                        start=_date(values["baseline_start"], timezone),
                        end=_date(values["baseline_end"], timezone),
                    )
                    if baseline.end > window.start:
                        invalid.append("baseline_window")
                except (ValueError, KeyError):
                    invalid.append("baseline_window")
    if bool(values.get("baseline_start")) != bool(values.get("baseline_end")):
        missing.append(
            "baseline_end" if values.get("baseline_start") else "baseline_start"
        )
    return tuple(missing), tuple(invalid)


def freeze_arguments(
    decision: TurnDecision, draft: TracingRequestDraft, config: dict[str, Any]
) -> dict[str, Any]:
    """Freeze the reviewed candidate, never a second model-provided range."""
    args: dict[str, Any] = dict(draft.values)
    args.update(analysis_id=decision.analysis_id, revision=decision.revision)
    timezone = args.setdefault("timezone", "Asia/Shanghai")
    args.setdefault("pollutant", "pm2.5")
    for key in ("start", "end", "baseline_start", "baseline_end"):
        if key in args:
            args[key] = _date(args[key], timezone).isoformat()
    args["neighbor_radius_km"] = float(args["neighbor_radius_km"])
    cfg = config.get("configurable") or {}
    args["capability_snapshot_ref"] = str(cfg.get("capability_selection_id") or "")
    return args


class TracingTurnMiddleware(AgentMiddleware):
    """Restrict both model-visible schemas and actual tool dispatch by turn."""

    def __init__(self, policy: AnalysisPolicy, tool_names: frozenset[str]) -> None:
        """Freeze the policy and registered conversation facts."""
        self.policy = policy
        self.catalog = capability_catalog(policy, tool_names)

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any]:
        """Start a fresh decision for a new input, preserving resumable turns."""
        config = current_observation_config(runtime)
        context = turn_context(state, config)
        if current_decision(state, config):
            return {"turn_context": context}
        return {
            "turn_context": context,
            "turn_decision": None,
            "turn_publication": None,
            "budget_exhausted": False,
        }

    def _fallback(
        self, state: Any, config: dict[str, Any], reason: str
    ) -> TurnDecision:
        return TurnDecision(
            context=turn_context(state, config),
            proposed_intent="unknown",
            route="fallback",
            reason=reason,
        )

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """Offer only permitted schemas and reject inconsistent call batches."""
        config = current_observation_config(getattr(request, "runtime", None))
        state = request.state
        decision = current_decision(state, config)
        if decision is None and state.get("turn_decision") is not None:
            return ModelResponse(
                result=[AIMessage(content="", additional_kwargs={"ui_visible": False})]
            )
        if decision and decision.route != "analysis":
            return ModelResponse(
                result=[AIMessage(content="", additional_kwargs={"ui_visible": False})]
            )
        allowed = (
            {PLAN_TOOL}
            if decision is None
            else (
                ANALYSIS_TOOLS - {"freeze_tracing_request"}
                if decision.frozen
                else {"freeze_tracing_request"}
            )
        )
        tools = [
            t
            for t in request.tools
            if (
                getattr(t, "name", None)
                or (t.get("name") if isinstance(t, dict) else None)
            )
            in allowed
        ]
        prompt = (
            "\n本轮必须先且仅调用submit_tracing_turn，不输出自由正文。不要复制历史工具调用。\n"
            if decision is None
            else "\n按本轮已批准分析继续。\n"
        )
        prompt += "交流blocks目录：" + json.dumps(
            self.catalog.entries, ensure_ascii=False
        )
        previous = state.get("request_draft")
        if isinstance(previous, TracingRequestDraft) and scope_binding(
            previous.scope
        ) == scope_binding(turn_context(state, config).scope):
            prompt += "\n可明确继承的条件草稿：" + json.dumps(
                previous.values, ensure_ascii=False
            )
        existing = getattr(request, "system_message", None)
        response = await handler(
            request.override(
                tools=tools,
                system_message=SystemMessage(
                    content=str(existing.content if existing else "") + prompt
                ),
            )
        )
        inner = (
            response.model_response
            if isinstance(response, ExtendedModelResponse)
            else response
        )
        messages = inner.result if isinstance(inner, ModelResponse) else [inner]
        calls = [c for m in messages if isinstance(m, AIMessage) for c in m.tool_calls]
        if decision is None and (len(calls) != 1 or calls[0]["name"] != PLAN_TOOL):
            return ExtendedModelResponse(
                model_response=ModelResponse(
                    result=[
                        AIMessage(content="", additional_kwargs={"ui_visible": False})
                    ]
                ),
                command=Command(
                    update={
                        "turn_decision": self._fallback(state, config, "invalid_plan")
                    }
                ),
            )
        if decision is not None and (
            any(c["name"] not in allowed for c in calls)
            or (not decision.frozen and len(calls) > 1)
        ):
            if decision.frozen:
                return ModelResponse(
                    result=[
                        AIMessage(content="", additional_kwargs={"ui_visible": False})
                    ]
                )
            return ExtendedModelResponse(
                model_response=ModelResponse(
                    result=[
                        AIMessage(content="", additional_kwargs={"ui_visible": False})
                    ]
                ),
                command=Command(
                    update={
                        "turn_decision": self._fallback(
                            state, config, "incompatible_tools"
                        )
                    }
                ),
            )
        return response

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Enforce the current turn before invoking any tool implementation."""
        config = current_observation_config(getattr(request, "runtime", None))
        state = request.state
        call = request.tool_call
        name = call["name"]
        decision = current_decision(state, config)
        if decision is None and isinstance(state.get("turn_decision"), TurnDecision):
            return Command(
                update={
                    "turn_decision": state["turn_decision"].model_copy(
                        update={"reason": "execution_changed"}
                    ),
                    "messages": [self._rejected(call)],
                }
            )
        if name == PLAN_TOOL and decision is None:
            return self._submit(state, config, call)
        if decision is None or decision.route != "analysis":
            return self._rejected(call)
        if not decision.frozen:
            if name != "freeze_tracing_request":
                return self._rejected(call)
            draft = state.get("request_draft")
            if not isinstance(draft, TracingRequestDraft) or scope_binding(
                draft.scope
            ) != scope_binding(decision.context.scope):
                return self._rejected(call)
            result = await handler(
                request.override(
                    tool_call={
                        **call,
                        "args": freeze_arguments(decision, draft, config),
                    }
                )
            )
            if (
                isinstance(result, Command)
                and isinstance(result.update, dict)
                and result.update.get("request")
            ):
                frozen = result.update["request"]
                for key in (
                    "station_findings",
                    "grid_findings",
                    "meteo_findings",
                    "evidence_review",
                    "allowlist",
                    "validated_report",
                    "publication_session",
                ):
                    result.update[key] = None
                result.update["turn_decision"] = decision.model_copy(
                    update={"frozen": True, "request_digest": frozen.request_digest}
                )
            else:
                content = result.content if isinstance(result, ToolMessage) else ""
                try:
                    payload = json.loads(content) if isinstance(content, str) else {}
                except (ValueError, TypeError):
                    payload = {}
                reason = payload.get("status")
                candidates = tuple(
                    str(x.get("station_code", ""))
                    for x in payload.get("candidates", [])
                    if isinstance(x, dict)
                )
                reason = (
                    "station_ambiguous"
                    if reason == "needs_disambiguation"
                    else "station_unavailable"
                    if reason == "catalog_unavailable"
                    else "station_invalid"
                )
                return Command(
                    update={
                        "turn_decision": decision.model_copy(
                            update={
                                "route": "clarify",
                                "reason": reason,
                                "candidates": candidates,
                            }
                        ),
                        "messages": [result] if isinstance(result, ToolMessage) else [],
                    }
                )
            return result
        frozen = state.get("request")
        if (
            name not in ANALYSIS_TOOLS
            or name == "freeze_tracing_request"
            or frozen is None
            or frozen.analysis_id != decision.analysis_id
            or frozen.revision != decision.revision
            or frozen.request_digest != decision.request_digest
            or state.get("scope") != decision.context.scope
        ):
            return self._rejected(call)
        return await handler(request)

    @staticmethod
    def _rejected(call: dict[str, Any]) -> ToolMessage:
        return ToolMessage(
            content="当前轮未许可该操作。",
            tool_call_id=call["id"],
            name=call["name"],
            status="error",
        )

    def _submit(
        self, state: Any, config: dict[str, Any], call: dict[str, Any]
    ) -> Command:
        context = turn_context(state, config)
        text = next(
            m.content
            for m in reversed(state["messages"])
            if isinstance(m, HumanMessage)
        )
        try:
            plan = SubmitTurnInput.model_validate(call["args"]).plan
            if plan.intent == "conversation":
                if (
                    plan.slots
                    or plan.reset_fields
                    or plan.inherit_previous
                    or not plan.blocks
                    or any(k not in self.catalog.entries for k in plan.blocks)
                ):
                    raise ValueError("invalid conversation plan")
                decision = TurnDecision(
                    context=context,
                    proposed_intent=plan.intent,
                    route="conversation",
                    blocks=tuple(dict.fromkeys(plan.blocks)),
                    tone=plan.tone,
                    catalog_version=self.catalog.version,
                )
                draft = state.get("request_draft")
            else:
                draft = merge_draft(plan, context, text, state.get("request_draft"))
                missing, invalid = draft_issues(draft, self.policy)
                previous = state.get("request")
                inherit = (
                    plan.inherit_previous
                    and previous is not None
                    and state.get("scope") is not None
                    and scope_binding(state["scope"]) == scope_binding(context.scope)
                )
                decision = TurnDecision(
                    context=context,
                    proposed_intent=plan.intent,
                    route="clarify" if missing or invalid else "analysis",
                    missing=missing,
                    invalid=invalid,
                    analysis_id=previous.analysis_id
                    if inherit
                    else "analysis_" + uuid.uuid4().hex,
                    revision=previous.revision + 1 if inherit else 1,
                )
        except (ValidationError, ValueError, TypeError):
            decision = self._fallback(state, config, "invalid_plan")
            draft = state.get("request_draft")
        logger.info(
            "tracing turn proposed=%s selected=%s reason=%s attempt=%s run=%s",
            decision.proposed_intent,
            decision.route,
            decision.reason or "none",
            context.attempt_id,
            context.run_id,
        )
        return Command(
            update={
                "turn_decision": decision,
                "request_draft": draft,
                "messages": [
                    ToolMessage(
                        content=json.dumps(
                            {
                                "route": decision.route,
                                "missing": decision.missing,
                                "invalid": decision.invalid,
                            }
                        ),
                        tool_call_id=call["id"],
                        name=PLAN_TOOL,
                    )
                ],
            }
        )
