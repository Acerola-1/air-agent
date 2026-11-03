"""Publish-time review for one Basic QA answer unit.

The check runs on a sentence that has not been published yet. A concrete
standard, clause or limit is quoted from a verified knowledge excerpt when
that excerpt contains the claimed values. A value that appears only in this
turn's web results is rewritten into a server sentence that cites ``[web:n]``
and does not call the value a statutory limit. Units without a concrete value
pass through. Question text never selects this path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage

from common.knowledge.policy import CITE_RE, is_specific_regulation_text

WEB_CITE_RE = re.compile(r"\[web:(\d+)\]")
_LOOSE_CITE_RE = re.compile(r"\[(?:citation|cite):([0-9a-fA-F-]{36})\](?!\])")
_HTML_TABLE_RE = re.compile(r"<table\b[^>]*>[\s\S]*?(?:</table>|$)", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HEADING_RE = re.compile(r"^#{1,6}\s*")
_STANDARD_RE = re.compile(
    r"(?<![A-Za-z])((?:GB(?:/T)?|HJ(?:/T)?)\s*\d{2,5}(?:[-—]\d{2,4})?)"
)
_CLAUSE_RE = re.compile(r"(第[一二三四五六七八九十百零〇\d]{1,6}条)")
_UNIT_NUMBER = re.compile(
    r"(?P<number>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>毫克/立方米|微克/立方米|mg/m3|mg/m³|μg/m3|ug/m3|m3|m³)"
)
_LIMIT_NUMBER = re.compile(
    r"(?:限值|不得超过|不应超过|不超过)\s*(?:为|是|：|:)?\s*(\d+(?:\.\d+)?)"
)
_FAILED_OUTCOMES = frozenset(
    {
        "permission_denied",
        "context_changed",
        "evidence_unusable",
        "unavailable",
        "timeout",
        "cancelled",
        "unknown",
    }
)
_COUNT_SUFFIX = r"(?:\s*(?:个|项|名|处|次|步|年|月|日|号))"

ABSTAIN_NO_MATCH = (
    "这一句涉及具体标准或限值，知识库未核到可核验原文，不能给出确定数值或文号。"
)
ABSTAIN_NOT_CALLED = (
    "这一句涉及具体标准或限值，本轮没有可核验的知识库原文，不能给出确定数值或文号。"
)
ABSTAIN_FAILED = "这一句涉及具体标准或限值，该部分数据暂时无法获取。"
ABSTAIN_MISMATCH = "这一句中的具体条款或限值与已核验原文不一致，已停止公开这一句。"
ABSTAIN_UNKNOWN_CITE = "这一句引用了未能核验的知识库来源，已停止公开这一句。"
ABSTAIN_UNKNOWN_WEB = "这一句引用了未能对应的互联网来源，已停止公开这一句。"


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "").replace("—", "-").replace("－", "-").upper()


def _has_number(text: str, number: str) -> bool:
    return re.search(rf"(?<!\d){re.escape(number)}(?!\d)", text or "") is not None


@dataclass(frozen=True)
class _Claim:
    limits: frozenset[str]
    pairs: frozenset[tuple[str, str]]
    standards: frozenset[str]
    clauses: frozenset[str]

    @property
    def concrete(self) -> bool:
        return bool(self.limits or self.pairs or self.standards or self.clauses)

    @property
    def numbers(self) -> frozenset[str]:
        return frozenset(self.limits) | frozenset(
            number for number, _unit in self.pairs
        )


def _claim_from(text: str) -> _Claim:
    return _Claim(
        limits=frozenset(_LIMIT_NUMBER.findall(text or "")),
        pairs=frozenset(
            (match.group("number"), match.group("unit").lower())
            for match in _UNIT_NUMBER.finditer(text or "")
        ),
        standards=frozenset(
            _compact(match.group(1)) for match in _STANDARD_RE.finditer(text or "")
        ),
        clauses=frozenset(_CLAUSE_RE.findall(text or "")),
    )


def _text_supports(text: str, claim: _Claim) -> bool:
    compact = _compact(text)
    lowered = text or ""
    if any(not _has_number(lowered, number) for number in claim.limits):
        return False
    for number, unit in claim.pairs:
        if not _has_number(lowered, number) or unit.lower() not in lowered.lower():
            return False
    if any(token not in compact for token in claim.standards):
        return False
    return all(clause in (text or "") for clause in claim.clauses)


def _displays(text: str, claim: _Claim) -> list[str]:
    pairs = [
        f"{match.group('number')}{match.group('unit')}"
        for match in _UNIT_NUMBER.finditer(text or "")
    ]
    if pairs:
        return list(dict.fromkeys(pairs))
    if claim.limits:
        return list(claim.limits)
    if claim.standards:
        return [
            re.sub(r"\s+", "", match.group(1))
            for match in _STANDARD_RE.finditer(text or "")
        ]
    return list(claim.clauses)


@dataclass
class EvidenceSnapshot:
    """Knowledge and web evidence already returned in this turn."""

    knowledge_status: str
    citations: dict[str, dict[str, str]]
    web: dict[int, str]


def load_evidence(messages: list[Any]) -> EvidenceSnapshot:
    """Read this turn's knowledge and web tool results. Does not call tools."""
    latest_user = max(
        (
            index
            for index, message in enumerate(messages)
            if isinstance(message, HumanMessage)
        ),
        default=-1,
    )
    citations: dict[str, dict[str, str]] = {}
    saw_failed = False
    saw_no_match = False
    saw_search = False
    web: dict[int, str] = {}
    for message in messages[latest_user + 1 :]:
        if not isinstance(message, ToolMessage):
            continue
        if message.name == "knowledge_search":
            saw_search = True
            found, failed, empty = _knowledge_payload(message.content)
            citations.update(found)
            saw_failed = saw_failed or failed
            saw_no_match = saw_no_match or empty
        elif message.name == "web_search":
            web.update(_web_payload(message.content))
    if citations:
        status = "retrieved"
    elif saw_failed:
        status = "failed"
    elif saw_no_match:
        status = "no_match"
    elif saw_search:
        status = "failed"
    else:
        status = "not_called"
    return EvidenceSnapshot(status, citations, web)


