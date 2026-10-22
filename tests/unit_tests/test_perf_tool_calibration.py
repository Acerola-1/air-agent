"""Calibration tests for the performance sender's SSE and clock semantics."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from scripts.unified_campaign.dataset import load_plan
from scripts.unified_campaign.sender import send_sample


def _frame(event: str, payload: object) -> bytes:
    return (
        f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
    )


class _Bff:
    """Serve a scripted SSE body; all delays are measured from stream start."""

    def __init__(
        self,
        frames: list[tuple[float, bytes]],
        *,
        confirm_delay: float = 0.0,
        confirm: dict[str, object] | None = None,
    ) -> None:
        self.frames = frames
        self.confirm_delay = confirm_delay
        self.confirm = confirm or {
            "confirmed": True,
            "request_id": "req-1",
            "attempt_id": "att-1",
            "submission_id": "sub-1",
        }
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or "0"))
                if self.path == "/api/threads":
                    self._json({"thread_id": "thread-1"})
                    return
                if self.path.endswith("/runs/stream"):
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.end_headers()
                    for delay, chunk in owner.frames:
                        if delay:
                            time.sleep(delay)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    return
                if owner.confirm_delay:
                    time.sleep(owner.confirm_delay)
                self._json(owner.confirm)

            def _json(self, body: object) -> None:
                payload = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()


def _base(sample, **kwargs):
    return replace(sample, **kwargs)


def _send(base: str, sample, **kwargs) -> dict:
    with httpx.Client(timeout=10) as client:
        return send_sample(
            client,
            sample,
            bff_base_url=base,
            cookie="session=test",
            tenant_id="tenant-a",
            context_version=1,
            **kwargs,
        )


def _sample():
    return next(item for item in load_plan()[0] if item.case_id == "BQA-001")


def _protocol(
    seqs: tuple[int, ...] = (1, 2),
    text: str = "第一段第二段",
    aborted=False,
    omit_done=False,
    bad_hash=False,
):
    frames: list[tuple[float, bytes]] = [
        (0.0, _frame("metadata", {"run_id": "run-1"})),
        (
            0.0,
            _frame(
                "custom",
                {
                    "type": "final_output_start",
                    "output_id": "out-1",
                    "output_protocol_version": 1,
                },
            ),
        ),
    ]
    midpoint = max(1, len(text) // 2)
    pieces = [text[:midpoint], text[midpoint:]]
    for index, seq in enumerate(seqs):
        frames.append(
            (
                0.0,
                _frame(
                    "custom",
                    {
                        "type": "final_output_delta",
                        "message": pieces[index % len(pieces)],
                        "seq": seq,
                        "output_protocol_version": 1,
                        "message_id": "msg-1",
                    },
                ),
            )
        )
    if aborted:
        frames.append(
            (
                0.0,
                _frame(
                    "custom",
                    {
                        "type": "final_output_aborted",
                        "reason_code": "user_stop",
                        "output_protocol_version": 1,
                    },
                ),
            )
        )
    if not omit_done:
        digest = hashlib.sha256(
            "bad".encode() if bad_hash else text.encode()
        ).hexdigest()
        frames.append(
            (
                0.0,
                _frame(
                    "custom",
                    {
                        "type": "final_output_done",
                        "message": text,
                        "content_sha256": digest,
                        "output_protocol_version": 1,
                    },
                ),
            )
        )
    return frames


def test_first_delta_arrives_before_source_completes() -> None:
    sample = _sample()
    frames = [
        (0.0, _frame("metadata", {"run_id": "run-1"})),
        (
            0.0,
            _frame(
                "custom",
                {
                    "type": "final_output_start",
                    "output_protocol_version": 1,
                    "output_id": "out-1",
                },
            ),
        ),
        (
            0.0,
            _frame(
                "custom",
                {
                    "type": "final_output_delta",
                    "message": "第一段",
                    "seq": 1,
                    "output_protocol_version": 1,
                },
            ),
        ),
        (
            0.6,
            _frame(
                "custom",
                {
                    "type": "final_output_delta",
                    "message": "第二段",
                    "seq": 2,
                    "output_protocol_version": 1,
                },
            ),
        ),
        (
            0.0,
            _frame(
                "custom",
                {
                    "type": "final_output_done",
                    "message": "第一段第二段",
                    "content_sha256": hashlib.sha256(
                        "第一段第二段".encode()
                    ).hexdigest(),
                    "output_protocol_version": 1,
                },
            ),
        ),
    ]
    with _Bff(frames) as base:
        row = _send(base, sample)
    assert row["primary_result"] == "stream_completed"
    assert row["client_first_safe_delta_ms"] is not None
    assert row["client_first_safe_delta_ms"] < 400
    assert row["client_stream_end_ms"] >= 550
    assert row["client_last_safe_delta_ms"] >= 550
    assert row["delta_count"] == 2


def test_confirm_latency_is_outside_completion_clock() -> None:
    sample = _sample()
    with _Bff(_protocol(), confirm_delay=0.8) as base:
        row = _send(base, sample)
    assert row["primary_result"] == "stream_completed"
    assert row["client_complete_ms"] < 500
    assert row["client_confirmation_end_ms"] >= 750
    assert row["client_confirmation_end_ms"] > row["client_complete_ms"]


def test_seq_gap_is_a_protocol_error_not_success() -> None:
    with _Bff(_protocol(seqs=(1, 3))) as base:
        row = _send(base, _sample())
    assert row["seq_status"] == "gap_or_replay"
    assert row["primary_result"] == "protocol_error"


def test_done_hash_mismatch_is_a_protocol_error() -> None:
    with _Bff(_protocol(bad_hash=True)) as base:
        row = _send(base, _sample())
    assert row["done_hash_match"] is False
    assert row["primary_result"] == "protocol_error"


def test_aborted_stream_never_counts_as_completed_success() -> None:
    with _Bff(_protocol(aborted=True)) as base:
        row = _send(base, _sample())
    assert row["aborted_reason"] == "user_stop"
    assert row["primary_result"] == "runtime_aborted"


def test_missing_done_after_new_protocol_start_is_incomplete() -> None:
    with _Bff(_protocol(omit_done=True)) as base:
        row = _send(base, _sample())
    assert row["output_started"] is True
    assert row["primary_result"] == "stream_incomplete"


def test_deadline_is_enforced_in_worker_threads() -> None:
    """The sender deadline must work under the real load-runner thread model."""
    import threading

    sample = _sample()
    frames = [
        (0.0, _frame("metadata", {"run_id": "run-1"})),
        (
            2.0,
            _frame(
                "messages",
                [
                    {
                        "type": "ai",
                        "content": "迟到回答",
                        "id": "a1",
                        "additional_kwargs": {"ui_visible": True},
                    },
                    {"run_id": "run-1"},
                ],
            ),
        ),
    ]
    result: dict = {}
    with _Bff(frames) as base:
        worker = threading.Thread(
            target=lambda: result.update(
                _send(base, _sample_with_deadline(sample, 0.3))
            )
        )
        worker.start()
        worker.join(timeout=5)
    assert result["stream_status"] == "timeout"
    assert result["primary_result"] == "client_timeout"


def _sample_with_deadline(sample, seconds: int):
    return replace(sample, deadline_seconds=seconds)
