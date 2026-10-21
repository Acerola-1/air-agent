"""Independent expectations built only from the planned campaign."""

from __future__ import annotations

from collections import Counter

from scripts.unified_campaign.dataset import PlannedSample


def build_expectations(samples: list[PlannedSample]) -> dict[str, object]:
    """Count requests, users, intents, and graphs without reading the system under test.

    Q4 history user turns are real future admissions. They stay outside the
    40-question smoke denominator and must still be present after drain.
    Repeating a measured case does not add a semantic sample.
    """
    by_cohort = Counter(sample.cohort for sample in samples)
    by_graph = Counter(sample.graph_id for sample in samples)
    by_intent = Counter(
        sample.expected_intent or "unlabeled"
        for sample in samples
        if sample.graph_id == "basic-qa"
    )
    measured_intent = Counter(
        sample.expected_intent or "unlabeled"
        for sample in samples
        if sample.counts_as_semantic_sample
    )
    semantic = [sample for sample in samples if sample.counts_as_semantic_sample]
    return {
        "schema_version": "campaign-expectations.v1",
        "semantic_denominator": len(semantic),
        "semantic_case_ids": [sample.case_id for sample in semantic],
        "by_cohort": dict(by_cohort),
        "by_graph": dict(by_graph),
        "by_expected_intent": dict(by_intent),
        "by_expected_intent_measured_only": dict(measured_intent),
        "request_ids_expected_after_send": len(samples),
        "history_must_remain_visible": by_cohort.get("history", 0),
        "note": "榜单中的重复频率来自本测试设定，不代表真实用户需求。",
    }