def _knowledge_payload(content: Any) -> tuple[dict[str, dict[str, str]], bool, bool]:
    payload = _json_object(content)
    if payload is None:
        return {}, True, False
    outcome = payload.get("outcome")
    if outcome != "ok" or payload.get("retrieval_status") not in {
        "retrieved",
        "complete",
    }:
        return (
            {},
            outcome in _FAILED_OUTCOMES or outcome != "no_match",
            outcome == "no_match",
        )
    fragments = {
        item.get("citation_id"): item.get("content")
        for item in payload.get("fragments", [])
        if isinstance(item, dict)
    }
    found: dict[str, dict[str, str]] = {}
    for reference in payload.get("references") or []:
        if not isinstance(reference, dict) or reference.get("verified") is not True:
            continue
        citation_id = reference.get("citation_id")
        if not isinstance(citation_id, str):
            continue
        found[citation_id.lower()] = {
            "doc_name": str(reference.get("doc_name") or "未命名文档"),
            "content": str(
                fragments.get(citation_id) or reference.get("excerpt") or ""
            ),
        }
    if found:
        return found, False, False
    return {}, False, True


def _web_payload(content: Any) -> dict[int, str]:
    payload = _json_object(content)
    if payload is None or payload.get("status") != "ok":
        return {}
    found: dict[int, str] = {}
    for item in payload.get("results") or []:
        if not isinstance(item, dict) or isinstance(item.get("n"), bool):
            continue
        number = item.get("n")
        if not isinstance(number, int) or number <= 0:
            continue
        found[number] = "\n".join(
            str(item.get(key) or "")
            for key in ("title", "summary", "snippet", "content_excerpt")
        )
    return found


