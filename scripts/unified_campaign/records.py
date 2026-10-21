"""Write the basic-qa performance schema without question or answer text."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "basic-qa-perf.v1"


def percentile_cont(values: list[float], probability: float) -> float | None:
    """Linear interpolation matching percentile_cont. Empty input stays missing."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _dump(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def write_performance_bundle(
    directory: Path,
    *,
    run_id: str,
    campaign_id: str,
    manifest: dict[str, Any],
    requests: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    browser: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a new run directory and return the generated summary."""
    directory.mkdir(parents=True, exist_ok=False)
    measured = [row for row in requests if row.get("cohort") == "measured"]
    summary = _summary(run_id, campaign_id, measured, requests, calls, browser)
    _dump(directory / "manifest.json", manifest)
    _dump(directory / "summary.json", summary)
    _jsonl(directory / "requests.jsonl", requests)
    _jsonl(directory / "calls.jsonl", calls)
    _jsonl(directory / "resources.jsonl", resources)
    _jsonl(directory / "checks.jsonl", checks)
    _jsonl(directory / "browser.jsonl", browser)
    _dump(
        directory / "evidence-index.json",
        {
            "schema_version": SCHEMA,
            "run_id": run_id,
            "campaign_id": campaign_id,
            "files": sorted(path.name for path in directory.iterdir()),
            "question_text_stored": False,
        },
    )
    (directory / "report.md").write_text(_report(summary, manifest))
    return summary


def _summary(
    run_id: str,
    campaign_id: str,
    measured: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    browser: list[dict[str, Any]],
) -> dict[str, Any]:
    outcomes = Counter(row.get("primary_result") for row in measured)
    sent = [row for row in measured if row.get("sent")]
    pipeline_rows = [
        row
        for row in sent
        if row.get("primary_result") == "stream_completed"
        and row.get("client_complete_ms") is not None
    ]
    pipeline = [float(row["client_complete_ms"]) for row in pipeline_rows]
    first_answers = [
        float(row["client_first_answer_ms"])
        for row in pipeline_rows
        if row.get("client_first_answer_ms") is not None
    ]
    observed_calls = [
        row
        for row in calls
        if row.get("source") == "admin-observations"
        and row.get("status") != "not_collected"
    ]
    usage_known = [
        row for row in observed_calls if row.get("usage_status") == "provided"
    ]
    return {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "campaign_id": campaign_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "layer": "P",
        "profile": "S",
        "n_planned_measured": len(measured),
        "n_sent_measured": len(sent),
        "outcomes": dict(outcomes),
        "completion_p50_ms": percentile_cont(pipeline, 0.50),
        "completion_p95_ms": percentile_cont(pipeline, 0.95),
        "first_answer_p50_ms": percentile_cont(first_answers, 0.50),
        "first_answer_p95_ms": percentile_cont(first_answers, 0.95),
        "pipeline_p50_ms": percentile_cont(pipeline, 0.50),
        "pipeline_p95_ms": percentile_cont(pipeline, 0.95),
        "p95_exploratory": len(pipeline_rows) < 200,
        "token_coverage": {
            "calls": len(observed_calls),
            "usage_provided": len(usage_known),
            "missing_reason": None
            if observed_calls and len(usage_known) == len(observed_calls)
            else "未采集或提供商未返回",
        },
        "browser_samples": len(browser),
        "all_request_rows": len(requests),
        "smoke_claim": "仅冒烟范围；不表示性能回归或容量通过",
    }


def _report(summary: dict[str, Any], manifest: dict[str, Any]) -> str:
    blocked = manifest.get("blocked_reason")
    conclusion = "BLOCKED" if blocked else manifest.get("conclusion", "未执行")
    return "\n".join(
        [
            f"# basic QA 冒烟报告：{summary['run_id']}",
            "",
            "规范：[basic QA 并发性能回归规范](../../../testing/basic-qa-performance-regression.md)。",
            "",
            f"- 结论：{conclusion}",
            f"- campaign_id：{summary['campaign_id']}",
            f"- 层次/档位：{summary['layer']}/{summary['profile']}",
            f"- 计量计划/发送：{summary['n_planned_measured']}/{summary['n_sent_measured']}",
            f"- 主结果：{json.dumps(summary['outcomes'], ensure_ascii=False)}",
            f"- 按时成功完成 P50/P95 毫秒：{summary['completion_p50_ms']}/{summary['completion_p95_ms']}",
            f"- P95 探索性：{summary['p95_exploratory']}",
            f"- Token：{json.dumps(summary['token_coverage'], ensure_ascii=False)}",
            f"- 浏览器样本：{summary['browser_samples']}",
            f"- 阻塞：{blocked or '无'}",
            "",
            summary["smoke_claim"],
            "",
        ]
    )
