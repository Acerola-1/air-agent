"""Versioned user-visible answer stream contract (agent-output-stream-v1).

The contract is deliberately separate from the business event ledger: answer
text may only travel on this SSE custom channel, never through ordinary runtime
events or logs.  Identity fields are bound from the trusted runtime context; a
browser declaration is never authoritative.

Event shapes (all carry ``version``, ``created_at``, ``processing_id``,
``thread_id``, ``run_id``, ``graph_id``, ``tenant_id``, ``output_id``,
``message_id``, ``seq`` and ``output_protocol_version``):

``final_output_start``
    seq=0, ``delivery_mode``.  Marks the beginning of one answer output.

``final_output_delta``
    ``message`` is the newly public text.  ``seq`` is strictly increasing by 1.

``final_output_done``
    ``message`` is the complete answer, ``content_sha256`` its digest.  It is
    the only successful terminal event and must equal the concatenated deltas.

``final_output_aborted``
    ``reason_code`` plus ``partial_chars``.  No delta or done may follow.

DeepAgents section streams additionally carry ``section_id`` and
``plan_digest`` on deltas and ``report_id``/``revision`` on done.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

OUTPUT_PROTOCOL_VERSION = 1

EVENT_START = "final_output_start"
EVENT_DELTA = "final_output_delta"
EVENT_DONE = "final_output_done"
EVENT_ABORTED = "final_output_aborted"

DELIVERY_MODE_TOKEN = "token"
DELIVERY_MODE_VALIDATED_SECTION = "validated_section"
DELIVERY_MODE_SINGLE_SHOT = "single_shot"
DELIVERY_MODE_POLICY_BOUND = "policy_bound"
DELIVERY_MODES = frozenset(
    {
        DELIVERY_MODE_TOKEN,
        DELIVERY_MODE_VALIDATED_SECTION,
        DELIVERY_MODE_SINGLE_SHOT,
        DELIVERY_MODE_POLICY_BOUND,
    }
)

ABORT_REASONS = frozenset(
    {
        "cancelled",
        "permission_revoked",
        "deleted",
        "policy_blocked",
        "generation_failed",
        "superseded",
        "stream_error",
        "not_confirmed",
        "unknown",
    }
)

# Identity-bearing fields that must be the same for every event of one output.
SCOPE_FIELDS = (
    "tenant_id",
    "thread_id",
    "run_id",
    "graph_id",
    "output_id",
    "message_id",
)

REQUIRED_BASE_FIELDS = (
    "version",
    "created_at",
    "processing_id",
    "thread_id",
    "run_id",
    "graph_id",
    "tenant_id",
    "output_id",
    "message_id",
    "seq",
    "output_protocol_version",
    "type",
)

_EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    EVENT_START: ("delivery_mode",),
    EVENT_DELTA: ("message",),
    EVENT_DONE: ("message", "content_sha256"),
    EVENT_ABORTED: ("reason_code", "partial_chars"),
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def output_digest(text: str) -> str:
    """Return the canonical SHA-256 hex digest of the published UTF-8 text."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def build_output_event(
    base: dict[str, Any],
    event_type: str,
    *,
    delivery_mode: str,
    output_id: str,
    message_id: str,
    seq: int,
    **fields: Any,
) -> dict[str, Any]:
    """Attach protocol identity to a runtime-event payload."""
    return {
        **base,
        "type": event_type,
        "output_protocol_version": OUTPUT_PROTOCOL_VERSION,
        "delivery_mode": delivery_mode,
        "output_id": output_id,
        "message_id": message_id,
        "seq": seq,
        **fields,
    }


@dataclass
class StreamValidationResult:
    """Accumulated validation state for one output stream."""

    errors: list[str] = field(default_factory=list)
    text: str = ""
    starts: int = 0
    deltas: int = 0
    terminal: str | None = None


