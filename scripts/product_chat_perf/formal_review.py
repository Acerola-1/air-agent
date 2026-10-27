"""Reconcile retained formal-round evidence offline without rewriting it."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.product_chat_perf.quality import assess_answer
from scripts.product_chat_perf.run_baseline import _answer_quality
from scripts.unified_campaign.records import percentile_cont


def request_key(row: dict[str, Any]) -> str:
    """Use the physical submission identity, never the reusable question label."""
    key = row.get("submission_key") or row.get("attempt_id")
    if not key:
        raise ValueError("请求缺少 submission_key/attempt_id，不能可靠关联")
    return str(key)


def identity_evidence(
    observations: list[dict[str, Any]], user_id: str, thread_id: str
) -> dict[str, str]:
    """Missing observations or attribution are UNKNOWN, never vacuous PASS."""
    result = {}
    for field, expected in [("user_id", user_id), ("session_id", thread_id)]:
        values = [o.get(field) for o in observations]
        if any(v and v != expected for v in values):
            status = "FAIL"
        elif not values or not any(values):
            status = "UNKNOWN"
        elif field == "user_id" and not all(values):
            status = "UNKNOWN"
        else:
            # SDK child spans can omit session_id; at least one must attest it.
            status = "PASS"
        result[field] = status
    return result


def recover_answers(
    rows: list[dict[str, Any]], answers: list[dict[str, Any]]
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Recover old label-only evidence using unique time AND recorded digest.

    Old planned_at was incorrectly recorded at send completion. Only this old
    format uses the two-second association window. New records carry the exact
    submission key. Ambiguity, hash mismatch and missing nonempty answers fail
    closed instead of choosing the most recent answer for a question label.
    """
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_key = {request_key(r): r for r in rows}
    if len(by_key) != len(rows):
        raise ValueError("重复物理请求标识")
    for row in rows:
        by_label[str(row["sample_id"])].append(row)
    recovered: dict[str, str] = {}
    proofs = []
    for answer in answers:
        explicit = answer.get("submission_key") or answer.get("attempt_id")
        distance = None
        if explicit:
            candidates = [by_key[str(explicit)]] if str(explicit) in by_key else []
            method = "explicit_submission"
        else:
            stamp = datetime.fromisoformat(answer["recorded_at"])
            candidates = []
            for row in by_label[str(answer["sample_id"])]:
                end = row.get("ended_at") or row.get("planned_at")
                if end:
                    delta = abs((stamp - datetime.fromisoformat(end)).total_seconds())
                    if delta <= 2:
                        candidates.append(row)
                        distance = delta
            method = "legacy_unique_time_and_hash"
        if len(candidates) != 1:
            raise ValueError("答案关联缺失或不唯一；必须人工复核，不能覆盖")
        row = candidates[0]
        key = request_key(row)
        if key in recovered:
            raise ValueError("同一请求存在多份答案，拒绝覆盖")
        text = str(answer.get("answer") or "")
        digest = hashlib.sha256(text.encode()).hexdigest()
        expected = row.get("answer_sha256")
        if expected and digest != expected:
            raise ValueError("答案与该请求的 SHA256 不符")
        if not explicit and text and not expected:
            raise ValueError("旧格式非空答案缺少请求摘要，无法证明关联")
        recovered[key] = text
        proofs.append(
            {
                "submission_key": key,
                "sample_id": row["sample_id"],
                "stage": row.get("stage"),
                "concurrency_level": row.get("concurrency_level"),
                "method": method,
                "end_time_distance_seconds": distance,
                "sha256_verified": bool(expected),
                "answer_sha256": digest if text else None,
            }
        )
    if any(r.get("answer_nonempty") and request_key(r) not in recovered for r in rows):
        raise ValueError("存在未找回的非空答案")
    return recovered, proofs


