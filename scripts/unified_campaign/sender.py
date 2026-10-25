"""Send one planned sample through the BFF admission and stream path.

The stream is parsed incrementally so the first-answer timestamp reflects the
first visible token of *this* run. Historical assistant messages in reused
threads, tool-call arguments and reasoning text are never treated as the
answer. Ledger association is confirmed against the BFF's trusted confirm
endpoint and cross-checked with the stream metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
import signal
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import httpx

from scripts.unified_campaign.dataset import PlannedSample

_AI_TYPES = frozenset({"ai", "assistant", "AIMessage", "AIMessageChunk"})
_NON_VISIBLE_BLOCKS = frozenset({"reasoning", "thinking", "tool_use", "tool_call"})
_THINK = re.compile(r"<think>[\s\S]*?</think>")
_THINK_OPEN = re.compile(r"<think>[\s\S]*$")


class _ClientDeadline(Exception):
    """Raised by SIGALRM when the per-question deadline is exhausted."""


def _raise_deadline(_signum: int, _frame: object) -> None:
    raise _ClientDeadline()


def _headers(
    cookie: str, tenant_id: str, context_version: int, submission_key: str
) -> dict[str, str]:
    return {
        "cookie": cookie,
        "content-type": "application/json",
        "x-tenant-id": tenant_id,
        "x-tenant-context-version": str(context_version),
        "x-submission-key": submission_key,
        "x-operation-kind": "initial",
    }


def _reused_payload(raw: str) -> dict[str, Any] | None:
    """Return the ledger ids when the BFF reports an already confirmed submission."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("status") != "already_confirmed":
        return None
    return payload


