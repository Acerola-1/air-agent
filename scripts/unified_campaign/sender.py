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

    def _mark_first(self) -> None:
        if self.first_answer_ms is None:
            self.first_answer_ms = int((self.clock() - self.started) * 1000)

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
            if payload.get("type") == "final_output_done":
                text = payload.get("message")
                if isinstance(text, str):
                    cleaned = _strip_reasoning(text)
                    if cleaned:
                        self.final_text = cleaned
                        self._mark_first()

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
            if not self.answer_by_id:
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
    try:
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
                        state.consume(event_name, data)
                    stream_status = "completed"
    except httpx.TimeoutException:
        stream_status = "timeout"
    except httpx.TransportError:
        stream_status = "transport_failed"

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

    answer = state.answer_text()
    complete_ms = int((clock() - started) * 1000)
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
            "client_complete_ms": complete_ms,
            "answer_associated": association
            in {"confirmed", "confirmed_without_metadata"},
            "association_status": association,
            "stream_status": stream_status,
            "business_status": "confirmed" if confirmed else "unconfirmed",
            "stream_error_code": state.error_code,
            "answer_sha256": None
            if answer is None
            else hashlib.sha256(answer.encode()).hexdigest(),
            "answer_nonempty": bool(answer),
            "counts_as_semantic_sample": sample.counts_as_semantic_sample,
        }
    )
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
