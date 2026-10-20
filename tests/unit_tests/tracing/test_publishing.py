"""Publication, dedupe, suppression and report-store tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fixtures import allow_publication, build_bundle, make_request, make_scope
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
        registry=registry,
        gate=gate,
        policy=DEFAULT_POLICY,
        report_store=store,
        lifecycle_probe=allow_publication,
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
        lifecycle_probe=allow_publication,
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
    from fixtures import allow_publication, build_bundle

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
        lifecycle_probe=allow_publication,
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


@pytest.mark.anyio
async def test_lifecycle_probe_denial_or_failure_suppresses_publication(monkeypatch):
    request, scope, analysis, report, registry, gate = await _objects()
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        publishing,
        "emit_event",
        lambda config, event_type, **values: events.append((event_type, values)),
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
    runtime = SimpleNamespace(
        config={"configurable": {"thread_id": "thread-a", "execution_epoch": "epoch-1"}}
    )

    async def _raise(_config, _scope):
        raise RuntimeError("ledger unavailable")

    async def _deny(_config, _scope):
        return False

    for probe in (_deny, _raise):
        middleware = TracingPublishMiddleware(
            registry=registry,
            gate=gate,
            policy=DEFAULT_POLICY,
            report_store=InMemoryReportStore(),
            lifecycle_probe=probe,
        )
        update = await middleware.aafter_agent(state, runtime)
        assert update == {}
    assert events == []


@pytest.mark.anyio
async def test_default_ledger_probe_fails_closed_without_ledger_context():
    request, scope, analysis, report, registry, gate = await _objects()
    middleware = TracingPublishMiddleware(
        registry=registry,
        gate=gate,
        policy=DEFAULT_POLICY,
        report_store=InMemoryReportStore(),
    )
    config = {
        "configurable": {
            "thread_id": "thread-a",
            "execution_epoch": "epoch-1",
            "ledger_business_code": "intelligent-tracing",
            "ledger_business_policy_version": "2026-10-19.1",
            "ledger_business_permission": "agent:business:intelligent-tracing:use",
            "ledger_graph_id": "intelligent-tracing",
            "langgraph_auth_user": {
                "identity": "u",
                "tenant_id": "t",
                "membership_id": "m",
                "session_id": "s",
                "session_ctx_version": 1,
            },
        }
    }
    # 缺少 ledger_request_id/attempt_id：探针必须拒绝而不是直连数据库。
    assert await middleware._publication_allowed(config, scope) is False


@pytest.mark.anyio
async def test_default_ledger_probe_state_matrix(monkeypatch):
    from intelligent_tracing import publishing as publishing_module

    _request, scope, _analysis, _report, registry, gate = await _objects()
    middleware = TracingPublishMiddleware(
        registry=registry,
        gate=gate,
        policy=DEFAULT_POLICY,
        report_store=InMemoryReportStore(),
    )
    config = {
        "configurable": {
            "thread_id": scope.thread_id,
            "ledger_request_id": "request-1",
            "ledger_attempt_id": "attempt-1",
            "langgraph_auth_user": {
                "identity": scope.user_id,
                "tenant_id": scope.tenant_id,
                "membership_id": scope.membership_id,
                "session_id": "session-1",
                "session_ctx_version": 1,
            },
        }
    }
    base_row = {
        "attempt_id": "attempt-1",
        "state": "running",
        "state_revision": 2,
        "cancel_requested": False,
        "cancel_confirmed": False,
        "ended_at": None,
        "business_code": scope.business_code,
        "business_policy_version": scope.business_policy_version,
        "request_id": "request-1",
        "thread_id": scope.thread_id,
    }

    async def _no_guard(_config):
        return None

    monkeypatch.setattr(publishing_module, "guard_runtime_call", _no_guard)

    async def _not_deleted(*args, **kwargs):
        return False

    monkeypatch.setattr(publishing_module, "thread_deletion_exists", _not_deleted)

    async def _row(row):
        async def _fetch(*args, **kwargs):
            return row

        monkeypatch.setattr(publishing_module, "fetch_attempt_lifecycle", _fetch)

    await _row(base_row)
    assert await middleware._publication_allowed(config, scope) is True

    # 取消意图本身不等于已取消：running + cancel_requested 仍可发布当前验证版本。
    await _row({**base_row, "cancel_requested": True})
    assert await middleware._publication_allowed(config, scope) is True

    # 成功终态可迟到发布，不撤销已验证内容。
    await _row({**base_row, "state": "succeeded", "ended_at": "2026-01-01T00:00:00Z"})
    assert await middleware._publication_allowed(config, scope) is True

    for patch in (
        {"state": "cancelled"},
        {"state": "failed"},
        {"state": "timed_out"},
        {"cancel_confirmed": True},
        {"request_id": "other-request"},
        {"business_code": "basic-qa"},
    ):
        await _row({**base_row, **patch})
        assert await middleware._publication_allowed(config, scope) is False

    async def _deleted(*args, **kwargs):
        return True

    monkeypatch.setattr(publishing_module, "thread_deletion_exists", _deleted)
    await _row(base_row)
    assert await middleware._publication_allowed(config, scope) is False


@pytest.mark.anyio
async def test_default_ledger_probe_ledger_failure_suppresses(monkeypatch):
    from intelligent_tracing import publishing as publishing_module

    _request, scope, _analysis, _report, registry, gate = await _objects()
    middleware = TracingPublishMiddleware(
        registry=registry,
        gate=gate,
        policy=DEFAULT_POLICY,
        report_store=InMemoryReportStore(),
    )
    config = {
        "configurable": {
            "thread_id": scope.thread_id,
            "ledger_request_id": "request-1",
            "ledger_attempt_id": "attempt-1",
        }
    }

    async def _boom(*args, **kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(publishing_module, "guard_runtime_call", _boom)
    assert await middleware._publication_allowed(config, scope) is False


@pytest.mark.anyio
async def test_fallback_rerender_preserves_source_basis_and_old_claims():
    from intelligent_tracing.schemas import KnowledgeBasisItem

    request, scope, analysis, old, registry, gate = await _objects()
    store = InMemoryReportStore()
    middleware = TracingPublishMiddleware(
        registry=registry,
        gate=gate,
        policy=DEFAULT_POLICY,
        report_store=store,
        lifecycle_probe=allow_publication,
    )
    basis = KnowledgeBasisItem(
        citation_id="11111111-1111-4111-8111-111111111111",
        doc_name="F-A 合成标准",
        excerpt="示例限值35测试单位，仅供软件测试。",
        location=None,
        location_status="not_provided",
        source_version="test-v1",
        source_sha256="a" * 64,
    )
    report = await middleware._render_fallback(
        request, scope, analysis.allowlist, run_id="test-run", knowledge_basis=(basis,)
    )
    assert report.knowledge_basis == (basis,)
    assert report.claim_ids == old.claim_ids and report.evidence_ids == old.evidence_ids
    assert "### 知识依据" in report.body_markdown
    assert (
        await middleware._render_fallback(
            request, scope, analysis.allowlist, knowledge_basis=None
        )
        is None
    )


@pytest.mark.anyio
async def test_report_rejects_cross_run_citation_before_rag_call(monkeypatch):
    from unittest.mock import AsyncMock

    from common.knowledge import store, validation
    from intelligent_tracing.tools import _resolve_knowledge_basis

    request, scope, analysis, old, registry, gate = await _objects()
    plan = fallback_plan(analysis.allowlist).model_copy(
        update={"knowledge_reference_ids": ("11111111-1111-4111-8111-111111111111",)}
    )
    import intelligent_tracing.tools as tools

    monkeypatch.setattr(tools, "identity_from_config", lambda config: object())
    monkeypatch.setattr(
        store,
        "get_citation",
        AsyncMock(
            return_value={
                "thread_id": scope.thread_id,
                "run_id": "old-run",
                "source_sha256": "a" * 64,
            }
        ),
    )
    checked = AsyncMock()
    monkeypatch.setattr(validation, "validate_citations", checked)
    result = await _resolve_knowledge_basis(
        plan,
        scope=scope,
        config={"configurable": {"run_id": "11111111-1111-4111-8111-111111111111"}},
    )
    assert result["status"] == "knowledge_reference_invalid"
    assert result["errors"] == ["citation_scope_mismatch"]
    checked.assert_not_awaited()
