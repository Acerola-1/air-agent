"""有界向量候选 + 意图/实体约束复核（design D4）.

本模块是纯函数：不做任何数据库/网络调用，便于独立验证
`action/object/time/negation` 约束、topK、临界复核与容量边界。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.demand_insights.normalization import IntentDraft, action_label, object_label


@dataclass(frozen=True)
class ClusterCandidate:
    cluster_id: str
    canonical_name: str
    action: str
    object: str
    time_kind: str
    negation: bool
    vector: tuple[float, ...]
    revision: int = 0
    status: str = "active"


@dataclass(frozen=True)
class MatchThresholds:
    similarity: float = 0.90
    margin: float = 0.03
    review_low: float = 0.80


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: ClusterCandidate
    score: float


@dataclass(frozen=True)
class MatchDecision:
    kind: str  # match | review | new | unclassified | capacity_review
    cluster_id: str | None
    score: float | None
    margin: float | None
    reason: str
    candidates: tuple[ScoredCandidate, ...] = ()


def valid_vector(vector: Sequence[float] | None, dimension: int) -> bool:
    if not vector or dimension <= 0 or len(vector) != dimension or dimension > 4096:
        return False
    total = 0.0
    for value in vector:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            return False
        total += float(value) * float(value)
    return math.isfinite(total) and total > 0


def cosine(left: Sequence[float], right: Sequence[float]) -> float | None:
    """维度不一致/非有限/零范数返回 None，绝不跨空间比较。"""
    if not left or not right or len(left) != len(right):
        return None
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right, strict=True):
        if not (math.isfinite(a) and math.isfinite(b)):
            return None
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0 or right_norm <= 0:
        return None
    return dot / math.sqrt(left_norm * right_norm)


def constraints_compatible(intent: IntentDraft, candidate: ClusterCandidate) -> bool:
    """参数（城市/日期/污染物）不是簇身份；任务动作/对象/否定/时间类型是."""
    if intent.action != candidate.action or intent.object != candidate.object:
        return False
    if intent.negation != candidate.negation:
        return False
    if intent.time_kind == candidate.time_kind:
        return True
    return intent.time_kind == "unknown" or candidate.time_kind == "unknown"


def top_candidates(
    intent_vector: Sequence[float],
    candidates: Iterable[ClusterCandidate],
    *,
    limit: int,
    dimension: int,
) -> tuple[ScoredCandidate, ...]:
    if not valid_vector(intent_vector, dimension):
        return ()
    scored: list[ScoredCandidate] = []
    for candidate in candidates:
        if not valid_vector(candidate.vector, dimension):
            continue
        score = cosine(intent_vector, candidate.vector)
        if score is not None:
            scored.append(ScoredCandidate(candidate=candidate, score=score))
    scored.sort(key=lambda item: (-item.score, item.candidate.cluster_id))
    return tuple(scored[:limit])


def decide(
    intent: IntentDraft,
    scored: Sequence[ScoredCandidate],
    *,
    thresholds: MatchThresholds,
    total_active_clusters: int,
    max_clusters: int,
) -> MatchDecision:
    """先约束过滤再比较分数；容量超限转 capacity_review，不静默截断."""
    compatible = [item for item in scored if constraints_compatible(intent, item.candidate)]
    if total_active_clusters >= max_clusters:
        if compatible:
            best = compatible[0]
            second_score = compatible[1].score if len(compatible) > 1 else 0.0
            if (
                best.score >= thresholds.similarity
                and best.score - second_score >= thresholds.margin
            ):
                return MatchDecision(
                    "match",
                    best.candidate.cluster_id,
                    best.score,
                    best.score - second_score,
                    "capacity_exact_match",
                    tuple(compatible[:10]),
                )
        return MatchDecision(
            "capacity_review",
            None,
            compatible[0].score if compatible else None,
            None,
            "cluster_capacity_exceeded",
            tuple(scored[:10]),
        )
    if not compatible:
        return MatchDecision("new", None, None, None, "no_compatible_cluster", tuple(scored[:10]))
    best = compatible[0]
    second_score = compatible[1].score if len(compatible) > 1 else 0.0
    margin = best.score - second_score
    if best.score >= thresholds.similarity and margin >= thresholds.margin:
        return MatchDecision(
            "match",
            best.candidate.cluster_id,
            best.score,
            margin,
            "similarity_and_margin",
            tuple(compatible[:10]),
        )
    if best.score >= thresholds.review_low:
        return MatchDecision(
            "review",
            None,
            best.score,
            margin,
            "near_threshold",
            tuple(compatible[:10]),
        )
    return MatchDecision(
        "new", None, best.score, margin, "below_review_low", tuple(compatible[:10])
    )


def canonical_cluster_name(intent: IntentDraft) -> str:
    """安全、可读且不含城市/日期/账号原句的标准问题名."""
    base = f"{action_label(intent.action)}{object_label(intent.object)}"
    if intent.negation:
        return f"{base}（否定）"
    return base[:80]


def constraints_from_intent(intent: IntentDraft) -> dict[str, Any]:
    return {
        "action": intent.action,
        "object": intent.object,
        "time_kind": intent.time_kind,
        "negation": intent.negation,
        "slot_keys": sorted(key for key, value in intent.slots.items() if value is not None),
    }
