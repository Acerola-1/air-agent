"""Service-side evidence policy for concrete regulation/limit answers.

The model's own statement is never enough. A concrete standard, clause or
limit may only be published when the current turn contains a verified
knowledge citation; obvious number/unit/year mismatches against the cited
excerpt block the answer instead of exposing an unverified specific claim.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

CITE_RE = re.compile(r"\[\[cite:([0-9a-fA-F-]{36})\]\]")
_SPECIFIC_PATTERNS = (
    re.compile(r"《[^》]{1,80}》"),
    re.compile(r"(?<![A-Za-z])(?:GB(?:/T)?|HJ(?:/T)?)\s*\d{2,5}(?:[-—]\d{2,4})?"),
    re.compile(r"第[一二三四五六七八九十百零〇\d]{1,6}条"),
    re.compile(r"(?:标准|排放|浓度|环境)限值"),
    re.compile(r"(?:不得超过|不应超过|限值为)\s*\d+(?:\.\d+)?"),
)
_UNIT_NUMBER = re.compile(
    r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>毫克/立方米|微克/立方米|mg/m3|mg/m³|μg/m3|ug/m3|m3|m³)"
)
_YEAR = re.compile(r"\b(20\d{2})\s*年?(?:版)?")
_LIMIT_NUMBER = re.compile(
    r"(?:限值|不得超过|不应超过|不超过)\s*(?:为|是|：|:)?\s*(\d+(?:\.\d+)?)"
)

_BLOCKED_MESSAGE = (
    "该回答涉及具体法规条款或标准限值，但未取得可核验的知识库依据，"
    "因此不能给出确定数值或文号。请补充标准名称/条款，或稍后重试检索。"
)
_UNSUPPORTED_MESSAGE = (
    "该回答中的具体条款或限值与已核验原文不一致，已停止公开。"
    "请以检索返回的原文为依据重新提问。"
)


def is_specific_regulation_text(text: str) -> bool:
    """Whether the text contains a concrete standard/clause/limit claim."""
    value = text or ""
    return any(pattern.search(value) for pattern in _SPECIFIC_PATTERNS)


def citation_ids_from_messages(messages: Sequence[Any]) -> dict[str, dict[str, Any]]:
    """Collect citation_id → reference from this turn's knowledge tool results."""
    found: dict[str, dict[str, Any]] = {}
    latest_user = max(
        (
            index
            for index, message in enumerate(messages)
            if isinstance(message, HumanMessage)
        ),
        default=-1,
    )
    for message in messages[latest_user + 1 :]:
        if not isinstance(message, ToolMessage) or message.name != "knowledge_search":
            continue
        content = message.content
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("outcome") != "ok" or payload.get("retrieval_status") not in {
            "retrieved",
            "complete",
        }:
            continue
        fragments = {
            item.get("citation_id"): item.get("content")
            for item in payload.get("fragments", [])
            if isinstance(item, dict)
        }
        for reference in payload.get("references") or []:
            if not isinstance(reference, dict):
                continue
            citation_id = reference.get("citation_id")
            if isinstance(citation_id, str) and reference.get("verified") is True:
                found[citation_id.lower()] = {
                    **reference,
                    "content": fragments.get(citation_id)
                    or reference.get("excerpt")
                    or "",
                }
    return found