def _json_object(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


@dataclass
class AnswerReview:
    """Replace unpublished units that state an unsupported concrete value."""

    evidence: EvidenceSnapshot
    blocked_numbers: set[str] = field(default_factory=set)
    blocked_standards: set[str] = field(default_factory=set)

    @classmethod
    def from_messages(cls, messages: list[Any]) -> AnswerReview:
        """Build a review from this turn's tool results."""
        return cls(load_evidence(messages))

    def review_unit(self, unit: str) -> str:
        """Return the text that may be published for this one unit."""
        unit = _normalize_loose_cites(unit)
        if _unknown_knowledge_cites(unit, self.evidence.citations):
            self._remember(unit)
            return ABSTAIN_UNKNOWN_CITE
        if _unknown_web_cites(unit, self.evidence.web):
            self._remember(unit)
            return ABSTAIN_UNKNOWN_WEB
        claim = _claim_from(unit)
        repeated = self._repeated_numbers(unit)
        if not claim.concrete and not repeated:
            return unit
        if claim.concrete and not is_specific_regulation_text(unit) and not repeated:
            return unit
        covered = claim
        if repeated and not covered.concrete:
            covered = _Claim(
                limits=frozenset(repeated),
                pairs=frozenset(),
                standards=frozenset(),
                clauses=frozenset(),
            )
        citations = self.evidence.citations
        if covered.concrete and _supported_by(citations, covered):
            quoted = _quotations(citations, covered)
            if quoted:
                return quoted
        if _conflicts(citations, covered):
            self._remember(unit)
            return ABSTAIN_MISMATCH
        web_numbers = _web_numbers(self.evidence.web, covered)
        # 法规句可以由本轮网页证据改写成服务端归因句。仅复述被拦数字、
        # 本身不再构成法规句时，必须由该句自己的 [web:n] 支撑。
        if web_numbers and (
            is_specific_regulation_text(unit)
            or _cites_supporting_web(unit, web_numbers)
        ):
            return _web_sentence(unit, covered, web_numbers)
        self._remember(unit)
        return _abstain(self.evidence.knowledge_status)

    def _remember(self, unit: str) -> None:
        claim = _claim_from(unit)
        self.blocked_numbers.update(claim.numbers)
        self.blocked_standards.update(claim.standards)

    def _repeated_numbers(self, unit: str) -> set[str]:
        found: set[str] = set()
        for number in self.blocked_numbers:
            pattern = rf"(?<!\d){re.escape(number)}(?!\d)(?!{_COUNT_SUFFIX})"
            if re.search(pattern, unit or ""):
                found.add(number)
        return found


def _unknown_knowledge_cites(unit: str, citations: dict[str, dict[str, str]]) -> bool:
    return any(
        match.group(1).lower() not in citations
        for match in CITE_RE.finditer(unit or "")
    )


def _unknown_web_cites(unit: str, web: dict[int, str]) -> bool:
    return any(
        int(match.group(1)) not in web for match in WEB_CITE_RE.finditer(unit or "")
    )


def _corpus(reference: dict[str, str]) -> str:
    return f"{reference.get('doc_name') or ''}\n{reference.get('content') or ''}"


def _supported_by(citations: dict[str, dict[str, str]], claim: _Claim) -> bool:
    if not claim.concrete:
        return False
    return any(
        _text_supports(_corpus(reference), claim) for reference in citations.values()
    )


def _conflicts(citations: dict[str, dict[str, str]], claim: _Claim) -> bool:
    """Return whether a verified excerpt states a different limit for the same standard."""
    if not claim.standards or not claim.limits:
        return False
    for reference in citations.values():
        text = _corpus(reference)
        if not any(token in _compact(text) for token in claim.standards):
            continue
        excerpt_limits = set(_LIMIT_NUMBER.findall(text))
        if excerpt_limits and excerpt_limits.isdisjoint(claim.limits):
            return True
    return False


def _normalize_loose_cites(unit: str) -> str:
    """Turn `[citation:<id>]` into the marker the chat UI renders as a source button."""
    return _LOOSE_CITE_RE.sub(
        lambda match: f"[[cite:{match.group(1).lower()}]]",
        unit or "",
    )


def _escape_emphasis(text: str) -> str:
    """Keep underscores and asterisks from becoming Markdown emphasis."""
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_")


def _prose(content: str) -> str:
    """Drop catalog JSON, HTML tables and heading marks before quoting."""
    text = _HTML_TABLE_RE.sub(" ", content or "")
    text = _HTML_TAG_RE.sub("", text).replace("$", "")
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in "{[":
            continue
        stripped = _HEADING_RE.sub("", stripped).strip()
        if stripped:
            lines.append(stripped)
    return "\n".join(lines)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？])|\n+", text)
    return [" ".join(part.split()) for part in parts if part and part.strip()]


def _mentions_claim(sentence: str, claim: _Claim) -> bool:
    if any(_has_number(sentence, number) for number in claim.limits):
        return True
    if any(_has_number(sentence, number) for number, _unit in claim.pairs):
        return True
    if any(clause in sentence for clause in claim.clauses):
        return True
    compact = _compact(sentence)
    return any(token in compact for token in claim.standards)


def _readable_span(content: str, claim: _Claim) -> str | None:
    """Return one short plain sentence that actually carries the claim."""
    picked = [
        sentence
        for sentence in _sentences(_prose(content))
        if _mentions_claim(sentence, claim)
    ]
    if not picked:
        return None
    span = ""
    for sentence in picked:
        span = sentence if not span else f"{span}{sentence}"
        if _text_supports(span, claim) or len(span) >= 160:
            break
    visible = _escape_emphasis(span[:240].rstrip())
    return visible or None


def _quotations(citations: dict[str, dict[str, str]], claim: _Claim) -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for citation_id, reference in citations.items():
        if not _text_supports(_corpus(reference), claim):
            continue
        span = _readable_span(reference.get("content") or "", claim)
        if not span or span in seen:
            continue
        seen.add(span)
        lines.append(f"原文：{span} [[cite:{citation_id}]]")
        if len(lines) == 2:
            break
    return "\n\n".join(lines)


def _cites_supporting_web(unit: str, numbers: list[int]) -> bool:
    cited = {int(match.group(1)) for match in WEB_CITE_RE.finditer(unit or "")}
    return bool(cited.intersection(numbers))


def _web_numbers(web: dict[int, str], claim: _Claim) -> list[int]:
    if not claim.concrete:
        return []
    return [
        number for number, text in sorted(web.items()) if _text_supports(text, claim)
    ]


def _web_sentence(unit: str, claim: _Claim, numbers: list[int]) -> str:
    shown = "、".join(_displays(unit, claim))
    marks = " ".join(f"[web:{number}]" for number in numbers)
    if claim.limits or claim.pairs:
        return f"网页记载为 {shown} {marks}，该数值未经知识库原文核验。"
    return f"网页提到 {shown} {marks}，不能据此确认标准条文或限值。"


def _abstain(status: str) -> str:
    if status == "failed":
        return ABSTAIN_FAILED
    if status == "no_match":
        return ABSTAIN_NO_MATCH
    if status == "retrieved":
        return ABSTAIN_MISMATCH
    return ABSTAIN_NOT_CALLED
