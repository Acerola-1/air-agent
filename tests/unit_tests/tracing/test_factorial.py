"""A0/A1/B0/B1 factorial smoke over the same kernel and renderer."""

from __future__ import annotations

import json

import pytest
from fake_model import ScriptedChatModel
from fixtures import build_bundle, make_request, make_scope
from langchain_core.messages import AIMessage, HumanMessage

from intelligent_tracing.experiments import (
    report_manifest,
    run_fixed_case,
)
from intelligent_tracing.graph import build_tracing_graph
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.report_store import InMemoryReportStore
from intelligent_tracing.sources import FixtureDataSource


def _lead_model(*, include_reviewer: bool) -> ScriptedChatModel:
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"description": "站点", "subagent_type": "station-data"},
                    "id": "t1",
                },
                {
                    "name": "task",
                    "args": {"description": "网格", "subagent_type": "grid-data"},
                    "id": "t2",
                },
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"description": "气象", "subagent_type": "meteo-analyst"},
                    "id": "t3",
                }
            ],
        ),
    ]
    if include_reviewer:
        responses.append(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "审查",
                            "subagent_type": "evidence-reviewer",
                        },
                        "id": "t4",
                    }
                ],
            )
        )
    responses.extend(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "build_validated_report", "args": {}, "id": "t5"}],
            ),
            AIMessage(content="模型自由文本不发布。"),
        ]
    )
    return ScriptedChatModel(responses=responses)


def _meteo_model() -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "MeteoNarrative",
                        "args": {
                            "summary": "风场可用",
                            "supplemental_checks": [],
                            "limitations_observed": [],
                        },
                        "id": "m1",
                    }
                ],
            )
        ]
    )


def _reviewer_model(analysis_id: str) -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "EvidenceReview",
                        "args": {
                            "analysis_id": analysis_id,
                            "revision": 1,
                            "issues": ["缺少源清单"],
                            "contradictions": [],
                            "omissions": [],
                            "downgrade_suggestions": [],
                            "reviewed_claim_ids": [],
                            "review_summary": "保留限制",
                        },
                        "id": "r1",
                    }
                ],
            )
        ]
    )


async def _run_deepagents(*, reviewer: bool):
    request = make_request()
    lead = _lead_model(include_reviewer=reviewer)
    graph = build_tracing_graph(
        model=lead,
        meteo_model=_meteo_model(),
        reviewer_model=_reviewer_model(request.analysis_id),
        data_source=FixtureDataSource(build_bundle()),
        station_catalog=build_bundle().stations,
        report_store=InMemoryReportStore(),
        enable_reviewer=reviewer,
    )
    out = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="分析")],
            "request": request,
            "scope": make_scope(),
        },
        config={
            "configurable": {
                "thread_id": "thread-a",
                "execution_epoch": "epoch-1",
            }
        },
    )
    return lead, out["validated_report"], out


@pytest.mark.anyio
async def test_factorial_cells_share_deterministic_report():
    request = make_request()
    bundle = build_bundle()
    scope = make_scope()
    manifests = []
    for iteration in range(3):
        a0 = await run_fixed_case(
            "A0", request, bundle, scope=scope, policy=DEFAULT_POLICY
        )
        a1 = await run_fixed_case(
            "A1",
            request,
            bundle,
            scope=scope,
            policy=DEFAULT_POLICY,
            reviewer_model=_reviewer_model(request.analysis_id),
        )
        b0_lead, b0_report, _ = await _run_deepagents(reviewer=False)
        b1_lead, b1_report, _ = await _run_deepagents(reviewer=True)
        digests = {result.report.content_sha256 for result in (a0, a1)} | {
            b0_report.content_sha256,
            b1_report.content_sha256,
        }
        assert len(digests) == 1
        assert a0.analysis.allowlist.digest == a1.analysis.allowlist.digest
        assert a0.report.claim_ids == b0_report.claim_ids == b1_report.claim_ids
        assert b0_lead.calls >= 1 and b1_lead.calls >= b0_lead.calls
        manifests.extend(
            [
                report_manifest(a0, model_name="none"),
                report_manifest(a1, model_name="scripted-reviewer"),
                {
                    "group": "B0",
                    "model": "scripted-lead",
                    "report_digest": b0_report.content_sha256,
                    "lead_calls": b0_lead.calls,
                },
                {
                    "group": "B1",
                    "model": "scripted-lead",
                    "report_digest": b1_report.content_sha256,
                    "lead_calls": b1_lead.calls,
                },
            ]
        )
    assert len({item["report_digest"] for item in manifests}) == 1
    # Evidence is inspectable and contains no secret/session data.
    assert "sk-" not in json.dumps(manifests)
