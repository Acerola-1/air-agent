"""DeepAgents validated-section publication tests."""

from __future__ import annotations

import asyncio

import pytest
from fake_model import ScriptedChatModel
from fixtures import (
    allow_publication,
    build_bundle,
    make_request,
    make_scope,
)
from langchain_core.messages import AIMessage, HumanMessage

from intelligent_tracing.claims import fallback_plan
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.gate import ReportGate
from intelligent_tracing.graph import build_tracing_graph
from intelligent_tracing.orchestrator import analyze_bundle
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.report_store import InMemoryReportStore
from intelligent_tracing.schemas import ReportPlan
from intelligent_tracing.section_publishing import (
    SectionPublicationError,
    publish_frozen_plan,
)
from intelligent_tracing.sources import FixtureDataSource


def _lead() -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
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
            AIMessage(content="专员/lead 草稿不应出现在正文。"),
        ]
    )


def _meteo() -> ScriptedChatModel:
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


def _reviewer(analysis_id: str) -> ScriptedChatModel:
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
                            "issues": [],
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


async def _run_graph(section_barrier=None):
    request = make_request()
    bundle = build_bundle(scenario="transport")
    graph = build_tracing_graph(
        model=_lead(),
        meteo_model=_meteo(),
        reviewer_model=_reviewer(request.analysis_id),
        data_source=FixtureDataSource(bundle),
        station_catalog=bundle.stations,
        report_store=InMemoryReportStore(),
        lifecycle_probe=allow_publication,
        section_barrier=section_barrier,
    )
    events: list[dict] = []
    last_values = None
    async for mode, chunk in graph.astream(
        {
            "messages": [HumanMessage(content="分析目标站 PM2.5")],
            "request": request,
            "scope": make_scope(),
        },
        config={
            "configurable": {
                "thread_id": "thread-a",
                "execution_epoch": "epoch-1",
                "run_id": "run-test",
            }
        },
        stream_mode=["custom", "values"],
    ):
        if mode == "custom":
            events.append(chunk)
        else:
            last_values = chunk
    return request, events, last_values


@pytest.mark.anyio
async def test_first_section_arrives_while_next_section_is_blocked():
    reached = asyncio.Event()
    release = asyncio.Event()

    async def barrier(section_id: str) -> None:
        if section_id == "claims-2":
            reached.set()
            await release.wait()

    request = make_request()
    bundle = build_bundle(scenario="transport")
    graph = build_tracing_graph(
        model=_lead(),
        meteo_model=_meteo(),
        reviewer_model=_reviewer(request.analysis_id),
        data_source=FixtureDataSource(bundle),
        station_catalog=bundle.stations,
        report_store=InMemoryReportStore(),
        lifecycle_probe=allow_publication,
        section_barrier=barrier,
    )
    events: list[dict] = []
    values_holder: dict = {}

    async def consume():
        async for mode, chunk in graph.astream(
            {
                "messages": [HumanMessage(content="分析目标站 PM2.5")],
                "request": request,
                "scope": make_scope(),
            },
            config={
                "configurable": {
                    "thread_id": "thread-a",
                    "execution_epoch": "epoch-1",
                    "run_id": "run-test",
                }
            },
            stream_mode=["custom", "values"],
        ):
            if mode == "custom":
                events.append(chunk)
            else:
                values_holder["values"] = chunk

    async def watcher():
        await reached.wait()
        await asyncio.sleep(0.1)
        deltas = [
            event for event in events if event.get("type") == "final_output_delta"
        ]
        assert [event.get("section_id") for event in deltas] == ["header", "claims-1"]
        assert not [
            event for event in events if event.get("type") == "final_output_done"
        ]
        release.set()

    task = asyncio.create_task(consume())
    watch = asyncio.create_task(watcher())
    await asyncio.wait_for(asyncio.gather(task, watch), timeout=20)

    deltas = [event for event in events if event.get("type") == "final_output_delta"]
    assert len(deltas) >= 4
    assert "专员/lead 草稿" not in "".join(event.get("message", "") for event in events)
    done = [event for event in events if event.get("type") == "final_output_done"]
    assert len(done) == 1
    report = values_holder["values"]["validated_report"]
    assert done[0]["report_id"] == report.report_id
    assert done[0]["message"] == report.body_markdown


@pytest.mark.anyio
async def test_after_agent_reuses_completed_session_without_second_done():
    request, events, values = await _run_graph()
    assert [event["type"] for event in events].count("final_output_done") == 1
    session = values["publication_session"]
    assert session.completed is True
    assert values["validated_report"].report_id == session.report_id
    # 全部节均记录在发布会话中且顺序固定
    assert session.published_section_ids[-1] == "limits"


