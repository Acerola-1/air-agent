"""本域持久化：profile/候选簇/意图/归属/主题.

所有函数在调用方事务内执行；worker 在每个任务内先设置 tenant/worker GUC。
不在此模块发起模型外呼，避免持事务等待外部服务。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.demand_insights.config import InsightsSettings
from app.demand_insights.errors import InsightsConflict, InsightsNotFound
from app.demand_insights.matching import (
    ClusterCandidate,
    canonical_cluster_name,
    constraints_from_intent,
)
from app.demand_insights.normalization import IntentDraft

TOPICS: tuple[tuple[str, str], ...] = (
    ("air_quality_query", "空气质量查询"),
    ("pollution_cause", "污染成因"),
    ("forecast", "空气质量预测"),
    ("health_advice", "健康建议"),
    ("other", "其他需求"),
)


def topic_code_for(action: str, object_name: str) -> str:
    if action == "cause" or object_name == "pollution_source":
        return "pollution_cause"
    if action == "forecast":
        return "forecast"
    if action == "advice":
        return "health_advice"
    if action in ("query", "trend"):
        return "air_quality_query"
    return "other"


async def get_profile_state(session: AsyncSession, *, tenant_id: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT p.id AS id, s.active_profile_id, s.assignment_revision, s.revision,
                       p.profile_key, p.profile_version, p.state, p.normalizer_version,
                       p.analysis_version, p.embedding_profile, p.embedding_model,
                       p.embedding_dimension, p.llm_profile, p.thresholds, p.policy,
                       p.calibration
                FROM agent_insight_profile_state s
                JOIN agent_insight_profiles p
                  ON p.id=s.active_profile_id AND p.tenant_id=s.tenant_id
                WHERE s.tenant_id=:tenant
                """
                ),
                {"tenant": tenant_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def get_profile(
    session: AsyncSession, *, tenant_id: str, profile_id: uuid.UUID
) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT id, profile_key, profile_version, state, normalizer_version,
                       analysis_version, embedding_profile, embedding_model,
                       embedding_dimension, llm_profile, thresholds, policy, calibration,
                       revision, activated_at
                FROM agent_insight_profiles
                WHERE tenant_id=:tenant AND id=:id
                """
                ),
                {"tenant": tenant_id, "id": profile_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise InsightsNotFound("分析版本不存在", code="profile_not_found")
    return dict(row)


async def ensure_default_profile(
    session: AsyncSession,
    *,
    tenant_id: str,
    settings: InsightsSettings,
    embedding_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """首次启用时创建不可变默认 profile + 主题；重复调用幂等."""
    state = await get_profile_state(session, tenant_id=tenant_id)
    if state is not None:
        return state
    embedding = embedding_override or {
        "profile": settings.embedding_profile,
        "model": settings.embedding_model,
        "dimension": settings.embedding_dimension,
    }
    if not embedding.get("model") or not embedding.get("dimension"):
        from app.demand_insights.model_gateway import ModelNotConfigured

        raise ModelNotConfigured("Embedding 未配置，不能创建分析版本")
    thresholds = {
        "similarity": settings.similarity_threshold,
        "margin": settings.margin_threshold,
        "review_low": settings.review_low,
    }
    policy = {
        "max_candidates": settings.max_candidates,
        "max_clusters": settings.max_clusters,
        "max_intents": settings.max_intents,
        "max_input_chars": settings.max_input_chars,
        "max_context_chars": settings.max_context_chars,
        "context_messages": settings.context_messages,
        "normalizer_version": settings.normalizer_version,
    }
    profile_id = uuid.uuid4()
    now = dt.datetime.now(dt.UTC)
    await session.execute(
        text(
            """
            INSERT INTO agent_insight_profiles
                (id, tenant_id, profile_key, profile_version, state, normalizer_version,
                 analysis_version, embedding_profile, embedding_model, embedding_dimension,
                 llm_profile, thresholds, policy, calibration, revision, created_at,
                 updated_at)
            VALUES (:id, :tenant, :key, 1, 'draft', :normalizer, :analysis,
                    :embedding_profile, :embedding_model, :dimension, :llm_profile,
                    CAST(:thresholds AS jsonb), CAST(:policy AS jsonb), '{}'::jsonb, 0,
                    :now, :now)
            """
        ),
        {
            "id": profile_id,
            "tenant": tenant_id,
            "key": settings.default_profile_key,
            "normalizer": settings.normalizer_version,
            "analysis": settings.default_analysis_version,
            "embedding_profile": embedding["profile"],
            "embedding_model": embedding["model"],
            "dimension": embedding["dimension"],
            "llm_profile": settings.llm_profile if settings.llm_configured else None,
            "thresholds": __import__("json").dumps(thresholds, ensure_ascii=False),
            "policy": __import__("json").dumps(policy, ensure_ascii=False),
            "now": now,
        },
    )
    await _seed_topics(session, tenant_id=tenant_id, profile_id=profile_id, now=now)
    await session.execute(
        text(
            """
            INSERT INTO agent_insight_profile_state
                (tenant_id, active_profile_id, assignment_revision, revision, updated_at)
            VALUES (:tenant, :profile, 0, 1, :now)
            ON CONFLICT (tenant_id) DO UPDATE SET
                active_profile_id=EXCLUDED.active_profile_id,
                revision=agent_insight_profile_state.revision + 1,
                updated_at=now()
            """
        ),
        {"tenant": tenant_id, "profile": profile_id, "now": now},
    )
    await session.execute(
        text(
            "UPDATE agent_insight_profiles SET state='active', revision=revision+1,"
            " activated_at=:now, updated_at=:now WHERE tenant_id=:tenant AND id=:id"
        ),
        {"tenant": tenant_id, "id": profile_id, "now": now},
    )
    state = await get_profile_state(session, tenant_id=tenant_id)
    assert state is not None
    return state


async def _seed_topics(
    session: AsyncSession, *, tenant_id: str, profile_id: uuid.UUID, now: dt.datetime
) -> None:
    for code, name in TOPICS:
        await session.execute(
            text(
                """
                INSERT INTO agent_question_topics
                    (id, tenant_id, profile_id, code, name_zh, status, revision,
                     created_at, updated_at)
                VALUES (gen_random_uuid(), :tenant, :profile, :code, :name, 'active', 0,
                        :now, :now)
                ON CONFLICT (tenant_id, profile_id, code) DO NOTHING
                """
            ),
            {
                "tenant": tenant_id,
                "profile": profile_id,
                "code": code,
                "name": name,
                "now": now,
            },
        )


async def load_topic_map(
    session: AsyncSession, *, tenant_id: str, profile_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT code, id FROM agent_question_topics"
                    " WHERE tenant_id=:tenant AND profile_id=:profile AND status='active'"
                ),
                {"tenant": tenant_id, "profile": profile_id},
            )
        )
        .mappings()
        .all()
    )
    return {row["code"]: row["id"] for row in rows}


async def load_candidates(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile: dict[str, Any],
    max_clusters: int,
) -> tuple[list[ClusterCandidate], int]:
    total = int(
        await session.scalar(
            text(
                """
                SELECT count(*) FROM agent_question_clusters
                WHERE tenant_id=:tenant AND profile_id=:profile
                  AND status IN ('active','candidate') AND merged_into IS NULL
                """
            ),
            {"tenant": tenant_id, "profile": profile["id"]},
        )
        or 0
    )
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT c.id, c.canonical_name, c.action, c.object, c.time_kind,
                       c.negation, c.revision, c.status, v.prototype, v.dimension
                FROM agent_question_clusters c
                JOIN agent_question_cluster_vectors v
                  ON v.cluster_id=c.id AND v.tenant_id=c.tenant_id
                WHERE c.tenant_id=:tenant AND c.profile_id=:profile
                  AND c.status IN ('active','candidate') AND c.merged_into IS NULL
                  AND v.dimension=:dimension
                ORDER BY c.created_at, c.id
                LIMIT :limit
                """
                ),
                {
                    "tenant": tenant_id,
                    "profile": profile["id"],
                    "dimension": profile["embedding_dimension"],
                    "limit": max_clusters,
                },
            )
        )
        .mappings()
        .all()
    )
    candidates: list[ClusterCandidate] = []
    for row in rows:
        prototype = row["prototype"]
        if not prototype or len(prototype) != profile["embedding_dimension"]:
            continue
        candidates.append(
            ClusterCandidate(
                cluster_id=str(row["id"]),
                canonical_name=row["canonical_name"],
                action=row["action"],
                object=row["object"],
                time_kind=row["time_kind"],
                negation=bool(row["negation"]),
                vector=tuple(float(value) for value in prototype),
                revision=int(row["revision"]),
                status=row["status"],
            )
        )
    return candidates, total