def _cited_excerpts(
    text: str, citations: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    cited = [match.group(1).lower() for match in CITE_RE.finditer(text)]
    excerpts: list[str] = []
    unknown: list[str] = []
    for citation_id in cited:
        reference = citations.get(citation_id)
        if reference is None:
            unknown.append(citation_id)
        else:
            # 文档名/标准号也是服务端登记的可信元数据（如“GBT18883-2002”），
            # 用它支持答案中的标准版本，但数值仍只能来自原文片段。
            excerpts.append(
                f"{reference.get('doc_name') or ''}\n{reference.get('content') or ''}"
            )
    return excerpts, unknown


def _numbers_with_units(text: str) -> set[tuple[str, str]]:
    return {
        (match.group("number"), match.group("unit").lower())
        for match in _UNIT_NUMBER.finditer(text or "")
    }


def _years(text: str) -> set[str]:
    return set(_YEAR.findall(text or ""))


def with_server_citations(answer: str, messages: Sequence[Any]) -> str:
    """Append server-verified citation markers when the model omitted them.

    This adds only opaque citation ids, never document names, numbers or
    excerpts. The subsequent policy check still verifies numbers/units/years
    against the cited excerpts, so a fabricated specific value is not rescued.
    """
    text = answer or ""
    question = next(
        (
            m.content
            for m in reversed(messages)
            if isinstance(m, HumanMessage) and isinstance(m.content, str)
        ),
        "",
    )
    if CITE_RE.search(text) or not (
        is_specific_regulation_text(text) or is_specific_regulation_text(question)
    ):
        return text
    citations = citation_ids_from_messages(messages)
    if not citations:
        return text
    ids = list(citations)[:3]
    markers = " ".join(f"[[cite:{citation_id}]]" for citation_id in ids)
    return f"{text.rstrip()}\n\n依据：{markers}"


def enforce_answer_policy(
    answer: str, messages: Sequence[Any]
) -> tuple[str, bool, str | None]:
    """Return ``(published_text, blocked, reason)`` before the answer is public.

    The check runs on the complete answer (callers must not stream unverified
    fragments first). A concrete claim with no citation, an unknown citation id,
    or a number/unit/year absent from the cited excerpts is replaced by a clear
    refusal instead of being published and retracted later.
    """
    text = with_server_citations(answer, messages)
    if not text.strip():
        return text, False, None
    citations = citation_ids_from_messages(messages)
    question = next(
        (
            m.content
            for m in reversed(messages)
            if isinstance(m, HumanMessage) and isinstance(m.content, str)
        ),
        "",
    )
    paragraphs = [part for part in re.split(r"\n\s*\n", text) if part.strip()]
    excerpts, unknown = _cited_excerpts(text, citations)
    if unknown:
        return _BLOCKED_MESSAGE, True, "unknown_citation_id"
    if is_specific_regulation_text(question) or any(
        is_specific_regulation_text(part) for part in paragraphs
    ):
        if not excerpts:
            return _BLOCKED_MESSAGE, True, "retrieval_required"
        joined = "\n".join(excerpts)
        claimed_numbers = set().union(
            *(_numbers_with_units(part) for part in paragraphs)
        )
        supported_numbers = _numbers_with_units(joined)
        if claimed_numbers and not claimed_numbers <= supported_numbers:
            return _UNSUPPORTED_MESSAGE, True, "unsupported_number_or_unit"
        claimed_years = set().union(*(_years(part) for part in paragraphs))
        supported_years = _years(joined)
        if claimed_years and not claimed_years <= supported_years:
            return _UNSUPPORTED_MESSAGE, True, "unsupported_version"
        claimed_limits = set().union(
            *(set(_LIMIT_NUMBER.findall(part)) for part in paragraphs)
        )
        supported_limits = set(_LIMIT_NUMBER.findall(joined))
        if claimed_limits and not claimed_limits <= supported_limits:
            return _UNSUPPORTED_MESSAGE, True, "unsupported_number_or_unit"
        # Number checks alone cannot prove scope, subject, negation or semantic
        # support. Until a rewrite is verifiable, publish bounded exact source
        # quotations rather than an unchecked model paraphrase (D5).
        question = next(
            (
                m.content
                for m in reversed(messages)
                if isinstance(m, HumanMessage) and isinstance(m.content, str)
            ),
            "",
        )
        rendered = ["已检索到以下原文片段；仅据所列原文提供依据："]
        for citation_id in dict.fromkeys(
            match.group(1).lower() for match in CITE_RE.finditer(text)
        ):
            reference = citations[citation_id]
            content = str(reference.get("content") or "")
            if not content.strip():
                return _BLOCKED_MESSAGE, True, "source_text_missing"
            excerpt = content[:500]
            if "范围" in question:
                section = re.search(r"(?m)^#{0,6}\s*1[.、\s]+范围\s*\n", content)
                if section:
                    excerpt = content[section.end() :].split("\n#", 1)[0][:500].strip()
                elif "本标准适用于" in content:
                    start = content.index("本标准适用于")
                    excerpt = content[start : start + 500].split("\n#", 1)[0].strip()
            quote = "\n".join(f"> {line}" for line in excerpt.splitlines())
            rendered.append(
                f"\n《{reference.get('doc_name') or '未命名文档'}》 [[cite:{citation_id}]]\n\n{quote}"
            )
        return "\n".join(rendered), False, "quoted_source_only"
    return text, False, None


def apply_answer_policy(response: AIMessage, messages: Sequence[Any]) -> AIMessage:
    """Apply the policy to an AIMessage, preserving tool calls."""
    if response.tool_calls:
        # Tool arguments are still executed; speculative accompanying prose is
        # not a verified answer and must not enter raw state/updates as正文.
        return response.model_copy(update={"content": ""})
    content = response.content
    if not isinstance(content, str):
        return response
    published, blocked, reason = enforce_answer_policy(content, messages)
    if not blocked:
        if published == content:
            return response
        return response.model_copy(update={"content": published})
    return response.model_copy(
        update={
            "content": published,
            "additional_kwargs": {
                **response.additional_kwargs,
                "knowledge_policy_blocked": reason,
            },
        }
    )
