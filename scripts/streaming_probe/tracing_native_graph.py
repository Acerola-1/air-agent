# ruff: noqa: D101, D103, T201
"""Controlled official-native entry for the tracing section publication pipeline.

This graph runs the real ``ReportGate``/``publish_frozen_plan`` path with fixture
evidence and forwards only the resulting ``final_output_*`` custom events on the
official LangGraph server.  Four-specialist DeepAgents execution is covered by
the in-process/BFF controlled probes; this entry isolates the official native
SSE behaviour of the tracing publication lifecycle.
"""

from __future__ import annotations

import sys
import uuid
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import StreamWriter

sys.path.insert(0, "/deps/air_agent/src")
sys.path.insert(0, "/deps/air_agent/tests/unit_tests/tracing")

from fixtures import build_bundle, make_request, make_scope  # noqa: E402

from intelligent_tracing.claims import fallback_plan  # noqa: E402
from intelligent_tracing.evidence import EvidenceRegistry  # noqa: E402
from intelligent_tracing.gate import ReportGate  # noqa: E402
from intelligent_tracing.orchestrator import analyze_bundle  # noqa: E402
from intelligent_tracing.policy import DEFAULT_POLICY  # noqa: E402
from intelligent_tracing.report_store import InMemoryReportStore  # noqa: E402
from intelligent_tracing.schemas import RuntimeScope, TracingRequest  # noqa: E402
from intelligent_tracing.section_publishing import publish_frozen_plan  # noqa: E402


class NativeState(TypedDict):
    messages: Annotated[list[Any], add_messages]
    request: dict[str, Any]
    scope: dict[str, Any]


async def run(
    state: NativeState, config: RunnableConfig, *, writer: StreamWriter
) -> dict[str, Any]:
    request = TracingRequest.model_validate(state["request"])
    scope = RuntimeScope.model_validate(state["scope"])
    registry = EvidenceRegistry()
    analysis = analyze_bundle(
        request,
        build_bundle(scenario="transport"),
        registry=registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    gate = ReportGate(registry=registry, policy=DEFAULT_POLICY)
    frozen, errors = gate.precheck(
        fallback_plan(analysis.allowlist),
        analysis.allowlist,
        request,
        scope,
        report_id=f"rep_{uuid.uuid4().hex}",
    )
    if frozen is None:
        return {"messages": [AIMessage(content="report precheck failed: " + ",".join(errors))]}
    _session, report = await publish_frozen_plan(
        gate=gate,
        report_store=InMemoryReportStore(),
        frozen=frozen,
        config=dict(config or {}),
        writer=writer,
    )
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
        ]
    }


builder = StateGraph(NativeState)
builder.add_node("run", run)
builder.add_edge(START, "run")
graph = builder.compile()

DEFAULT_REQUEST = make_request().model_dump(mode="json")
DEFAULT_SCOPE = make_scope().model_dump(mode="json")
