"""G0 DeepAgents 0.7.28 wiring, concurrency and failure spike."""

from __future__ import annotations

import asyncio
import time

import pytest
from conversation_fixtures import analysis_prefix, analysis_question, run_config
from deepagents.middleware.subagents import TaskToolSchema
from fake_model import ScriptedChatModel
from fixtures import (
    BUSINESS_CODE,
    BUSINESS_PERMISSION,
    BUSINESS_POLICY_VERSION,
    allow_publication,
    build_bundle,
    make_request,
    make_scope,
)
from langchain_core.messages import AIMessage, HumanMessage

from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.graph import build_tracing_graph
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.report_store import InMemoryReportStore
from intelligent_tracing.sources import FixtureDataSource
from intelligent_tracing.state import latest_finding_reducer, latest_request_reducer
from intelligent_tracing.subagents import build_station_runnable


@pytest.fixture(autouse=True)
def controlled_runtime_identity(monkeypatch):
    """G0 uses a controlled identity service; no production database is queried."""
    from unittest.mock import AsyncMock

    import common.identity.runtime as identity

    monkeypatch.setattr(identity, "revalidate", AsyncMock())


def _lead_model(
    *, final_text: str = "污染主要由某方位工业活动造成。"
) -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "分析站点",
                            "subagent_type": "station-data",
                        },
                        "id": "t1",
                    },
                    {
                        "name": "task",
                        "args": {
                            "description": "分析网格",
                            "subagent_type": "grid-data",
                        },
                        "id": "t2",
                    },
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "分析气象",
                            "subagent_type": "meteo-analyst",
                        },
                        "id": "t3",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "审查证据",
                            "subagent_type": "evidence-reviewer",
                        },
                        "id": "t4",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "build_validated_report",
                        "args": {},
                        "id": "t5",
                    }
                ],
            ),
            AIMessage(content=final_text),
        ]
    )


def _meteo_model() -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "MeteoNarrative",
                        "args": {
                            "summary": "风场一致性可用",
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
                            "review_summary": "建议保留限制",
                        },
                        "id": "r1",
                    }
                ],
            )
        ]
    )


def _graph(
    source,
    *,
    lead=None,
    meteo=None,
    reviewer=None,
    store=None,
):
    request = make_request()
    bundle = build_bundle(scenario="transport")
    lead = lead or _lead_model()
    prefix = analysis_prefix()
    if (
        lead.responses[0].tool_calls
        and lead.responses[0].tool_calls[0]["name"] == "freeze_tracing_request"
    ):
        prefix = prefix[:1]
    lead.responses[:0] = prefix
    graph = build_tracing_graph(
        model=lead,
        meteo_model=meteo or _meteo_model(),
        reviewer_model=reviewer or _reviewer_model(request.analysis_id),
        data_source=source,
        station_catalog=bundle.stations,
        report_store=store or InMemoryReportStore(),
        lifecycle_probe=allow_publication,
    )
    return request, graph


async def _invoke(graph, request):
    return await graph.ainvoke(
        {
            "messages": [HumanMessage(content=analysis_question())],
            "request": request,
            "scope": make_scope(),
        },
        config={
            "configurable": {
                "thread_id": "thread-a",
                "execution_epoch": "epoch-1",
                "ledger_attempt_id": "attempt-test",
                "run_id": "run-test",
                "ledger_business_code": BUSINESS_CODE,
                "ledger_business_policy_version": BUSINESS_POLICY_VERSION,
                "ledger_business_permission": BUSINESS_PERMISSION,
                "ledger_graph_id": "intelligent-tracing",
                "langgraph_auth_user": run_config()["configurable"][
                    "langgraph_auth_user"
                ],
            }
        },
    )


def test_task_schema_only_has_description_and_type():
    assert set(TaskToolSchema.model_fields) == {"description", "subagent_type"}


@pytest.mark.anyio
async def test_lead_and_specialist_tool_boundaries():
    request = make_request()
    lead_model = _lead_model()
    meteo_model = _meteo_model()
    reviewer_model = _reviewer_model(request.analysis_id)
    request, graph = _graph(
        FixtureDataSource(build_bundle()),
        lead=lead_model,
        meteo=meteo_model,
        reviewer=reviewer_model,
    )
    await _invoke(graph, request)
    assert set(lead_model.bound_tools) == {
        "submit_tracing_turn",
        "freeze_tracing_request",
        "get_analysis_policy_summary",
        "build_validated_report",
        "publish_tracing_canvas",
        "knowledge_search",
        "task",
    }
    assert "- general-purpose:" not in lead_model.bound_tools["task"]
    for name in ("station-data", "grid-data", "meteo-analyst", "evidence-reviewer"):
        assert name in lead_model.bound_tools["task"]
    # 气象数值由确定性中间件计算；专员不再持有取数工具。
    assert set(meteo_model.bound_tools) == {"MeteoNarrative"}
    assert set(reviewer_model.bound_tools) == {"EvidenceReview"}
    for tools in (
        lead_model.bound_tools,
        meteo_model.bound_tools,
        reviewer_model.bound_tools,
    ):
        for name in tools:
            assert "trajectory" not in name.lower()
            assert "pscf" not in name.lower()
            assert name not in {
                "execute",
                "read_file",
                "write_file",
                "edit_file",
                "create_artifact",
            }


