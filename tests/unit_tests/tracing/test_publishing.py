"""Publication, dedupe, suppression and report-store tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fixtures import build_bundle, make_request, make_scope
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage

import intelligent_tracing.publishing as publishing
from intelligent_tracing.claims import fallback_plan
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.gate import ReportGate
from intelligent_tracing.orchestrator import analyze_bundle
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.publishing import TracingPublishMiddleware
from intelligent_tracing.report_store import InMemoryReportStore, ReportRereadError
from intelligent_tracing.tools import build_report_tools


async def _objects(scenario: str = "transport"):
    request = make_request()
    scope = make_scope()
    registry = EvidenceRegistry()
    analysis = analyze_bundle(
        request,
        build_bundle(scenario=scenario),
        registry=registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    gate = ReportGate(registry=registry, policy=DEFAULT_POLICY)
    result = gate.validate_and_render(
        fallback_plan(analysis.allowlist), analysis.allowlist, request, scope
    )
    assert result.ok and result.report
    return request, scope, analysis, result.report, registry, gate


@pytest.mark.anyio
async def test_publish_uses_validated_report_and_marks_model_prose_invisible(
    monkeypatch,
):
    request, scope, analysis, report, registry, gate = await _objects()
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        publishing,
        "emit_event",
        lambda config, event_type, **values: events.append((event_type, values)),
    )
    store = InMemoryReportStore()
    await store.save(report)
    middleware = TracingPublishMiddleware(
        registry=registry, gate=gate, policy=DEFAULT_POLICY, report_store=store
    )

    async def handler(_request):
        return ModelResponse(
            result=[AIMessage(content="污染主要由某方位工业活动造成。")]
        )

    model_response = await middleware.awrap_model_call(None, handler)
    assert model_response.result[0].additional_kwargs["ui_visible"] is False

    state = {
        "request": request,
        "scope": scope,
        "station_findings": analysis.station,
        "grid_findings": analysis.spatial,
        "meteo_findings": analysis.meteo,
        "allowlist": analysis.allowlist,
        "validated_report": report,
    }
    runtime = SimpleNamespace(
        config={"configurable": {"thread_id": "thread-a", "execution_epoch": "epoch-1"}}
    )
    update = await middleware.aafter_agent(state, runtime)
    assert update is not None
    visible = update["messages"][0]
    assert visible.additional_kwargs["ui_visible"] is True
    assert visible.id == report.report_id
    assert events and events[0][0] == "final_output_done"
    assert "无法计算贡献率" in events[0][1]["message"]
    assert "污染主要由某方位工业活动造成" not in events[0][1]["message"]


@pytest.mark.anyio
async def test_tombstone_and_epoch_mismatch_suppress_publication(monkeypatch):
    request, scope, analysis, report, registry, gate = await _objects()
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        publishing,
        "emit_event",
        lambda config, event_type, **values: events.append((event_type, values)),
    )
    middleware = TracingPublishMiddleware(
        registry=registry,
        gate=gate,
        policy=DEFAULT_POLICY,
        report_store=InMemoryReportStore(),
    )
    state = {
        "request": request,
        "scope": scope,
        "station_findings": analysis.station,
        "grid_findings": analysis.spatial,
        "meteo_findings": analysis.meteo,
        "allowlist": analysis.allowlist,
        "validated_report": report,
    }
    deleted = SimpleNamespace(
        config={
            "configurable": {
                "thread_id": "thread-a",
                "execution_epoch": "epoch-1",
                "tracing_deleted": True,
            }
        }
    )
    assert await middleware.aafter_agent(state, deleted) == {}
    stale_epoch = SimpleNamespace(
        config={
            "configurable": {
                "thread_id": "thread-a",
                "execution_epoch": "epoch-2",
            }
        }
    )
    assert await middleware.aafter_agent(state, stale_epoch) == {}
    assert events == []


@pytest.mark.anyio
async def test_report_store_detects_tampering_and_scope_change():
    request, scope, _analysis, report, _registry, _gate = await _objects()
    store = InMemoryReportStore()
    await store.save(report)
    assert await store.get(report.report_id, scope=scope) is not None
    tampered = report.model_copy(
        update={"body_markdown": report.body_markdown + " 已确认企业"}
    )
    await store.save(tampered) if False else None
    # Directly inject the tampered canonical record to prove digest re-check.
    store._reports[report.report_id] = tampered.model_dump_json()
    with pytest.raises(ReportRereadError):
        await store.get(report.report_id, scope=scope)
    other_scope = make_scope(context_version="cv-other")
    with pytest.raises(ReportRereadError):
        await store.get(report.report_id, scope=other_scope)


def test_canvas_tool_schema_has_no_content_bypass():
    registry = EvidenceRegistry()
    gate = ReportGate(registry=registry, policy=DEFAULT_POLICY)
    _report_tool, canvas_tool = build_report_tools(
        registry=registry,
        gate=gate,
        policy=DEFAULT_POLICY,
        report_store=InMemoryReportStore(),
    )
    schema = canvas_tool.args_schema.model_json_schema()
    assert "content" not in schema.get("properties", {})
    assert set(schema["properties"]) <= {"report_id", "open_in"}


@pytest.mark.anyio
async def test_publish_rebuilds_when_findings_change_after_report(monkeypatch):
    from fixtures import build_bundle

    from intelligent_tracing.orchestrator import analyze_bundle

    request, scope, analysis, report, registry, gate = await _objects("transport")
    newer = analyze_bundle(
        request,
        build_bundle(scenario="no_anomaly"),
        registry=registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        publishing,
        "emit_event",
        lambda config, event_type, **values: events.append((event_type, values)),
    )
    middleware = TracingPublishMiddleware(
        registry=registry,
        gate=gate,
        policy=DEFAULT_POLICY,
        report_store=InMemoryReportStore(),
    )
    state = {
        "request": request,
        "scope": scope,
        "station_findings": newer.station,
        "grid_findings": newer.spatial,
        "meteo_findings": newer.meteo,
        "allowlist": analysis.allowlist,
        "validated_report": report,
    }
    runtime = SimpleNamespace(
        config={"configurable": {"thread_id": "thread-a", "execution_epoch": "epoch-1"}}
    )
    update = await middleware.aafter_agent(state, runtime)
    assert update is not None
    published = update["validated_report"]
    assert published.report_id != report.report_id
    assert "未发现符合当前冻结策略的异常" in published.body_markdown
    assert events and "未发现符合当前冻结策略的异常" in events[0][1]["message"]


@pytest.mark.anyio
async def test_canvas_tool_returns_server_rendered_ref_without_db():
    from types import SimpleNamespace

    from intelligent_tracing.tools import build_report_tools

    request, scope, analysis, report, registry, gate = await _objects()
    _report_tool, canvas_tool = build_report_tools(
        registry=registry,
        gate=gate,
        policy=DEFAULT_POLICY,
        report_store=InMemoryReportStore(),
    )
    runtime = SimpleNamespace(
        state={
            "request": request,
            "scope": scope,
            "station_findings": analysis.station,
            "grid_findings": analysis.spatial,
            "meteo_findings": analysis.meteo,
            "validated_report": report,
        },
        config={"configurable": {"thread_id": "thread-a"}},
        tool_call_id="canvas-call",
    )
    result = await canvas_tool.coroutine(  # type: ignore[union-attr]
        runtime, report_id=report.report_id
    )
    assert result["status"] == "published"
    assert result["storage_status"] in {"stored", "unavailable"}
    assert result["artifact_ref"]["content"] == report.canvas.content
    assert result["artifact_ref"]["validated_report_ref"] == report.report_id
    assert result["artifact_ref"]["content_sha256"] == report.content_sha256