@pytest.mark.anyio
async def test_precheck_rejects_unknown_claim_before_any_section():
    request = make_request()
    scope = make_scope()
    registry = EvidenceRegistry()
    analysis = analyze_bundle(
        request,
        build_bundle(scenario="transport"),
        registry=registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    gate = ReportGate(registry=registry, policy=DEFAULT_POLICY)
    forged = ReportPlan(
        analysis_id=request.analysis_id,
        revision=1,
        claim_ids=("claim.free_text_industry",),
    )
    frozen, errors = gate.precheck(
        forged,
        analysis.allowlist,
        request,
        scope,
        report_id="rep_test",
    )
    assert frozen is None
    assert any("claim_not_allowlisted" in error for error in errors)


@pytest.mark.anyio
async def test_section_validation_detects_tampering_and_publish_aborts() -> None:
    request = make_request()
    scope = make_scope()
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
        report_id="rep_test",
    )
    assert frozen is not None and not errors
    section = gate.render_section(frozen, frozen.sections[1])
    tampered = section.__class__(
        section_id=section.section_id,
        kind=section.kind,
        claim_ids=section.claim_ids,
        blocks=("### 异常过程\n已确认企业为某工厂。",),
    )
    assert "section_content_drift" in gate.validate_section(frozen, tampered)
    assert "attribution_overclaim_in_text" in gate.validate_section(frozen, tampered)

    events: list[dict] = []

    async def failing_barrier(section_id: str) -> None:
        if section_id == "claims-2":
            raise RuntimeError("controlled failure")

    with pytest.raises(SectionPublicationError):
        await publish_frozen_plan(
            gate=gate,
            report_store=InMemoryReportStore(),
            frozen=frozen,
            config={"configurable": {"thread_id": "thread-a"}},
            writer=events.append,
            section_barrier=failing_barrier,
        )
    assert events[0]["type"] == "final_output_start"
    assert events[-1]["type"] == "final_output_aborted"
    assert not [event for event in events if event["type"] == "final_output_done"]


@pytest.mark.anyio
async def test_completed_publication_is_idempotent_without_replaying_sections():
    request = make_request()
    scope = make_scope()
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
        report_id="rep_resume_test",
    )
    assert frozen is not None and not errors
    store = InMemoryReportStore()
    first_events: list[dict] = []
    session, report = await publish_frozen_plan(
        gate=gate,
        report_store=store,
        frozen=frozen,
        config={"configurable": {"thread_id": "thread-a"}},
        writer=first_events.append,
    )
    assert session.completed is True
    second_events: list[dict] = []
    resumed, resumed_report = await publish_frozen_plan(
        gate=gate,
        report_store=store,
        frozen=frozen,
        config={"configurable": {"thread_id": "thread-a"}},
        writer=second_events.append,
        session=session,
    )
    assert second_events == []
    assert resumed_report.report_id == report.report_id
    assert resumed.published_section_ids == session.published_section_ids


@pytest.mark.anyio
async def test_section_failure_never_republishes_from_after_agent():
    async def barrier(section_id):
        if section_id == "claims-2":
            raise RuntimeError("controlled section failure")

    _, events, values = await _run_graph(barrier)
    public = [e for e in events if e.get("type", "").startswith("final_output_")]
    assert [e["type"] for e in public] == [
        "final_output_start",
        "final_output_delta",
        "final_output_delta",
        "final_output_aborted",
    ]
    assert len({e["output_id"] for e in public}) == 1
    assert values["publication_session"].failed_reason
    assert not values.get("validated_report")


@pytest.mark.anyio
async def test_tracing_done_message_id_equals_history():
    _, events, values = await _run_graph()
    done = next(e for e in events if e.get("type") == "final_output_done")
    visible = [
        m
        for m in values["messages"]
        if getattr(m, "additional_kwargs", {}).get("ui_visible")
    ]
    assert len(visible) == 1
    assert done["message_id"] == visible[0].id == values["validated_report"].report_id