@pytest.mark.anyio
async def test_station_subgraph_sets_structured_response_and_owned_field():
    request = make_request()
    bundle = build_bundle(scenario="transport")
    runnable = build_station_runnable(
        source=FixtureDataSource(bundle),
        registry=EvidenceRegistry(),
        policy=DEFAULT_POLICY,
    )
    state = {
        "messages": [HumanMessage(content="站点任务")],
        "request": request,
        "scope": make_scope(),
    }
    result = await runnable.ainvoke(state)
    assert "messages" in result
    assert result["station_findings"].status == "anomaly"
    assert result["structured_response"] == result["station_findings"]
    with pytest.raises(ValueError, match="缺少已冻结请求"):
        await runnable.ainvoke({"messages": [HumanMessage(content="无请求")]})


class TimingSource(FixtureDataSource):
    def __init__(self, bundle, delays: tuple[float, ...] = (0.12, 0.12)):
        super().__init__(bundle)
        self.delays = delays
        self.starts: list[float] = []
        self.ends: list[float] = []

    async def fetch(self, request, *, scope, policy):
        index = len(self.starts)
        self.starts.append(time.monotonic())
        await asyncio.sleep(self.delays[index % len(self.delays)])
        bundle = await super().fetch(request, scope=scope, policy=policy)
        self.ends.append(time.monotonic())
        return bundle


@pytest.mark.anyio
async def test_specialists_share_one_snapshot_and_reverse_completion():
    source = TimingSource(build_bundle(), delays=(0.18, 0.03))
    request, graph = _graph(source)
    out = await _invoke(graph, request)
    # station-data, grid-data and meteo-analyst share one scoped snapshot;
    # they must not each issue a separate real MCP fetch.
    assert len(source.starts) == 1
    assert out["station_findings"].status == "anomaly"
    assert out["grid_findings"].pattern == "regional_contiguous"
    assert out["validated_report"] is not None


@pytest.mark.anyio
async def test_one_specialist_failure_keeps_partial_result_and_report(monkeypatch):
    import intelligent_tracing.subagents as subagents_module

    def _fail(*args, **kwargs):
        raise RuntimeError("controlled grid analysis failure")

    monkeypatch.setattr(subagents_module, "analyze_spatial", _fail)
    request, graph = _graph(FixtureDataSource(build_bundle()))
    out = await _invoke(graph, request)
    assert out["station_findings"].status == "anomaly"
    assert "grid_data_unavailable" in out["grid_findings"].limitations
    assert out["validated_report"] is not None
    assert "无法计算贡献率" in out["validated_report"].body_markdown
    assert out["validated_report"].limitations


def test_reducers_keep_new_revision_and_drop_old_late_result():
    request = make_request(revision=2)
    old = make_request(revision=1)
    assert latest_request_reducer(old, request) == request
    assert latest_request_reducer(request, old) == request
    from fixtures import build_bundle

    from intelligent_tracing.evidence import EvidenceRegistry
    from intelligent_tracing.orchestrator import analyze_bundle
    from intelligent_tracing.policy import DEFAULT_POLICY as policy

    scope = make_scope()
    new_analysis = analyze_bundle(
        request,
        build_bundle(scenario="transport"),
        registry=EvidenceRegistry(),
        scope=scope,
        policy=policy,
    )
    old_analysis = analyze_bundle(
        old,
        build_bundle(scenario="transport"),
        registry=EvidenceRegistry(),
        scope=scope,
        policy=policy,
    )
    assert (
        latest_finding_reducer(new_analysis.station, old_analysis.station)
        == new_analysis.station
    )


@pytest.mark.anyio
async def test_final_model_prose_is_not_published():
    request, graph = _graph(
        FixtureDataSource(build_bundle()),
        lead=_lead_model(final_text="污染主要由某方位工业活动造成。"),
    )
    out = await _invoke(graph, request)
    visible = [
        message
        for message in out["messages"]
        if isinstance(message, AIMessage)
        and message.additional_kwargs.get("ui_visible") is True
    ]
    assert len(visible) == 1
    assert visible[0].id == out["validated_report"].report_id
    assert "无法计算贡献率" in visible[0].content
    assert "污染主要由某方位工业活动造成" not in visible[0].content


