"""agent-output-stream-v1 contract validation and anti-forgery tests."""

from __future__ import annotations

import json
from pathlib import Path

from common.output_streaming.contract import (
    EVENT_ABORTED,
    EVENT_DELTA,
    EVENT_DONE,
    EVENT_START,
    OutputStreamValidator,
    output_digest,
    validate_stream,
)

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "docs/contracts/agent-output-stream-v1/samples"


def _load(name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (SAMPLES / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _base(**overrides):
    event = {
        "version": 1,
        "created_at": "2026-10-21T10:00:00+00:00",
        "processing_id": "proc-1",
        "thread_id": "thread-1",
        "run_id": "run-1",
        "graph_id": "basic-qa",
        "tenant_id": "tenant-1",
        "output_id": "out-1",
        "message_id": "msg-1",
        "output_protocol_version": 1,
        "delivery_mode": "token",
    }
    event.update(overrides)
    return event


def _start(seq=0, **overrides):
    return _base(type=EVENT_START, seq=seq, **overrides)


def _delta(seq, message, **overrides):
    return _base(type=EVENT_DELTA, seq=seq, message=message, **overrides)


def _done(seq, message, **overrides):
    return _base(
        type=EVENT_DONE,
        seq=seq,
        message=message,
        content_sha256=output_digest(message),
        **overrides,
    )


def _aborted(seq, partial_chars, **overrides):
    return _base(
        type=EVENT_ABORTED,
        seq=seq,
        reason_code="cancelled",
        partial_chars=partial_chars,
        **overrides,
    )


def test_valid_token_stream_and_samples_pass():
    events = [_start(), _delta(1, "第一段。"), _delta(2, "第二段。"), _done(3, "第一段。第二段。")]
    result = validate_stream(events, require_identity=True)
    assert result.errors == []
    assert result.terminal == EVENT_DONE
    assert result.text == "第一段。第二段。"
    for sample in ("basic-qa-token.jsonl", "tracing-sections.jsonl", "aborted.jsonl"):
        loaded = _load(sample)
        assert validate_stream(loaded, require_identity=True).errors == [], sample


def test_missing_trusted_identity_is_rejected():
    events = [_start(tenant_id=""), _delta(1, "正文。"), _done(2, "正文。")]
    result = validate_stream(events, require_identity=True)
    assert "missing_trusted_tenant" in result.errors


def test_duplicate_start_and_replayed_sequence_are_rejected():
    events = [_start(), _start(), _delta(1, "正文。")]
    errors = validate_stream(events).errors
    assert "duplicate_start" in errors
    assert "event_after_terminal" in errors or "seq_gap_or_replay" in errors


def test_conflicting_sequence_and_gap_are_rejected():
    validator = OutputStreamValidator()
    validator.accept(_start())
    assert validator.accept(_delta(1, "A")) == []
    assert "seq_gap_or_replay" in validator.accept(_delta(1, "B"))
    validator = OutputStreamValidator()
    validator.accept(_start())
    assert "seq_gap_or_replay" in validator.accept(_delta(3, "C"))


def test_done_must_equal_deltas_and_digest():
    events = [_start(), _delta(1, "第一段。"), _done(3, "另一篇。")]
    errors = validate_stream(events).errors
    assert "done_not_equal_to_deltas" in errors
    events = [_start(), _delta(1, "第一段。")]
    bad = _done(2, "第一段。")
    bad["content_sha256"] = "0" * 64
    errors = validate_stream([*events, bad]).errors
    assert "done_digest_mismatch" in errors


def test_two_terminal_events_and_events_after_terminal_are_rejected():
    events = [_start(), _delta(1, "A"), _done(2, "A"), _delta(3, "B")]
    errors = validate_stream(events).errors
    assert "event_after_terminal" in errors
    events = [_start(), _delta(1, "A"), _aborted(2, 1), _done(3, "A")]
    errors = validate_stream(events).errors
    assert "event_after_terminal" in errors


def test_aborted_partial_length_must_match_published_prefix():
    events = [_start(), _delta(1, "部分。"), _aborted(2, 99)]
    assert "partial_chars_mismatch" in validate_stream(events).errors


def test_section_delta_requires_plan_digest():
    events = [
        _start(delivery_mode="validated_section", graph_id="intelligent-tracing"),
        _delta(
            1,
            "第一节。",
            graph_id="intelligent-tracing",
            section_id="header",
        ),
    ]
    assert "section_without_plan_digest" in validate_stream(events).errors


def test_legacy_done_is_not_a_protocol_stream():
    legacy = _load("legacy-done.jsonl")[0]
    assert "output_protocol_version" not in legacy
    assert "type" in legacy and legacy["type"] == "final_output_done"
