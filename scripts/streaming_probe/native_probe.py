# ruff: noqa: D103, T201
"""Native (in-process LangGraph stream) controlled acceptance probe.

Runs the real compiled graphs with controlled sources, captures the
``final_output_*`` lifecycle and proves a public delta exists while the source
is still blocked at a barrier.  Writes only sequence numbers, lengths, hashes
and timings - never user-visible answer text.

Usage:
    PYTHONPATH=src .venv/bin/python -m scripts.streaming_probe.native_probe \
        --graph basic-qa --out docs/testing/<run>/native-basic-qa.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tests/unit_tests/tracing") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests/unit_tests/tracing"))

from fake_model import ScriptedChatModel  # noqa: E402

from common.business_graph.builder import build_business_graph  # noqa: E402
from common.models import ModelRegistry  # noqa: E402
from common.output_streaming.contract import OutputStreamValidator  # noqa: E402
from data_analysis.graph import build_graph as build_data_graph  # noqa: E402
from scripts.streaming_probe.controlled import ControlledChatModel  # noqa: E402


def _summary(events: list[dict[str, Any]], times: dict[str, float]) -> dict[str, Any]:
    validator = OutputStreamValidator()
    for event in events:
        validator.accept(event)
    deltas = [event for event in events if event.get("type") == "final_output_delta"]
    done = [event for event in events if event.get("type") == "final_output_done"]
    aborted = [event for event in events if event.get("type") == "final_output_aborted"]
    return {
        "events": len(events),
        "deltas": len(deltas),
        "delta_lengths": [len(event.get("message", "")) for event in deltas],
        "sections": [
            event.get("section_id") for event in deltas if event.get("section_id")
        ],
        "done_count": len(done),
        "aborted": bool(aborted),
        "validation_errors": validator.state.errors,
        "answer_chars": len(validator.state.text),
        "answer_sha256": hashlib.sha256(
            validator.state.text.encode("utf-8")
        ).hexdigest(),
        "first_delta_source": "new_delta" if deltas else None,
        **times,
    }


async def _drain(graph, payload, config, events, times, on_event=None):
    async for mode, chunk in graph.astream(
        payload, config=config, stream_mode=["custom", "values"]
    ):
        if mode != "custom":
            continue
        if not str(chunk.get("type", "")).startswith("final_output_"):
            continue
        now = time.perf_counter()
        event = dict(chunk)
        event["_t"] = now
        events.append(event)
        if event["type"] == "final_output_delta":
            times.setdefault("t_first_safe_delta", now)
            times["t_last_safe_delta"] = now
        if on_event:
            on_event(event)


async def probe_basic_qa(out: Path) -> dict[str, Any]:
    model = ControlledChatModel()
    old = ModelRegistry.main_llm
    ModelRegistry.main_llm = model
    try:
        graph = build_business_graph(
            skills_dir=str(ROOT / "src/basic_qa/skills"), name="basic-qa"
        )
        events: list[dict[str, Any]] = []
        times: dict[str, float] = {}
        started = time.perf_counter()
        times["t_submit"] = started
        task = asyncio.create_task(
            _drain(
                graph,
                {"messages": [HumanMessage(content="你好")]},
                {"configurable": {"thread_id": "probe-thread"}},
                events,
                times,
            )
        )
        await asyncio.wait_for(model.reached.wait(), timeout=10)
        await asyncio.sleep(0.2)
        at_barrier = {
            "source_finished": model.finished,
            "deltas_at_barrier": len(
                [e for e in events if e.get("type") == "final_output_delta"]
            ),
            "done_at_barrier": len(
                [e for e in events if e.get("type") == "final_output_done"]
            ),
        }
        model.release.set()
        await asyncio.wait_for(task, timeout=20)
        times["t_stream_end"] = time.perf_counter()
        times["t_confirmation_end"] = times["t_stream_end"]
        result = _summary(events, times)
        result.update(
            {
                "graph": "basic-qa",
                "mode": "controlled",
                "input": "controlled long answer (12 fragments)",
                "barrier": at_barrier,
            }
        )
    finally:
        ModelRegistry.main_llm = old
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


async def probe_data_analysis(out: Path) -> dict[str, Any]:
    model = ControlledChatModel(answer="分析原稿（内部，不公开）。")
    old = ModelRegistry.main_llm
    ModelRegistry.main_llm = model
    try:
        graph = build_data_graph()
        events: list[dict[str, Any]] = []
        times: dict[str, float] = {}
        times["t_submit"] = time.perf_counter()
        task = asyncio.create_task(
            _drain(
                graph,
                {"messages": [HumanMessage(content="分析北京空气质量")]},
                {
                    "configurable": {
                        "thread_id": "probe-thread",
                        "menu_name": "",
                        "chat_mode": "fast",
                    }
                },
                events,
                times,
            )
        )
        await asyncio.wait_for(model.reached.wait(), timeout=10)
        await asyncio.sleep(0.2)
        at_barrier = {
            "source_finished": model.finished,
            "deltas_at_barrier": len(
                [e for e in events if e.get("type") == "final_output_delta"]
            ),
            "done_at_barrier": len(
                [e for e in events if e.get("type") == "final_output_done"]
            ),
        }
        model.release.set()
        await asyncio.wait_for(task, timeout=20)
        times["t_stream_end"] = time.perf_counter()
        times["t_confirmation_end"] = times["t_stream_end"]
        result = _summary(events, times)
        result.update(
            {
                "graph": "data-analysis",
                "mode": "controlled",
                "input": "controlled cleanup source (12 fragments)",
                "barrier": at_barrier,
            }
        )
    finally:
        ModelRegistry.main_llm = old
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


async def probe_tracing(out: Path) -> dict[str, Any]:
    from fixtures import (
        allow_publication,
        build_bundle,
        make_request,
        make_scope,
    )

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
                            "review_summary": "保留限制",
                        },
                        "id": "r1",
                    }
                ],
            )
        ]
    )
    reached = asyncio.Event()
    release = asyncio.Event()

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
    events: list[dict[str, Any]] = []
    times: dict[str, float] = {}
    times["t_submit"] = time.perf_counter()
    task = asyncio.create_task(
        _drain(
            graph,
            {
                "messages": [HumanMessage(content="分析目标站 PM2.5")],
                "request": request,
                "scope": make_scope(),
            },
            {
                "configurable": {
                    "thread_id": "probe-thread",
                    "execution_epoch": "epoch-1",
                    "run_id": "probe-run",
                }
            },
            events,
            times,
        )
    )
    await asyncio.wait_for(reached.wait(), timeout=20)
    await asyncio.sleep(0.2)
    at_barrier = {
        "source_finished": False,
        "section_ids": [
            e.get("section_id")
            for e in events
            if e.get("type") == "final_output_delta"
        ],
        "done_at_barrier": len(
            [e for e in events if e.get("type") == "final_output_done"]
        ),
    }
    release.set()
    await asyncio.wait_for(task, timeout=30)
    times["t_stream_end"] = time.perf_counter()
    times["t_confirmation_end"] = times["t_stream_end"]
    result = _summary(events, times)
    result.update(
        {
            "graph": "intelligent-tracing",
            "mode": "controlled",
            "input": "four specialist fixture + section barrier",
            "barrier": at_barrier,
        }
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--graph",
        choices=["basic-qa", "data-analysis", "intelligent-tracing", "all"],
        default="all",
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    graphs = (
        ["basic-qa", "data-analysis", "intelligent-tracing"]
        if args.graph == "all"
        else [args.graph]
    )
    for name in graphs:
        target = out / f"native-{name}.json" if out.suffix != ".json" else out
        runner = {
            "basic-qa": probe_basic_qa,
            "data-analysis": probe_data_analysis,
            "intelligent-tracing": probe_tracing,
        }[name]
        result = asyncio.run(runner(target))
        print(
            json.dumps(
                {
                    "graph": name,
                    "deltas": result["deltas"],
                    "done": result["done_count"],
                    "first_delta_source": result["first_delta_source"],
                    "barrier": result.get("barrier"),
                    "validation_errors": result["validation_errors"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
