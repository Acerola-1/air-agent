"""Controlled model and identity on the official isolated LangGraph API.

Only the probe launcher registers this module. Production routing, dispatch,
specialists, evidence gates and publication are used unchanged.
"""

from __future__ import annotations

import re
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/unit_tests/tracing"))

from conversation_fixtures import analysis_conditions, plan_message  # noqa: E402
from fake_model import ScriptedChatModel  # noqa: E402
from fixtures import allow_publication, build_bundle  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.runnables import RunnableConfig  # noqa: E402
from langgraph_sdk import Auth  # noqa: E402

import common.event_ledger.lifecycle as lifecycle  # noqa: E402
import common.event_ledger.store as ledger_store  # noqa: E402
import common.identity.runtime as identity  # noqa: E402
from common.business_registry import BUSINESS_POLICY_VERSION  # noqa: E402
from intelligent_tracing.graph import build_tracing_graph  # noqa: E402
from intelligent_tracing.report_store import InMemoryReportStore  # noqa: E402
from intelligent_tracing.sources import FixtureDataSource  # noqa: E402

identity.revalidate = AsyncMock()
identity._guard_knowledge_context = AsyncMock()
lifecycle._on_graph_end = AsyncMock()
ledger_store.thread_deletion_exists = AsyncMock(return_value=False)
auth = Auth()
app = FastAPI()


@app.post("/internal/threads/claim-confirmation")
async def confirm_claim(request: Request):
    """Test-only claim facade used by the real BFF, never formal ownership."""
    if (
        request.headers.get("authorization") != "Bearer conversation-probe"
        or request.headers.get("x-internal-token") != "conversation-probe"
    ):
        return JSONResponse({"confirmed": False}, status_code=403)
    return {"confirmed": True}


@auth.authenticate
async def authenticate(authorization: str | None):
    """Only the test runtime ticket is accepted by this self-owned server."""
    if authorization not in {
        "Bearer conversation-probe",
        "Bearer conversation-probe-b",
    }:
        raise Auth.exceptions.HTTPException(
            status_code=401, detail="probe ticket required"
        )
    tenant = "tenant-b" if authorization.endswith("-b") else "tenant-a"
    return {
        "identity": "user-a",
        "is_authenticated": True,
        "display_name": "Conversation Probe",
        "tenant_id": tenant,
        "membership_id": "membership-" + tenant[-1],
        "session_id": "probe-session",
        "session_ctx_version": 1,
        "permissions": ["agent:business:intelligent-tracing:use"],
    }


@auth.on.threads
async def thread_scope(ctx, value):
    """Filter owned test threads without using the formal ledger backend."""
    tenant = ctx.user["tenant_id"]
    if ctx.action == "create":
        if (value.get("metadata") or {}).get("tenant_id") != tenant:
            raise Auth.exceptions.HTTPException(
                status_code=403, detail="test tenant mismatch"
            )
        return True
    return {"tenant_id": tenant, "user_id": "user-a"}


class ProbeLead(ScriptedChatModel):
    """Fixed test stimuli, derived from the current turn, not a production router."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        index = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        current = messages[index:]
        question = current[0].content
        if question == "停止验收":
            time.sleep(8)
        completed = [m for m in current if isinstance(m, ToolMessage)]
        if not any(m.name == "submit_tracing_turn" for m in completed):
            if "半径" in question and "分析" not in question:
                value = "20" if "二十" in question or "20" in question else "10"
                response = plan_message(
                    {
                        "intent": "analysis",
                        "inherit_previous": True,
                        "slots": [
                            {
                                "field": "neighbor_radius_km",
                                "value": value,
                                "source_text": question,
                            }
                        ],
                    }
                )
            elif "请分析" in question:
                conditions = analysis_conditions()
                slots = [
                    {"field": k, "value": v, "source_text": v}
                    for k, v in conditions.items()
                    if v in question
                    and (
                        k != "neighbor_radius_km"
                        or re.search(r"(?:^|\s)10(?:\s|$)", question)
                    )
                ]
                response = plan_message({"intent": "analysis", "slots": slots})
            else:
                response = plan_message(
                    {
                        "intent": "conversation",
                        "blocks": [
                            "greeting",
                            "overview",
                            "station",
                            "weather",
                            "workflow",
                            "boundary",
                        ],
                    }
                )
        elif not any(m.name == "freeze_tracing_request" for m in completed):
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "freeze_tracing_request",
                        "args": {},
                        "id": str(uuid.uuid4()),
                    }
                ],
            )
        elif any(m.name == "build_validated_report" for m in completed):
            response = AIMessage(content="结束，内部正文不可见。")
        else:
            tasks = [
                c
                for m in current
                if isinstance(m, AIMessage)
                for c in m.tool_calls
                if c["name"] == "task"
            ]
            roles = (
                ["station-data", "grid-data"]
                if not tasks
                else ["meteo-analyst"]
                if len(tasks) == 2
                else ["evidence-reviewer"]
                if len(tasks) == 3
                else []
            )
            calls = [
                {
                    "name": "task",
                    "args": {"description": "分析已确认条件", "subagent_type": r},
                    "id": str(uuid.uuid4()),
                }
                for r in roles
            ]
            response = AIMessage(
                content="",
                tool_calls=calls
                or [
                    {
                        "name": "build_validated_report",
                        "args": {},
                        "id": str(uuid.uuid4()),
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=response)])


meteo = ScriptedChatModel(
    responses=[
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "MeteoNarrative",
                    "args": {
                        "summary": "受控气象说明",
                        "supplemental_checks": [],
                        "limitations_observed": [],
                    },
                    "id": "meteo",
                }
            ],
        )
    ]
)
reviewer = ScriptedChatModel(
    responses=[
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "EvidenceReview",
                    "args": {
                        "analysis_id": "advisory-only",
                        "revision": 1,
                        "issues": [],
                        "contradictions": [],
                        "omissions": [],
                        "downgrade_suggestions": [],
                        "reviewed_claim_ids": [],
                        "review_summary": "保留限制",
                    },
                    "id": "review",
                }
            ],
        )
    ]
)
_graph = build_tracing_graph(
    model=ProbeLead(responses=[]),
    meteo_model=meteo,
    reviewer_model=reviewer,
    data_source=FixtureDataSource(build_bundle()),
    station_catalog=build_bundle().stations,
    report_store=InMemoryReportStore(),
    lifecycle_probe=allow_publication,
)


def graph(config: RunnableConfig):
    """Inject only test-owned business context, retaining platform execution IDs."""
    cfg = dict(config.get("configurable") or {})
    meta = config.get("metadata") or {}
    cfg.update(
        ledger_business_code="intelligent-tracing",
        ledger_business_policy_version=BUSINESS_POLICY_VERSION,
        ledger_business_permission="agent:business:intelligent-tracing:use",
        ledger_graph_id="intelligent-tracing",
    )
    cfg.setdefault(
        "ledger_attempt_id", str(meta.get("ledger_attempt_id") or uuid.uuid4())
    )
    cfg.setdefault(
        "run_id",
        str(
            cfg.get("langgraph_run_id")
            or meta.get("run_id")
            or config.get("run_id")
            or uuid.uuid4()
        ),
    )
    cfg.setdefault("execution_epoch", cfg["run_id"])
    return _graph.with_config(configurable=cfg)
