# ruff: noqa: D101, D102, D103, D107, T201
"""Prepare Q4 fixed history in parallel, then measure Q4 turns sequentially.

History turns are setup, not the measured load: they are sent through the same
BFF/admission path and recorded separately (cohort=history).  The 10 Q4
measured questions are still sent one at a time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from scripts.unified_campaign.dataset import load_plan
from scripts.unified_campaign.sender import send_sample


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _run_case(
    sample_group: list[Any], config: dict[str, Any], cookie: str
) -> dict[str, Any]:
    rows = []
    thread_id = None
    with httpx.Client(timeout=None) as client:
        for sample in sample_group:
            row = send_sample(
                client,
                sample,
                bff_base_url=config["bff_base_url"],
                cookie=cookie,
                tenant_id=config["tenant_id"],
                context_version=int(config["context_version"]),
                thread_id=thread_id,
                include_answer_text=True,
            )
            row.update(
                {
                    "schema_version": "basic-qa-perf.v1",
                    "run_id": config["run_id"],
                    "round_id": "round-1",
                    "layer": "E",
                    "profile": "S",
                    "stage": "history",
                    "planned_at": _now(),
                    "deadline_seconds": sample.deadline_seconds,
                    "request_interval_seconds": 0,
                }
            )
            if row.get("thread_id"):
                thread_id = str(row["thread_id"])
            rows.append(row)
    return {"case_id": sample_group[0].case_id, "thread_id": thread_id, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoints", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--enable-real-models", action="store_true")
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args(argv)
    config = json.loads(args.endpoints.read_text())
    if not args.enable_real_models or not config.get("allow_real_models"):
        sys.stderr.write("真实模型未显式启用\n")
        return 2
    cookie = os.environ.get(config.get("cookie_env", "CAMPAIGN_COOKIE"), "")
    if not cookie:
        sys.stderr.write("缺少 cookie\n")
        return 2
    config["run_id"] = args.out_dir.name
    samples, _ = load_plan("round-1")
    q4_history: dict[str, list[Any]] = {}
    q4_measured: dict[str, Any] = {}
    for sample in samples:
        if sample.category != "Q4":
            continue
        if sample.cohort == "history":
            q4_history.setdefault(sample.case_id, []).append(sample)
        elif sample.cohort == "measured":
            q4_measured[sample.case_id] = sample
    args.out_dir.mkdir(parents=True, exist_ok=False)
    args.evidence_dir.mkdir(parents=True, exist_ok=False)
    started = _now()
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_run_case, group, config, cookie): case_id
            for case_id, group in q4_history.items()
        }
        for future in as_completed(futures):
            case_id = futures[future]
            try:
                results[case_id] = future.result()
            except Exception as exc:  # noqa: BLE001
                results[case_id] = {
                    "case_id": case_id,
                    "thread_id": None,
                    "rows": [],
                    "error": repr(exc),
                }
            print(f"history {case_id} done", flush=True)
    history_rows: list[dict[str, Any]] = []
    threads: dict[str, str] = {}
    answers = []
    for case_id, result in results.items():
        history_rows.extend(result.get("rows") or [])
        if result.get("thread_id"):
            threads[case_id] = str(result["thread_id"])
        for row in result.get("rows") or []:
            text = row.pop("_answer_text", None)
            if text:
                answers.append(
                    {
                        "sample_id": row["sample_id"],
                        "case_id": row["case_id"],
                        "answer": text,
                        "cohort": "history",
                        "recorded_at": _now(),
                    }
                )
    # Settle before the measured Q4 turns.
    time.sleep(10)
    measured_rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=None) as client:
        for case_id, sample in q4_measured.items():
            thread_id = threads.get(case_id)
            row = send_sample(
                client,
                sample,
                bff_base_url=config["bff_base_url"],
                cookie=cookie,
                tenant_id=config["tenant_id"],
                context_version=int(config["context_version"]),
                thread_id=thread_id,
                include_answer_text=True,
            )
            row.update(
                {
                    "schema_version": "basic-qa-perf.v1",
                    "run_id": config["run_id"],
                    "round_id": "round-1",
                    "layer": "E",
                    "profile": "S",
                    "stage": "measured",
                    "planned_at": _now(),
                    "deadline_seconds": sample.deadline_seconds,
                    "request_interval_seconds": 0,
                }
            )
            text = row.pop("_answer_text", None)
            if text:
                answers.append(
                    {
                        "sample_id": row["sample_id"],
                        "case_id": row["case_id"],
                        "answer": text,
                        "cohort": "measured",
                        "recorded_at": _now(),
                    }
                )
            measured_rows.append(row)
            print(
                f"measured {case_id} {row.get('primary_result')} {row.get('client_complete_ms')}",
                flush=True,
            )
    (args.out_dir / "q4-history.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in history_rows)
    )
    (args.out_dir / "q4-measured.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in measured_rows)
    )
    (args.out_dir / "threads.json").write_text(
        json.dumps(threads, ensure_ascii=False, indent=2) + "\n"
    )
    (args.out_dir / "q4-meta.json").write_text(
        json.dumps(
            {
                "started_at": started,
                "ended_at": _now(),
                "workers": args.workers,
                "threads": threads,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    (args.evidence_dir / "answers-q4.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in answers)
    )
    print(
        json.dumps(
            {
                "history_rows": len(history_rows),
                "measured_rows": len(measured_rows),
                "threads": len(threads),
            },
            ensure_ascii=False,
        )
    )
    return 0 if len(measured_rows) == 10 else 1


if __name__ == "__main__":
    raise SystemExit(main())
