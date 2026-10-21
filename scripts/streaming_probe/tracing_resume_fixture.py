"""Actual DeepAgents graph with deterministic specialists and a native pause.

Only for a self-owned, loopback test server with synthetic data. No alternate
publication graph is used: all orchestration and publication run production code.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/unit_tests/tracing"))

from fake_model import ScriptedChatModel  # noqa: E402
from fixtures import allow_publication, build_bundle, make_request  # noqa: E402
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langgraph.types import interrupt  # noqa: E402
from langgraph_sdk import Auth  # noqa: E402

import common.identity.runtime as identity_runtime  # noqa: E402
from common.identity.db import IdentityState  # noqa: E402
from intelligent_tracing.graph import build_tracing_graph  # noqa: E402
from intelligent_tracing.report_store import InMemoryReportStore  # noqa: E402
from intelligent_tracing.sources import FixtureDataSource  # noqa: E402


async def synthetic_identity_state(*args, **kwargs):
    """Provide a controlled identity dependency; do not contact real IAM."""
    return IdentityState(
        session_active=True,
        session_unexpired=True,
        session_ctx_version=1,
        user_active=True,
        account="probe",
        user_auth_revision=1,
        tenant_status="active",
        tenant_code="probe",
        tenant_auth_revision=1,
        membership_status="active",
        membership_revision=1,
    )


identity_runtime.fetch_identity_state = synthetic_identity_state

auth = Auth()


@auth.authenticate
async def authenticate(authorization: str | None):
    """Inject only a synthetic test identity, never accept client identity fields."""
    if authorization != "Bearer streaming-probe":
        raise Auth.exceptions.HTTPException(
            status_code=401, detail="probe token required"
        )
    return {
        "identity": "user-a",
        "display_name": "Streaming Probe",
        "is_authenticated": True,
        "tenant_id": "tenant-a",
        "membership_id": "membership-a",
        "session_id": "synthetic-session",
        "session_ctx_version": 1,
        "permissions": [],
    }


class StatelessLead(ScriptedChatModel):
    """Choose the next scripted role from checkpointed messages after restart."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any):
        completed = [m for m in messages if isinstance(m, ToolMessage)]
        if not any(m.name == "freeze_tracing_request" for m in completed):
            request = make_request()
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "freeze",
                        "name": "freeze_tracing_request",
                        "args": {
                            "station_code": request.target.station_code,
                            "region_code": request.target.region_code,
                            "region_name": request.target.region_name,
                            "pollutant": request.pollutant,
                            "start": request.time_window.start.isoformat(),
                            "end": request.time_window.end.isoformat(),
                            "neighbor_radius_km": request.neighbor_radius_km,
                            "analysis_id": request.analysis_id,
                            "revision": request.revision,
                            "baseline_start": request.baseline_window.start.isoformat(),
                            "baseline_end": request.baseline_window.end.isoformat(),
                        },
                    }
                ],
            )
        elif any(m.name == "build_validated_report" for m in completed):
            response = AIMessage(content="内部结束，不发布模型草稿。")
        else:
            calls = [
                c for m in messages if isinstance(m, AIMessage) for c in m.tool_calls
            ]
            tasks = [c for c in calls if c["name"] == "task"]
            roles = (
                ["station-data", "grid-data"]
                if not tasks
                else ["meteo-analyst"]
                if len(tasks) == 2
                else ["evidence-reviewer"]
                if len(tasks) == 3
                else []
            )
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": f"task-{role}",
                        "name": "task",
                        "args": {"description": role, "subagent_type": role},
                    }
                    for role in roles
                ]
                if roles
                else [{"id": "report", "name": "build_validated_report", "args": {}}],
            )
        return ChatResult(generations=[ChatGeneration(message=response)])


async def pause_at_section(section_id: str) -> None:
    """Pause before rendering the second claims section."""
    if section_id == "claims-2":
        interrupt({"section_id": section_id})


request = make_request()
bundle = build_bundle(scenario="transport")
graph = build_tracing_graph(
    model=StatelessLead(responses=[]),
    meteo_model=ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "meteo",
                        "name": "MeteoNarrative",
                        "args": {
                            "summary": "风场一致性可用",
                            "supplemental_checks": [],
                            "limitations_observed": [],
                        },
                    }
                ],
            )
        ]
    ),
    reviewer_model=ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "review",
                        "name": "EvidenceReview",
                        "args": {
                            "analysis_id": request.analysis_id,
                            "revision": 1,
                            "issues": [],
                            "contradictions": [],
                            "omissions": [],
                            "downgrade_suggestions": [],
                            "reviewed_claim_ids": [],
                            "review_summary": "保留限制",
                        },
                    }
                ],
            )
        ]
    ),
    data_source=FixtureDataSource(bundle),
    station_catalog=bundle.stations,
    report_store=InMemoryReportStore(),
    lifecycle_probe=allow_publication,
    section_barrier=pause_at_section,
)
