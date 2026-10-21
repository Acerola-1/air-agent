"""Single user-visible answer publisher shared by all three public graphs.

Only this module may place answer text on the custom SSE channel.  Internal
model tokens, specialist drafts and tool payloads stay invisible: the raw
``messages`` stream is not promoted to an answer channel.

The publisher enforces one lifecycle per output:

    start -> delta* -> done
    start -> delta* -> aborted

``done`` is idempotent for the same output identity.  A second terminal event is
rejected instead of being emitted.  Every delta and the final confirmation can
run an authorization probe; after a revoked/deleted run no further bytes are
sent.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from common.output_streaming.contract import (
    DELIVERY_MODE_TOKEN,
    EVENT_ABORTED,
    EVENT_DELTA,
    EVENT_DONE,
    EVENT_START,
    build_output_event,
    output_digest,
)
from common.runtime_events import emit_event, event_payload

Probe = Callable[[], Awaitable[bool] | bool]

_STATE_NEW = "new"
_STATE_OPEN = "open"
_STATE_DONE = "done"
_STATE_ABORTED = "aborted"


class OutputStateError(RuntimeError):
    """The caller attempted an invalid lifecycle transition."""


class OutputAborted(RuntimeError):
    """Publication stopped because authorization/deletion/policy failed."""

    def __init__(self, reason_code: str) -> None:
        """Carry a public reason code only."""
        super().__init__(reason_code)
        self.reason_code = reason_code


def runtime_authorization_probe(config: dict[str, Any]) -> Probe:
    """Build the per-batch guard: identity revalidation plus deletion tombstone.

    Missing ledger context is allowed only for controlled/unit runs; when the
    context exists but the ledger is unavailable the probe fails closed.
    """

    async def _probe() -> bool:
        from common.identity.runtime import guard_runtime_call

        await guard_runtime_call(config)
        try:
            from common.event_ledger.lifecycle import (
                LedgerContextMissing,
                require_ledger_context,
            )
            from common.event_ledger.store import thread_deletion_exists
        except Exception:  # noqa: BLE001 - import boundary
            return True
        try:
            scope, _request_id, _attempt_id = require_ledger_context(config)
        except LedgerContextMissing:
            return True
        except Exception:  # noqa: BLE001 - malformed context must fail closed
            return False
        try:
            deleted = await thread_deletion_exists(
                scope,
                thread_id=str(
                    (config.get("configurable") or {}).get("thread_id") or ""
                ),
            )
        except Exception:  # noqa: BLE001 - ledger outage must suppress output
            return False
        return not deleted

    return _probe


class OutputPublisher:
    """Publish one answer output through the versioned custom channel."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        graph_id: str,
        delivery_mode: str = DELIVERY_MODE_TOKEN,
        writer: Callable[[dict[str, Any]], Any] | None = None,
        authorization_probe: Probe | None = None,
        output_id: str | None = None,
        message_id: str | None = None,
    ) -> None:
        """Bind trusted runtime identity and optional guards."""
        self._config = dict(config or {})
        self._graph_id = graph_id
        self._delivery_mode = delivery_mode
        self._writer = writer
        self._probe = authorization_probe
        self.output_id = output_id or f"out_{uuid.uuid4().hex}"
        self.message_id = message_id or f"msg_{uuid.uuid4().hex}"
        self._seq = -1
        self._state = _STATE_NEW
        self._chunks: list[str] = []
        self._sections: list[str] = []

    # ------------------------------------------------------------------ read-only

    @property
    def state(self) -> str:
        """Current lifecycle state."""
        return self._state

    @property
    def emitted_text(self) -> str:
        """Text already public (concatenated deltas)."""
        return "".join(self._chunks)

    @property
    def total_chars(self) -> int:
        """Number of public UTF-8 characters."""
        return len(self.emitted_text)

    @property
    def sequence(self) -> int:
        """Last emitted sequence, for the trusted publication checkpoint."""
        return self._seq

    def restore(self, *, text: str, seq: int, sections: tuple[str, ...]) -> None:
        """Restore an open stream from a server-owned checkpoint, without replay."""
        if self._state != _STATE_NEW or seq < 0:
            raise OutputStateError("invalid publication checkpoint")
        self._chunks = [text] if text else []
        self._sections = list(sections)
        self._seq = seq
        self._state = _STATE_OPEN

    # ------------------------------------------------------------------ lifecycle

    def start(self, **extra: Any) -> dict[str, Any]:
        """Emit ``final_output_start`` (seq=0)."""
        if self._state != _STATE_NEW:
            raise OutputStateError("output already started")
        self._state = _STATE_OPEN
        self._seq = 0
        return self._emit(EVENT_START, self._seq, **extra)

    async def publish(self, text: str, **extra: Any) -> bool:
        """Emit one ``final_output_delta``; returns False for empty text."""
        if self._state == _STATE_ABORTED:
            raise OutputAborted("aborted")
        if self._state != _STATE_OPEN:
            raise OutputStateError("output is not open")
        if not text:
            return False
        await self._authorize()
        self._seq += 1
        self._chunks.append(text)
        section = extra.get("section_id")
        if section and section not in self._sections:
            self._sections.append(str(section))
        self._emit(EVENT_DELTA, self._seq, message=text, **extra)
        return True

    async def finish(self, full_text: str | None = None, **extra: Any) -> str:
        """Confirm the complete answer; idempotent for the same output."""
        if self._state == _STATE_DONE:
            if full_text is not None and full_text != self.emitted_text:
                raise OutputStateError("conflicting final text")
            return self.emitted_text
        if self._state == _STATE_ABORTED:
            raise OutputAborted("aborted")
        if self._state != _STATE_OPEN:
            raise OutputStateError("output is not open")
        complete = self.emitted_text if full_text is None else full_text
        if not complete.startswith(self.emitted_text):
            raise OutputStateError("final text does not extend the published prefix")
        await self._authorize()
        tail = complete[len(self.emitted_text) :]
        if tail:
            self._seq += 1
            self._chunks.append(tail)
            self._emit(EVENT_DELTA, self._seq, message=tail, **extra)
        self._seq += 1
        self._state = _STATE_DONE
        self._emit(
            EVENT_DONE,
            self._seq,
            message=complete,
            content_sha256=output_digest(complete),
            **extra,
        )
        return complete

    async def abort(
        self,
        reason_code: str,
        *,
        detail: str = "",
        **extra: Any,
    ) -> None:
        """Emit ``final_output_aborted``; never sends a success event."""
        if self._state in (_STATE_DONE, _STATE_ABORTED):
            return
        if self._state == _STATE_NEW:
            self.start()
        self._seq += 1
        self._state = _STATE_ABORTED
        self._emit(
            EVENT_ABORTED,
            self._seq,
            reason_code=reason_code,
            partial_chars=len(self.emitted_text),
            **extra,
        )
        if detail:
            logger.bind(output_stream=True).info(
                "output aborted graph={} output={} reason={} detail={}",
                self._graph_id,
                self.output_id,
                reason_code,
                detail[:200],
            )

    # ------------------------------------------------------------------ internals

    async def ensure_authorized(self) -> None:
        """Recheck authorization before final persistence."""
        await self._authorize()

    async def _authorize(self) -> None:
        if self._probe is None:
            return
        try:
            result = self._probe()
            allowed = await result if inspect.isawaitable(result) else result
        except OutputAborted:
            raise
        except Exception as exc:  # noqa: BLE001 - probe failure must fail closed
            await self.abort("permission_revoked", detail=type(exc).__name__)
            raise OutputAborted("permission_revoked") from exc
        if not allowed:
            await self.abort("permission_revoked")
            raise OutputAborted("permission_revoked")

    def _emit(self, event_type: str, seq: int, **fields: Any) -> dict[str, Any]:
        base = event_payload(self._config, event_type)
        payload = build_output_event(
            base,
            event_type,
            delivery_mode=self._delivery_mode,
            output_id=self.output_id,
            message_id=self.message_id,
            seq=seq,
            **{
                "graph_id": self._graph_id,
                "tenant_id": self._tenant_id(),
                **fields,
            },
        )
        if self._writer is not None:
            self._writer(payload)
        else:
            emit_event(
                self._config,
                event_type,
                **{
                    key: value
                    for key, value in payload.items()
                    if key
                    not in {
                        "version",
                        "created_at",
                        "processing_id",
                        "thread_id",
                        "run_id",
                        "type",
                    }
                },
            )
        return payload

    def _tenant_id(self) -> str:
        configurable = self._config.get("configurable") or {}
        user = configurable.get("langgraph_auth_user")
        if user is None:
            return ""
        get = (
            user.get
            if isinstance(user, dict)
            else (lambda key, default=None: getattr(user, key, default))
        )
        return str(get("tenant_id") or "")
