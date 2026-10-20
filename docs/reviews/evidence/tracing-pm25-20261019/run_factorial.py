"""Reproduce the G0 fixture factorial and tool-boundary evidence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from intelligent_tracing.experiments import report_manifest, run_fixed_case
from intelligent_tracing.graph import build_tracing_graph
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.report_store import InMemoryReportStore
from intelligent_tracing.sources import FixtureDataSource
from fixtures import build_bundle, make_request, make_scope
from fake_model import ScriptedChatModel

HERE = Path(__file__).resolve().parent


def lead_model(include_reviewer: bool) -> ScriptedChatModel:
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "task", "args": {"description": "站点", "subagent_type": "station-data"}, "id": "t1"},
                {"name": "task", "args": {"description": "网格", "subagent_type": "grid-data"}, "id": "t2"},
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[{"name": "task", "args": {"description": "气象", "subagent_type": "meteo-analyst"}, "id": "t3"}],
        ),
    ]
    if include_reviewer:
        responses.append(
            AIMessage(
                content="",
                tool_calls=[{"name": "task", "args": {"description": "审查", "subagent_type": "evidence-reviewer"}, "id": "t4"}],
            )
        )
    responses.extend(
        [
            AIMessage(content="", tool_calls=[{"name": "build_validated_report", "args": {}, "id": "t5"}]),
            AIMessage(content="模型自由文本不发布。"),
        ]
    )
    return ScriptedChatModel(responses=responses)


def meteo_model() -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "MeteoNarrative", "args": {"summary": "风场可用", "supplemental_checks": [], "limitations_observed": []}, "id": "m1"}],
            )
        ]
    )


def reviewer_model(analysis_id: str) -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "EvidenceReview", "args": {"analysis_id": analysis_id, "revision": 1, "issues": ["缺少源清单"], "contradictions": [], "omissions": [], "downgrade_suggestions": [], "reviewed_claim_ids": [], "review_summary": "保留限制"}, "id": "r1"}],
            )
        ],
    )


async def deepagents(reviewer: bool):
    request = make_request()
    lead = lead_model(include_reviewer=reviewer)
    meteo = meteo_model()
    rev = reviewer_model(request.analysis_id)
    graph = build_tracing_graph(
        model=lead,
        meteo_model=meteo,
        reviewer_model=rev,
        data_source=FixtureDataSource(build_bundle()),
        station_catalog=build_bundle().stations,
        report_store=InMemoryReportStore(),
        enable_reviewer=reviewer,
    )
    out = await graph.ainvoke(
        {"messages": [HumanMessage(content="分析")], "request": request, "scope": make_scope()},
        config={"configurable": {"thread_id": "thread-a", "execution_epoch": "epoch-1", "ledger_attempt_id": "attempt-evidence", "run_id": "run-evidence"}},
    )
    return lead, meteo, rev, out


async def main() -> None:
    bundle = build_bundle()
    request = make_request()
    scope = make_scope()
    manifests = []
    for _ in range(3):
        a0 = await run_fixed_case("A0", request, bundle, scope=scope, policy=DEFAULT_POLICY)
        a1 = await run_fixed_case("A1", request, bundle, scope=scope, policy=DEFAULT_POLICY, reviewer_model=reviewer_model(request.analysis_id))
        b0_lead, _b0_meteo, _b0_rev, b0 = await deepagents(False)
        b1_lead, b1_meteo, b1_rev, b1 = await deepagents(True)
        manifests.extend(
            [
                report_manifest(a0, model_name="none"),
                report_manifest(a1, model_name="scripted-reviewer"),
                {"group": "B0", "model": "scripted-lead", "report_digest": b0["validated_report"].content_sha256, "lead_calls": b0_lead.calls, "report_id": b0["validated_report"].report_id},
                {"group": "B1", "model": "scripted-lead", "report_digest": b1["validated_report"].content_sha256, "lead_calls": b1_lead.calls, "report_id": b1["validated_report"].report_id},
            ]
        )
    (HERE / "factorial-fixture.json").write_text(json.dumps(manifests, ensure_ascii=False, indent=2) + "\n")
    boundaries = {
        "lead_tools": sorted(b1_lead.bound_tools),
        "task_description": b1_lead.bound_tools.get("task", ""),
        "meteo_tools": sorted(b1_meteo.bound_tools),
        "reviewer_tools": sorted(b1_rev.bound_tools),
        "child_executions": [
            {"role": child.role, "status": child.status, "attempt_id": child.attempt_id, "run_id": child.run_id}
            for child in b1.get("child_executions", ())
        ],
        "trajectory_or_pscf_tools": [
            name
            for name in b1_lead.bound_tools
            if "trajectory" in name.lower() or "pscf" in name.lower()
        ],
    }
    (HERE / "g0-tool-boundaries.json").write_text(json.dumps(boundaries, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
