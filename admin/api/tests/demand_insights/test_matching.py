"""纯函数：向量边界、约束复核、容量与稳定命名."""

from __future__ import annotations

from app.demand_insights.matching import (
    ClusterCandidate,
    MatchThresholds,
    canonical_cluster_name,
    constraints_compatible,
    cosine,
    decide,
    top_candidates,
    valid_vector,
)
from app.demand_insights.normalization import IntentDraft


def _intent(action="query", obj="air_quality", time_kind="point", negation=False) -> IntentDraft:
    return IntentDraft(
        text="查北京今天空气质量",
        summary="查询空气质量",
        action=action,
        object=obj,
        time_kind=time_kind,
        negation=negation,
        slots={"city": "北京"},
        confidence=0.9,
    )


def _candidate(
    cluster_id,
    action="query",
    obj="air_quality",
    time_kind="point",
    vector=(1.0, 0.0),
    negation=False,
) -> ClusterCandidate:
    return ClusterCandidate(
        cluster_id=cluster_id,
        canonical_name="查询空气质量",
        action=action,
        object=obj,
        time_kind=time_kind,
        negation=negation,
        vector=vector,
    )


def test_cosine_rejects_dimension_and_zero() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0], [1.0, 0.0]) is None
    assert cosine([0.0, 0.0], [1.0, 0.0]) is None
    assert cosine([1.0, float("nan")], [1.0, 0.0]) is None


def test_vector_validation() -> None:
    assert valid_vector((1.0, 0.0), 2)
    assert not valid_vector((1.0,), 2)
    assert not valid_vector((0.0, 0.0), 2)
    assert not valid_vector((1.0, float("inf")), 2)


def test_query_and_cause_are_not_compatible() -> None:
    intent = _intent(action="cause", obj="pollution_source")
    assert not constraints_compatible(intent, _candidate("q"))
    assert constraints_compatible(_intent(), _candidate("q"))


def test_negation_must_match() -> None:
    assert not constraints_compatible(_intent(negation=True), _candidate("q"))


def test_decide_match_review_new_and_capacity() -> None:
    thresholds = MatchThresholds(similarity=0.9, margin=0.03, review_low=0.8)
    candidates = top_candidates(
        (1.0, 0.0), [_candidate("a", vector=(1.0, 0.0))], limit=10, dimension=2
    )
    assert (
        decide(
            _intent(),
            candidates,
            thresholds=thresholds,
            total_active_clusters=1,
            max_clusters=2000,
        ).kind
        == "match"
    )
    near = top_candidates((1.0, 0.0), [_candidate("a", vector=(0.85, 0.5))], limit=10, dimension=2)
    assert decide(
        _intent(),
        near,
        thresholds=thresholds,
        total_active_clusters=1,
        max_clusters=2000,
    ).kind in ("review", "new")
    empty = decide(
        _intent(),
        [],
        thresholds=thresholds,
        total_active_clusters=2000,
        max_clusters=2000,
    )
    assert empty.kind == "capacity_review"
    low = top_candidates((1.0, 0.0), [_candidate("a", vector=(0.85, 0.5))], limit=10, dimension=2)
    capacity = decide(
        _intent(),
        low,
        thresholds=thresholds,
        total_active_clusters=2000,
        max_clusters=2000,
    )
    assert capacity.kind == "capacity_review"


def test_topk_is_bounded_and_sorted() -> None:
    scored = top_candidates(
        (1.0, 0.0),
        [
            _candidate("a", vector=(1.0, 0.0)),
            _candidate("b", vector=(0.8, 0.6)),
            _candidate("c", vector=(0.0, 1.0)),
        ],
        limit=2,
        dimension=2,
    )
    assert [item.candidate.cluster_id for item in scored] == ["a", "b"]


def test_canonical_name_does_not_include_city_or_date() -> None:
    intent = _intent()
    name = canonical_cluster_name(intent)
    assert "北京" not in name
    assert "今天" not in name
