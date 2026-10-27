"""Decompose retained pre-answer time by interval union, never summed span walls."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.unified_campaign.records import percentile_cont


def union_us(intervals: list[tuple[int, int]]) -> int:
    """Count overlapping and nested intervals once."""
    total = 0
    left = right = None
    for start, end in sorted((a, b) for a, b in intervals if b > a):
        if right is None or start > right:
            if right is not None:
                total += right - left
            left, right = start, end
        else:
            right = max(right, end)
    return total + (right - left if right is not None else 0)


def breakdown(row: dict, spans: list[dict]) -> dict:
    """Align one legacy client clock to original server times, then clip intervals."""
    first_ms = row.get("client_first_answer_ms")
    if first_ms is None:
        return {"case_id": row["case_id"], "status": "no_body_observed"}
    if not spans:
        return {
            "case_id": row["case_id"],
            "first_body_ms": first_ms,
            "status": "missing_trace",
        }
    if row.get("ended_at"):
        start_us = round(datetime.fromisoformat(row["planned_at"]).timestamp() * 1e6)
        alignment = "explicit_send_start"
    else:
        elapsed = row.get("client_confirmation_end_ms") or row["observed_ms"]
        start_us = round(
            datetime.fromisoformat(row["planned_at"]).timestamp() * 1e6 - elapsed * 1000
        )
        alignment = "legacy_post_return_timestamp_minus_confirmation_elapsed"
    body_us = start_us + round(first_ms * 1000)
    model = []
    phase_intervals = defaultdict(list)
    by_id = {s["span_id"]: s for s in spans}
    tool = []
    intervals = []
    open_spans = 0
    open_components = 0
    for span in spans:
        begin, end = span["start_us"], span.get("end_us")
        if end is None:
            open_spans += 1
            if span["type"] in {"GENERATION", "TOOL"} and begin < body_us:
                open_components += 1
            continue
        left, right = max(start_us, begin), min(body_us, end)
        if right <= left:
            continue
        clipped = (left, right)
        duration = (right - left) / 1000
        kind = span["type"]
        intervals.append(
            {
                "span_id": span["span_id"],
                "parent_span_id": span.get("parent_span_id"),
                "name": span["name"],
                "type": kind,
                "before_body_ms": duration,
                "start_offset_ms": (begin - start_us) / 1000,
                "level": span.get("level"),
            }
        )
        if kind == "GENERATION":
            model.append(clipped)
            parent = span.get("parent_span_id")
            names = []
            seen = set()
            while parent and parent not in seen:
                seen.add(parent)
                ancestor = by_id.get(parent)
                if ancestor is None:
                    break
                names.append(ancestor["name"])
                parent = ancestor.get("parent_span_id")
            phase = (
                "planning"
                if "call_model" in names
                else "answer"
                if "answer_model" in names
                else "unclassified"
            )
            phase_intervals[phase].append(clipped)
        elif kind == "TOOL":
            tool.append(clipped)
    model_us = union_us(model)
    covered_us = union_us(model + tool)
    earliest = min((s["start_us"] for s in spans), default=None)
    before_span_ms = (
        min(first_ms, max(0, (earliest - start_us) / 1000))
        if earliest is not None
        else None
    )
    return {
        "case_id": row["case_id"],
        "category": row["category"],
        "attempt_id": row.get("attempt_id"),
        "status": "incomplete_component_spans" if open_components else "observed",
        "first_body_ms": first_ms,
        "alignment": alignment,
        "model_union_ms": model_us / 1000,
        "model_phase_union_ms": {
            k: union_us(phase_intervals[k]) / 1000
            for k in ["planning", "answer", "unclassified"]
        },
        "model_phase_calls": {
            k: len(phase_intervals[k]) for k in ["planning", "answer", "unclassified"]
        },
        "tool_excluding_model_overlap_ms": (covered_us - model_us) / 1000,
        "unattributed_ms": first_ms - covered_us / 1000,
        "before_first_observation_ms": before_span_ms,
        "open_span_count": open_spans,
        "model_intervals_before_body": len(model),
        "tool_intervals_before_body": len(tool),
        "clock_alignment_warning": earliest is not None and earliest < start_us - 10000,
        "span_intervals": intervals,
    }


def analyze(source: Path, spans_path: Path, out: Path) -> dict[str, Any]:
    """Write an independent, metadata-only analysis of the original baseline."""
    if out.exists():
        raise ValueError("输出已存在，不覆盖证据")
    rows = [
        json.loads(s)
        for s in (source / "requests.baseline.jsonl").read_text().splitlines()
        if s.strip()
    ]
    rows = [r for r in rows if r.get("cohort") == "measured"]
    reconciliation = json.loads((source / "langfuse-reconcile.json").read_text())
    trace_of = {
        a["attempt_id"]: a["expected_trace_id"] for a in reconciliation["association"]
    }
    by_trace: dict[str, list[dict]] = defaultdict(list)
    for line in spans_path.read_text().splitlines():
        span = json.loads(line)
        by_trace[span["trace_id"]].append(span)
    for values in by_trace.values():
        if len({s["span_id"] for s in values}) != len(values):
            raise ValueError("重复 span 标识，不能将不同导入版本当成独立耗时")
    results = [breakdown(r, by_trace[trace_of[r["attempt_id"]]]) for r in rows]
    observed = [
        r
        for r in results
        if r["status"] == "observed" and not r["clock_alignment_warning"]
    ]
    components = [
        "first_body_ms",
        "model_union_ms",
        "tool_excluding_model_overlap_ms",
        "unattributed_ms",
        "before_first_observation_ms",
    ]

    def aggregate(items):
        return {
            "samples": len(items),
            "mean_ms": {
                k: sum(r[k] for r in items) / len(items) if items else None
                for k in components
            },
            "p50_ms": {
                k: percentile_cont([r[k] for r in items], 0.5) for k in components
            },
            "median_model_calls_before_body": percentile_cont(
                [r["model_intervals_before_body"] for r in items], 0.5
            ),
            "median_tool_calls_before_body": percentile_cont(
                [r["tool_intervals_before_body"] for r in items], 0.5
            ),
            "model_phase_mean_ms": {
                k: sum(r["model_phase_union_ms"][k] for r in items) / len(items)
                if items
                else None
                for k in ["planning", "answer", "unclassified"]
            },
            "model_phase_median_calls": {
                k: percentile_cont([r["model_phase_calls"][k] for r in items], 0.5)
                for k in ["planning", "answer", "unclassified"]
            },
        }

    summary = {
        "source_run": source.name,
        "measurement": "legacy first client body; not browser-visible time",
        "status_counts": dict(Counter(r["status"] for r in results)),
        "clock_alignment_warnings": sum(
            r.get("clock_alignment_warning", False) for r in results
        ),
        "overall": aggregate(observed),
        "by_category": {
            c: aggregate([r for r in observed if r["category"] == c])
            for c in ["Q1", "Q2", "Q3", "Q4"]
        },
        "slowest_cases": [
            {
                k: r[k]
                for k in [
                    "case_id",
                    *components,
                    "model_intervals_before_body",
                    "tool_intervals_before_body",
                ]
            }
            for r in sorted(observed, key=lambda r: r["first_body_ms"], reverse=True)[
                :5
            ]
        ],
        "limitations": [
            "Client absolute timestamps reconstructed from send-return clock; allow millisecond alignment uncertainty.",
            "Model/tool overlaps counted once; category medians must not be added.",
            "Unattributed time includes uninstrumented IAM/network/scheduling/code; cannot assign its cause without spans.",
            "Before-first-observation is part of unattributed time, not another additive component.",
            "Two aborted cases have no body timestamp and are retained outside latency quantiles.",
            "Recovered spans are original historical events; this is not a new performance run.",
        ],
    }
    out.mkdir(parents=True)
    (out / "requests.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results)
    )
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    inputs = [
        source / "requests.baseline.jsonl",
        source / "langfuse-reconcile.json",
        spans_path,
    ]
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "network_requests": 0,
                "inputs_sha256": {
                    str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs
                },
            },
            indent=2,
        )
    )
    return summary


def main() -> None:
    """Analyze a retained baseline without model or network requests."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--spans", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    summary = analyze(args.source, args.spans, args.out)
    print(json.dumps(summary, ensure_ascii=False))  # noqa: T201


if __name__ == "__main__":
    main()
