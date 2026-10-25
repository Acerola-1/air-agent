"""Source-aware answer checks, separate from transport and ledger completion."""

from __future__ import annotations

import re
from typing import Any


def assess_answer(
    case: dict[str, Any],
    answer: str,
    *,
    source: str,
    tool_count: int,
    oracle: dict[str, Any] | None = None,
    history_verified: bool = False,
) -> dict[str, Any]:
    """Check only frozen, source-compatible facts; missing real oracle blocks.

    An oracle is a controlled extraction of the actual authorized tool result,
    not the fixture's expected numeric values. This function does not declare
    a term match to be proof that a complete answer is factually correct.
    """
    if not answer.strip():
        return {
            "status": "FAIL",
            "reasons": ["empty_answer"],
            "rule_version": "source-aware-v2",
        }
    if source not in {"fixture", "real"}:
        return {
            "status": "BLOCKED",
            "reasons": ["unknown_data_source"],
            "rule_version": "source-aware-v2",
        }
    if source == "real" and (
        not oracle
        or not oracle.get("evidence_ref")
        or oracle.get("case_id") != case["case_id"]
    ):
        return {
            "status": "BLOCKED",
            "reasons": ["actual_result_oracle_missing"],
            "rule_version": "source-aware-v2",
        }
    checks = oracle if source == "real" else case.get("quality_v2")
    if not isinstance(checks, dict):
        return {
            "status": "BLOCKED",
            "reasons": ["frozen_quality_rules_missing"],
            "rule_version": "source-aware-v2",
        }
    reasons: list[str] = []
    if case.get("category") == "Q4" and not history_verified:
        reasons.append("history_not_verified")
    requires_query = checks.get("requires_new_query", case.get("category") != "Q4")
    if requires_query and tool_count == 0:
        reasons.append("required_query_missing")
    compact = answer.replace(" ", "")
    for group in checks.get("required_terms", []):
        terms = group if isinstance(group, list) else [group]
        if not any(str(term).replace(" ", "") in compact for term in terms):
            reasons.append("missing:" + "/".join(map(str, terms)))
    for term in checks.get("forbidden_terms", []):
        if str(term).replace(" ", "") in compact:
            reasons.append("forbidden:" + str(term))
    for pattern in checks.get("required_patterns", []):
        if not re.search(pattern, answer):
            reasons.append("pattern_missing:" + pattern)
    for fact in checks.get("numeric_facts", []):
        # Explicit labels/units avoid accepting an unrelated number elsewhere.
        pattern = rf"{re.escape(str(fact['label']))}[^\n\d]{{0,30}}{re.escape(str(fact['value']))}(?!\d)"
        if not re.search(pattern, answer):
            reasons.append("numeric_fact_missing:" + str(fact["label"]))
    return {
        "status": "FAIL" if reasons else "PASS",
        "reasons": reasons,
        "rule_version": "source-aware-v2",
        "evidence_ref": checks.get("evidence_ref"),
        "source": source,
    }


def is_effective_body(text: str) -> bool:
    """Exclude formatting-only units and stock action promises from metrics."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and all(line.startswith("#") for line in lines):
        return False
    clean = re.sub(r"[\s#*_`>\-:：。！!？?，,;；]+", "", text)
    clean = re.sub(r"^(?:好的|好|您好|你好|收到|没问题)(?:的)?", "", clean)
    if len(clean) < 5:
        return False
    if re.fullmatch(r"(?:核心)?(?:结论|结果|说明|分析|对比明细|数据说明)", clean):
        return False
    return not re.match(
        r"^(?:正在|我来|我会|我将|我正在|让我|接下来(?:我|将)|首先(?:我会|我将)|您(?:想了解|想查询|的问题是|询问的是)|你的问题是|根据您的要求)",
        clean,
    )