class OutputStreamValidator:
    """Server-side contract validator used by tests and acceptance probes.

    It enforces: one start, monotonic contiguous sequence numbers, no events
    after a terminal, exact delta/done equality, digest equality and no two
    conflicting terminal events.  It is strict on purpose: duplicate frames in
    transport are a client-side concern, not a valid server sequence.
    """

    def __init__(self, *, require_identity: bool = False) -> None:
        """Create a validator; ``require_identity`` turns empty tenant into an error."""
        self._require_identity = require_identity
        self._state = StreamValidationResult()
        self._last_seq = -1
        self._scope: dict[str, str] | None = None
        self._delta_messages: list[str] = []

    @property
    def state(self) -> StreamValidationResult:
        """Read-only accumulated state."""
        return self._state

    def accept(self, event: dict[str, Any]) -> list[str]:
        """Validate one event and return any new errors."""
        errors: list[str] = []
        if not isinstance(event, dict):
            return ["event_not_object"]
        for key in REQUIRED_BASE_FIELDS:
            if key not in event:
                errors.append(f"missing_field:{key}")
        if errors:
            self._state.errors.extend(errors)
            return errors

        if event.get("output_protocol_version") != OUTPUT_PROTOCOL_VERSION:
            errors.append("unsupported_protocol_version")
        event_type = str(event.get("type") or "")
        if event_type not in _EVENT_FIELDS:
            errors.append(f"unknown_event_type:{event_type}")
        for key in _EVENT_FIELDS.get(event_type, ()):
            if key not in event:
                errors.append(f"missing_field:{key}")

        scope = {key: str(event.get(key) or "") for key in SCOPE_FIELDS}
        if self._scope is None:
            self._scope = scope
        elif scope != self._scope:
            errors.append("scope_mismatch")
        if self._require_identity and not scope["tenant_id"]:
            errors.append("missing_trusted_tenant")
        if not scope["thread_id"]:
            errors.append("missing_thread_id")
        if not scope["output_id"]:
            errors.append("missing_output_id")
        if not scope["message_id"]:
            errors.append("missing_message_id")

        seq = event.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            errors.append("seq_not_integer")
        elif seq != self._last_seq + 1:
            errors.append("seq_gap_or_replay")
        if self._state.terminal is not None:
            errors.append("event_after_terminal")

        if event_type == EVENT_START:
            self._state.starts += 1
            if self._state.starts > 1:
                errors.append("duplicate_start")
            if seq != 0:
                errors.append("start_seq_not_zero")
            if event.get("delivery_mode") not in DELIVERY_MODES:
                errors.append("invalid_delivery_mode")
        elif event_type == EVENT_DELTA:
            if self._state.starts != 1:
                errors.append("delta_before_start")
            message = event.get("message")
            if not isinstance(message, str) or not message:
                errors.append("delta_empty_message")
            else:
                self._state.deltas += 1
                self._delta_messages.append(message)
                self._state.text += message
            if event.get("section_id") is not None and not event.get("plan_digest"):
                errors.append("section_without_plan_digest")
        elif event_type == EVENT_DONE:
            if self._state.starts != 1:
                errors.append("done_before_start")
            message = event.get("message")
            if not isinstance(message, str):
                errors.append("done_message_not_string")
            else:
                joined = "".join(self._delta_messages)
                if message != joined:
                    errors.append("done_not_equal_to_deltas")
                if event.get("content_sha256") != output_digest(message):
                    errors.append("done_digest_mismatch")
            if event_type == EVENT_DONE:
                self._state.terminal = EVENT_DONE
        elif event_type == EVENT_ABORTED:
            if self._state.starts != 1:
                errors.append("aborted_before_start")
            if event.get("reason_code") not in ABORT_REASONS:
                errors.append("invalid_abort_reason")
            partial = event.get("partial_chars")
            if not isinstance(partial, int) or isinstance(partial, bool):
                errors.append("partial_chars_not_integer")
            elif partial != len(self._state.text):
                errors.append("partial_chars_mismatch")
            self._state.terminal = EVENT_ABORTED

        if not errors:
            self._last_seq = seq
        self._state.errors.extend(errors)
        return errors


def validate_stream(events: list[dict[str, Any]], *, require_identity: bool = False):
    """Validate a complete event list and return the accumulated result."""
    validator = OutputStreamValidator(require_identity=require_identity)
    for event in events:
        validator.accept(event)
    return validator.state
