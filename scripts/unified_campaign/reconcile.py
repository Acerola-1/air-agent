"""Compare an independent campaign index with Change6/7 API payloads.

The insight index is intent-level: one request can split into several
intents, and different intents may legitimately belong to different clusters.
What must never happen is the same intent receiving more than one effective
cluster (or the same split index being written twice under different ids).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _intent_key(row: dict[str, Any]) -> str:
    """Return a stable intent identity, falling back to request + split index."""
    intent_id = row.get("intent_id")
    if intent_id:
        return str(intent_id)
    request_id = str(row.get("request_id") or "")
    split_index = row.get("split_index")
    return f"{request_id}#{split_index if split_index is not None else 0}"


def _split_key(row: dict[str, Any]) -> str:
    request_id = str(row.get("request_id") or "")
    split_index = row.get("split_index")
    return f"{request_id}#{split_index if split_index is not None else 0}"


def reconcile(
    *,
    expectations: dict[str, Any],
    sent_rows: list[dict[str, Any]],
    metrics: dict[str, Any] | None,
    insight_sources: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Check conservation using the campaign index, not the SQL being tested.

    `metrics["question_count"]` is the Change6 distinct-request total.
    `insight_sources` rows are intent-level rows with `request_id`,
    `intent_id` (optional), `split_index` (optional) and `cluster_id`
    (null/absent means the intent is listed as unclassified).
    Missing payloads stay blocked rather than passing.
    """
    admitted = [row for row in sent_rows if row.get("request_id")]
    admitted_ids = [str(row["request_id"]) for row in admitted]
    distinct_admitted = set(admitted_ids)
    history_ids = [
        str(row["request_id"]) for row in admitted if row.get("cohort") == "history"
    ]
    checks: list[dict[str, Any]] = [
        _check(
            "campaign-admissions",
            expectations["request_ids_expected_after_send"],
            len(admitted_ids),
            "每个计划样本都有独立 request_id",
        ),
        _check(
            "history-visible-in-index",
            expectations["history_must_remain_visible"],
            len(history_ids),
            "多轮历史产生的真实请求仍在活动索引中",
        ),
    ]
    if metrics is None:
        checks.append(_blocked("change6-question-total", "未取得统计接口响应"))
    else:
        checks.append(
            _check(
                "change6-question-total",
                len(distinct_admitted),
                metrics.get("question_count"),
                "空隔离库窗口内的提问数等于本次已受理请求",
            )
        )
    if insight_sources is None:
        checks.extend(
            [
                _blocked(
                    "change7-source-coverage",
                    "未取得洞察来源；不能把缺失写成归类通过",
                ),
                _blocked("change7-intent-identity", "未取得洞察来源"),
                _blocked("change7-single-effective-cluster", "未取得洞察来源"),
            ]
        )
    else:
        checks.extend(
            _insight_checks(
                expectations, distinct_admitted, history_ids, insight_sources
            )
        )
    status = "PASS"
    if any(item["status"] == "FAIL" for item in checks):
        status = "FAIL"
    elif any(item["status"] == "BLOCKED" for item in checks):
        status = "BLOCKED"
    return {
        "status": status,
        "checks": checks,
        "admitted_request_count": len(admitted_ids),
        "note": expectations.get("note"),
    }


def _insight_checks(
    expectations: dict[str, Any],
    admitted: set[str],
    history_ids: list[str],
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = [row for row in sources if isinstance(row, dict) and row.get("request_id")]
    covered_requests = {str(row["request_id"]) for row in rows}
    clusters_by_intent: dict[str, set[str]] = defaultdict(set)
    intents_by_split: dict[str, set[str]] = defaultdict(set)
    intents_by_request: dict[str, set[str]] = defaultdict(set)
    classified_intents: set[str] = set()
    for row in rows:
        request_id = str(row["request_id"])
        if row.get("gap_reason") and not row.get("intent_id"):
            # A gap accounts for the request but is not an intent identity.
            continue
        key = _intent_key(row)
        intents_by_request[request_id].add(key)
        intents_by_split[_split_key(row)].add(key)
        cluster_id = row.get("cluster_id")
        if cluster_id:
            clusters_by_intent[key].add(str(cluster_id))
            classified_intents.add(key)
    all_intents = {key for keys in intents_by_request.values() for key in keys}
    missing = sorted(admitted - covered_requests)
    duplicate_splits = {
        split: keys for split, keys in intents_by_split.items() if len(keys) > 1
    }
    conflicting_intents = {
        intent: clusters
        for intent, clusters in clusters_by_intent.items()
        if len(clusters) > 1
    }
    multi_intent = {
        request_id: keys
        for request_id, keys in intents_by_request.items()
        if len(keys) > 1
    }
    checks: list[dict[str, Any]] = [
        _check(
            "change7-source-coverage",
            0,
            len(missing),
            "已受理请求要么进入来源，要么进入未归类清单",
        ),
        _check(
            "change7-intent-identity",
            0,
            len(duplicate_splits),
            "同一请求的同一拆分序号只能对应一个意图",
        ),
        _check(
            "change7-single-effective-cluster",
            0,
            len(conflicting_intents),
            "同一意图不能同时获得多个有效簇归属；不同意图可以分属不同簇",
        ),
        {
            "check_id": "change7-history-retained",
            "expected": len(history_ids),
            "actual": len(
                [
                    request_id
                    for request_id in history_ids
                    if request_id in covered_requests
                ]
            ),
            "status": "PASS"
            if all(request_id in covered_requests for request_id in history_ids)
            else "FAIL",
            "detail": "历史 cohort 的请求没有从洞察来源中消失",
        },
        {
            "check_id": "change7-intent-counts",
            "expected": None,
            "actual": {
                "intents": len(all_intents),
                "classified": len(classified_intents),
                "unclassified": len(all_intents - classified_intents),
                "multi_intent_requests": len(multi_intent),
            },
            "status": "PASS",
            "detail": "意图数按 intent_id 去重；多意图请求按 request_id 聚合",
        },
    ]
    declared = expectations.get("multi_intent_request_ids")
    if isinstance(declared, list) and declared:
        satisfied = {
            str(request_id)
            for request_id in declared
            if len(intents_by_request.get(str(request_id), set())) >= 2
        }
        missing_multi = sorted({str(item) for item in declared} - satisfied)
        checks.append(
            _check(
                "change7-declared-multi-intent",
                0,
                len(missing_multi),
                "声明的多意图请求都必须拆出至少两个意图",
            )
        )
    return checks


def _check(
    check_id: str, expected: object, actual: object, detail: str
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "expected": expected,
        "actual": actual,
        "status": "PASS" if expected == actual else "FAIL",
        "detail": detail,
    }


def _blocked(check_id: str, detail: str) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "expected": None,
        "actual": None,
        "status": "BLOCKED",
        "detail": detail,
    }
