"""需求归类校准/留出评测（真实 Embedding 与 fixture 分开）.

- 60 条校准样本用于选择相似度/间隔阈值（留一法，避免自匹配）；
- 100 条冻结留出样本只用于最终门槛，不参与调参；
- 每条消息先按生产规则拆成原子意图，再逐意图 Embedding；敏感样本不进入向量；
- `fake` 只验证算法/阈值管线，报告标记 fixture，不能当作真实 Embedding 质量；
  `real` 必须配置 INSIGHTS_EMBEDDING_*，失败如实返回阻塞。

用法：
    .venv/bin/python scripts/evaluate_insights_calibration.py --provider real \
        --output /tmp/insights-calibration.json
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.demand_insights.config import get_insights_settings
from app.demand_insights.matching import (
    ClusterCandidate,
    MatchThresholds,
    ScoredCandidate,
    cosine,
    decide,
)
from app.demand_insights.model_gateway import ModelGateway
from app.demand_insights.normalization import IntentDraft, normalize_question

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "demand_insights"
    / "fixtures"
    / "calibration_samples.jsonl"
)
KEY_CLASSES = {"cause", "forecast", "advice", "query_trend"}


def _load() -> list[dict]:
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line]
    if len(rows) != 160:
        raise SystemExit(f"样本数应为 160，实际 {len(rows)}")
    calibration = [row for row in rows if row["split"] == "calibration"]
    holdout = [row for row in rows if row["split"] == "holdout"]
    if len(calibration) != 60 or len(holdout) != 100:
        raise SystemExit("校准/留出必须为 60/100")
    if {row["id"] for row in calibration} & {row["id"] for row in holdout}:
        raise SystemExit("校准与留出存在重叠样本")
    return rows


def _label(action: str) -> str:
    """评测标签与生产簇身份一致：城市/日期/污染物是参数，不是任务身份."""
    if action == "cause":
        return "cause"
    if action == "forecast":
        return "forecast"
    if action == "advice":
        return "advice"
    if action == "trend":
        return "query_trend"
    if action == "query":
        return "query_air_quality"
    return "other"


def _normalize(record: dict):
    return normalize_question(
        record["text"],
        context_texts=record.get("context") or [],
        max_input_chars=4000,
        max_intents=5,
    )


def _key(draft: IntentDraft) -> tuple:
    return (_label(draft.action), draft.action, draft.object, draft.negation)


def _fake_embedding(text: str, dimension: int = 32) -> tuple[float, ...]:
    keywords = {
        "查": 0,
        "多少": 0,
        "浓度": 0,
        "趋势": 1,
        "近7天": 1,
        "为什么": 2,
        "原因": 2,
        "升高": 2,
        "预测": 3,
        "明天": 3,
        "改善": 3,
        "建议": 4,
        "注意": 4,
        "PM2.5": 5,
        "PM10": 5,
        "O3": 6,
        "NO2": 6,
        "SO2": 6,
        "CO": 6,
        "AQI": 7,
    }
    vector = [0.0] * dimension
    for keyword, index in keywords.items():
        if keyword in text:
            vector[index] += 1.0
    digest = hashlib.sha256(text.encode()).digest()
    for index in range(dimension):
        vector[index] += digest[index] / 255.0 * 0.05
    vector[0] += 0.2
    return tuple(vector)


def _intent_texts(records: list[dict]) -> list[tuple[str, str]]:
    """返回 (intent_key, text)；拒绝/sensitive 不进入向量."""
    rows: list[tuple[str, str]] = []
    for record in records:
        normalized = _normalize(record)
        if record["expected"]["rejected"]:
            continue
        for index, draft in enumerate(normalized.intents):
            rows.append((f"{record['id']}#{index}", draft.text or record["text"]))
    return rows


def _embed_all(records: list[dict], provider: str) -> tuple[dict[str, tuple[float, ...]], dict]:
    settings = get_insights_settings()
    dimension = settings.embedding_dimension if provider == "real" else 32
    rows = _intent_texts(records)
    if provider == "fake":
        return {key: _fake_embedding(text, dimension) for key, text in rows}, {
            "provider": "fake_fixture",
            "dimension": dimension,
            "model": "keyword-fixture",
            "profile": "fixture",
            "usage_tokens": None,
        }
    if not settings.embedding_configured:
        raise SystemExit("真实评测需要 INSIGHTS_EMBEDDING_* 配置；不以 fake 结果替代")
    gateway = ModelGateway(settings)

    async def _run():
        vectors: list[tuple[float, ...]] = []
        usage: int | None = 0
        info: dict[str, Any] = {
            "dimension": settings.embedding_dimension,
            "model": settings.embedding_model,
            "profile": settings.embedding_profile,
        }
        for offset in range(0, len(rows), 32):
            result = await gateway.embed([text for _key_value, text in rows[offset : offset + 32]])
            vectors.extend(result.vectors)
            info = {
                "dimension": result.dimension,
                "model": result.model,
                "profile": result.profile,
            }
            usage = (
                None
                if usage is None or result.usage_tokens is None
                else usage + result.usage_tokens
            )
        return vectors, info, usage

    try:
        vectors, info, usage = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"blocked": "real_embedding_unavailable", "error": type(exc).__name__}))
        raise SystemExit(3) from exc
    return (
        {key: vector for (key, _text), vector in zip(rows, vectors, strict=True)},
        {"provider": "real_embedding", **info, "usage_tokens": usage},
    )


def _stats(records: list[dict], vectors: dict[str, tuple[float, ...]], dimension: int):
    sums: dict[tuple, list[float]] = {}
    counts: collections.Counter = collections.Counter()
    samples: list[tuple[dict, IntentDraft, tuple[float, ...]]] = []
    for record in records:
        normalized = _normalize(record)
        if record["expected"]["rejected"]:
            continue
        for index, draft in enumerate(normalized.intents):
            vector = vectors.get(f"{record['id']}#{index}")
            if vector is None:
                continue
            key = _key(draft)
            bucket = sums.setdefault(key, [0.0] * dimension)
            for position, value in enumerate(vector):
                bucket[position] += value
            counts[key] += 1
            samples.append((record, draft, vector))
    return sums, counts, samples


def _candidates(sums: dict[tuple, list[float]], counts: collections.Counter):
    candidates = []
    labels = {}
    for index, (key, bucket) in enumerate(sums.items()):
        centroid = [value / counts[key] for value in bucket]
        cluster_id = f"cal-{index}"
        labels[cluster_id] = key
        candidates.append(
            ClusterCandidate(
                cluster_id=cluster_id,
                canonical_name=f"{key[0]}:{key[1]}:{key[2]}",
                action=key[1],
                object=key[2],
                time_kind="unknown",
                negation=key[3],
                vector=tuple(centroid),
            )
        )
    return candidates, labels


def _leave_one_out_scores(
    samples: list[tuple[dict, IntentDraft, tuple[float, ...]]],
    sums: dict[tuple, list[float]],
    counts: collections.Counter,
    dimension: int,
):
    """每个校准意图对每个候选簇的相似度；自身簇排除自身向量."""
    by_key = {key: index for index, key in enumerate(sums)}
    rows = []
    for record, draft, vector in samples:
        own = _key(draft)
        if counts[own] <= 1:
            continue
        scored = []
        for key, bucket in sums.items():
            if key == own:
                centroid = [
                    (bucket[position] - vector[position]) / (counts[key] - 1)
                    for position in range(dimension)
                ]
            else:
                centroid = [value / counts[key] for value in bucket]
            score = cosine(vector, centroid)
            if score is None:
                continue
            cluster_id = f"cal-{by_key[key]}"
            scored.append(
                ScoredCandidate(
                    candidate=ClusterCandidate(
                        cluster_id=cluster_id,
                        canonical_name=f"{key[0]}:{key[1]}:{key[2]}",
                        action=key[1],
                        object=key[2],
                        time_kind="unknown",
                        negation=key[3],
                        vector=(),
                    ),
                    score=score,
                )
            )
        scored.sort(key=lambda item: (-item.score, item.candidate.cluster_id))
        rows.append((record, draft, scored))
    return rows


def _score_threshold(rows, labels: dict[str, tuple], thresholds: MatchThresholds) -> dict[str, Any]:
    auto = correct = false_merges = 0
    clear = 0
    for _record, draft, scored in rows:
        clear += 1
        decision = decide(
            draft,
            scored,
            thresholds=thresholds,
            total_active_clusters=len(labels),
            max_clusters=20_000,
        )
        if decision.kind == "match" and decision.cluster_id:
            auto += 1
            if labels[decision.cluster_id][0] == _label(draft.action):
                correct += 1
            else:
                false_merges += 1
    precision = correct / auto if auto else 0.0
    coverage = correct / clear if clear else 0.0
    return {
        "clear": clear,
        "auto": auto,
        "correct": correct,
        "precision": precision,
        "coverage": coverage,
        "false_merges": false_merges,
    }


def tune_thresholds(records: list[dict], vectors: dict[str, tuple[float, ...]], dimension: int):
    calibration = [row for row in records if row["split"] == "calibration"]
    sums, counts, samples = _stats(calibration, vectors, dimension)
    _candidates_list, labels = _candidates(sums, counts)
    rows = _leave_one_out_scores(samples, sums, counts, dimension)
    for min_precision in (0.99, 0.95):
        best = None
        for margin_floor in (2, 0):
            for similarity_int in range(50, 96):
                similarity = similarity_int / 100
                for margin_int in range(margin_floor, 11):
                    margin = margin_int / 100
                    thresholds = MatchThresholds(
                        similarity=similarity,
                        margin=margin,
                        review_low=max(0.5, similarity - 0.1),
                    )
                    metrics = _score_threshold(rows, labels, thresholds)
                    if metrics["false_merges"] > 0 or metrics["precision"] < min_precision:
                        continue
                    score = (metrics["coverage"], metrics["auto"], margin, similarity)
                    if best is None or score > best[0]:
                        best = (score, thresholds, metrics)
            if best is not None:
                break
        if best is not None:
            _score, thresholds, metrics = best
            return thresholds, metrics, len(rows)
    settings = get_insights_settings()
    thresholds = MatchThresholds(
        similarity=settings.similarity_threshold,
        margin=settings.margin_threshold,
        review_low=settings.review_low,
    )
    return thresholds, _score_threshold(rows, labels, thresholds), len(rows)


def evaluate(
    records: list[dict], vectors: dict[str, tuple[float, ...]], provider_info: dict
) -> dict:
    settings = get_insights_settings()
    dimension = provider_info["dimension"]
    calibration = [row for row in records if row["split"] == "calibration"]
    holdout = [row for row in records if row["split"] == "holdout"]
    sums, counts, _samples = _stats(calibration, vectors, dimension)
    candidates, labels = _candidates(sums, counts)
    thresholds, calibration_metrics, calibration_rows = tune_thresholds(records, vectors, dimension)
    total_auto = correct_auto = clear_intents = 0
    false_merges = key_false_merges = 0
    rejected_expected = sensitive_leaks = 0
    confusion: collections.Counter = collections.Counter()
    hard_total = hard_auto = hard_correct = 0
    for record in holdout:
        normalized = _normalize(record)
        if record["expected"]["rejected"]:
            rejected_expected += 1
            if normalized.rejected_reason == record["expected"]["rejected"]:
                confusion[("sensitive", "rejected")] += 1
            else:
                sensitive_leaks += 1
                confusion[("sensitive", "sensitive_leak")] += 1
            continue
        for index, expected in enumerate(record["expected"]["intents"]):
            clear_intents += 1
            expected_label = _label(expected["action"])
            if index >= len(normalized.intents):
                confusion[(expected_label, "missing_intent")] += 1
                continue
            draft = normalized.intents[index]
            vector = vectors.get(f"{record['id']}#{index}")
            if vector is None:
                confusion[(expected_label, "missing_vector")] += 1
                continue
            scored = []
            for candidate in candidates:
                score = cosine(vector, candidate.vector)
                if score is not None:
                    scored.append(ScoredCandidate(candidate=candidate, score=score))
            scored.sort(key=lambda item: (-item.score, item.candidate.cluster_id))
            decision = decide(
                draft,
                scored,
                thresholds=thresholds,
                total_active_clusters=len(candidates),
                max_clusters=20_000,
            )
            if decision.kind == "match" and decision.cluster_id:
                total_auto += 1
                predicted = labels[decision.cluster_id]
                if record["hard_negative"]:
                    hard_total += 1
                    hard_auto += 1
                if predicted[0] == expected_label:
                    correct_auto += 1
                    if record["hard_negative"]:
                        hard_correct += 1
                    confusion[(expected_label, "auto_correct")] += 1
                else:
                    false_merges += 1
                    confusion[(expected_label, f"auto->{predicted[0]}")] += 1
                    if expected_label in KEY_CLASSES or predicted[0] in KEY_CLASSES:
                        key_false_merges += 1
            else:
                confusion[(expected_label, decision.kind)] += 1
    precision = correct_auto / total_auto if total_auto else 0.0
    coverage = correct_auto / clear_intents if clear_intents else 0.0
    gate = {
        "min_precision": settings.calibration_min_precision,
        "min_coverage": settings.calibration_min_coverage,
        "min_auto": settings.calibration_min_auto,
        "max_false_merges": settings.calibration_max_false_merge,
    }
    passed = (
        precision >= gate["min_precision"]
        and coverage >= gate["min_coverage"]
        and total_auto >= gate["min_auto"]
        and false_merges <= gate["max_false_merges"]
        and sensitive_leaks == 0
    )
    return {
        "provider": provider_info,
        "fixture_quality": provider_info["provider"] == "fake_fixture",
        "samples": {
            "total": len(records),
            "calibration": len(calibration),
            "holdout": len(holdout),
            "hard_negative_holdout_intents": hard_total,
        },
        "thresholds": {
            "similarity": thresholds.similarity,
            "margin": thresholds.margin,
            "review_low": thresholds.review_low,
        },
        "calibration_tuning": {
            "rows": calibration_rows,
            "auto_classified": calibration_metrics["auto"],
            "precision": round(calibration_metrics["precision"], 6),
            "coverage": round(calibration_metrics["coverage"], 6),
            "false_merges": calibration_metrics["false_merges"],
        },
        "holdout": {
            "clear_intents": clear_intents,
            "auto_classified": total_auto,
            "correct_auto": correct_auto,
            "precision": round(precision, 6),
            "coverage": round(coverage, 6),
            "false_merges": false_merges,
            "key_false_merges": key_false_merges,
            "hard_negative_auto": hard_auto,
            "hard_negative_correct": hard_correct,
            "rejected_expected": rejected_expected,
            "sensitive_leaks": sensitive_leaks,
            "unclassified_or_new": clear_intents - total_auto,
        },
        "confusion": [
            {"expected": key[0], "predicted": key[1], "count": count}
            for key, count in sorted(confusion.items())
        ],
        "gate": gate,
        "passed": bool(passed),
        "limitations": (
            ["fake_fixture 仅验证算法/阈值管线，不代表真实 Embedding 质量"]
            if provider_info["provider"] == "fake_fixture"
            else ["合成样本；真实生产分布需另行抽样校准"]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("fake", "real"), required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-gate", action="store_true")
    args = parser.parse_args()
    records = _load()
    vectors, provider_info = _embed_all(records, args.provider)
    report = evaluate(records, vectors, provider_info)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    if args.fail_on_gate and not report["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