def _iter_sse(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Yield one ``(event, data)`` pair per SSE frame as soon as it is complete."""
    event_name = "message"
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data:
                yield event_name, "\n".join(data)
            event_name = "message"
            data = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value.lstrip()
        if field == "event":
            event_name = value
        elif field == "data":
            data.append(value)
    if data:
        yield event_name, "\n".join(data)


def _strip_reasoning(text: str) -> str:
    """Remove model reasoning wrappers from visible content."""
    return _THINK_OPEN.sub("", _THINK.sub("", text)).strip()


def _visible_text(message: object) -> str | None:
    """Return user-visible assistant text, ignoring tool calls and reasoning.

    Mirrors ``src/common/output_contract.py``: an AI message is visible only
    when it carries no tool calls; reasoning blocks and ``<think>`` wrappers are
    not part of the answer.
    """
    if not isinstance(message, dict):
        return None
    kind = str(message.get("type") or message.get("role") or "")
    if kind not in _AI_TYPES:
        return None
    if message.get("tool_calls") or message.get("invalid_tool_calls"):
        return None
    additional = message.get("additional_kwargs")
    if isinstance(additional, dict) and additional.get("ui_visible") is False:
        return None
    content = message.get("content")
    parts: list[str] = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                block_type = str(block.get("type") or "text")
                if block_type in _NON_VISIBLE_BLOCKS:
                    continue
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
    text = _strip_reasoning("".join(parts))
    return text or None


def _merge_text(existing: str, piece: str) -> str:
    """Merge a streamed delta with a possibly cumulative message payload."""
    if not existing:
        return piece
    if piece.startswith(existing):
        return piece
    if existing.startswith(piece):
        return existing
    return existing + piece


def _message_list(payload: object) -> list[Any]:
    if isinstance(payload, dict):
        messages = payload.get("messages")
        if isinstance(messages, list):
            return messages
    return []


class _StreamState:
    """Accumulate one run's events without buffering the whole body first."""

    def __init__(self, started: float, clock: Callable[[], float]) -> None:
        self.started = started
        self.clock = clock
        self.run_id: str | None = None
        self.ledger: dict[str, str] = {}
        self.first_answer_ms: int | None = None
        self.answer_by_id: dict[str, str] = {}
        self.answer_order: list[str] = []
        self.final_text: str | None = None
        self.error_code: str | None = None
        self.saw_metadata = False
        self.initial_message_ids: set[str] | None = None
        self.values_answer_id: str | None = None
        self.values_answer: str | None = None
        # agent-output-stream-v1 layered timing.
        self.first_safe_delta_ms: int | None = None
        self.last_safe_delta_ms: int | None = None
        self.first_delta_source: str | None = None
        self.output_id: str | None = None
        self.output_message_id: str | None = None
        self.delta_count = 0
        self.output_protocol_version: int | None = None
        self.aborted_reason: str | None = None
        self.done_message: str | None = None
        self.done_sha256: str | None = None
        self.last_seq: int | None = None
        self.seq_status: str = "not_applicable"
        self.delta_text: str = ""
        self.done_hash_match: bool | None = None
        self.output_started: bool = False
        self.stream_end_ms: int | None = None
        self.confirmation_end_ms: int | None = None
        self.public_unit_version: int | None = None
        self.first_verified_result_ms: int | None = None
        self.last_effective_ms: int | None = None
        self.max_answer_gap_ms: int | None = None
        self.max_inter_unit_gap_ms: int | None = None
        self.answer_tail_silence_ms: int | None = None
        self.public_units: list[dict[str, Any]] = []
        self.effective_chars = 0
        self.output_frames: dict[int, str] = {}

    def _mark_first(self) -> None:
        if self.first_answer_ms is None:
            self.first_answer_ms = int((self.clock() - self.started) * 1000)

    def finish_observation(self, ended_ms: int) -> None:
        """Include silence after the last useful unit through stream end."""
        if self.public_unit_version == 1 and self.last_effective_ms is not None:
            tail = max(0, ended_ms - self.last_effective_ms)
            self.answer_tail_silence_ms = tail
            self.max_answer_gap_ms = max(self.max_answer_gap_ms or 0, tail)

    def _absorb_ledger(self, payload: dict[str, Any]) -> None:
        for field in (
            "ledger_request_id",
            "ledger_attempt_id",
            "ledger_submission_id",
        ):
            value = payload.get(field)
            if value:
                self.ledger[field] = str(value)

    def consume(self, event_name: str, raw: str) -> None:
        """Apply one SSE frame. Malformed frames are ignored, not guessed at."""
        if raw == "[DONE]":
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if event_name == "metadata" and isinstance(payload, dict):
            self.saw_metadata = True
            self.run_id = str(payload.get("run_id") or self.run_id or "") or None
            self._absorb_ledger(payload)
            return
        if event_name == "error":
            if isinstance(payload, dict):
                self.error_code = str(
                    payload.get("code") or payload.get("error") or "stream_error"
                )
            return
        if event_name == "messages":
            self._consume_messages(payload)
            return
        if event_name == "values":
            self._consume_values(payload)
            return
        if event_name == "custom" and isinstance(payload, dict):
            output_type = str(payload.get("type") or "")
            if output_type.startswith("final_output_"):
                self._consume_output_event(payload)
                return

    def _consume_output_event(self, payload: dict[str, Any]) -> None:
        """Parse the versioned answer lifecycle without treating progress as text."""
        version = payload.get("output_protocol_version")
        if version is not None:
            self.output_protocol_version = int(version)
        output_type = str(payload.get("type") or "")
        if output_type == "final_output_start":
            if self.output_started:
                return
            self.output_started = True
            if payload.get("public_unit_version") == 1:
                self.public_unit_version = 1
                # Earlier untyped raw messages must not win the new metric.
                self.first_answer_ms = None
                self.first_delta_source = None
            self.output_id = (
                str(payload.get("output_id") or self.output_id or "") or None
            )
            self.output_message_id = (
                str(payload.get("message_id") or self.output_message_id or "") or None
            )
            return
        if output_type == "final_output_delta":
            text = payload.get("message")
            if not isinstance(text, str) or not text:
                return
            seq = payload.get("seq")
            if isinstance(seq, int):
                fingerprint = json.dumps(payload, sort_keys=True, ensure_ascii=False)
                if seq in self.output_frames:
                    if self.output_frames[seq] != fingerprint:
                        self.seq_status = "gap_or_replay"
                    return
                self.output_frames[seq] = fingerprint
            self.delta_count += 1
            self.delta_text += text
            seq = payload.get("seq")
            if isinstance(seq, int):
                if self.last_seq is not None and seq != self.last_seq + 1:
                    self.seq_status = "gap_or_replay"
                elif self.last_seq is None:
                    self.seq_status = "contiguous"
                self.last_seq = seq
            if self.first_safe_delta_ms is None:
                self.first_safe_delta_ms = int((self.clock() - self.started) * 1000)
            if self.public_unit_version == 1:
                from scripts.product_chat_perf.quality import is_effective_body

                kind = payload.get("unit_kind")
                unit_id = payload.get("unit_id")
                valid = (
                    payload.get("public_unit_version") == 1
                    and kind in {"explanation", "finding", "conclusion"}
                    and isinstance(unit_id, str)
                    and bool(unit_id)
                    and payload.get("fragment_kind") in {"context", "answer", "report"}
                    and isinstance(payload.get("fragment_id"), str)
                    and bool(payload.get("fragment_id"))
                    and is_effective_body(text)
                )
                if valid:
                    now_ms = int((self.clock() - self.started) * 1000)
                    self._mark_first()
                    self.first_delta_source = (
                        self.first_delta_source or "public_unit_v1"
                    )
                    if kind == "finding" and self.first_verified_result_ms is None:
                        self.first_verified_result_ms = now_ms
                    if self.last_effective_ms is not None:
                        gap = now_ms - self.last_effective_ms
                        self.max_answer_gap_ms = max(self.max_answer_gap_ms or 0, gap)
                        self.max_inter_unit_gap_ms = max(
                            self.max_inter_unit_gap_ms or 0, gap
                        )
                    self.last_effective_ms = now_ms
                    self.effective_chars += len(text)
                    self.public_units.append(
                        {
                            "unit_id": unit_id,
                            "unit_kind": kind,
                            "fragment_kind": payload.get("fragment_kind"),
                            "fragment_id": payload.get("fragment_id"),
                            "received_ms": now_ms,
                            "chars": len(text),
                            "sha256": hashlib.sha256(text.encode()).hexdigest(),
                        }
                    )
            else:
                self.first_delta_source = (
                    self.first_delta_source or "legacy_untyped_delta"
                )
                self._mark_first()
            self.last_safe_delta_ms = int((self.clock() - self.started) * 1000)
            self.output_message_id = (
                str(payload.get("message_id") or self.output_message_id or "") or None
            )
            return
        if output_type == "final_output_done":
            text = payload.get("message")
            if isinstance(text, str):
                cleaned = _strip_reasoning(text)
                if cleaned:
                    self.final_text = cleaned
                    self.done_message = cleaned
                    self.done_sha256 = payload.get("content_sha256")
                    if isinstance(self.done_sha256, str) and self.delta_text:
                        self.done_hash_match = (
                            hashlib.sha256(self.delta_text.encode()).hexdigest()
                            == self.done_sha256
                        )
                    if (
                        self.first_safe_delta_ms is None
                        and self.public_unit_version != 1
                    ):
                        # Legacy or single-shot: complete text arrived in one event.
                        self.first_delta_source = (
                            "legacy_done"
                            if payload.get("output_protocol_version") is None
                            else "single_shot"
                        )
                        self._mark_first()
            return
        if output_type == "final_output_aborted":
            self.aborted_reason = str(payload.get("reason_code") or "unknown")

    def _consume_messages(self, payload: object) -> None:
        """Handle a messages-tuple frame: ``[message, metadata]``."""
        if not isinstance(payload, list) or len(payload) < 2:
            return
        message, metadata = payload[0], payload[1]
        if not isinstance(message, dict):
            return
        if isinstance(metadata, dict):
            run_id = metadata.get("run_id")
            if run_id and self.run_id and str(run_id) != self.run_id:
                return
            self._absorb_ledger(metadata)
        text = _visible_text(message)
        if not text:
            return
        message_id = str(message.get("id") or "")
        if message_id:
            self.answer_by_id[message_id] = _merge_text(
                self.answer_by_id.get(message_id, ""), text
            )
            if message_id not in self.answer_order:
                self.answer_order.append(message_id)
        else:
            self.answer_by_id[""] = _merge_text(self.answer_by_id.get("", ""), text)
            if "" not in self.answer_order:
                self.answer_order.append("")
        if self.public_unit_version != 1:
            self.first_delta_source = self.first_delta_source or "legacy_message"
            self._mark_first()

    def _consume_values(self, payload: object) -> None:
        """Use full-state frames only as a fallback, never for history text.

        The first values frame carries the state at run start (including prior
        assistant turns on reused threads). Those ids are recorded and skipped;
        only AI messages created by this run may become the answer.
        """
        if not isinstance(payload, dict):
            return
        messages = _message_list(payload)
        if self.initial_message_ids is None:
            self.initial_message_ids = {
                str(message.get("id"))
                for message in messages
                if isinstance(message, dict) and message.get("id")
            }
            return
        for message in messages:
            if not isinstance(message, dict):
                continue
            message_id = str(message.get("id") or "")
            if message_id and message_id in self.initial_message_ids:
                continue
            text = _visible_text(message)
            if not text:
                continue
            self.values_answer_id = message_id
            self.values_answer = text
            if not self.answer_by_id and self.public_unit_version != 1:
                self.first_delta_source = (
                    self.first_delta_source or "legacy_state_fallback"
                )
                self._mark_first()

    def answer_text(self) -> str | None:
        """Return the final visible answer of this run."""
        if self.final_text:
            return self.final_text
        for message_id in reversed(self.answer_order):
            text = self.answer_by_id.get(message_id, "").strip()
            if text:
                return text
        return self.values_answer


def send_sample(
    client: httpx.Client,
    sample: PlannedSample,
    *,
    bff_base_url: str,
    cookie: str,
    tenant_id: str,
    context_version: int,
    thread_id: str | None = None,
    submission_key: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    include_answer_text: bool = False,
    abort_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Create or reuse a thread, stream one run, and confirm the ledger association."""
    base = bff_base_url.rstrip("/")
    started = clock()
    key = submission_key or uuid.uuid4().hex
    if thread_id is None:
        created = client.post(
            f"{base}/api/threads",
            headers=_headers(cookie, tenant_id, context_version, key),
            json={"metadata": {"graph_id": sample.graph_id}},
            timeout=min(10, sample.deadline_seconds),
        )
        if created.status_code != 200:
            return _failed(
                sample,
                key,
                "admission_failed",
                started,
                clock,
                thread_id=None,
                http_status=created.status_code,
            )
        thread_id = str(created.json()["thread_id"])
    body: dict[str, Any] = {
        "assistant_id": sample.graph_id,
        "input": {"messages": [{"type": "human", "content": sample.content}]},
        "stream_mode": ["values", "messages-tuple", "custom"],
    }
    if sample.menu_name:
        body["config"] = {"configurable": {"menu_name": sample.menu_name}}
        body["menu_name"] = sample.menu_name
    state = _StreamState(started, clock)
    reused: dict[str, Any] | None = None
    stream_status = "not_started"
    http_status: int | None = None
    old_handler = None
    timer_armed = False
    deadline_event = abort_event or threading.Event()
    deadline_timer = threading.Timer(
        max(0.2, float(sample.deadline_seconds) - (clock() - started)),
        deadline_event.set,
    )
    deadline_timer.daemon = True
    deadline_timer.start()
    try:
        if threading.current_thread() is threading.main_thread():
            old_handler = signal.signal(signal.SIGALRM, _raise_deadline)
            remaining = max(0.2, float(sample.deadline_seconds) - (clock() - started))
            signal.setitimer(signal.ITIMER_REAL, remaining)
            timer_armed = True
        with client.stream(
            "POST",
            f"{base}/api/threads/{thread_id}/runs/stream",
            headers=_headers(cookie, tenant_id, context_version, key),
            json=body,
            timeout=sample.deadline_seconds,
        ) as response:
            http_status = response.status_code
            if response.status_code >= 400:
                stream_status = "http_error"
            else:
                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type:
                    reused = _reused_payload(response.read().decode())
                    stream_status = "completed"
                else:
                    for event_name, data in _iter_sse(response.iter_lines()):
                        if deadline_event.is_set():
                            raise _ClientDeadline()
                        state.consume(event_name, data)
                    if deadline_event.is_set():
                        raise _ClientDeadline()
                    state.stream_end_ms = int((clock() - started) * 1000)
                    stream_status = "completed"
    except _ClientDeadline:
        stream_status = "timeout"
    except httpx.TimeoutException:
        stream_status = "timeout"
    except httpx.TransportError:
        stream_status = "timeout" if deadline_event.is_set() else "transport_failed"
    finally:
        deadline_timer.cancel()
        if timer_armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)

    if reused is not None:
        complete_ms = int((clock() - started) * 1000)
        row = _base(sample, key, "reused", started, complete_ms, thread_id)
        row.update(
            {
                "sent": True,
                "request_id": reused.get("ledger_request_id"),
                "attempt_id": reused.get("ledger_attempt_id"),
                "submission_id": reused.get("ledger_submission_id"),
                "platform_run_id": reused.get("run_id"),
                "answer_associated": bool(reused.get("ledger_request_id")),
                "answer_nonempty": None,
                "reused": True,
                "stream_status": stream_status,
                "business_status": "reused",
                "counts_as_semantic_sample": False,
            }
        )
        return row

    run_id = state.run_id
    confirmed = False
    association = "missing"
    confirm_payload: dict[str, Any] = {}
    if run_id:
        try:
            confirm = client.post(
                f"{base}/api/agent-runs/confirm",
                headers=_headers(cookie, tenant_id, context_version, key),
                json={"thread_id": thread_id, "run_id": run_id},
                timeout=5,
            )
        except httpx.HTTPError:
            confirm = None
        if confirm is not None and confirm.status_code == 200:
            confirm_payload = confirm.json()
            confirmed = bool(confirm_payload.get("confirmed"))
            for source, target in (
                ("request_id", "ledger_request_id"),
                ("attempt_id", "ledger_attempt_id"),
                ("submission_id", "ledger_submission_id"),
            ):
                value = confirm_payload.get(source)
                if not value:
                    continue
                known = state.ledger.get(target)
                if known and str(known) != str(value):
                    association = "mismatch"
                state.ledger[target] = str(value)

    if association == "mismatch":
        pass
    elif state.ledger.get("ledger_request_id") and confirmed:
        association = "confirmed"
    elif confirmed:
        association = "confirmed_without_metadata"
    else:
        association = "unconfirmed"

    state.confirmation_end_ms = int((clock() - started) * 1000)
    answer = state.answer_text()
    # Completion is the end of the answer stream, not the later ledger confirm.
    # Keeping confirm latency in a separate clock prevents a slow confirm call
    # from inflating the user-visible completion metric.
    complete_ms = (
        state.stream_end_ms
        if state.stream_end_ms is not None
        else state.confirmation_end_ms
    )
    state.finish_observation(complete_ms)
    if http_status is not None and http_status >= 400:
        primary = "admission_failed"
    elif stream_status == "timeout":
        primary = "client_timeout"
    elif stream_status == "transport_failed":
        primary = "transport_failed"
    elif not run_id:
        primary = "runtime_failed"
    elif state.error_code:
        primary = "runtime_failed"
    elif state.aborted_reason:
        primary = "runtime_aborted"
    elif state.seq_status == "gap_or_replay":
        primary = "protocol_error"
    elif state.done_hash_match is False:
        primary = "protocol_error"
    elif (
        state.output_protocol_version is not None
        and state.output_started
        and state.final_text is None
    ):
        # New protocol started but never produced final_output_done. Values or
        # an earlier assistant message must not be promoted to a completed run.
        primary = "stream_incomplete"
    elif association == "mismatch":
        primary = "association_mismatch"
    elif not confirmed:
        primary = "unconfirmed"
    elif answer:
        primary = "stream_completed"
    else:
        primary = "no_answer"

    row = _base(sample, key, primary, started, complete_ms, thread_id)
    row.update(
        {
            "sent": http_status is not None,
            "http_status": http_status,
            "request_id": state.ledger.get("ledger_request_id"),
            "attempt_id": state.ledger.get("ledger_attempt_id"),
            "submission_id": state.ledger.get("ledger_submission_id"),
            "platform_run_id": run_id,
            "client_first_answer_ms": state.first_answer_ms,
            "client_first_safe_delta_ms": state.first_safe_delta_ms,
            "client_last_safe_delta_ms": state.last_safe_delta_ms,
            "client_stream_end_ms": state.stream_end_ms,
            "client_confirmation_end_ms": state.confirmation_end_ms,
            "client_complete_ms": complete_ms,
            "public_unit_version": state.public_unit_version,
            "answer_rule_version": "meaningful-body-v1"
            if state.public_unit_version == 1
            else "legacy-untyped",
            "client_first_verified_result_ms": state.first_verified_result_ms,
            "client_last_effective_answer_ms": state.last_effective_ms,
            "max_answer_gap_ms": state.max_answer_gap_ms,
            "max_inter_unit_gap_ms": state.max_inter_unit_gap_ms,
            "answer_tail_silence_ms": state.answer_tail_silence_ms,
            "answer_gap_status": "measured"
            if state.last_effective_ms is not None
            else "insufficient_effective_units",
            "answer_gap_right_censored": state.aborted_reason is not None
            or state.done_message is None
            or stream_status != "completed",
            "effective_chars": state.effective_chars
            if state.public_unit_version == 1
            else None,
            "visible_tokens_per_second": None,
            "visible_tokens_missing_reason": "no_verified_visible_token_timestamps",
            "public_units": state.public_units,
            "first_result_missing_reason": "no_finding_unit"
            if state.first_verified_result_ms is None
            else None,
            "first_delta_source": state.first_delta_source,
            "delta_count": state.delta_count,
            "output_id": state.output_id,
            "output_message_id": state.output_message_id,
            "output_protocol_version": state.output_protocol_version,
            "aborted_reason": state.aborted_reason,
            "answer_associated": association
            in {"confirmed", "confirmed_without_metadata"},
            "association_status": association,
            "stream_status": stream_status,
            "business_status": "confirmed" if confirmed else "unconfirmed",
            "stream_error_code": state.error_code,
            "seq_status": state.seq_status,
            "done_hash_match": state.done_hash_match,
            "output_started": state.output_started,
            "answer_sha256": None
            if answer is None
            else hashlib.sha256(answer.encode()).hexdigest(),
            "answer_nonempty": bool(answer),
            "counts_as_semantic_sample": sample.counts_as_semantic_sample,
        }
    )
    if include_answer_text:
        row["_answer_text"] = answer
    return row


def _base(
    sample: PlannedSample,
    key: str,
    primary: str,
    started: float,
    complete_ms: int,
    thread_id: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": "basic-qa-perf.v1",
        "sample_id": sample.sample_id,
        "case_id": sample.case_id,
        "category": sample.category,
        "graph_id": sample.graph_id,
        "cohort": sample.cohort,
        "content_sha256": sample.content_sha256,
        "submission_key": key,
        "thread_id": thread_id,
        "primary_result": primary,
        "observed_ms": complete_ms,
        "monotonic_origin": started,
        "sent": primary != "admission_failed",
    }


def _failed(
    sample: PlannedSample,
    key: str,
    primary: str,
    started: float,
    clock: Callable[[], float],
    thread_id: str | None,
    *,
    http_status: int | None = None,
) -> dict[str, Any]:
    row = _base(
        sample, key, primary, started, int((clock() - started) * 1000), thread_id
    )
    row.update(
        {
            "http_status": http_status,
            "answer_associated": False,
            "association_status": "not_applicable",
            "stream_status": "http_error" if http_status else "not_started",
            "business_status": "not_applicable",
            "counts_as_semantic_sample": sample.counts_as_semantic_sample,
        }
    )
    return row