@pytest.mark.anyio
async def test_child_executions_link_existing_attempt_without_new_request():
    request, graph = _graph(FixtureDataSource(build_bundle()))
    out = await _invoke(graph, request)
    children = out.get("child_executions", ())
    roles = {child.role for child in children}
    assert roles == {
        "station-data",
        "grid-data",
        "meteo-analyst",
        "evidence-reviewer",
    }
    for child in children:
        assert child.analysis_id == out["request"].analysis_id
        assert child.revision == out["request"].revision
        assert child.attempt_id == "attempt-test"
        assert child.run_id == "run-test"
        assert child.status in {"succeeded", "failed", "partial"}


def test_tracing_build_does_not_leave_global_harness_profile():
    from deepagents.profiles.harness.harness_profiles import _HARNESS_PROFILES

    request = make_request()
    _graph(FixtureDataSource(build_bundle()))
    key = "openai:scripted-tracing"
    assert key not in _HARNESS_PROFILES
    assert request.analysis_id


@pytest.mark.anyio
async def test_freeze_then_delegate_without_injected_scope(monkeypatch):
    from unittest.mock import AsyncMock

    import common.identity.runtime as identity_runtime

    monkeypatch.setattr(identity_runtime, "revalidate", AsyncMock(return_value=None))
    request = make_request()
    bundle = build_bundle()
    lead = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "freeze_tracing_request",
                        "args": {
                            "station_code": "S001",
                            "pollutant": "pm2.5",
                            "start": request.time_window.start.isoformat(),
                            "end": request.time_window.end.isoformat(),
                            "timezone": "Asia/Shanghai",
                            "neighbor_radius_km": 10,
                            "baseline_start": request.baseline_window.start.isoformat(),
                            "baseline_end": request.baseline_window.end.isoformat(),
                            "analysis_id": request.analysis_id,
                            "revision": 1,
                            "capability_snapshot_ref": "sel-001",
                        },
                        "id": "freeze",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "description": "站点",
                            "subagent_type": "station-data",
                        },
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
                        "args": {
                            "description": "气象",
                            "subagent_type": "meteo-analyst",
                        },
                        "id": "t3",
                    }
                ],
            ),
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
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "build_validated_report", "args": {}, "id": "t5"}],
            ),
            AIMessage(content="模型自由文本不发布。"),
        ]
    )
    lead.responses[:0] = analysis_prefix()[:1]
    graph = build_tracing_graph(
        model=lead,
        meteo_model=_meteo_model(),
        reviewer_model=_reviewer_model(request.analysis_id),
        data_source=FixtureDataSource(bundle),
        station_catalog=bundle.stations,
        report_store=InMemoryReportStore(),
        lifecycle_probe=allow_publication,
    )
    out = await graph.ainvoke(
        {"messages": [HumanMessage(content=analysis_question())]},
        config={
            "configurable": {
                "thread_id": "thread-trusted",
                "execution_epoch": "epoch-trusted",
                "run_id": "run-trusted",
                "ledger_attempt_id": "attempt-trusted",
                "capability_selection_id": "sel-001",
                "ledger_business_code": BUSINESS_CODE,
                "ledger_business_policy_version": BUSINESS_POLICY_VERSION,
                "ledger_business_permission": BUSINESS_PERMISSION,
                "ledger_graph_id": "intelligent-tracing",
                "langgraph_auth_user": {
                    "identity": "user-trusted",
                    "tenant_id": "tenant-trusted",
                    "membership_id": "membership-trusted",
                    "session_id": "session-trusted",
                    "session_ctx_version": 5,
                },
            }
        },
    )
    assert out["request"].analysis_id == out["turn_decision"].analysis_id
    assert out["request"].request_digest == out["turn_decision"].request_digest
    assert out["scope"].tenant_id == "tenant-trusted"
    assert out["scope"].user_id == "user-trusted"
    assert out["scope"].thread_id == "thread-trusted"
    assert out["scope"].context_version == "5"
    assert out["scope"].epoch == "epoch-trusted"
    assert out["validated_report"] is not None
    assert out["validated_report"].scope == out["scope"]
    assert all(
        child.attempt_id == "attempt-trusted" for child in out["child_executions"]
    )


def test_default_graph_wires_business_mcp_source_and_catalog(monkeypatch):
    from intelligent_tracing import graph as graph_module

    created: dict[str, object] = {}

    class FakeCatalog:
        async def list_stations(self, *, config, region_name):
            return ()

    class FakeSource:
        def __init__(self, *, station_catalog):
            created["source_catalog"] = station_catalog

    monkeypatch.setattr(graph_module, "StationCatalogProvider", FakeCatalog)
    monkeypatch.setattr(graph_module, "BusinessMCPDataSource", FakeSource)
    graph_module.build_tracing_graph(
        model=_lead_model(),
        meteo_model=_meteo_model(),
        reviewer_model=_reviewer_model(make_request().analysis_id),
        report_store=InMemoryReportStore(),
        lifecycle_probe=allow_publication,
    )
    assert isinstance(created["source_catalog"], FakeCatalog)
