# ruff: noqa: D103, D107, T201
"""Controlled identity + LangGraph-flavoured fixture for the real Next.js BFF.

This server stands in for the admin API and the LangGraph platform so the
actual ``frontend/app/api/[..._path]/route.ts`` proxy can be exercised without
touching the formal 15432/19100 stack.  It runs the real compiled graphs and
streams their ``custom`` events in the platform SSE envelope.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tests/unit_tests/tracing") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests/unit_tests/tracing"))

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from loguru import logger  # noqa: E402

from basic_qa.builder import build_basic_qa_graph  # noqa: E402
from common.models import ModelRegistry  # noqa: E402
from data_analysis.graph import build_graph as build_data_graph  # noqa: E402
from scripts.streaming_probe.controlled import ControlledChatModel  # noqa: E402

GRAPH = os.environ.get("PROBE_GRAPH", "basic-qa")
app = FastAPI()

_state: dict[str, Any] = {
    "graph": None,
    "model": None,
    "release": None,
    "events": [],
    "source_finished": False,
    "started": False,
    "tracing_release": None,
    "tracing_reached": None,
    "tracing_deltas_at_barrier": [],
    "auth_me_hits": 0,
    "ticket_hits": 0,
    "admit_hits": 0,
    "stream_hits": 0,
    "revoked": False,
}


def _session_context() -> dict[str, Any]:
    return {
        "user": {
            "id": "user-probe",
            "account": "probe",
            "display_name": "Probe",
            "is_active": True,
            "last_login_at": None,
            "created_at": "2026-01-01T00:00:00Z",
            "roles": [],
            "followed_region_codes": [],
        },
        "session": {
            "session_id": "session-probe",
            "active_tenant_id": "tenant-probe",
            "ctx_version": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z",
        },
        "current_tenant": {
            "id": "tenant-probe",
            "code": "probe",
            "name": "Probe Tenant",
            "status": "active",
        },
        "membership": {
            "id": "membership-probe",
            "tenant_id": "tenant-probe",
            "department_id": None,
            "status": "active",
            "revision": 1,
            "role_codes": ["member"],
        },
        "permissions": [],
        "platform_admin": False,
        "context_version": 1,
        "tenants": [],
        "needs_tenant_selection": False,
    }


@app.get("/auth/me")
async def auth_me():
    _state["auth_me_hits"] += 1
    if _state.get("revoked"):
        return JSONResponse({"error": "会话已撤销"}, status_code=401)
    return _session_context()


@app.post("/auth/runtime-ticket")
async def runtime_ticket():
    _state["ticket_hits"] += 1
    return {"ticket": "probe-ticket", "expires_at": "2099-01-01T00:00:00Z"}


@app.post("/auth/login")
async def auth_login():
    response = JSONResponse(_session_context())
    response.headers.append(
        "set-cookie",
        "air_agent_session=00000000-0000-4000-8000-000000000000.probe; Path=/; HttpOnly; SameSite=Lax",
    )
    return response


@app.post("/threads/search")
async def search_threads():
    thread_id = _state.get("thread_id")
    if not thread_id:
        return []
    return [
        {
            "thread_id": thread_id,
            "metadata": {"graph_id": GRAPH, "title": "Probe thread"},
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    ]


@app.post("/agent/requests")
async def admit(request: Request):
    _state["admit_hits"] += 1
    body = await request.json()
    return {
        "request_id": "req-probe",
        "attempt_id": "att-probe",
        "submission_id": "sub-probe",
        "admission_status": "admitted",
        "operation_kind": body.get("operation_kind", "initial"),
        "reused": False,
        "thread_id": body.get("thread_id"),
        "graph_id": body.get("graph_id"),
        "ledger_marker": "probe-marker",
        "capability_selection": None,
    }


@app.post("/threads")
async def create_thread(request: Request):
    body = await request.json()
    return {"thread_id": body.get("thread_id") or "probe-thread"}


@app.post("/internal/threads/claim-confirmation")
async def claim_confirmation():
    return {"confirmed": True}


@app.get("/threads/{thread_id}/state")
async def thread_state(thread_id: str):
    return {"values": _state.get("last_values") or {"messages": []}}


@app.post("/probe/revoke")
async def probe_revoke():
    _state["revoked"] = True
    return {"revoked": True}


@app.post("/probe/unrevoke")
async def probe_unrevoke():
    _state["revoked"] = False
    return {"revoked": False}


@app.post("/probe/release")
async def probe_release():
    if _state["release"] is not None:
        _state["release"].set()
    if _state["tracing_release"] is not None:
        _state["tracing_release"].set()
    return {"released": True}


@app.get("/probe/state")
async def probe_state():
    return {
        "source_finished": _state["source_finished"],
        "started": _state["started"],
        "custom_events": len(_state["events"]),
        "deltas": len(
            [e for e in _state["events"] if e.get("type") == "final_output_delta"]
        ),
        "sections_at_barrier": _state["tracing_deltas_at_barrier"],
        "revoked": _state.get("revoked", False),
        "auth_me_hits": _state["auth_me_hits"],
        "ticket_hits": _state["ticket_hits"],
        "admit_hits": _state["admit_hits"],
        "stream_hits": _state["stream_hits"],
        "at_barrier": bool(
            (_state["model"] is not None and _state["model"].reached.is_set())
            or (
                _state["tracing_reached"] is not None
                and _state["tracing_reached"].is_set()
            )
        ),
    }


def _controlled_model() -> ControlledChatModel:
    model = ControlledChatModel()
    _state["release"] = model.release
    _state["model"] = model
    return model


def _install_revocation_probe() -> None:
    from basic_qa import nodes as bg_nodes
    from data_analysis import nodes as data_nodes
    from intelligent_tracing import section_publishing

    def _probe_factory(_config):
        async def _probe() -> bool:
            return not _state.get("revoked", False)

        return _probe

    bg_nodes.runtime_authorization_probe = _probe_factory
    data_nodes.runtime_authorization_probe = _probe_factory
    section_publishing.runtime_authorization_probe = _probe_factory


def _build_probe_graph():
    _install_revocation_probe()
    if GRAPH == "basic-qa":
        ModelRegistry.main_llm = _controlled_model()
        graph = build_basic_qa_graph(
            skills_dir=str(ROOT / "src/basic_qa/skills"), name="basic-qa"
        )
        return graph, {"messages": [HumanMessage(content="你好")]}
    if GRAPH == "data-analysis":
        ModelRegistry.main_llm = _controlled_model()
        graph = build_data_graph()
        return graph, {"messages": [HumanMessage(content="分析北京空气质量")]}
    return _build_tracing_graph()


def _build_tracing_graph():
    from fake_model import ScriptedChatModel
    from fixtures import allow_publication, build_bundle, make_request, make_scope

    from intelligent_tracing.graph import build_tracing_graph
    from intelligent_tracing.report_store import InMemoryReportStore
    from intelligent_tracing.sources import FixtureDataSource

    request = make_request()
    bundle = build_bundle(scenario="transport")
    lead = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "task", "args": {"description": "站点", "subagent_type": "station-data"}, "id": "t1"},
                    {"name": "task", "args": {"description": "网格", "subagent_type": "grid-data"}, "id": "t2"},
                ],
            ),
            AIMessage(content="", tool_calls=[{"name": "task", "args": {"description": "气象", "subagent_type": "meteo-analyst"}, "id": "t3"}]),
            AIMessage(content="", tool_calls=[{"name": "task", "args": {"description": "审查", "subagent_type": "evidence-reviewer"}, "id": "t4"}]),
            AIMessage(content="", tool_calls=[{"name": "build_validated_report", "args": {}, "id": "t5"}]),
            AIMessage(content="lead 草稿不应出现。"),
        ]
    )
    meteo = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "MeteoNarrative",
                        "args": {"summary": "ok", "supplemental_checks": [], "limitations_observed": []},
                        "id": "m1",
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
                            "analysis_id": request.analysis_id,
                            "revision": 1,
                            "issues": [],
                            "contradictions": [],
                            "omissions": [],
                            "downgrade_suggestions": [],
                            "reviewed_claim_ids": [],
                            "review_summary": "ok",
                        },
                        "id": "r1",
                    }
                ],
            )
        ]
    )
    reached = asyncio.Event()
    release = asyncio.Event()
    _state["tracing_reached"] = reached
    _state["tracing_release"] = release
    _state["tracing_deltas_at_barrier"] = []

    async def barrier(section_id: str) -> None:
        if section_id == "claims-2":
            reached.set()
            await release.wait()

    graph = build_tracing_graph(
        model=lead,
        meteo_model=meteo,
        reviewer_model=reviewer,
        data_source=FixtureDataSource(bundle),
        station_catalog=bundle.stations,
        report_store=InMemoryReportStore(),
        lifecycle_probe=allow_publication,
        section_barrier=barrier,
    )
    return graph, {
        "messages": [HumanMessage(content="分析目标站 PM2.5")],
        "request": request,
        "scope": make_scope(),
    }


async def _sse_stream(payload: dict[str, Any], thread_id: str):
    if _state["graph"] is None:
        _state["graph"], _state["initial"] = _build_probe_graph()
    payload = {**_state.get("initial", {}), **payload}
    run_id = "run-probe"
    yield f"event: metadata\ndata: {json.dumps({'run_id': run_id})}\n\n"
    _state["started"] = True
    config = {"configurable": {"thread_id": thread_id, "run_id": run_id}}
    try:
        async for mode, chunk in _state["graph"].astream(
            payload, config=config, stream_mode=["custom", "values"]
        ):
            if mode == "custom":
                if not str(chunk.get("type", "")).startswith("final_output_"):
                    continue
                _state["events"].append(chunk)
                if _state["tracing_reached"] is not None and _state["tracing_reached"].is_set():
                    _state["tracing_deltas_at_barrier"] = [
                        e.get("section_id")
                        for e in _state["events"]
                        if e.get("type") == "final_output_delta"
                    ]
                yield f"event: custom\ndata: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0)
            else:
                _state["last_values"] = chunk
                safe = json.dumps(
                    chunk,
                    ensure_ascii=False,
                    default=lambda value: value.model_dump(mode="json")
                    if hasattr(value, "model_dump")
                    else str(value),
                )
                yield f"event: values\ndata: {safe}\n\n"
        _state["source_finished"] = True
        yield "event: end\ndata: null\n\n"
    except Exception as exc:  # noqa: BLE001
        logger.exception("probe graph failed")
        yield f"event: error\ndata: {json.dumps({'error': type(exc).__name__})}\n\n"


@app.post("/threads/{thread_id}/runs/stream")
async def run_stream(thread_id: str, request: Request):
    _state["stream_hits"] += 1
    _state["thread_id"] = thread_id
    body = await request.json()
    payload = body.get("input") or {}
    return StreamingResponse(_sse_stream(payload, thread_id), media_type="text/event-stream")


@app.get("/threads/{thread_id}/runs")
async def list_runs(thread_id: str):
    return [{"run_id": "run-probe", "status": "cancelled"}]


@app.get("/threads/{thread_id}/runs/{run_id}")
async def get_run(thread_id: str, run_id: str):
    return {"run_id": run_id, "status": "cancelled"}


@app.post("/threads/{thread_id}/runs/{run_id}/cancel")
async def cancel_run(thread_id: str, run_id: str):
    return {"ok": True}


@app.get("/ok")
async def ok():
    return {"ok": True}
