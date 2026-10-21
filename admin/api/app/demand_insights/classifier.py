"""单个来源的标准化 → Embedding → 有界候选 → 约束/LLM 复核 → 原子写入.

事务边界：读取来源/候选后提交；Embedding/LLM 在事务外；最终写入重新复核
来源存活、当前 profile 与候选版本，并用事务级 advisory lock 防并发重复建簇。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.demand_insights import source_reader, storage
from app.demand_insights.config import InsightsSettings
from app.demand_insights.matching import (
    MatchDecision,
    MatchThresholds,
    constraints_compatible,
    decide,
    top_candidates,
)
from app.demand_insights.model_gateway import (
    ModelCallFailed,
    ModelGateway,
    ModelNotConfigured,
    parse_intent_payload,
)
from app.demand_insights.normalization import IntentDraft, normalize_question, redact


@dataclass(frozen=True)
class SourceProcessingResult:
    source_id: uuid.UUID
    status: str
    split_count: int
    classified: int
    unclassified: int
    usage_tokens: int | None
    failure_code: str | None = None
    retryable: bool = False


def _merge_usage(current: int | None, added: int | None) -> int | None:
    if current is None or added is None:
        return None
    return current + added


async def _record_skip(
    session: AsyncSession,
    *,
    tenant_id: str,
    source_id: uuid.UUID,
    reason: str,
    retryable: bool = False,
    finalize=None,
) -> SourceProcessingResult:
    await source_reader.mark_source(
        session,
        tenant_id=tenant_id,
        source_id=source_id,
        status="skipped" if not retryable else "failed",
        failure_code=reason,
        next_attempt_at=(dt.datetime.now(dt.UTC) + dt.timedelta(seconds=60) if retryable else None),
    )
    result = SourceProcessingResult(
        source_id=source_id,
        status="failed" if retryable else "skipped",
        split_count=0,
        classified=0,
        unclassified=0,
        usage_tokens=None if retryable else 0,
        failure_code=reason,
        retryable=retryable,
    )
    if finalize is not None:
        await finalize(session, result)
    await session.commit()
    return result


async def process_source(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile: dict[str, Any],
    source: dict[str, Any],
    settings: InsightsSettings,
    gateway: ModelGateway,
    guard: Callable[[], Awaitable[None]] | None = None,
    finalize: Callable[[AsyncSession, SourceProcessingResult], Awaitable[None]] | None = None,
) -> SourceProcessingResult:
    from app.demand_insights.errors import InsightsUnavailable

    for key in (
        "embedding_profile",
        "embedding_model",
        "embedding_dimension",
        "normalizer_version",
    ):
        if profile[key] != getattr(settings, key):
            raise InsightsUnavailable(
                "部署模型与冻结分析版本不匹配", code="profile_config_mismatch"
            )
    source_id = source["id"]
    content = await source_reader.load_source_content(session, tenant_id=tenant_id, source=source)
    if content is None:
        return await _record_skip(
            session,
            tenant_id=tenant_id,
            source_id=source_id,
            reason="content_unavailable",
            retryable=False,
            finalize=finalize,
        )
    context_texts = await source_reader.load_context_texts(
        session,
        tenant_id=tenant_id,
        user_id=source["user_id"],
        thread_id=source["thread_id"],
        request_id=source["request_id"],
        before=source["admitted_at"],
        limit=settings.context_messages,
        max_chars=settings.max_context_chars,
    )
    normalized = normalize_question(
        content.text,
        context_texts=context_texts,
        max_input_chars=settings.max_input_chars,
        max_intents=settings.max_intents,
        max_context_chars=settings.max_context_chars,
        policy_version=settings.normalizer_version,
    )
    if normalized.rejected_reason in ("sensitive_content", "too_long", "empty"):
        return await _record_skip(
            session,
            tenant_id=tenant_id,
            source_id=source_id,
            reason=normalized.rejected_reason,
            finalize=finalize,
        )
    if normalized.review_reason == "too_many_intents":
        return await _record_skip(
            session,
            tenant_id=tenant_id,
            source_id=source_id,
            reason="too_many_intents",
            finalize=finalize,
        )
    if normalized.review_reason == "unresolved_context" and not settings.llm_configured:
        return await _record_skip(
            session,
            tenant_id=tenant_id,
            source_id=source_id,
            reason="unresolved_context",
            finalize=finalize,
        )

    drafts: list[IntentDraft] = list(normalized.intents)
    usage: int | None = 0
    used_llm = False
    if settings.llm_configured and (
        len(drafts) > 1
        or normalized.review_reason == "unresolved_context"
        or any(draft.action == "other" for draft in drafts)
    ):
        await session.commit()
        if guard is not None:
            await guard()
        # 只把已脱敏/拆分的文本交给辅助模型；原文可能含手机号/账号。
        safe_text = "；".join(draft.text for draft in drafts)
        safe_context: list[str] = []
        for context_text in context_texts:
            cleaned_context, _categories, context_sensitive = redact(context_text)
            if not context_sensitive:
                safe_context.append(cleaned_context)
        try:
            result = await gateway.split_intents(safe_text, safe_context)
            drafts = parse_intent_payload(result.payload, max_intents=settings.max_intents)
            usage = _merge_usage(usage, result.usage_tokens)
            used_llm = True
        except ModelCallFailed:
            if normalized.review_reason == "unresolved_context":
                return await _record_skip(
                    session,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    reason="unresolved_context",
                    finalize=finalize,
                )
        except ModelNotConfigured:
            pass

    if not settings.embedding_configured:
        raise ModelNotConfigured("Embedding 未配置，作业暂停")
    await session.commit()
    if guard is not None:
        await guard()
    try:
        embedding = await gateway.embed([draft.text for draft in drafts])
    except ModelCallFailed as exc:
        await session.rollback()
        return await _record_skip(
            session,
            tenant_id=tenant_id,
            source_id=source_id,
            reason=exc.code,
            retryable=True,
            finalize=finalize,
        )
    usage = _merge_usage(usage, embedding.usage_tokens)
    pairs = list(zip(drafts, embedding.vectors, strict=True))

    thresholds = MatchThresholds(
        similarity=float(profile["thresholds"].get("similarity", settings.similarity_threshold)),
        margin=float(profile["thresholds"].get("margin", settings.margin_threshold)),
        review_low=float(profile["thresholds"].get("review_low", settings.review_low)),
    )
    max_clusters = int(profile["policy"].get("max_clusters", settings.max_clusters))
    max_candidates = int(profile["policy"].get("max_candidates", settings.max_candidates))

    candidates, total_clusters = await storage.load_candidates(
        session, tenant_id=tenant_id, profile=profile, max_clusters=max_clusters
    )
    preliminary: list[Any] = []
    for draft, vector in pairs:
        scored = top_candidates(
            vector, candidates, limit=max_candidates, dimension=profile["embedding_dimension"]
        )
        preliminary.append(
            decide(
                draft,
                scored,
                thresholds=thresholds,
                total_active_clusters=total_clusters,
                max_clusters=max_clusters,
            )
        )
    await session.commit()

    reviews: dict[int, dict[str, Any]] = {}
    if settings.llm_configured:
        for index, decision in enumerate(preliminary):
            if decision.kind != "review":
                continue
            payload = [
                {
                    "cluster_id": item.candidate.cluster_id,
                    "canonical_name": item.candidate.canonical_name,
                    "action": item.candidate.action,
                    "object": item.candidate.object,
                    "time_kind": item.candidate.time_kind,
                    "score": round(item.score, 4),
                }
                for item in decision.candidates[:max_candidates]
            ]
            try:
                if guard is not None:
                    await guard()
                result = await gateway.review_match(drafts[index].summary, payload)
                reviews[index] = result.payload
                usage = _merge_usage(usage, result.usage_tokens)
            except (ModelCallFailed, ModelNotConfigured):
                reviews[index] = {
                    "cluster_id": None,
                    "confidence": 0.0,
                    "reason_code": "insufficient_information",
                }

    if guard is not None:
        await guard()
    topic_map = await storage.load_topic_map(session, tenant_id=tenant_id, profile_id=profile["id"])
    now = dt.datetime.now(dt.UTC)
    content_expiry = source.get("retention_until") or (now + dt.timedelta(days=90))
    policy = dict(profile["policy"])
    assignment_revision = await storage.next_assignment_revision(session, tenant_id=tenant_id)
    classified = 0
    unclassified = 0
    try:
        live = await source_reader.source_live(session, tenant_id=tenant_id, source_id=source_id)
        if live is None:
            await session.rollback()
            return SourceProcessingResult(
                source_id=source_id,
                status="skipped",
                split_count=0,
                classified=0,
                unclassified=0,
                usage_tokens=usage,
                failure_code="source_deleted",
            )
        fresh_content = await source_reader.load_source_content(
            session, tenant_id=tenant_id, source=source, now=now
        )
        if fresh_content is None:
            await session.rollback()
            return await _record_skip(
                session,
                tenant_id=tenant_id,
                source_id=source_id,
                reason="content_unavailable",
                finalize=finalize,
            )

        fresh_candidates, fresh_total = await storage.load_candidates(
            session, tenant_id=tenant_id, profile=profile, max_clusters=max_clusters
        )
        fresh_ids = {candidate.cluster_id for candidate in fresh_candidates}
        for index, (draft, vector) in enumerate(pairs):
            lock_key = "insight-cluster:" + ":".join(
                (
                    tenant_id,
                    str(profile["id"]),
                    hashlib.sha256(draft.text.encode("utf-8")).hexdigest()[:40],
                )
            )
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": lock_key},
            )
            scored = top_candidates(
                vector,
                fresh_candidates,
                limit=max_candidates,
                dimension=profile["embedding_dimension"],
            )
            decision = decide(
                draft,
                scored,
                thresholds=thresholds,
                total_active_clusters=fresh_total,
                max_clusters=max_clusters,
            )
            review = reviews.get(index)
            method = "vector_new" if used_llm else "rule"
            if (
                review
                and decision.kind == "review"
                and review.get("cluster_id") in fresh_ids
                and any(
                    candidate.cluster_id == review.get("cluster_id")
                    and constraints_compatible(draft, candidate)
                    for candidate in fresh_candidates
                )
            ):
                confidence = float(review.get("confidence") or 0.0)
                if confidence >= 0.7:
                    decision = MatchDecision(
                        "match",
                        review["cluster_id"],
                        confidence,
                        decision.margin,
                        "llm_review",
                        decision.candidates,
                    )
                    method = "llm"
            if draft.action == "other" or draft.confidence < 0.65:
                decision = MatchDecision(
                    "unclassified",
                    None,
                    decision.score,
                    decision.margin,
                    "low_confidence",
                    decision.candidates,
                )
            intent_id = await storage.save_intent(
                session,
                tenant_id=tenant_id,
                profile=profile,
                source=source,
                draft=draft,
                vector=vector,
                content_expiry=content_expiry,
                policy=policy,
                now=now,
            )
            if decision.kind == "match" and decision.cluster_id:
                target = await storage.follow_merge(
                    session,
                    tenant_id=tenant_id,
                    cluster_id=uuid.UUID(decision.cluster_id),
                )
                if str(target["profile_id"]) != str(profile["id"]) or target["status"] not in (
                    "active",
                    "candidate",
                ):
                    decision = MatchDecision(
                        "unclassified",
                        None,
                        decision.score,
                        decision.margin,
                        "profile_mismatch",
                        decision.candidates,
                    )
                else:
                    await storage.save_assignment(
                        session,
                        tenant_id=tenant_id,
                        profile_id=profile["id"],
                        intent_id=intent_id,
                        cluster_id=target["id"],
                        unclassified_reason=None,
                        confidence=decision.score or 0.0,
                        method="llm" if method == "llm" else "vector",
                        assignment_revision=assignment_revision,
                        now=now,
                    )
                    classified += 1
                    continue
            if decision.kind == "new":
                topic_code = storage.topic_code_for(draft.action, draft.object)
                cluster_id = await storage.create_cluster(
                    session,
                    tenant_id=tenant_id,
                    profile=profile,
                    draft=draft,
                    vector=vector,
                    intent_id=intent_id,
                    topic_id=topic_map.get(topic_code),
                    status="candidate",
                    now=now,
                )
                await storage.save_assignment(
                    session,
                    tenant_id=tenant_id,
                    profile_id=profile["id"],
                    intent_id=intent_id,
                    cluster_id=cluster_id,
                    unclassified_reason=None,
                    confidence=max(0.0, 1.0 - (decision.score or 0.0)) if decision.score else 0.5,
                    method=method,
                    assignment_revision=assignment_revision,
                    now=now,
                )
                classified += 1
                fresh_candidates, fresh_total = await storage.load_candidates(
                    session, tenant_id=tenant_id, profile=profile, max_clusters=max_clusters
                )
                fresh_ids = {candidate.cluster_id for candidate in fresh_candidates}
                continue
            reason = (
                "capacity_review"
                if decision.kind == "capacity_review"
                else "near_threshold"
                if decision.kind == "review"
                else decision.reason
            )
            await storage.save_assignment(
                session,
                tenant_id=tenant_id,
                profile_id=profile["id"],
                intent_id=intent_id,
                cluster_id=None,
                unclassified_reason=reason,
                confidence=decision.score or draft.confidence,
                method="rule",
                assignment_revision=assignment_revision,
                now=now,
            )
            unclassified += 1
        await source_reader.mark_source(
            session,
            tenant_id=tenant_id,
            source_id=source_id,
            status="processed",
            split_count=len(pairs),
        )
        result = SourceProcessingResult(
            source_id=source_id,
            status="processed",
            split_count=len(pairs),
            classified=classified,
            unclassified=unclassified,
            usage_tokens=usage,
        )
        if finalize is not None:
            await finalize(session, result)
        await session.commit()
        return result
    except Exception:
        await session.rollback()
        raise
