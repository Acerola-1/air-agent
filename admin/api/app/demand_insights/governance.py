"""人工维护：改名、指派、合并、拆分、版本重算与原子切换.

- 所有写操作要求 operation_id + expected_revision；同 ID 同载荷返回原结果，
  同 ID 异载荷 409；跨企业/跨 profile 404 或拒绝。
- 合并保留 source→target 重定向并检测环；拆分先签发绑定来源集合/修订/过期的
  预览 token，提交时重新校验。
- 重算建立新 profile，旧 active 保持可读；激活需重建完成且校准门禁通过。
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.demand_insights import storage
from app.demand_insights.config import InsightsSettings
from app.demand_insights.errors import (
    InsightsConflict,
    InsightsNotFound,
    InsightsPayloadInvalid,
)
from app.demand_insights.normalization import normalize_text, redact
from app.demand_insights.scope import parse_uuid
from app.services.identity import Identity

PREVIEW_TTL_SECONDS = 600


def _preview_key(settings: InsightsSettings) -> bytes:
    if not settings.internal_cursor_secret:
        raise InsightsConflict("预览签名未配置", code="preview_signing_unavailable")
    return hmac.digest(
        settings.internal_cursor_secret.encode(), b"air-agent.insights.split.v1", "sha256"
    )


def _sign_preview(payload: dict[str, Any], settings: InsightsSettings) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    body = base64.urlsafe_b64encode(raw.encode()).decode("ascii")
    signature = hmac.new(_preview_key(settings), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _verify_preview(token: str, settings: InsightsSettings) -> dict[str, Any]:
    try:
        body, signature = token.rsplit(".", 1)
        expected = hmac.new(_preview_key(settings), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("signature")
        payload = json.loads(base64.urlsafe_b64decode(body.encode("ascii")))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InsightsPayloadInvalid("预览 token 非法", code="bad_preview_token") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise InsightsPayloadInvalid("预览 token 非法", code="bad_preview_token")
    if dt.datetime.fromisoformat(payload["e"]) <= dt.datetime.now(dt.UTC):
        raise InsightsConflict("预览已过期，请重新预览", code="preview_expired")
    return payload


def _safe_name(raw: str) -> str:
    value = normalize_text(raw)
    if not value or len(value) > 80:
        raise InsightsPayloadInvalid("名称长度必须在 1..80", code="invalid_name")
    cleaned, redactions, sensitive = redact(value)
    if sensitive or redactions:
        raise InsightsPayloadInvalid("名称不能包含账号/凭据/联系方式", code="unsafe_name")
    return cleaned


async def _load_cluster(
    session: AsyncSession, *, tenant_id: str, profile_id: uuid.UUID, cluster_id: uuid.UUID
) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT id, topic_id, canonical_name, action, object, time_kind, negation,
                       constraints, status, merged_into, revision, representative_intent_id
                FROM agent_question_clusters
                WHERE tenant_id=:tenant AND profile_id=:profile AND id=:id
                """
                ),
                {"tenant": tenant_id, "profile": profile_id, "id": cluster_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise InsightsNotFound("标准问题不存在", code="cluster_not_found")
    return dict(row)


async def _active_intents(
    session: AsyncSession, *, tenant_id: str, profile_id: uuid.UUID, cluster_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT i.id, i.request_id, i.split_index, i.action, i.object, i.time_kind,
                       i.negation, i.slots, ic.embedding, ic.embedding_dimension,
                       a.id AS assignment_id
                FROM agent_question_assignments a
                JOIN agent_question_intents i
                  ON i.id=a.intent_id AND i.tenant_id=a.tenant_id
                JOIN agent_question_intent_contents ic
                  ON ic.intent_id=i.id AND ic.tenant_id=i.tenant_id
                WHERE a.tenant_id=:tenant AND a.profile_id=:profile
                  AND a.cluster_id=:cluster AND a.status='active'
                ORDER BY i.request_id, i.split_index
                """
                ),
                {"tenant": tenant_id, "profile": profile_id, "cluster": cluster_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def _operation_existing(
    session: AsyncSession, *, tenant_id: str, operation_id: str, digest: str
) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT payload_digest, result_snapshot FROM agent_insight_operations"
                    " WHERE tenant_id=:tenant AND operation_id=:operation"
                ),
                {"tenant": tenant_id, "operation": operation_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    if row["payload_digest"] != digest or row["result_snapshot"] is None:
        raise InsightsConflict("同一操作 ID 携带不同内容", code="event_conflict")
    snapshot = dict(row["result_snapshot"])
    snapshot["replayed"] = True
    return snapshot


async def question_action(
    session: AsyncSession,
    *,
    identity: Identity,
    tenant_id: str,
    profile: dict[str, Any],
    cluster_id: uuid.UUID,
    payload: dict[str, Any],
    settings: InsightsSettings,
) -> dict[str, Any]:
    action = payload.get("action")
    if action not in ("rename", "assign", "merge", "split"):
        raise InsightsPayloadInvalid("未知维护动作", code="invalid_action")
    operation_id = str(payload.get("operation_id") or "")
    if not operation_id:
        raise InsightsPayloadInvalid("operation_id 必填", code="invalid_operation_id")
    expected_revision = int(payload.get("expected_revision") or 0)
    preview = bool(payload.get("preview"))
    if action == "assign":
        # 未归类指派没有“源簇”语义；expected_revision 是归属修订，不是簇修订。
        cluster = {"id": cluster_id, "revision": expected_revision, "status": "active"}
    else:
        cluster = await _load_cluster(
            session, tenant_id=tenant_id, profile_id=profile["id"], cluster_id=cluster_id
        )
        if cluster["status"] == "merged":
            raise InsightsConflict("标准问题已合并，请使用目标 ID", code="cluster_merged")
    digest = storage.hash_payload({"cluster_id": str(cluster_id), **payload})
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"insight-operation:{tenant_id}:{operation_id}"},
    )
    existing = await _operation_existing(
        session, tenant_id=tenant_id, operation_id=operation_id, digest=digest
    )
    if existing is not None:
        return existing
    if action != "assign" and int(cluster["revision"]) != expected_revision:
        raise InsightsConflict("标准问题已变化，请刷新后重试", code="revision_conflict")
    if action == "rename":
        return await _rename(
            session,
            identity=identity,
            tenant_id=tenant_id,
            cluster=cluster,
            payload=payload,
            operation_id=operation_id,
            digest=digest,
            preview=preview,
        )
    if action == "merge":
        return await _merge(
            session,
            identity=identity,
            tenant_id=tenant_id,
            profile=profile,
            cluster=cluster,
            payload=payload,
            operation_id=operation_id,
            digest=digest,
            preview=preview,
        )
    if action == "assign":
        return await _assign(
            session,
            identity=identity,
            tenant_id=tenant_id,
            profile=profile,
            payload=payload,
            operation_id=operation_id,
            digest=digest,
            preview=preview,
        )
    return await _split(
        session,
        identity=identity,
        tenant_id=tenant_id,
        profile=profile,
        cluster=cluster,
        payload=payload,
        operation_id=operation_id,
        digest=digest,
        preview=preview,
        settings=settings,
    )


async def _rename(
    session: AsyncSession,
    *,
    identity: Identity,
    tenant_id: str,
    cluster: dict[str, Any],
    payload: dict[str, Any],
    operation_id: str,
    digest: str,
    preview: bool,
) -> dict[str, Any]:
    name = _safe_name(str(payload.get("new_name") or ""))
    if preview:
        return {
            "action": "rename",
            "preview": True,
            "cluster_id": str(cluster["id"]),
            "new_name": name,
            "expected_revision": int(cluster["revision"]),
            "replayed": False,
        }
    revision = int(cluster["revision"]) + 1
    result = await session.execute(
        text(
            "UPDATE agent_question_clusters SET canonical_name=:name, revision=:revision,"
            " updated_at=now() WHERE tenant_id=:tenant AND id=:id AND revision=:expected"
        ),
        {
            "name": name,
            "revision": revision,
            "tenant": tenant_id,
            "id": cluster["id"],
            "expected": cluster["revision"],
        },
    )
    if result.rowcount != 1:
        raise InsightsConflict("标准问题已变化，请刷新后重试", code="revision_conflict")
    snapshot = {
        "action": "rename",
        "preview": False,
        "cluster_id": str(cluster["id"]),
        "new_name": name,
        "revision": revision,
        "replayed": False,
    }
    await storage.record_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
        action="question_rename",
        target_type="cluster",
        target_id=cluster["id"],
        expected_revision=int(cluster["revision"]),
        result_revision=revision,
        payload_digest=digest,
        result_snapshot=snapshot,
        actor_user_id=identity.user.id,
        actor_account=identity.user.account,
    )
    return snapshot


async def _merge(
    session: AsyncSession,
    *,
    identity: Identity,
    tenant_id: str,
    profile: dict[str, Any],
    cluster: dict[str, Any],
    payload: dict[str, Any],
    operation_id: str,
    digest: str,
    preview: bool,
) -> dict[str, Any]:
    target_id = parse_uuid(str(payload.get("target_cluster_id") or ""), label="目标标准问题")
    if target_id == cluster["id"]:
        raise InsightsPayloadInvalid("不能合并到自身", code="invalid_merge_target")
    target = await storage.follow_merge(session, tenant_id=tenant_id, cluster_id=target_id)
    if str(target["profile_id"]) != str(profile["id"]):
        raise InsightsNotFound("目标标准问题不存在", code="cluster_not_found")
    if target["status"] not in ("active", "candidate"):
        raise InsightsConflict("目标标准问题不可用", code="invalid_merge_target")
    # 环检测：目标链不能回到源簇
    cursor = target["id"]
    for _ in range(10):
        row = (
            (
                await session.execute(
                    text(
                        "SELECT merged_into FROM agent_question_clusters"
                        " WHERE tenant_id=:tenant AND id=:id"
                    ),
                    {"tenant": tenant_id, "id": cursor},
                )
            )
            .mappings()
            .first()
        )
        if row is None or row["merged_into"] is None:
            break
        if row["merged_into"] == cluster["id"]:
            raise InsightsConflict("合并会形成环", code="merge_cycle")
        cursor = row["merged_into"]
    intents = await _active_intents(
        session, tenant_id=tenant_id, profile_id=profile["id"], cluster_id=cluster["id"]
    )
    if preview:
        return {
            "action": "merge",
            "preview": True,
            "cluster_id": str(cluster["id"]),
            "target_cluster_id": str(target["id"]),
            "affected_intents": len(intents),
            "expected_revision": int(cluster["revision"]),
            "replayed": False,
        }
    revision = int(cluster["revision"]) + 1
    assignment_revision = await storage.next_assignment_revision(session, tenant_id=tenant_id)
    now = dt.datetime.now(dt.UTC)
    for intent in intents:
        await storage.save_assignment(
            session,
            tenant_id=tenant_id,
            profile_id=profile["id"],
            intent_id=intent["id"],
            cluster_id=target["id"],
            unclassified_reason=None,
            confidence=1.0,
            method="manual",
            assignment_revision=assignment_revision,
            now=now,
        )
    result = await session.execute(
        text(
            "UPDATE agent_question_clusters SET status='merged', merged_into=:target,"
            " revision=:revision, updated_at=:now"
            " WHERE tenant_id=:tenant AND id=:id AND revision=:expected"
        ),
        {
            "target": target["id"],
            "revision": revision,
            "now": now,
            "tenant": tenant_id,
            "id": cluster["id"],
            "expected": cluster["revision"],
        },
    )
    if result.rowcount != 1:
        raise InsightsConflict("标准问题已变化，请刷新后重试", code="revision_conflict")
    await session.execute(
        text(
            "UPDATE agent_question_clusters SET revision=revision+1, updated_at=:now"
            " WHERE tenant_id=:tenant AND id=:id"
        ),
        {"now": now, "tenant": tenant_id, "id": target["id"]},
    )
    await session.execute(
        text(
            "DELETE FROM agent_question_cluster_vectors WHERE tenant_id=:tenant AND cluster_id=:id"
        ),
        {"tenant": tenant_id, "id": cluster["id"]},
    )
    await storage.refresh_cluster_prototype(session, tenant_id=tenant_id, cluster_id=target["id"])
    snapshot = {
        "action": "merge",
        "preview": False,
        "cluster_id": str(cluster["id"]),
        "target_cluster_id": str(target["id"]),
        "affected_intents": len(intents),
        "revision": revision,
        "assignment_revision": assignment_revision,
        "replayed": False,
    }
    await storage.record_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
        action="question_merge",
        target_type="cluster",
        target_id=cluster["id"],
        expected_revision=int(cluster["revision"]),
        result_revision=revision,
        payload_digest=digest,
        result_snapshot=snapshot,
        actor_user_id=identity.user.id,
        actor_account=identity.user.account,
        now=now,
    )
    return snapshot


async def _assign(
    session: AsyncSession,
    *,
    identity: Identity,
    tenant_id: str,
    profile: dict[str, Any],
    payload: dict[str, Any],
    operation_id: str,
    digest: str,
    preview: bool,
) -> dict[str, Any]:
    intent_ids = [parse_uuid(str(value), label="意图") for value in payload.get("intent_ids") or []]
    if not intent_ids or len(intent_ids) > 100:
        raise InsightsPayloadInvalid("intent_ids 必须在 1..100", code="invalid_intent_ids")
    target_raw = payload.get("target_cluster_id")
    target_id: uuid.UUID | None = None
    if target_raw:
        target = await storage.follow_merge(
            session,
            tenant_id=tenant_id,
            cluster_id=parse_uuid(str(target_raw), label="目标标准问题"),
        )
        if str(target["profile_id"]) != str(profile["id"]):
            raise InsightsNotFound("目标标准问题不存在", code="cluster_not_found")
        target_id = target["id"]
    else:
        name = _safe_name(str(payload.get("new_cluster_name") or ""))
    changed = await _validate_intents(
        session,
        tenant_id=tenant_id,
        profile_id=profile["id"],
        analysis_version=profile["analysis_version"],
        intent_ids=intent_ids,
    )
    current_assignment_revision = int(
        await session.scalar(
            text(
                "SELECT assignment_revision FROM agent_insight_profile_state"
                " WHERE tenant_id=:tenant"
            ),
            {"tenant": tenant_id},
        )
        or 0
    )
    if current_assignment_revision != int(payload.get("expected_revision") or 0):
        raise InsightsConflict("归属版本已变化，请刷新后重试", code="revision_conflict")
    if preview:
        return {
            "action": "assign",
            "preview": True,
            "intent_ids": [str(value) for value in intent_ids],
            "target_cluster_id": str(target_id) if target_id else None,
            "affected_intents": len(changed),
            "replayed": False,
        }
    now = dt.datetime.now(dt.UTC)
    assignment_revision = await storage.next_assignment_revision(session, tenant_id=tenant_id)
    if target_id is None:
        first = changed[0]
        topic_map = await storage.load_topic_map(
            session, tenant_id=tenant_id, profile_id=profile["id"]
        )
        from app.demand_insights.normalization import IntentDraft

        draft = IntentDraft(
            text="",
            summary=name,
            action=first["action"],
            object=first["object"],
            time_kind=first["time_kind"],
            negation=bool(first["negation"]),
            slots=first["slots"],
            confidence=1.0,
        )
        target_id = await storage.create_cluster(
            session,
            tenant_id=tenant_id,
            profile=profile,
            draft=draft,
            vector=tuple(float(value) for value in first["embedding"]),
            intent_id=first["id"],
            topic_id=topic_map.get(storage.topic_code_for(first["action"], first["object"])),
            status="active",
            now=now,
        )
    for intent in changed:
        await storage.save_assignment(
            session,
            tenant_id=tenant_id,
            profile_id=profile["id"],
            intent_id=intent["id"],
            cluster_id=target_id,
            unclassified_reason=None,
            confidence=1.0,
            method="manual",
            assignment_revision=assignment_revision,
            now=now,
        )
    await storage.refresh_cluster_prototype(session, tenant_id=tenant_id, cluster_id=target_id)
    snapshot = {
        "action": "assign",
        "preview": False,
        "intent_ids": [str(value) for value in intent_ids],
        "target_cluster_id": str(target_id),
        "affected_intents": len(changed),
        "assignment_revision": assignment_revision,
        "replayed": False,
    }
    await storage.record_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
        action="question_assign",
        target_type="cluster",
        target_id=target_id,
        expected_revision=int(payload.get("expected_revision") or 0),
        result_revision=assignment_revision,
        payload_digest=digest,
        result_snapshot=snapshot,
        actor_user_id=identity.user.id,
        actor_account=identity.user.account,
        now=now,
    )
    return snapshot


async def _validate_intents(
    session: AsyncSession,
    *,
    tenant_id: str,
    profile_id: uuid.UUID,
    analysis_version: str,
    intent_ids: list[uuid.UUID],
) -> list[dict[str, Any]]:
    placeholders = ", ".join(f":id{index}" for index in range(len(intent_ids)))
    params = {f"id{index}": value for index, value in enumerate(intent_ids)}
    params.update({"tenant": tenant_id, "profile": profile_id, "av": analysis_version})
    rows = (
        (
            await session.execute(
                text(
                    f"""
                SELECT i.id, i.action, i.object, i.time_kind, i.negation, i.slots,
                       ic.embedding, ic.embedding_dimension
                FROM agent_question_intents i
                JOIN agent_question_intent_contents ic
                  ON ic.intent_id=i.id AND ic.tenant_id=i.tenant_id
                JOIN agent_question_assignments a
                  ON a.intent_id=i.id AND a.tenant_id=i.tenant_id
                 AND a.profile_id=:profile AND a.status='active'
                WHERE i.tenant_id=:tenant AND i.analysis_version=:av
                  AND i.id IN ({placeholders})
                """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != len(intent_ids):
        raise InsightsNotFound("部分意图不存在或不属于当前分析版本", code="intent_not_found")
    return [dict(row) for row in rows]


async def _split(
    session: AsyncSession,
    *,
    identity: Identity,
    tenant_id: str,
    profile: dict[str, Any],
    cluster: dict[str, Any],
    payload: dict[str, Any],
    operation_id: str,
    digest: str,
    preview: bool,
    settings: InsightsSettings,
) -> dict[str, Any]:
    groups = payload.get("groups")
    if not isinstance(groups, list) or len(groups) < 2 or len(groups) > 10:
        raise InsightsPayloadInvalid("拆分至少 2 组且最多 10 组", code="invalid_split_groups")
    normalized_groups: list[dict[str, Any]] = []
    all_ids: list[uuid.UUID] = []
    for group in groups:
        if not isinstance(group, dict):
            raise InsightsPayloadInvalid("拆分组格式非法", code="invalid_split_groups")
        name = _safe_name(str(group.get("name") or ""))
        ids = [parse_uuid(str(value), label="意图") for value in group.get("intent_ids") or []]
        if not ids:
            raise InsightsPayloadInvalid("拆分组不能为空", code="invalid_split_groups")
        normalized_groups.append({"name": name, "intent_ids": ids})
        all_ids.extend(ids)
    if len(set(all_ids)) != len(all_ids):
        raise InsightsPayloadInvalid("拆分意图不能重复分配", code="invalid_split_groups")
    source_intents = await _active_intents(
        session, tenant_id=tenant_id, profile_id=profile["id"], cluster_id=cluster["id"]
    )
    source_ids = {row["id"] for row in source_intents}
    if set(all_ids) != source_ids:
        raise InsightsPayloadInvalid("拆分必须覆盖全部来源意图", code="split_sources_incomplete")
    assignment_revision = int(
        await session.scalar(
            text(
                "SELECT assignment_revision FROM agent_insight_profile_state"
                " WHERE tenant_id=:tenant"
            ),
            {"tenant": tenant_id},
        )
        or 0
    )
    if preview:
        token = _sign_preview(
            {
                "v": 1,
                "t": tenant_id,
                "p": str(profile["id"]),
                "c": str(cluster["id"]),
                "r": int(cluster["revision"]),
                "a": assignment_revision,
                "ids": sorted(str(value) for value in source_ids),
                "groups": [
                    {
                        "name": group["name"],
                        "ids": sorted(str(value) for value in group["intent_ids"]),
                    }
                    for group in normalized_groups
                ],
                "e": (
                    dt.datetime.now(dt.UTC) + dt.timedelta(seconds=PREVIEW_TTL_SECONDS)
                ).isoformat(),
            },
            settings,
        )
        return {
            "action": "split",
            "preview": True,
            "preview_token": token,
            "cluster_id": str(cluster["id"]),
            "groups": [
                {"name": group["name"], "intent_count": len(group["intent_ids"])}
                for group in normalized_groups
            ],
            "expected_revision": int(cluster["revision"]),
            "replayed": False,
        }
    token = _verify_preview(str(payload.get("preview_token") or ""), settings)
    if (
        token.get("t") != tenant_id
        or token.get("p") != str(profile["id"])
        or token.get("c") != str(cluster["id"])
        or int(token.get("r", -1)) != int(cluster["revision"])
        or int(token.get("a", -1)) != assignment_revision
        or token.get("ids") != sorted(str(value) for value in source_ids)
    ):
        raise InsightsConflict("拆分预览已失效，请重新预览", code="preview_stale")
    now = dt.datetime.now(dt.UTC)
    next_revision = await storage.next_assignment_revision(session, tenant_id=tenant_id)
    by_id = {row["id"]: row for row in source_intents}
    topic_map = await storage.load_topic_map(session, tenant_id=tenant_id, profile_id=profile["id"])
    created: list[dict[str, Any]] = []
    from app.demand_insights.normalization import IntentDraft

    for index, group in enumerate(normalized_groups):
        first = by_id[group["intent_ids"][0]]
        if index == 0:
            target_id = cluster["id"]
            await session.execute(
                text(
                    "UPDATE agent_question_clusters SET canonical_name=:name,"
                    " revision=revision+1, updated_at=:now"
                    " WHERE tenant_id=:tenant AND id=:id"
                ),
                {"name": group["name"], "now": now, "tenant": tenant_id, "id": target_id},
            )
        else:
            draft = IntentDraft(
                text="",
                summary=group["name"],
                action=first["action"],
                object=first["object"],
                time_kind=first["time_kind"],
                negation=bool(first["negation"]),
                slots=first["slots"],
                confidence=1.0,
            )
            target_id = await storage.create_cluster(
                session,
                tenant_id=tenant_id,
                profile=profile,
                draft=draft,
                vector=tuple(float(value) for value in first["embedding"]),
                intent_id=first["id"],
                topic_id=topic_map.get(storage.topic_code_for(first["action"], first["object"])),
                status="active",
                now=now,
            )
        for intent_id in group["intent_ids"]:
            await storage.save_assignment(
                session,
                tenant_id=tenant_id,
                profile_id=profile["id"],
                intent_id=intent_id,
                cluster_id=target_id,
                unclassified_reason=None,
                confidence=1.0,
                method="manual",
                assignment_revision=next_revision,
                now=now,
            )
        await storage.refresh_cluster_prototype(session, tenant_id=tenant_id, cluster_id=target_id)
        created.append(
            {
                "cluster_id": str(target_id),
                "name": group["name"],
                "intent_count": len(group["intent_ids"]),
            }
        )
    snapshot = {
        "action": "split",
        "preview": False,
        "cluster_id": str(cluster["id"]),
        "groups": created,
        "assignment_revision": next_revision,
        "replayed": False,
    }
    await storage.record_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
        action="question_split",
        target_type="cluster",
        target_id=cluster["id"],
        expected_revision=int(cluster["revision"]),
        result_revision=next_revision,
        payload_digest=digest,
        result_snapshot=snapshot,
        actor_user_id=identity.user.id,
        actor_account=identity.user.account,
        now=now,
    )
    return snapshot


async def rebuild_profile(
    session: AsyncSession,
    *,
    identity: Identity,
    tenant_id: str,
    profile: dict[str, Any],
    operation_id: str,
    expected_revision: int,
    settings: InsightsSettings,
) -> dict[str, Any]:
    state_revision = int(
        await session.scalar(
            text("SELECT revision FROM agent_insight_profile_state WHERE tenant_id=:tenant"),
            {"tenant": tenant_id},
        )
        or 0
    )
    if state_revision != expected_revision:
        raise InsightsConflict("分析状态已变化，请刷新后重试", code="revision_conflict")
    digest = storage.hash_payload(
        {
            "profile_id": str(profile["id"]),
            "action": "rebuild",
            "expected_revision": expected_revision,
        }
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"insight-operation:{tenant_id}:{operation_id}"},
    )
    existing = await _operation_existing(
        session, tenant_id=tenant_id, operation_id=operation_id, digest=digest
    )
    if existing is not None:
        return existing
    next_version = int(
        await session.scalar(
            text(
                "SELECT COALESCE(max(profile_version),0)+1 FROM agent_insight_profiles"
                " WHERE tenant_id=:tenant AND profile_key=:key"
            ),
            {"tenant": tenant_id, "key": profile["profile_key"]},
        )
        or 1
    )
    new_profile_id = uuid.uuid4()
    now = dt.datetime.now(dt.UTC)
    analysis_version = f"{profile['analysis_version']}-r{next_version}"
    await session.execute(
        text(
            """
            INSERT INTO agent_insight_profiles
                (id, tenant_id, profile_key, profile_version, state, normalizer_version,
                 analysis_version, embedding_profile, embedding_model, embedding_dimension,
                 llm_profile, thresholds, policy, calibration, revision, created_at,
                 updated_at)
            VALUES (:id, :tenant, :key, :version, 'draft', :normalizer, :analysis,
                    :embedding_profile, :embedding_model, :dimension, :llm_profile,
                    CAST(:thresholds AS jsonb), CAST(:policy AS jsonb), '{}'::jsonb, 0,
                    :now, :now)
            """
        ),
        {
            "id": new_profile_id,
            "tenant": tenant_id,
            "key": profile["profile_key"],
            "version": next_version,
            "normalizer": settings.normalizer_version,
            "analysis": analysis_version,
            "embedding_profile": profile["embedding_profile"],
            "embedding_model": profile["embedding_model"],
            "dimension": profile["embedding_dimension"],
            "llm_profile": profile.get("llm_profile"),
            "thresholds": json.dumps(profile["thresholds"], ensure_ascii=False),
            "policy": json.dumps(profile["policy"], ensure_ascii=False),
            "now": now,
        },
    )
    for code, name in storage.TOPICS:
        await session.execute(
            text(
                "INSERT INTO agent_question_topics"
                " (id, tenant_id, profile_id, code, name_zh, status, revision,"
                "  created_at, updated_at)"
                " VALUES (gen_random_uuid(), :tenant, :profile, :code, :name, 'active', 0,"
                "         :now, :now)"
                " ON CONFLICT (tenant_id, profile_id, code) DO NOTHING"
            ),
            {
                "tenant": tenant_id,
                "profile": new_profile_id,
                "code": code,
                "name": name,
                "now": now,
            },
        )
    snapshot = {
        "action": "rebuild",
        "profile_id": str(new_profile_id),
        "analysis_version": analysis_version,
        "state_revision": state_revision,
        "replayed": False,
    }
    await storage.record_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
        action="profile_rebuild",
        target_type="profile",
        target_id=new_profile_id,
        expected_revision=expected_revision,
        result_revision=state_revision,
        payload_digest=digest,
        result_snapshot=snapshot,
        actor_user_id=identity.user.id,
        actor_account=identity.user.account,
        now=now,
    )
    return snapshot


async def activate_profile(
    session: AsyncSession,
    *,
    identity: Identity,
    tenant_id: str,
    profile: dict[str, Any],
    operation_id: str,
    expected_revision: int,
    settings: InsightsSettings,
) -> dict[str, Any]:
    state = await storage.get_profile_state(session, tenant_id=tenant_id)
    if state is None or int(state["revision"]) != expected_revision:
        raise InsightsConflict("分析状态已变化，请刷新后重试", code="revision_conflict")
    if str(state["active_profile_id"]) == str(profile["id"]):
        raise InsightsConflict("该分析版本已是当前版本", code="already_active")
    calibration = profile.get("calibration") or {}
    if not calibration.get("passed"):
        raise InsightsConflict("新分析版本尚未通过校准门禁", code="calibration_required")
    if (
        float(calibration.get("precision", 0)) < settings.calibration_min_precision
        or float(calibration.get("coverage", 0)) < settings.calibration_min_coverage
        or int(calibration.get("auto_classified", 0)) < settings.calibration_min_auto
        or int(calibration.get("false_merges", 0)) > settings.calibration_max_false_merge
    ):
        raise InsightsConflict("校准结果未达到发布门槛", code="calibration_gate_failed")
    rebuild = await session.scalar(
        text(
            "SELECT 1 FROM agent_insight_jobs WHERE tenant_id=:tenant AND profile_id=:profile"
            " AND job_kind='rebuild' AND state='completed' ORDER BY completed_at DESC LIMIT 1"
        ),
        {"tenant": tenant_id, "profile": profile["id"]},
    )
    if not rebuild:
        raise InsightsConflict("重建作业尚未完成", code="rebuild_incomplete")
    digest = storage.hash_payload(
        {
            "profile_id": str(profile["id"]),
            "action": "activate",
            "expected_revision": expected_revision,
        }
    )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"insight-operation:{tenant_id}:{operation_id}"},
    )
    existing = await _operation_existing(
        session, tenant_id=tenant_id, operation_id=operation_id, digest=digest
    )
    if existing is not None:
        return existing
    now = dt.datetime.now(dt.UTC)
    new_revision = expected_revision + 1
    result = await session.execute(
        text(
            "UPDATE agent_insight_profile_state SET active_profile_id=:profile,"
            " revision=:revision, updated_at=:now"
            " WHERE tenant_id=:tenant AND revision=:expected"
        ),
        {
            "profile": profile["id"],
            "revision": new_revision,
            "now": now,
            "tenant": tenant_id,
            "expected": expected_revision,
        },
    )
    if result.rowcount != 1:
        raise InsightsConflict("分析状态已变化，请刷新后重试", code="revision_conflict")
    await session.execute(
        text(
            "UPDATE agent_insight_profiles SET state='retired', updated_at=:now"
            " WHERE tenant_id=:tenant AND state='active' AND id<>:profile"
        ),
        {"now": now, "tenant": tenant_id, "profile": profile["id"]},
    )
    await session.execute(
        text(
            "UPDATE agent_insight_profiles SET state='active', activated_at=:now,"
            " updated_at=:now WHERE tenant_id=:tenant AND id=:profile"
        ),
        {"now": now, "tenant": tenant_id, "profile": profile["id"]},
    )
    snapshot = {
        "action": "activate",
        "profile_id": str(profile["id"]),
        "state_revision": new_revision,
        "replayed": False,
    }
    await storage.record_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
        action="profile_activate",
        target_type="profile",
        target_id=profile["id"],
        expected_revision=expected_revision,
        result_revision=new_revision,
        payload_digest=digest,
        result_snapshot=snapshot,
        actor_user_id=identity.user.id,
        actor_account=identity.user.account,
        now=now,
    )
    return snapshot
