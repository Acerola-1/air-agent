"""P/R controlled concurrency load runner (closed-loop).

This is deliberately separate from ``run_baseline``: P/R requires controlled
model/MCP dependencies, at least two minutes of warm-up with eight distinct
questions, and at least three minutes plus forty measured questions per round.
It refuses to run without both ``--enable-load`` and ``allow_load=true`` in the
endpoint file, and it stops escalating when the frozen 10% failure rule trips.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from scripts.product_chat_perf.run_baseline import ResourceSampler, _now_utc
from scripts.unified_campaign.dataset import PlannedSample, load_plan
from scripts.unified_campaign.records import percentile_cont
from scripts.unified_campaign.sender import send_sample

SHANGHAI = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[2]


def _outcomes(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("primary_result") or "unknown") for row in rows))


def _stop_rule(rows: list[dict[str, Any]], window_seconds: int = 60) -> bool:
    """Return true when a 60s window has >=20 ended/cutoff samples and >10% non-success."""
    ended = [
        row
        for row in rows
        if row.get("primary_result") not in {None, "unknown"}
        and row.get("client_complete_ms") is not None
    ]
    if len(ended) < 20:
        return False
    times = sorted(int(row["client_complete_ms"]) for row in ended)
    for index in range(len(times) - 19):
        low, high = times[index], times[index + 19]
        if high - low > window_seconds * 1000:
            continue
        window = [row for row in ended if low <= int(row["client_complete_ms"]) <= high]
        failed = sum(
            1 for row in window if row.get("primary_result") != "stream_completed"
        )
        if len(window) >= 20 and failed / len(window) > 0.10:
            return True
    return False


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [row for row in rows if row.get("primary_result") == "stream_completed"]
    first = [
        float(row["client_first_answer_ms"])
        for row in successes
        if row.get("client_first_answer_ms") is not None
    ]
    complete = [
        float(row["client_complete_ms"])
        for row in successes
        if row.get("client_complete_ms") is not None
    ]
    return {
        "planned": len(rows),
        "sent": sum(1 for row in rows if row.get("sent")),
        "outcomes": _outcomes(rows),
        "success": len(successes),
        "first_answer_p50_ms": percentile_cont(first, 0.50),
        "first_answer_p95_ms": percentile_cont(first, 0.95),
        "complete_p50_ms": percentile_cont(complete, 0.50),
        "complete_p95_ms": percentile_cont(complete, 0.95),
        "first_answer_samples": len(first),
        "complete_samples": len(complete),
    }


def _send_one(
    client: httpx.Client,
    sample: PlannedSample,
    config: dict[str, Any],
    cookie: str,
    thread_id: str | None = None,
) -> dict[str, Any]:
    row = send_sample(
        client,
        sample,
        bff_base_url=config["bff_base_url"],
        cookie=cookie,
        tenant_id=config["tenant_id"],
        context_version=int(config["context_version"]),
        thread_id=thread_id,
    )
    row["stage"] = "measured"
    row["concurrency"] = int(config["concurrency"])
    row["round_id"] = str(config["round_id"])
    return row


def _load_measured_threads(
    client: httpx.Client,
    samples: list[PlannedSample],
    config: dict[str, Any],
    cookie: str,
) -> dict[str, str]:
    """Pre-create Q4 threads and their five normal history turns per case."""
    threads: dict[str, str] = {}
    history = [sample for sample in samples if sample.cohort == "history"]
    measured = {
        sample.case_id: sample for sample in samples if sample.cohort == "measured"
    }
    for case_id, parent in measured.items():
        if parent.category != "Q4":
            continue
        created = client.post(
            f"{config['bff_base_url'].rstrip('/')}/api/threads",
            headers={
                "cookie": cookie,
                "content-type": "application/json",
                "x-tenant-id": config["tenant_id"],
                "x-tenant-context-version": str(config["context_version"]),
                "x-submission-key": f"q4-history-{case_id}-{config['round_id']}",
                "x-operation-kind": "initial",
            },
            json={"metadata": {"graph_id": "basic-qa"}},
        )
        if created.status_code != 200:
            continue
        thread_id = str(created.json()["thread_id"])
        for turn in [item for item in history if item.case_id == case_id]:
            _send_one(client, turn, config, cookie, thread_id=thread_id)
        threads[case_id] = thread_id
    return threads


def _run_round(
    config: dict[str, Any],
    samples: list[PlannedSample],
    cookie: str,
    output: Path,
    evidence: Path,
) -> list[dict[str, Any]]:
    measured = [sample for sample in samples if sample.cohort == "measured"]
    warmup = [sample for sample in measured if sample.category in {"Q1", "Q2"}][:8]
    if len(warmup) < 8:
        warmup = measured[:8]
    rows: list[dict[str, Any]] = []
    lock = threading.Lock()
    queue_in: queue.Queue[PlannedSample] = queue.Queue()
    stop_warmup = threading.Event()

    def worker() -> None:
        with httpx.Client(timeout=None) as client:
            while True:
                try:
                    sample = queue_in.get(timeout=0.2)
                except queue.Empty:
                    if stop_warmup.is_set():
                        return
                    continue
                row = _send_one(client, sample, config, cookie)
                with lock:
                    rows.append(row)

    # Warm-up: at least eight distinct questions for at least two minutes.
    with ThreadPoolExecutor(max_workers=int(config["concurrency"])) as pool:
        for _ in range(int(config["concurrency"])):
            pool.submit(worker)
        start = time.monotonic()
        sent_warmup = 0
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= float(config["warmup_seconds"]) and sent_warmup >= 8:
                break
            if queue_in.qsize() < max(2, int(config["concurrency"]) * 2):
                for sample in warmup:
                    queue_in.put(sample)
                    sent_warmup += 1
            time.sleep(0.05)
        stop_warmup.set()
        pool.shutdown(wait=True)

    # Measurement: one fresh thread per question, Q4 history preloaded once.
    with httpx.Client(timeout=None) as client:
        q4_threads = _load_measured_threads(
            client, samples, {**config, "concurrency": 1}, cookie
        )
    stop = threading.Event()
    queue_in = queue.Queue()
    rows = []

    def measure_worker() -> None:
        with httpx.Client(timeout=None) as client:
            while not stop.is_set():
                try:
                    sample = queue_in.get(timeout=0.2)
                except queue.Empty:
                    continue
                row = _send_one(
                    client,
                    sample,
                    config,
                    cookie,
                    thread_id=q4_threads.get(sample.case_id)
                    if sample.category == "Q4"
                    else None,
                )
                with lock:
                    rows.append(row)

    start = time.monotonic()
    workers = [
        threading.Thread(target=measure_worker, daemon=True)
        for _ in range(int(config["concurrency"]))
    ]
    for worker_thread in workers:
        worker_thread.start()
    sent = 0
    cycles = 0
    while time.monotonic() - start < float(config["measure_seconds"]) or sent < 40:
        if queue_in.qsize() < max(4, int(config["concurrency"]) * 3):
            for sample in measured:
                queue_in.put(sample)
                sent += 1
            cycles += 1
        time.sleep(0.05)
        if _stop_rule(rows):
            stop.set()
            break
    stop.set()
    for worker_thread in workers:
        worker_thread.join(timeout=30)
    with open(output / "requests.partial.jsonl", "a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows


def main(argv: list[str] | None = None) -> int:
    """Run the configured P/R rounds and write the bundle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoints", type=Path, required=True)
    parser.add_argument("--performance-out", type=Path, required=True)
    parser.add_argument("--evidence-out", type=Path, required=True)
    parser.add_argument("--concurrencies", default="1,5,10")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--warmup-seconds", type=float, default=120.0)
    parser.add_argument("--measure-seconds", type=float, default=180.0)
    parser.add_argument("--enable-load", action="store_true")
    args = parser.parse_args(argv)
    config = json.loads(args.endpoints.read_text())
    if not args.enable_load or not config.get("allow_load"):
        sys.stderr.write("P/R 未显式启用；拒绝执行\n")
        return 2
    cookie = os.environ.get(config.get("cookie_env", "CAMPAIGN_COOKIE"), "")
    if not cookie:
        sys.stderr.write("缺少 cookie 环境变量；拒绝发送\n")
        return 2
    if args.performance_out.exists() or args.evidence_out.exists():
        sys.stderr.write("输出目录已存在；拒绝覆盖\n")
        return 2
    args.performance_out.mkdir(parents=True)
    args.evidence_out.mkdir(parents=True)
    samples, digests = load_plan()
    sampler = ResourceSampler(list(config.get("containers") or []))
    sampler.start()
    all_rows: list[dict[str, Any]] = []
    started = datetime.now(UTC)
    stopped_at: int | None = None
    for level in [
        int(item) for item in str(args.concurrencies).split(",") if item.strip()
    ]:
        for round_number in range(1, args.rounds + 1):
            if stopped_at is not None:
                break
            round_config = {
                **config,
                "concurrency": level,
                "round_id": f"c{level}-r{round_number}",
                "warmup_seconds": args.warmup_seconds,
                "measure_seconds": args.measure_seconds,
            }
            rows = _run_round(
                round_config, samples, cookie, args.performance_out, args.evidence_out
            )
            all_rows.extend(rows)
            if _stop_rule(rows):
                stopped_at = level
                break
    sampler.stop()
    args.performance_out.joinpath("requests.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in all_rows) + "\n"
    )
    args.performance_out.joinpath("resources.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in sampler.rows) + "\n"
    )
    summary = {
        "schema_version": "basic-qa-perf.v1",
        "run_id": args.performance_out.name,
        "recorded_at": _now_utc(),
        "layer": "P",
        "profile": "R",
        "stage": "controlled-concurrency",
        "overall": _stats(all_rows),
        "by_round": {
            key: _stats([row for row in all_rows if row.get("round_id") == key])
            for key in sorted({str(row.get("round_id")) for row in all_rows})
        },
        "stopped_at_concurrency": stopped_at,
        "stop_rule": "60s window, >=20 ended, >10% non-success",
        "conclusion": "STOPPED" if stopped_at is not None else "EXPLORATORY",
        "limitations": [
            "controlled model/MCP only",
            "not E/R",
            "not a capacity or SLO claim",
        ],
        "started_at": started.isoformat(),
        "digests": digests,
    }
    (args.performance_out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    (args.performance_out / "checks.jsonl").write_text("")
    (args.performance_out / "browser.jsonl").write_text("")
    sys.stdout.write(
        json.dumps(
            {
                "run_id": args.performance_out.name,
                "rows": len(all_rows),
                "stopped_at": stopped_at,
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