async def next_assignment_revision(session: AsyncSession, *, tenant_id: str) -> int:
    value = await session.scalar(
        text(
            """
            UPDATE agent_insight_profile_state
               SET assignment_revision=assignment_revision+1,
                   revision=revision+1,
                   updated_at=now()
             WHERE tenant_id=:tenant
            RETURNING assignment_revision
            """
        ),
        {"tenant": tenant_id},
    )
    if value is None:
        raise InsightsConflict("分析状态未初始化", code="profile_not_initialized")
    return int(value)


async def follow_merge(
    session: AsyncSession, *, tenant_id: str, cluster_id: uuid.UUID
) -> dict[str, Any]:
    """跟随 merged_into 重定向（最多 10 跳，检测到环即拒绝）."""
    seen: set[uuid.UUID] = set()
    current = cluster_id
    for _ in range(10):
        if current in seen:
            raise InsightsConflict("簇合并关系存在环", code="merge_cycle")
        seen.add(current)
        row = (
            (
                await session.execute(
                    text(
                        """
                        SELECT id, status, merged_into, revision, profile_id
                        FROM agent_question_clusters
                        WHERE tenant_id=:tenant AND id=:id
                        """
                    ),
                    {"tenant": tenant_id, "id": current},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise InsightsNotFound("标准问题不存在", code="cluster_not_found")
        if row["merged_into"] is None:
            return dict(row)
        current = row["merged_into"]
    raise InsightsConflict("簇合并链过深", code="merge_cycle")


async def save_intent(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile: dict[str, Any],
    source: dict[str, Any],
    draft: IntentDraft,
    vector: tuple[float, ...],
    content_expiry: dt.datetime,
    policy: dict[str, Any],
    now: dt.datetime | None = None,
) -> uuid.UUID:
    moment = now or dt.datetime.now(dt.UTC)
    intent_id = uuid.uuid4()
    inserted = await session.scalar(
        text(
            """
            INSERT INTO agent_question_intents
                (id, tenant_id, source_id, request_id, user_id, split_index, action, object,
                 time_kind, negation, slots, confidence, normalizer_version, analysis_version,
                 embedding_profile, embedding_model, embedding_dimension, content_expiry,
                 policy, created_at, updated_at)
            VALUES (:id, :tenant, :source, :request, :user, :split, :action, :object,
                    :time_kind, :negation, CAST(:slots AS jsonb), :confidence, :normalizer,
                    :analysis, :embedding_profile, :embedding_model, :dimension, :expiry,
                    CAST(:policy AS jsonb), :now, :now)
            ON CONFLICT (source_id, split_index, analysis_version) DO UPDATE SET
                updated_at=agent_question_intents.updated_at
            RETURNING id
            """
        ),
        {
            "id": intent_id,
            "tenant": tenant_id,
            "source": source["id"],
            "request": source["request_id"],
            "user": source["user_id"],
            "split": draft.split_index,
            "action": draft.action,
            "object": draft.object,
            "time_kind": draft.time_kind,
            "negation": draft.negation,
            "slots": __import__("json").dumps(draft.slots, ensure_ascii=False),
            "confidence": round(draft.confidence, 4),
            "normalizer": policy.get("normalizer_version", "qdi-normalizer-v1"),
            "analysis": profile["analysis_version"],
            "embedding_profile": profile["embedding_profile"],
            "embedding_model": profile["embedding_model"],
            "dimension": profile["embedding_dimension"],
            "expiry": content_expiry,
            "policy": __import__("json").dumps(policy, ensure_ascii=False),
            "now": moment,
        },
    )
    if inserted is None:
        raise InsightsConflict("意图保存失败", code="intent_not_saved")
    await session.execute(
        text(
            """
            INSERT INTO agent_question_intent_contents
                (intent_id, tenant_id, normalized_text, summary, embedding,
                 embedding_dimension, created_at)
            VALUES (:id, :tenant, :text, :summary, CAST(:embedding AS real[]), :dimension, :now)
            ON CONFLICT (intent_id, tenant_id) DO UPDATE SET
                normalized_text=EXCLUDED.normalized_text,
                summary=EXCLUDED.summary,
                embedding=EXCLUDED.embedding,
                embedding_dimension=EXCLUDED.embedding_dimension
            """
        ),
        {
            "id": inserted,
            "tenant": tenant_id,
            "text": draft.text[:2000],
            "summary": draft.summary[:120],
            "embedding": [float(value) for value in vector],
            "dimension": len(vector),
            "now": moment,
        },
    )
    return inserted


async def create_cluster(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile: dict[str, Any],
    draft: IntentDraft,
    vector: tuple[float, ...],
    intent_id: uuid.UUID,
    topic_id: uuid.UUID | None,
    status: str = "candidate",
    now: dt.datetime | None = None,
) -> uuid.UUID:
    moment = now or dt.datetime.now(dt.UTC)
    cluster_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO agent_question_clusters
                (id, tenant_id, profile_id, topic_id, canonical_name, action, object,
                 time_kind, negation, constraints, status, revision,
                 representative_intent_id, created_at, updated_at)
            VALUES (:id, :tenant, :profile, :topic, :name, :action, :object, :time_kind,
                    :negation, CAST(:constraints AS jsonb), :status, 0, :intent, :now, :now)
            """
        ),
        {
            "id": cluster_id,
            "tenant": tenant_id,
            "profile": profile["id"],
            "topic": topic_id,
            "name": canonical_cluster_name(draft),
            "action": draft.action,
            "object": draft.object,
            "time_kind": draft.time_kind,
            "negation": draft.negation,
            "constraints": __import__("json").dumps(
                constraints_from_intent(draft), ensure_ascii=False
            ),
            "status": status,
            "intent": intent_id,
            "now": moment,
        },
    )
    await session.execute(
        text(
            """
            INSERT INTO agent_question_cluster_vectors
                (cluster_id, tenant_id, prototype, dimension, updated_at)
            VALUES (:id, :tenant, CAST(:vector AS real[]), :dimension, :now)
            """
        ),
        {
            "id": cluster_id,
            "tenant": tenant_id,
            "vector": [float(value) for value in vector],
            "dimension": len(vector),
            "now": moment,
        },
    )
    return cluster_id


async def refresh_cluster_prototype(
    session: AsyncSession, *, tenant_id: str, cluster_id: uuid.UUID
) -> None:
    """从存活意图重算原型；没有剩余来源时清空原型并 retire."""
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT ic.embedding, i.embedding_dimension
                FROM agent_question_assignments a
                JOIN agent_question_intents i
                  ON i.id=a.intent_id AND i.tenant_id=a.tenant_id
                JOIN agent_question_intent_contents ic
                  ON ic.intent_id=i.id AND ic.tenant_id=i.tenant_id
                WHERE a.tenant_id=:tenant AND a.cluster_id=:cluster AND a.status='active'
                """
                ),
                {"tenant": tenant_id, "cluster": cluster_id},
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        await session.execute(
            text(
                """
                UPDATE agent_question_clusters
                   SET status='retired', revision=revision+1, updated_at=now()
                 WHERE tenant_id=:tenant AND id=:cluster AND status <> 'merged'
                """
            ),
            {"tenant": tenant_id, "cluster": cluster_id},
        )
        await session.execute(
            text(
                "DELETE FROM agent_question_cluster_vectors"
                " WHERE tenant_id=:tenant AND cluster_id=:cluster"
            ),
            {"tenant": tenant_id, "cluster": cluster_id},
        )
        return
    dimension = int(rows[0]["embedding_dimension"])
    totals = [0.0] * dimension
    valid = 0
    for row in rows:
        vector = row["embedding"]
        if not vector or len(vector) != dimension:
            continue
        for index, value in enumerate(vector):
            totals[index] += float(value)
        valid += 1
    if not valid:
        return
    prototype = [value / valid for value in totals]
    await session.execute(
        text(
            """
            INSERT INTO agent_question_cluster_vectors
                (cluster_id, tenant_id, prototype, dimension, updated_at)
            VALUES (:cluster, :tenant, CAST(:vector AS real[]), :dimension, now())
            ON CONFLICT (cluster_id, tenant_id) DO UPDATE SET
                prototype=EXCLUDED.prototype,
                dimension=EXCLUDED.dimension,
                updated_at=now()
            """
        ),
        {
            "cluster": cluster_id,
            "tenant": tenant_id,
            "vector": prototype,
            "dimension": dimension,
        },
    )


async def save_assignment(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    intent_id: uuid.UUID,
    cluster_id: uuid.UUID | None,
    unclassified_reason: str | None,
    confidence: float,
    method: str,
    assignment_revision: int,
    now: dt.datetime | None = None,
) -> uuid.UUID:
    moment = now or dt.datetime.now(dt.UTC)
    await session.execute(
        text(
            """
            UPDATE agent_question_assignments
               SET status='superseded'
             WHERE tenant_id=:tenant AND intent_id=:intent AND profile_id=:profile
               AND status='active'
            """
        ),
        {"tenant": tenant_id, "intent": intent_id, "profile": profile_id},
    )
    assignment_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO agent_question_assignments
                (id, tenant_id, profile_id, intent_id, cluster_id, unclassified_reason,
                 confidence, method, assignment_revision, status, created_at)
            VALUES (:id, :tenant, :profile, :intent, :cluster, :reason, :confidence,
                    :method, :revision, 'active', :now)
            """
        ),
        {
            "id": assignment_id,
            "tenant": tenant_id,
            "profile": profile_id,
            "intent": intent_id,
            "cluster": cluster_id,
            "reason": unclassified_reason,
            "confidence": round(max(0.0, min(confidence, 1.0)), 4),
            "method": method,
            "revision": assignment_revision,
            "now": moment,
        },
    )
    return assignment_id


def hash_payload(payload: dict[str, Any]) -> str:
    import json

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def record_operation(
    session: AsyncSession,
    *,
    tenant_id: str,
    operation_id: str,
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    expected_revision: int,
    result_revision: int,
    payload_digest: str,
    result_snapshot: dict[str, Any],
    actor_user_id: uuid.UUID,
    actor_account: str,
    now: dt.datetime | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO agent_insight_operations
                (id, tenant_id, operation_id, action, target_type, target_id,
                 expected_revision, result_revision, payload_digest, result_snapshot,
                 actor_user_id, actor_account, created_at)
            VALUES (gen_random_uuid(), :tenant, :operation, :action, :target_type, :target,
                    :expected, :result, :digest, CAST(:snapshot AS jsonb), :actor,
                    :account, :now)
            ON CONFLICT (tenant_id, operation_id) DO NOTHING
            """
        ),
        {
            "tenant": tenant_id,
            "operation": operation_id,
            "action": action,
            "target_type": target_type,
            "target": target_id,
            "expected": expected_revision,
            "result": result_revision,
            "digest": payload_digest,
            "snapshot": __import__("json").dumps(result_snapshot, ensure_ascii=False),
            "actor": actor_user_id,
            "account": actor_account,
            "now": now or dt.datetime.now(dt.UTC),
        },
    )
