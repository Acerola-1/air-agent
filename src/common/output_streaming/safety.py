"""Bounded safety buffer for user-visible answer increments.

The buffer converts raw model chunks into public units that never leak
reasoning, tool JSON or half a citation.  It deliberately keeps small amounts
of text back: a public unit must be a complete sentence/line/citation so the
knowledge policy can check it before anyone sees it.  Bounded growth is a
feature: a source that never produces a safe boundary fails closed instead of
accumulating the whole answer forever.
"""

from __future__ import annotations

from dataclasses import dataclass

_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_TOOL_PROTOCOL_OPEN = "<｜｜DSML｜｜"
_CITE_OPEN = "[[cite:"
_CITE_CLOSE = "]]"

_BOUNDARIES = ("\n\n", "\n", "。", "！", "？", "；", ";", ". ", "! ", "? ", "]]")


class StreamSafetyError(ValueError):
    """A safe public unit cannot be produced from the buffered source."""

    def __init__(self, reason_code: str) -> None:
        """Store a public reason code (never raw model text)."""
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SafetyLimits:
    """Boundaries for the incremental buffer."""

    soft_limit: int = 320
    hard_limit: int = 1200
    max_buffer: int = 4096


class SafeTextStream:
    """Incremental reasoning/citation-safe segmenter.

    ``feed`` returns zero or more units that are safe to publish right now.
    ``flush`` returns the tail after validating that no internal construct is
    left open.  ``clean_text`` accumulates everything that would be public and
    excludes ``<think>`` blocks.
    """

    def __init__(self, limits: SafetyLimits | None = None) -> None:
        """Create an empty buffer."""
        self._limits = limits or SafetyLimits()
        self._buf = ""
        self._in_think = False
        self._at_start = True
        self._emitted: list[str] = []

    @property
    def pending(self) -> str:
        """Text still held back by the safety rules."""
        return self._buf

    @property
    def emitted(self) -> str:
        """Everything already returned as a public unit."""
        return "".join(self._emitted)

    @property
    def clean_text(self) -> str:
        """Public text seen so far plus the still-pending tail (sans think)."""
        return "".join(self._emitted) + self._buf

    def feed(self, chunk: str) -> list[str]:
        """Append a raw model chunk and return now-public units."""
        if chunk:
            self._buf += chunk
        units = self._drain(final=False)
        self._emitted.extend(units)
        return units

    def flush(self) -> list[str]:
        """Return the final units; fail if a marker/JSON/citation is incomplete."""
        units = self._drain(final=True)
        self._emitted.extend(units)
        return units

    # ------------------------------------------------------------------ internals

    def _drain(self, *, final: bool) -> list[str]:
        units: list[str] = []
        while self._buf:
            if self._in_think:
                close = self._buf.find(_THINK_CLOSE)
                if close < 0:
                    if final:
                        raise StreamSafetyError("unterminated_reasoning")
                    self._guard_size(internal=True)
                    break
                self._buf = self._buf[close + len(_THINK_CLOSE) :]
                self._in_think = False
                continue

            open_at = self._buf.find(_THINK_OPEN)
            if open_at >= 0:
                prefix = self._buf[:open_at]
                self._buf = self._buf[open_at + len(_THINK_OPEN) :]
                self._in_think = True
                if prefix:
                    self._assert_no_tool_protocol(prefix)
                    self._at_start = False
                    units.append(prefix)
                continue

            self._assert_no_tool_protocol(self._buf)
            if final:
                self._assert_no_open_marker(self._buf)
                units.append(self._buf)
                self._buf = ""
                break

            hold = max(
                self._partial_suffix(self._buf, marker)
                for marker in (_THINK_OPEN, _TOOL_PROTOCOL_OPEN)
            )
            work = self._buf[: len(self._buf) - hold] if hold else self._buf

            if self._at_start:
                stripped = work.lstrip()
                if stripped.startswith(("{", "[")) and not stripped.startswith(
                    _CITE_OPEN
                ):
                    complete = self._balanced_json(stripped)
                    if complete is None:
                        self._guard_size(internal=True)
                        break
                    rest = stripped[complete:]
                    self._buf = rest + (self._buf[len(work) :] if hold else "")
                    self._at_start = False
                    units.append(stripped[:complete])
                    continue
                if stripped:
                    self._at_start = False

            taken = self._take_complete_unit(work)
            if taken is None:
                if hold:
                    break
                if len(self._buf) >= self._limits.max_buffer:
                    raise StreamSafetyError("buffer_limit_exceeded")
                break
            unit, rest = taken
            self._buf = rest + (self._buf[len(work) :] if hold else "")
            units.append(unit)
        return units

    def _take_complete_unit(self, work: str) -> tuple[str, str] | None:
        """Return ``(unit, rest)`` for the last safe boundary within limits."""
        limit = min(len(work), self._limits.soft_limit)
        best = self._last_boundary(work, limit)
        if best <= 0 and len(work) > self._limits.hard_limit:
            best = self._last_boundary(work, self._limits.hard_limit)
            if best <= 0:
                raise StreamSafetyError("unit_too_large")
        if best <= 0:
            return None
        return work[:best], work[best:]

    def _last_boundary(self, text: str, limit: int) -> int:
        """Last boundary end position within ``limit`` that is not inside a cite."""
        best = 0
        for marker in _BOUNDARIES:
            start = 0
            while True:
                index = text.find(marker, start, limit)
                if index < 0:
                    break
                end = index + len(marker)
                if not self._inside_citation(text, end):
                    best = max(best, end)
                start = index + 1
        return best

    @staticmethod
    def _inside_citation(text: str, position: int) -> bool:
        open_at = text.rfind(_CITE_OPEN, 0, position)
        if open_at < 0:
            return False
        close_at = text.find(_CITE_CLOSE, open_at + len(_CITE_OPEN))
        return close_at < 0 or position <= close_at + len(_CITE_CLOSE)

    def _assert_no_open_marker(self, text: str) -> None:
        self._assert_no_tool_protocol(text)
        if _THINK_OPEN in text or _THINK_CLOSE in text:
            raise StreamSafetyError("unterminated_reasoning")
        open_at = text.rfind(_CITE_OPEN)
        if open_at >= 0 and text.find(_CITE_CLOSE, open_at + len(_CITE_OPEN)) < 0:
            raise StreamSafetyError("unterminated_citation")
        if text.count("[[") != text.count("]]"):
            raise StreamSafetyError("unterminated_citation")
        if self._partial_suffix(text, _TOOL_PROTOCOL_OPEN) >= 2:
            raise StreamSafetyError("tool_calls_in_answer_role")

    @staticmethod
    def _assert_no_tool_protocol(text: str) -> None:
        """Treat raw provider tool framing like a structured call in answer role."""
        if _TOOL_PROTOCOL_OPEN in text:
            raise StreamSafetyError("tool_calls_in_answer_role")

    def _guard_size(self, *, internal: bool) -> None:
        if len(self._buf) > self._limits.max_buffer:
            raise StreamSafetyError(
                "buffer_limit_exceeded" if not internal else "internal_buffer_limit"
            )

    @staticmethod
    def _partial_suffix(text: str, marker: str) -> int:
        """Length of the suffix of ``text`` that is a proper prefix of ``marker``."""
        for size in range(min(len(marker) - 1, len(text)), 0, -1):
            if text.endswith(marker[:size]):
                return size
        return 0

    @staticmethod
    def _balanced_json(text: str) -> int | None:
        """Return the length of a complete JSON value at the start of ``text``."""
        if not text or text[0] not in "[{":
            return None
        depth = 0
        in_string = False
        escaped = False
        for index, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
                if depth == 0:
                    return index + 1
        return None