def history_complete(row: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    """Require all five successful, associated history turns on this thread."""
    if row.get("category") != "Q4":
        return True
    history = [
        r
        for r in rows
        if r.get("cohort") == "history"
        and r.get("case_id") == row.get("case_id")
        and r.get("stage") == row.get("stage")
        and r.get("concurrency_level") == row.get("concurrency_level")
    ]
    return len(history) == 5 and all(
        r.get("primary_result") == "stream_completed"
        and r.get("answer_associated")
        and r.get("answer_nonempty")
        and r.get("thread_id") == row.get("thread_id")
        for r in history
    )


def latency_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep the attempt denominator and unavailable legacy metrics explicit."""
    first = [
        r["client_first_answer_ms"]
        for r in rows
        if r.get("client_first_answer_ms") is not None
    ]
    completed = [r for r in rows if r.get("primary_result") == "stream_completed"]
    end = [
        r["client_complete_ms"]
        for r in completed
        if r.get("client_complete_ms") is not None
    ]
    after_first = [
        r["client_complete_ms"] - r["client_first_answer_ms"]
        for r in completed
        if r.get("client_first_answer_ms") is not None
    ]
    return {
        "attempted": len(rows),
        "runs_started": sum(bool(r.get("attempt_id")) for r in rows),
        "completed": len(completed),
        "first_body_observed": len(first),
        "no_first_body": len(rows) - len(first),
        "first_body_p50_ms": percentile_cont(first, 0.5),
        "first_body_p95_ms": percentile_cont(first, 0.95),
        "complete_p50_ms": percentile_cont(end, 0.5),
        "complete_p95_ms": percentile_cont(end, 0.95),
        "post_first_body_p50_ms": percentile_cont(after_first, 0.5),
        "max_effective_body_gap_ms": None,
        "gap_missing_reason": "legacy stream has no effective-unit timeline",
        "failures": dict(
            Counter(
                r.get("primary_result")
                for r in rows
                if r.get("primary_result") != "stream_completed"
            )
        ),
    }


def review_bundle(
    source: Path, answers_path: Path, out: Path, *, oracles_path: Path | None = None
) -> dict[str, Any]:
    """Recompute associations and checks, retaining original files untouched."""
    if out.exists():
        raise ValueError("输出已存在，禁止覆盖原始报告或复算结果")
    files = sorted(source.glob("requests*.jsonl"))
    rows = [
        json.loads(s) for f in files for s in f.read_text().splitlines() if s.strip()
    ]
    answers = [
        json.loads(s) for s in answers_path.read_text().splitlines() if s.strip()
    ]
    recovered, proofs = recover_answers(rows, answers)
    reconciliation = json.loads((source / "langfuse-reconcile.json").read_text())
    by_attempt = {a["attempt_id"]: a for a in reconciliation["association"]}
    oracles = json.loads(oracles_path.read_text()) if oracles_path else {}
    cases_path = (
        Path(__file__).resolve().parents[2]
        / "docs/testing/data/basic-qa-reference-cases.v1.json"
    )
    cases = {c["case_id"]: c for c in json.loads(cases_path.read_text())["cases"]}
    baseline = [
        r
        for r in rows
        if r.get("stage") == "baseline" and r.get("cohort") == "measured"
    ]
    checks = []
    for row in baseline:
        key = request_key(row)
        evidence = by_attempt.get(row.get("attempt_id"), {})
        answer = recovered.get(key, "")
        old_ok, old_reasons = _answer_quality(
            cases[row["case_id"]], answer, int(evidence.get("tool_spans") or 0)
        )
        history_ok = history_complete(row, rows)
        if not answer:
            quality = {"status": "FAIL", "reasons": ["empty_answer"]}
        elif not history_ok:
            quality = {"status": "BLOCKED", "reasons": ["required_history_incomplete"]}
        else:
            quality = assess_answer(
                cases[row["case_id"]],
                answer,
                source="real",
                tool_count=int(evidence.get("tool_spans") or 0),
                oracle=oracles.get(key),
                history_verified=history_ok,
            )
        checks.append(
            {
                "submission_key": key,
                "case_id": row["case_id"],
                "category": row["category"],
                "answer_recovered": key in recovered,
                "history_complete": history_ok,
                "trace_found": bool(evidence.get("trace_found")),
                "tool_evidence": "observed"
                if evidence.get("trace_found")
                else "unknown_missing_trace",
                "legacy_rule_recomputed": {
                    "pass": old_ok,
                    "reasons": old_reasons,
                    "usable_as_factual_quality": False,
                },
                "factual_quality": quality,
            }
        )
    trace_rows = []
    for item in reconciliation["association"]:
        found = bool(item.get("trace_found"))
        trace_rows.append(
            {
                "attempt_id": item["attempt_id"],
                "trace_id": item["expected_trace_id"],
                "found": found,
                "user_identity": ("PASS" if item.get("identity_user_ok") else "FAIL")
                if found
                else "UNKNOWN",
                "session_identity": (
                    "PASS" if item.get("identity_session_ok") else "FAIL"
                )
                if found
                else "UNKNOWN",
                "evidence_scope": "retained observed-span checks, not completeness proof",
            }
        )
    summary = {
        "schema_version": "formal-performance-review.v1",
        "source_run_id": source.name,
        "answer_records": len(answers),
        "answers_recovered": len(recovered),
        "legacy_label_collision_count": sum(
            n > 1 for n in Counter(r["sample_id"] for r in rows).values()
        ),
        "baseline": latency_summary(baseline),
        "baseline_by_category": {
            c: latency_summary([r for r in baseline if r["category"] == c])
            for c in ["Q1", "Q2", "Q3", "Q4"]
        },
        "concurrency": {
            str(c): latency_summary(
                [
                    r
                    for r in rows
                    if r.get("stage") == "concurrency"
                    and r.get("concurrency_level") == c
                ]
            )
            for c in [1, 2, 5, 10]
        },
        "quality": dict(Counter(c["factual_quality"]["status"] for c in checks)),
        "legacy_rule_unmixed_pass_count": sum(
            c["legacy_rule_recomputed"]["pass"] for c in checks
        ),
        "q4_history_incomplete_cases": [
            c["case_id"] for c in checks if not c["history_complete"]
        ],
        "langfuse": {
            "runs": len(trace_rows),
            "found": sum(r["found"] for r in trace_rows),
            "user_identity": dict(Counter(r["user_identity"] for r in trace_rows)),
            "session_identity": dict(
                Counter(r["session_identity"] for r in trace_rows)
            ),
            "completeness": "not established by trace existence",
        },
    }
    out.mkdir(parents=True)
    for name, values in [
        ("answer-associations.jsonl", proofs),
        ("quality-checks.jsonl", checks),
        ("trace-checks.jsonl", trace_rows),
    ]:
        (out / name).write_text(
            "".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values)
        )
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    inputs = [*files, source / "langfuse-reconcile.json", cases_path]
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "network_requests": 0,
                "input_sha256": {
                    str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in inputs
                },
                "private_answers_sha256": hashlib.sha256(
                    answers_path.read_bytes()
                ).hexdigest(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return summary


def main() -> None:
    """Run the network-free evidence review command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--oracles", type=Path)
    args = parser.parse_args()
    print(  # noqa: T201 - CLI output contains summaries, never answers
        json.dumps(
            review_bundle(
                args.source, args.answers, args.out, oracles_path=args.oracles
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
