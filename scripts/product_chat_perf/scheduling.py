"""Measured closed/open arrival scheduling, budgets and truthful stage outcomes."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Budget:
    """Reserve before each physical send; unknown usage consumes the reservation."""

    max_seconds: float
    max_requests: int
    max_tokens: int
    started: float = field(default_factory=time.monotonic)
    requests: int = 0
    tokens: int = 0
    actual_tokens: int = 0
    missing_usage: int = 0
    stopped_reason: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reserve(self, token_ceiling: int) -> bool:
        """Atomically reserve a worst-case request allowance, never oversubscribe."""
        with self._lock:
            if self.stopped_reason:
                return False
            if time.monotonic() - self.started >= self.max_seconds:
                self.stopped_reason = "time_budget"
            elif self.requests >= self.max_requests:
                self.stopped_reason = "request_budget"
            elif self.tokens + token_ceiling > self.max_tokens:
                self.stopped_reason = "token_budget"
            if self.stopped_reason:
                return False
            self.requests += 1
            self.tokens += token_ceiling
            return True

    def settle(self, reserved: int, actual: int | None) -> None:
        """Retain unknown reservations; actual excess stops subsequent sends."""
        with self._lock:
            if type(actual) is not int or actual < 0:
                self.missing_usage += 1
                return
            self.actual_tokens += actual
            self.tokens += actual - reserved
            if actual > reserved or self.tokens > self.max_tokens:
                self.stopped_reason = "token_ceiling_exceeded"

    def snapshot(self) -> dict[str, Any]:
        """Return counts, including unknown usage, without treating it as zero."""
        with self._lock:
            return {
                "limits": {
                    "max_seconds": self.max_seconds,
                    "max_requests": self.max_requests,
                    "max_tokens": self.max_tokens,
                },
                "requests": self.requests,
                "reserved_or_actual_tokens": self.tokens,
                "known_actual_tokens": self.actual_tokens,
                "missing_usage_requests": self.missing_usage,
                "stopped_reason": self.stopped_reason,
            }


def error_window_tripped(
    rows: list[dict[str, Any]], window_seconds: float = 60
) -> bool:
    """Use completion instants in one coordinator clock, not request durations."""
    ended = sorted(
        (
            row
            for row in rows
            if isinstance(row.get("ended_at_offset_ms"), (int, float))
            and row.get("primary_result") not in {None, "unknown", "not_sent"}
        ),
        key=lambda row: row["ended_at_offset_ms"],
    )
    left = 0
    for right, end in enumerate(ended):
        while (
            end["ended_at_offset_ms"] - ended[left]["ended_at_offset_ms"]
            > window_seconds * 1000
        ):
            left += 1
        window = ended[left : right + 1]
        if (
            len(window) >= 20
            and sum(
                row.get("primary_result")
                not in {"stream_completed", "completed_success"}
                for row in window
            )
            / len(window)
            > 0.10
        ):
            return True
    return False


def run_stage(
    stage: dict[str, Any],
    send: Callable[[int], dict[str, Any]],
    *,
    budget: Budget,
    on_row: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    """Continuously schedule and drain a bounded stage.

    Open arrivals are planned independently of completions. At the connection
    ceiling an arrival is explicitly not sent rather than delayed invisibly.
    The callback must enforce its frozen physical request deadline.
    """
    origin = time.monotonic()
    duration = float(stage["duration_seconds"])
    concurrency = int(stage["concurrency"])
    minimum = int(stage["min_samples"])
    ceiling = int(stage["token_ceiling_per_request"])
    arrival = stage["arrival"]
    interval = 1 / float(stage.get("arrival_rate_per_second", 1))
    rows: list[dict[str, Any]] = []
    arrivals: list[dict[str, Any]] = []
    pending: dict[Future, tuple[int, float]] = {}
    sent = planned = peak = 0
    stop_reason: str | None = None
    measure_end: float | None = None
    max_samples = stage.get("max_samples")
    execution_lock = threading.Lock()
    live = [0, 0]

    def invoke(index: int, planned_offset: float) -> dict[str, Any]:
        started = time.monotonic()
        with execution_lock:
            live[0] += 1
            live[1] = max(live[1], live[0])
        try:
            row = send(index)
        except Exception as exc:  # noqa: BLE001 - retain failures instead of losing a future
            row = {
                "primary_result": "transport_failed",
                "error_type": type(exc).__name__,
                "sent": True,
            }
        ended = time.monotonic()
        with execution_lock:
            live[0] -= 1
        row.update(
            {
                "sample_id": row.get("sample_id") or f"{stage['stage_id']}:{index}",
                "stage_id": stage["stage_id"],
                "planned_at_offset_ms": planned_offset * 1000,
                "sent_at_offset_ms": (started - origin) * 1000,
                "ended_at_offset_ms": (ended - origin) * 1000,
                "scheduling_lag_ms": max(0, (started - origin - planned_offset) * 1000),
                "observed_ms": (ended - started) * 1000,
            }
        )
        budget.settle(ceiling, row.get("total_tokens"))
        return row

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        accepting = True
        while accepting or pending:
            elapsed = time.monotonic() - origin
            if accepting:
                external_stop = should_stop() if should_stop else None
                if external_stop or budget.stopped_reason or error_window_tripped(rows):
                    stop_reason = (
                        external_stop or budget.stopped_reason or "error_window"
                    )
                    accepting = False
                elif elapsed >= duration and sent >= minimum:
                    accepting = False
                elif max_samples is not None and sent >= max_samples:
                    stop_reason = "sample_pool_exhausted"
                    accepting = False
                if not accepting:
                    measure_end = elapsed
            if accepting:
                if arrival == "closed":
                    while len(pending) < concurrency:
                        if max_samples is not None and sent >= max_samples:
                            break
                        if not budget.reserve(ceiling):
                            stop_reason, accepting = budget.stopped_reason, False
                            measure_end = time.monotonic() - origin
                            break
                        planned += 1
                        pending[pool.submit(invoke, sent, elapsed)] = (sent, elapsed)
                        sent += 1
                        peak = max(peak, len(pending))
                else:
                    while planned * interval <= elapsed and (
                        planned * interval < duration or planned < minimum
                    ):
                        if max_samples is not None and sent >= max_samples:
                            stop_reason, accepting = "sample_pool_exhausted", False
                            measure_end = elapsed
                            break
                        scheduled = planned * interval
                        planned += 1
                        if len(pending) >= concurrency:
                            arrivals.append(
                                {
                                    "planned_at_offset_ms": scheduled * 1000,
                                    "sent": False,
                                    "primary_result": "not_sent",
                                    "reason": "client_concurrency_limit",
                                }
                            )
                            continue
                        if not budget.reserve(ceiling):
                            stop_reason, accepting = budget.stopped_reason, False
                            measure_end = elapsed
                            break
                        pending[pool.submit(invoke, sent, scheduled)] = (
                            sent,
                            scheduled,
                        )
                        sent += 1
                        peak = max(peak, len(pending))
                    if elapsed >= duration and planned >= minimum:
                        accepting = False
                        measure_end = elapsed
            if pending:
                completed, _ = wait(pending, timeout=0.01, return_when=FIRST_COMPLETED)
                for future in completed:
                    pending.pop(future)
                    row = future.result()
                    row["completion_window"] = (
                        "measurement"
                        if measure_end is None
                        or row["ended_at_offset_ms"] <= measure_end * 1000
                        else "drain"
                    )
                    rows.append(row)
                    if on_row:
                        on_row(row)
            elif accepting:
                time.sleep(min(0.01, interval))
    return {
        "stage_id": stage["stage_id"],
        "rows": rows,
        "arrivals": arrivals,
        "planned": planned,
        "sent": sent,
        "not_sent": len(arrivals),
        "peak_inflight": live[1],
        "peak_pending_callbacks": peak,
        "inflight_scope": "started_to_ended_sender_callback",
        "measure_seconds": measure_end
        if measure_end is not None
        else time.monotonic() - origin,
        "total_seconds_including_drain": time.monotonic() - origin,
        "stop_reason": stop_reason,
        "budget": budget.snapshot(),
    }
