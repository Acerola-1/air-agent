# ruff: noqa: D101, D102, D103, D107, D415, T201
"""E-CAP-Q12-v1 最大稳定并发连续发压采集器（正式环境授权轮）。

保留的旧闭环采集工具，不是现行流程的 Locust 适配器。
现行流程见 docs/performance/pressure-testing-workflow.md。
真实执行仍受本工具的显式配置和原有保护约束，不从历史报告读取默认配置。
calibrate 子命令使用本机协议模拟，不发任何真实模型请求。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from scripts.unified_campaign.dataset import load_plan
from scripts.unified_campaign.sender import send_sample

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / ".local"
BFF = "http://127.0.0.1:3000"
PROFILE = "E-CAP-Q12-v1"
DEADLINE = {"Q1": 60, "Q2": 120}
MONITORED = [
    "air-agent-langgraph-api",
    "air-agent-langfuse-clickhouse",
    "air-agent-langfuse-worker",
]
CASE_IDS = [f"BQA-{i:03d}" for i in range(1, 6)] + [
    f"BQA-{i:03d}" for i in range(11, 16)
]
SEED = 20261025205020


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load(run_id: str) -> tuple[Path, Path, dict, dict]:
    perf = ROOT / "docs/reviews/basic-qa-performance" / run_id
    private = PRIVATE_ROOT / f"{run_id}-private"
    session = json.loads((private / "session.json").read_text())
    cond = json.loads((private / "conditions.json").read_text())
    return perf, private, session, cond


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Budget:
    """Global logical-question budget shared by every phase of this round."""

    def __init__(
        self, private: Path, max_questions: int, max_seconds: float = 28800
    ) -> None:
        private.mkdir(parents=True, exist_ok=True)
        self.path = private / "budget.json"
        self.max = max_questions
        self.max_seconds = max_seconds
        self.lock = threading.Lock()
        if self.path.is_file():
            self.state = json.loads(self.path.read_text())
        else:
            self.state = {"sent_total": 0, "started_at": _now()}
            self._flush()

    def _flush(self) -> None:
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1))

    def reserve(self, n: int) -> bool:
        with self.lock:
            if (
                datetime.now(UTC) - datetime.fromisoformat(self.state["started_at"])
            ).total_seconds() >= self.max_seconds:
                return False
            if self.state["sent_total"] + n > self.max:
                return False
            self.state["sent_total"] += n
            self._flush()
            return True

    def remaining(self) -> int:
        return self.max - self.state["sent_total"]


def _container_stats(names: list[str]) -> dict[str, dict]:
    completed = subprocess.run(
        ["container", "stats", "--no-stream", "--format", "json", *names],
        capture_output=True,
        text=True,
        check=False,
    )
    out: dict[str, dict] = {}
    if completed.returncode != 0:
        return out
    try:
        for row in json.loads(completed.stdout or "[]"):
            out[str(row.get("id"))] = {
                "mem_used_mb": round(
                    int(row.get("memoryUsageBytes") or 0) / 1048576, 1
                ),
                "mem_limit_mb": round(
                    int(row.get("memoryLimitBytes") or 0) / 1048576, 1
                ),
            }
    except json.JSONDecodeError:
        pass
    return out


class StopConditions:
    """Hard-stop rules: 60s error window, memory ceiling, component liveness."""

    def __init__(self, monitor_infra: bool = True) -> None:
        self.monitor_infra = monitor_infra
        self.event = threading.Event()
        self.reason: str | None = None
        self.finished_recent: list[tuple[float, bool]] = []
        self.mem_since: dict[str, float] = {}
        self.memory_events: list[dict] = []
        self.lock = threading.Lock()

    def trigger(self, reason: str) -> None:
        if not self.event.is_set():
            self.reason = f"{_now()} {reason}"
            self.event.set()

    def record_finish(self, ok: bool) -> None:
        with self.lock:
            now = time.monotonic()
            self.finished_recent.append((now, ok))
            self.finished_recent = [t for t in self.finished_recent if now - t[0] <= 60]
            recent = list(self.finished_recent)
        if len(recent) >= 20:
            bad = sum(1 for _, ok in recent if not ok)
            if bad / len(recent) > 0.10:
                self.trigger(
                    f"60s 错误窗：{len(recent)} 个结束请求中 {bad} 非成功（>10%）"
                )

    def poll_infra(self) -> None:
        if not self.monitor_infra:
            return
        rows = _container_stats(MONITORED)
        if len(rows) != len(MONITORED):
            self.trigger("资源采集缺失，停止新发")
            return
        now = time.monotonic()
        for name in MONITORED:
            row = rows.get(name)
            if not row or not row.get("mem_limit_mb"):
                continue
            ratio = row["mem_used_mb"] / (row["mem_limit_mb"] * 0.9)
            with self.lock:
                if ratio >= 1.0:
                    self.mem_since.setdefault(name, now)
                    if now - self.mem_since[name] >= 60:
                        self.trigger(f"{name} 内存达到硬限额 90% 持续 60s")
                else:
                    self.mem_since.pop(name, None)
                self.memory_events.append(
                    {"t": _now(), "name": name, **row, "over_90pct": ratio >= 1.0}
                )
        try:
            result = subprocess.run(
                ["container", "ls", "--format", "json"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            running = {
                r["id"]
                for r in json.loads(result.stdout)
                if r["status"]["state"] == "running"
            }
        except (subprocess.SubprocessError, ValueError, KeyError):
            self.trigger("容器状态采集不可用")
            return
        for name in MONITORED:
            if name not in running:
                self.trigger(f"组件退出：{name}")


def _samples() -> list[Any]:
    all_samples, _ = load_plan("round-1")
    by_case = {s.case_id: s for s in all_samples if s.cohort == "measured"}
    base = [by_case[c] for c in CASE_IDS]
    rng = random.Random(SEED)
    rng.shuffle(base)
    return base  # _send_one already rotates by finite-list modulo.


class LevelRunner:
    """Sustained closed-loop runner for one concurrency level."""

    def __init__(
        self,
        run_id: str,
        level: int,
        stage: str,
        extra: dict[str, Any],
        session: dict,
        cond: dict,
        budget: Budget,
        stops: StopConditions,
        bff: str = BFF,
    ) -> None:
        self.run_id = run_id
        self.level = level
        self.stage = stage
        self.extra = extra
        self.session = session
        self.cond = cond
        self.budget = budget
        self.stops = stops
        self.bff = bff
        self.samples = _samples()
        self._seq = 0
        self._seq_lock = threading.Lock()
        self.rows: list[dict] = []
        self.rows_lock = threading.Lock()
        self.inflight = 0
        self.inflight_lock = threading.Lock()
        self.inflight_samples: list[tuple[str, int, str]] = []
        self.phase = "warmup"
        self.measure_until = 0.0
        self.measure_sends_target = 0
        self.measure_sends = 0
        self.measure_counts: Counter[str] = Counter()
        self.origin = time.monotonic()
        self.measure_start_offset_ms = 0.0
        self.measure_end_offset_ms = 0.0
        self.new_send_stopped = threading.Event()

    def _next_seq(self) -> int:
        with self._seq_lock:
            seq = self._seq
            self._seq += 1
            return seq

    def _send_one(self, client=None) -> None:
        planned_mono = time.monotonic()
        if self.new_send_stopped.is_set() or self.stops.event.is_set():
            return
        if not self.budget.reserve(1):
            self.stops.trigger("全局时间或逻辑问题预算耗尽")
            self.new_send_stopped.set()
            return
        with self._seq_lock:
            seq = self._seq
            self._seq += 1
            phase = self.phase
            sample = self.samples[seq % len(self.samples)]
            if phase == "measure":
                self.measure_sends += 1
                self.measure_counts[sample.category] += 1
        s = replace(
            sample,
            sample_id=f"{self.run_id}:{self.stage}:r{self.extra.get('round')}:C{self.level}:{phase}:{seq}:{sample.case_id}",
        )
        planned_at = _now()
        with self.inflight_lock:
            self.inflight += 1
        delay = None
        try:
            with (
                httpx.Client(timeout=None) if client is None else nullcontext(client)
            ) as active:
                delay = (time.monotonic() - planned_mono) * 1000
                row = send_sample(
                    active,
                    s,
                    bff_base_url=self.bff,
                    cookie=self.session["cookie"],
                    tenant_id=self.session["tenant_id"],
                    context_version=int(self.session["context_version"]),
                    thread_id=None,
                    include_answer_text=False,
                )
        except Exception as exc:
            row = {
                "primary_result": "sender_exception",
                "sent": False,
                "error_type": type(exc).__name__,
            }
        finally:
            with self.inflight_lock:
                self.inflight -= 1
        row.update(
            {
                "schema_version": "maxcap-perf.v1",
                "run_id": self.run_id,
                "profile": PROFILE,
                "stage": self.stage,
                "level": self.level,
                "round": self.extra.get("round"),
                "sequence": seq,
                "phase": phase,
                "category": s.category,
                "case_id": s.case_id,
                "planned_at": planned_at,
                "ended_at": _now(),
                "planned_offset_ms": (planned_mono - self.origin) * 1000,
                "ended_offset_ms": (time.monotonic() - self.origin) * 1000,
                "dispatch_delay_ms": delay,
                "deadline_seconds": DEADLINE[s.category],
            }
        )
        self.stops.record_finish(row.get("primary_result") == "stream_completed")
        with self.rows_lock:
            self.rows.append(row)

    def _worker(self) -> None:
        with httpx.Client(timeout=None) as client:
            while not self.new_send_stopped.is_set() and not self.stops.event.is_set():
                self._send_one(client)

    def run(
        self,
        warmup_min_seconds: int,
        warmup_min_completions: int,
        measure_min_seconds: int,
        measure_min_sends: int,
        drain_extra_seconds: int,
        sink_path: Path,
    ) -> dict[str, Any]:
        started_mono = time.monotonic()
        self.origin = started_mono
        if sink_path.exists():
            raise ValueError("输出已存在，拒绝覆盖")
        monitors_stop = threading.Event()

        def inflight_sampler() -> None:
            while not monitors_stop.wait(1.0):
                with self.inflight_lock:
                    n = self.inflight
                self.inflight_samples.append((_now(), n, self.phase))

        def infra_monitor() -> None:
            while not monitors_stop.wait(5.0):
                self.stops.poll_infra()
                if self.stops.event.is_set():
                    self.new_send_stopped.set()

        monitors = [
            threading.Thread(target=inflight_sampler, daemon=True),
            threading.Thread(target=infra_monitor, daemon=True),
        ]
        for t in monitors:
            t.start()
        workers = [
            threading.Thread(target=self._worker, daemon=True)
            for _ in range(self.level)
        ]
        for w in workers:
            w.start()

        warm_deadline = started_mono + warmup_min_seconds
        while time.monotonic() < warm_deadline and not self.stops.event.is_set():
            time.sleep(1.0)
        while (
            len(self.rows) < warmup_min_completions
            and not self.stops.event.is_set()
            and time.monotonic() < started_mono + 600
        ):
            time.sleep(1.0)
        warmup_rows = len(self.rows)
        warmup_seconds = round(time.monotonic() - started_mono, 1)

        with self._seq_lock:
            self.phase = "measure"
        self.measure_until = time.monotonic() + measure_min_seconds
        self.measure_sends_target = measure_min_sends
        measure_started = time.monotonic()
        self.measure_start_offset_ms = (measure_started - self.origin) * 1000
        while not self.new_send_stopped.is_set() and not self.stops.event.is_set():
            with self._seq_lock:
                samples_met = self.measure_sends >= measure_min_sends and all(
                    self.measure_counts[c] >= math.ceil(measure_min_sends / 2)
                    for c in ["Q1", "Q2"]
                )
            if time.monotonic() >= self.measure_until and samples_met:
                self.new_send_stopped.set()
                break
            time.sleep(0.05)
        measure_wall = round(time.monotonic() - measure_started, 1)

        self.measure_end_offset_ms = (time.monotonic() - self.origin) * 1000
        with self._seq_lock:
            self.phase = "drain"
        self.new_send_stopped.set()
        drain_started = time.monotonic()
        drain_deadline = drain_started + max(DEADLINE.values()) + drain_extra_seconds
        while self.inflight > 0 and time.monotonic() < drain_deadline:
            time.sleep(1.0)
        drain_seconds = round(time.monotonic() - drain_started, 1)
        if self.inflight:
            self.stops.trigger("排空超时，仍有未知运行")
        for worker in workers:
            worker.join(timeout=1)
        monitors_stop.set()
        for t in monitors:
            t.join(timeout=3)

        with self.rows_lock:
            all_rows = list(self.rows)
        measure_rows = [r for r in all_rows if r["phase"] == "measure"]
        result = self._evaluate(
            all_rows,
            measure_rows,
            warmup_rows,
            warmup_seconds,
            measure_wall,
            drain_seconds,
        )
        result["stop_reason"] = self.stops.reason
        result["stopped_by_condition"] = self.stops.event.is_set()
        result["seq_next"] = self._seq
        result["inflight_memory_events"] = self.stops.memory_events[-20:]
        sink_path.parent.mkdir(parents=True, exist_ok=True)
        with sink_path.open("a") as fh:
            for r in all_rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        return result

    def _evaluate(
        self,
        all_rows: list[dict],
        measure_rows: list[dict],
        warmup_rows: int,
        warmup_seconds: float,
        measure_wall: float,
        drain_seconds: float,
    ) -> dict[str, Any]:
        by_cat: dict[str, list[dict]] = {"Q1": [], "Q2": []}
        for r in measure_rows:
            by_cat.setdefault(r.get("category") or "?", []).append(r)

        def pct(values: list[float], q: float) -> float | None:
            if not values:
                return None
            srt = sorted(values)
            k = (len(srt) - 1) * q
            f, c = math.floor(k), math.ceil(k)
            return srt[f] if f == c else srt[f] + (srt[c] - srt[f]) * (k - f)

        def agg(rows: list[dict]) -> dict:
            ok = [
                r
                for r in rows
                if r.get("primary_result") == "stream_completed"
                and r.get("answer_nonempty")
                and r.get("answer_associated")
                and (
                    r.get("client_complete_ms")
                    if r.get("client_complete_ms") is not None
                    else 1e9
                )
                <= (r.get("deadline_seconds") or 1e9) * 1000
            ]
            ok_ids = {id(r) for r in ok}
            firsts = [
                float(r["client_first_answer_ms"])
                for r in ok
                if r.get("client_first_answer_ms") is not None
            ]
            comps = [float(r["client_complete_ms"]) for r in ok]
            failures = Counter(
                str(r.get("primary_result")) for r in rows if id(r) not in ok_ids
            )
            return {
                "sent": len(rows),
                "on_time_success": len(ok),
                "success_rate": round(len(ok) / len(rows), 4) if rows else None,
                "first_body_p50_ms": pct(firsts, 0.5),
                "first_body_p95_ms": pct(firsts, 0.95),
                "complete_p50_ms": pct(comps, 0.5),
                "complete_p95_ms": pct(comps, 0.95),
                "failures": dict(failures),
                "success_completed_within_measure_window": sum(
                    self.measure_start_offset_ms
                    <= r["ended_offset_ms"]
                    <= self.measure_end_offset_ms
                    for r in ok
                ),
            }

        overall = agg(measure_rows)
        q1 = agg(by_cat.get("Q1", []))
        q2 = agg(by_cat.get("Q2", []))
        measure_samples = [n for _, n, ph in self.inflight_samples if ph == "measure"]
        attainment = (
            round(
                sum(1 for n in measure_samples if n >= 0.9 * self.level)
                / len(measure_samples),
                4,
            )
            if measure_samples
            else None
        )
        delays = [
            float(r["dispatch_delay_ms"])
            for r in measure_rows
            if r.get("dispatch_delay_ms") is not None
        ]
        slo = self.cond["slo"]
        reasons = []
        for label, a in (("overall", overall), ("Q1", q1), ("Q2", q2)):
            if (a["success_rate"] or 0) < slo["completion_success_rate_min"]:
                reasons.append(f"{label}_rate")
            if (a["first_body_p95_ms"] or 1e9) > slo["legacy_first_body_p95_ms_max"]:
                reasons.append(f"{label}_first_p95")
            if (a["complete_p95_ms"] or 1e9) > slo["complete_p95_ms_max"]:
                reasons.append(f"{label}_complete_p95")
        if attainment is None or attainment < 0.9:
            reasons.append("inflight")
        if (pct(delays, 0.95) or 0) > 100:
            reasons.append("dispatch")
        if self.stops.event.is_set():
            reasons.append("stop_condition")
        return {
            "level": self.level,
            "stage": self.stage,
            "round": self.extra.get("round"),
            "warmup": {"rows": warmup_rows, "seconds": warmup_seconds},
            "measure": {
                "seconds": measure_wall,
                "sends": overall["sent"],
                "drain_seconds": drain_seconds,
            },
            "inflight_attainment": attainment,
            "inflight_max": max(
                (n for _, n, ph in self.inflight_samples if ph == "measure"), default=0
            ),
            "dispatch_delay_p95_ms": pct(delays, 0.95),
            "overall": overall,
            "Q1": q1,
            "Q2": q2,
            "passed": not reasons,
            "pass_reasons_failed": reasons,
            "throughput_per_sec": round(
                overall["success_completed_within_measure_window"] / measure_wall, 3
            )
            if measure_wall
            else None,
            "dispatches_per_sec": round(overall["sent"] / measure_wall, 3)
            if measure_wall
            else None,
        }


def cmd_login(args: argparse.Namespace) -> int:
    if not getattr(args, "conditions", None):
        raise SystemExit("请显式提供本轮 --conditions；历史报告默认配置已清理")
    cond = json.loads(Path(args.conditions).read_text())
    import scripts.formal_root_round as frr

    rc = frr.cmd_login(argparse.Namespace(run_id=args.run_id, tenant_id=args.tenant_id))
    if rc != 0:
        return rc
    private = PRIVATE_ROOT / f"{args.run_id}-private"
    cond["status"] = "prepared"
    cond["run_id"] = args.run_id
    cond["private_evidence_root"] = str(private.relative_to(ROOT))
    cond["execution_started_at"] = None
    (private / "conditions.json").write_text(
        json.dumps(cond, ensure_ascii=False, indent=1)
    )
    print("conditions copied to private dir")
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    run_id = args.run_id
    private = PRIVATE_ROOT / f"{run_id}-private"
    cond = json.loads((private / "conditions.json").read_text())
    head = subprocess.run(
        ["git", "log", "-1", "--format=%H"], capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--short"], capture_output=True, text=True, check=True
    ).stdout
    from scripts.product_chat_perf.local_resources import inventory

    actual = inventory()
    cond.update(
        {
            "status": "resource_preflight_pending",
            "execution_started_at": None,
            "actual_head": head,
            "dirty_file_count": len(dirty.splitlines()),
            "source_snapshot_sha256": _sha256_file(
                ROOT / "scripts/max_concurrency_round.py"
            ),
            "uvlock_sha256": _sha256_file(ROOT / "uv.lock"),
            "runtime_verified_at": None,
            "actual_resources": actual,
            "local_resource_readiness": {
                "baseline_verified": False,
                "evidence_path": None,
            },
            "test_data_root": str(
                (ROOT / "docs/reviews/basic-qa-performance" / run_id).relative_to(ROOT)
            ),
        }
    )
    (private / "conditions.json").write_text(
        json.dumps(cond, ensure_ascii=False, indent=1)
    )
    print(
        json.dumps(
            {
                "frozen": False,
                "status": cond["status"],
                "head": head[:8],
                "dirty": cond["dirty_file_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def require_local_ready(args: argparse.Namespace) -> None:
    """Block paid traffic until local baseline and external supervision are verified."""
    if not getattr(args, "enable_real_load", False):
        raise SystemExit("本地准备阶段禁止真实负载；需显式 --enable-real-load")
    _, _, _, cond = _load(args.run_id)
    gate = cond.get("local_resource_readiness") or {}
    if gate.get("baseline_verified") is not True or not gate.get("evidence_path"):
        raise SystemExit("本地资源基线未核验，不得启动真实负载")
    evidence = Path(gate["evidence_path"])
    if not evidence.is_absolute():
        evidence = ROOT / evidence
    if not evidence.is_file():
        raise SystemExit("本地资源基线证据不存在")
    checked = json.loads(evidence.read_text())
    if checked.get("status") != "VERIFIED_FOR_LOCAL_BOUNDED_LOAD":
        raise SystemExit("资源基线尚未通过")
    age = (
        datetime.now(UTC) - datetime.fromisoformat(checked["recorded_at"])
    ).total_seconds()
    if not 0 <= age <= 1800:
        raise SystemExit("资源基线已过期，需重新采集")
    if os.environ.get("AIR_AGENT_PERF_SERVICE_GUARD") != "enabled":
        raise SystemExit("必须启用服务组资源监督，不能只监控发压进程")
    if os.environ.get("AIR_AGENT_PERF_SUPERVISOR_PID") != str(os.getppid()):
        raise SystemExit("必须先启动独立资源监督器，再启动发压子进程")
    if checked.get("profile_sha256") != os.environ.get("AIR_AGENT_PERF_PROFILE_SHA256"):
        raise SystemExit("资源基线与监督配置不一致")


def cmd_precheck(args: argparse.Namespace) -> int:
    require_local_ready(args)
    run_id = args.run_id
    perf, private, session, cond = _load(run_id)
    budget = Budget(
        private,
        cond["budgets"]["global_max_logical_questions"],
        cond["budgets"]["global_max_seconds"],
    )
    samples, _ = load_plan("round-1")
    by_case = {s.case_id: s for s in samples if s.cohort == "measured"}
    rows = []
    with httpx.Client(timeout=None) as client:
        for case_id in ("BQA-001", "BQA-011"):
            s = by_case[case_id]
            if not budget.reserve(1):
                raise SystemExit("预检发送前全局预算耗尽")
            row = send_sample(
                client,
                s,
                bff_base_url=BFF,
                cookie=session["cookie"],
                tenant_id=session["tenant_id"],
                context_version=int(session["context_version"]),
                thread_id=None,
                include_answer_text=False,
            )
            row.update(
                {"stage": "precheck", "case_id": case_id, "category": s.category}
            )
            rows.append(row)
        h = {
            "cookie": session["cookie"],
            "content-type": "application/json",
            "x-tenant-id": "e7af6ccf-d37c-59bb-bb13-4cea1c272879",
            "x-tenant-context-version": str(session["context_version"]),
            "x-submission-key": f"neg-{uuid.uuid4().hex}",
            "origin": BFF,
        }
        r = client.post(
            f"{BFF}/api/threads", headers=h, json={"metadata": {"graph_id": "basic-qa"}}
        )
        neg_wrong = {"status": r.status_code, "body": r.text[:80]}
        r = client.post(
            f"{BFF}/api/threads",
            headers={
                "content-type": "application/json",
                "x-submission-key": f"neg-{uuid.uuid4().hex}",
            },
            json={"metadata": {}},
        )
        nosess = {"status": r.status_code, "body": r.text[:80]}
    out = {
        "precheck_rows": rows,
        "negatives": {"wrong_tenant": neg_wrong, "no_session": nosess},
        "recorded_at": _now(),
    }
    (perf / "precheck.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    with (perf / "requests.precheck.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    ok_real = all(r.get("primary_result") == "stream_completed" for r in rows)
    ok_neg = neg_wrong["status"] in (403, 409) and nosess["status"] == 401
    print(
        json.dumps(
            {
                "rows": [
                    {
                        k: r.get(k)
                        for k in (
                            "case_id",
                            "primary_result",
                            "client_first_answer_ms",
                            "client_complete_ms",
                        )
                    }
                    for r in rows
                ],
                "negatives": out["negatives"],
                "pass": ok_real and ok_neg,
            },
            ensure_ascii=False,
        )
    )
    return 0 if (ok_real and ok_neg) else 1


def cmd_level(args: argparse.Namespace) -> int:
    require_local_ready(args)
    run_id = args.run_id
    perf, private, session, cond = _load(run_id)
    budget = Budget(
        private,
        cond["budgets"]["global_max_logical_questions"],
        cond["budgets"]["global_max_seconds"],
    )
    stops = StopConditions()
    sink = (
        perf
        / f"requests.{args.stage}C{args.level}{'-r' + str(args.round) if args.round is not None else ''}.jsonl"
    )
    extra = {"round": args.round}
    runner = LevelRunner(
        run_id, args.level, args.stage, extra, session, cond, budget, stops
    )
    search = cond["search"]
    result = runner.run(
        warmup_min_seconds=search["warmup_min_seconds"],
        warmup_min_completions=search["warmup_min_completions"],
        measure_min_seconds=args.seconds,
        measure_min_sends=args.min_sends,
        drain_extra_seconds=30,
        sink_path=sink,
    )
    result["budget_after"] = budget.state["sent_total"]
    out_name = f"{args.stage}C{args.level}{'-r' + str(args.round) if args.round is not None else ''}.json"
    (perf / out_name).write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "level",
                    "stage",
                    "passed",
                    "pass_reasons_failed",
                    "inflight_attainment",
                    "dispatch_delay_p95_ms",
                    "budget_after",
                    "stop_reason",
                    "stopped_by_condition",
                )
            },
            ensure_ascii=False,
        )
    )
    print(
        json.dumps(
            {"overall": result["overall"], "Q1": result["Q1"], "Q2": result["Q2"]},
            ensure_ascii=False,
        )
    )
    return 0


MOCK_DELAY = {"v": 0.0}


def cmd_calibrate(args: argparse.Namespace) -> int:
    """本机协议模拟：补发闭环、唯一ID、预算停止、排空、拒覆盖、聚合可复算。"""
    state = {"served": 0}
    state_lock = threading.Lock()

    class MockBFF(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            request = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/threads":
                with state_lock:
                    state["served"] += 1
                body = json.dumps({"thread_id": str(uuid.uuid4())}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif "/runs/stream" in self.path:
                rid = uuid.uuid4().hex
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                time.sleep(MOCK_DELAY["v"])
                frames = [
                    (
                        "metadata",
                        json.dumps(
                            {
                                "run_id": rid,
                                "ledger_request_id": "req-" + rid[:8],
                                "ledger_attempt_id": "att-" + rid[:8],
                                "ledger_submission_id": "sub-" + rid[:8],
                            }
                        ),
                    ),
                    (
                        "messages",
                        json.dumps(
                            [
                                {
                                    "id": "answer-" + rid,
                                    "type": "ai",
                                    "content": "mock answer body",
                                },
                                {"run_id": rid},
                            ]
                        ),
                    ),
                ]
                for name, data in frames:
                    self.wfile.write(f"event: {name}\ndata: {data}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
            elif "/api/agent-runs/confirm" in self.path:
                rid = request["run_id"]
                body = json.dumps(
                    {
                        "confirmed": True,
                        "request_id": "req-" + rid[:8],
                        "attempt_id": "att-" + rid[:8],
                        "submission_id": "sub-" + rid[:8],
                    }
                ).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), MockBFF)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    bff = f"http://127.0.0.1:{port}"

    cal_dir = ROOT / f".local/{args.run_id}-calibration"
    cal_private = ROOT / f".local/{args.run_id}-calibration-private"
    if cal_dir.exists() or cal_private.exists():
        raise SystemExit("校准输出已存在；拒绝覆盖")
    cal_private.mkdir(parents=True)
    (cal_private / "session.json").write_text(
        json.dumps({"cookie": "mock=1", "tenant_id": "t", "context_version": 1})
    )
    cond = {
        "slo": {
            "completion_success_rate_min": 0.99,
            "legacy_first_body_p95_ms_max": 45000,
            "complete_p95_ms_max": 60000,
        }
    }
    (cal_private / "conditions.json").write_text(json.dumps(cond, ensure_ascii=False))
    session = json.loads((cal_private / "session.json").read_text())

    # 场景 1：快速完成 → 补发保持满在途、计量达标、唯一 submission
    MOCK_DELAY["v"] = 0.05
    budget = Budget(cal_private / "scenario1", 5000)
    stops = StopConditions(monitor_infra=False)
    runner = LevelRunner(
        "calib", 5, "explore", {}, session, cond, budget, stops, bff=bff
    )
    r1 = runner.run(2, 5, 6, 30, 3, cal_dir / "requests-scenario1.jsonl")
    keys = [
        json.loads(x)["submission_key"]
        for x in (cal_dir / "requests-scenario1.jsonl").read_text().splitlines()
        if x.strip()
    ]
    c1 = {
        "replenish_full_inflight": (r1["inflight_attainment"] or 0) >= 0.9,
        "measure_sends_met": r1["measure"]["sends"] >= 30,
        "unique_submission_keys": len(keys) == len(set(keys)),
        "no_stop_triggered": not stops.event.is_set(),
        "aggregable": r1["overall"]["sent"] == r1["Q1"]["sent"] + r1["Q2"]["sent"],
    }

    # 场景 2：慢请求期间闭环不补批（在途不超过 C，慢请求不被替换）
    MOCK_DELAY["v"] = 1.5
    budget = Budget(cal_private / "scenario2", 5000)
    stops = StopConditions(monitor_infra=False)
    runner = LevelRunner(
        "calib", 3, "explore", {}, session, cond, budget, stops, bff=bff
    )
    r2 = runner.run(1, 3, 5, 6, 3, cal_dir / "requests-scenario2.jsonl")
    rows2 = [
        json.loads(x)
        for x in (cal_dir / "requests-scenario2.jsonl").read_text().splitlines()
        if x.strip()
    ]
    c2 = {
        "inflight_never_exceeded_C": r2["inflight_max"] <= 3,
        "slow_requests_completed_not_replaced": all(
            r.get("primary_result") == "stream_completed" for r in rows2
        ),
        "unique_threads": len({r.get("thread_id") for r in rows2}) == len(rows2),
    }

    # 场景 3：预算耗尽触发停止并排空
    MOCK_DELAY["v"] = 0.0
    budget = Budget(cal_private / "scenario3", 4)
    stops = StopConditions(monitor_infra=False)
    runner = LevelRunner(
        "calib", 3, "explore", {}, session, cond, budget, stops, bff=bff
    )
    runner.run(1, 2, 8, 10, 2, cal_dir / "requests-scenario3.jsonl")
    c3 = {"budget_stop": stops.event.is_set() and "预算" in (stops.reason or "")}

    server.shutdown()
    checks = {**c1, **c2, **c3}
    print(
        json.dumps(
            {"calibration": checks, "scenario1_sends": r1["measure"]["sends"]},
            ensure_ascii=False,
        )
    )
    return 0 if all(checks.values()) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E-CAP-Q12-v1 最大并发连续发压")
    parser.add_argument("--run-id", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("login")
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--conditions", type=Path)
    p.set_defaults(func=cmd_login)
    p = sub.add_parser("freeze")
    p.set_defaults(func=cmd_freeze)
    p = sub.add_parser("precheck")
    p.add_argument("--enable-real-load", action="store_true")
    p.set_defaults(func=cmd_precheck)
    p = sub.add_parser("level")
    p.add_argument("--level", type=int, required=True)
    p.add_argument("--stage", choices=["explore", "confirm", "soak"], required=True)
    p.add_argument("--seconds", type=int, required=True)
    p.add_argument("--min-sends", type=int, required=True)
    p.add_argument("--round", type=int, default=None)
    p.add_argument("--enable-real-load", action="store_true")
    p.set_defaults(func=cmd_level)
    p = sub.add_parser("calibrate")
    p.set_defaults(func=cmd_calibrate)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