@pytest.mark.anyio
async def test_real_checkpoint_pause_rebuild_and_resume_without_repeated_sections(
    tmp_path,
):
    from unittest.mock import patch

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.types import Command, interrupt

    request, scope = make_request(), make_scope()
    bundle = build_bundle(scenario="transport")
    events = []
    report_store = InMemoryReportStore()
    config = {
        "configurable": {
            "thread_id": "thread-a",
            "execution_epoch": "epoch-1",
            "run_id": "run-test",
        }
    }

    async def pause(section_id):
        if section_id == "claims-2":
            interrupt(
                {
                    "section": section_id,
                    "note": "503 connection timeout is control payload",
                }
            )

    def build(saver, *, resumed=False):
        return build_tracing_graph(
            model=ScriptedChatModel(responses=[AIMessage(content="结束")])
            if resumed
            else _lead(),
            meteo_model=_meteo(),
            reviewer_model=_reviewer(request.analysis_id),
            data_source=FixtureDataSource(bundle),
            station_catalog=bundle.stations,
            report_store=report_store,
            lifecycle_probe=allow_publication,
            checkpointer=saver,
            section_barrier=pause,
        )

    async def consume(graph, value):
        values = None
        async for mode, chunk in graph.astream(
            value, config=config, stream_mode=["custom", "values"]
        ):
            if mode == "custom" and chunk.get("type", "").startswith("final_output_"):
                events.append(chunk)
            elif mode == "values":
                values = chunk
        return values

    path = str(tmp_path / "publication.sqlite")
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        graph = build(saver)
        await consume(
            graph,
            {
                "messages": [HumanMessage(content="分析目标站 PM2.5")],
                "request": request,
                "scope": scope,
            },
        )
        assert [
            e.get("section_id") for e in events if e["type"] == "final_output_delta"
        ] == ["header", "claims-1"]
        assert not [
            e
            for e in events
            if e["type"] in {"final_output_done", "final_output_aborted"}
        ]
        assert (await graph.aget_state(config)).tasks[0].interrupts
        checkpoints = [item async for item in saver.alist(None)]
        progress = [
            item.checkpoint["channel_values"]["session"]
            for item in checkpoints
            if item.checkpoint["channel_values"].get("session")
        ]
        assert any(
            p.published_section_ids == ("header", "claims-1") and not p.completed
            for p in progress
        )

    # Close the database and rebuild every graph, model and evidence registry.
    # Preflight must not execute again against this empty process-local registry.
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        graph = build(saver, resumed=True)
        with patch.object(
            ReportGate,
            "precheck",
            side_effect=AssertionError("preflight repeated on resume"),
        ):
            values = await consume(graph, Command(resume=True))
    deltas = [e for e in events if e["type"] == "final_output_delta"]
    assert [e["section_id"] for e in deltas] == [
        "header",
        "claims-1",
        "claims-2",
        "limits",
    ]
    assert len({e["output_id"] for e in events}) == 1
    assert len({e["message_id"] for e in events}) == 1
    assert [e["seq"] for e in events] == list(range(len(events)))
    assert events[-1]["type"] == "final_output_done"
    assert "".join(e["message"] for e in deltas) == events[-1]["message"]
    assert values["publication_session"].completed


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["revoke", "attempt", "epoch"])
async def test_pending_publication_cannot_resume_after_authority_or_execution_changes(
    change, monkeypatch
):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command, interrupt

    import intelligent_tracing.section_publishing as publication

    request, scope = make_request(), make_scope()
    bundle = build_bundle(scenario="transport")
    config = {
        "configurable": {
            "thread_id": "thread-a",
            "execution_epoch": "epoch-1",
            "ledger_attempt_id": "first-attempt",
        }
    }

    async def pause(section_id):
        if section_id == "claims-2":
            interrupt({"section": section_id})

    graph = build_tracing_graph(
        model=_lead(),
        meteo_model=_meteo(),
        reviewer_model=_reviewer(request.analysis_id),
        data_source=FixtureDataSource(bundle),
        station_catalog=bundle.stations,
        report_store=InMemoryReportStore(),
        lifecycle_probe=allow_publication,
        checkpointer=InMemorySaver(),
        section_barrier=pause,
    )
    events = []

    async def consume(value):
        values = None
        async for mode, chunk in graph.astream(
            value, config=config, stream_mode=["custom", "values"]
        ):
            if mode == "custom" and chunk.get("type", "").startswith("final_output_"):
                events.append(chunk)
            elif mode == "values":
                values = chunk
        return values

    await consume(
        {"messages": [HumanMessage(content="分析")], "request": request, "scope": scope}
    )
    assert [
        e.get("section_id") for e in events if e["type"] == "final_output_delta"
    ] == ["header", "claims-1"]
    if change == "revoke":
        monkeypatch.setattr(
            publication, "runtime_authorization_probe", lambda config: lambda: False
        )
    elif change == "attempt":
        config["configurable"]["ledger_attempt_id"] = "another-attempt"
    else:
        config["configurable"]["execution_epoch"] = "epoch-2"
    values = await consume(Command(resume=True))
    assert [
        e.get("section_id") for e in events if e["type"] == "final_output_delta"
    ] == ["header", "claims-1"]
    assert not any(e["type"] == "final_output_done" for e in events)
    assert not values.get("validated_report")
    assert values["publication_session"].failed_reason
