"""受控正文读取：原问题精确版本、确认回答快照、意见与处理说明.

- 双权限：运营（`agent:operations:read`）+ 受控正文（`agent:content:read`）。
- 删除/跨企业优先 404；保留期到期返回 expired，不以旧缓存补造。
- 每页最多 8000 字符；不预取全量，不把正文放入 URL/普通日志/事件。
- 回答快照历史缺失返回 `not_collected`，绝不从 checkpoint/Langfuse 兜底。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.console.scope import (
    CONTENT_PAGE_CHARS,
    ConsoleError,
    content_page,
    parse_uuid,
    scope_session,
    tenant_id_of,
)
from app.services.identity import Identity
from app.services.ledger import NotFound

DELETED_GUARD = (
    "NOT EXISTS (SELECT 1 FROM agent_thread_deletions d"
    " WHERE d.tenant_id = {alias}.tenant_id AND d.thread_id = {alias}.thread_id)"
)


async def read_request_content(
    session: AsyncSession,
    identity: Identity,
    *,
    request_id: uuid.UUID,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """原问题快照：按 request 精确引用，不按可变 thread 指针漂移."""
    await scope_session(session, identity, require_ops=True, require_content=True)
    tenant = tenant_id_of(identity)
    if limit < 1 or limit > CONTENT_PAGE_CHARS:
        raise ConsoleError(f"每页最多 {CONTENT_PAGE_CHARS} 字符", code="invalid_limit")
    if offset < 0:
        raise ConsoleError("offset 不能为负", code="invalid_offset")
    request = (
        (
            await session.execute(
                text(
                    """
                SELECT r.id::text AS request_id, r.thread_id, r.graph_id,
                       COALESCE(r.business_code, r.graph_id) AS business_code,
                       r.admitted_at
                FROM agent_requests r
                WHERE r.tenant_id=:tenant AND r.id=:request
                  AND """
                    + DELETED_GUARD.format(alias="r")
                ),
                {"tenant": tenant, "request": str(request_id)},
            )
        )
        .mappings()
        .first()
    )
    if request is None:
        raise NotFound("对象不存在", code="not_found")
    now = dt.datetime.now(dt.UTC)
    settings = get_settings()
    policy_days = settings.ledger_content_retention_days_by_tenant.get(
        tenant, settings.ledger_content_retention_days
    )
    ref = (
        (
            await session.execute(
                text(
                    """
                SELECT c.id::text AS content_ref_id, c.message_id, c.content_version,
                       c.content_snapshot, c.retention_until, c.created_at,
                       c.deleted_at
                FROM agent_content_refs c
                WHERE c.tenant_id=:tenant AND c.request_id=:request
                ORDER BY c.content_version DESC, c.created_at DESC LIMIT 1
                """
                ),
                {"tenant": tenant, "request": str(request_id)},
            )
        )
        .mappings()
        .first()
    )
    base = {
        "request_id": request["request_id"],
        "thread_id": request["thread_id"],
        "graph_id": request["graph_id"],
        "business_code": request["business_code"],
        "admitted_at": request["admitted_at"].isoformat(),
        "content_version": None,
        "content_sha256": None,
        "retention_until": None,
        "capture_status": None,
    }
    if ref is None:
        return {
            "status": "not_collected",
            "offset": offset,
            "next_offset": None,
            "total_chars": 0,
            "text": None,
            "content_type": "text/plain",
            "cache_control": "no-store",
            **base,
            "message": "该请求没有可读取的原问题快照",
        }
    expired = (
        (ref["deleted_at"] is not None)
        or (ref["retention_until"] is not None and ref["retention_until"] <= now)
        or (ref["created_at"] < now - dt.timedelta(days=policy_days))
    )
    if expired:
        return {
            "status": "expired",
            "offset": offset,
            "next_offset": None,
            "total_chars": 0,
            "text": None,
            "content_type": "text/plain",
            "cache_control": "no-store",
            **base,
            "content_version": ref["content_version"],
            "retention_until": ref["retention_until"].isoformat()
            if ref["retention_until"]
            else None,
            "message": "原问题快照已过保留期或已删除",
        }
    raw = ref["content_snapshot"] or ""
    return {
        **content_page(
            text_value=raw,
            offset=offset,
            limit=limit,
            status="available",
            **{
                **base,
                "content_version": ref["content_version"],
                "content_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "retention_until": ref["retention_until"].isoformat()
                if ref["retention_until"]
                else None,
                "capture_status": "captured",
            },
            message_id=ref["message_id"],
        )
    }


async def read_answer_content(
    session: AsyncSession,
    identity: Identity,
    *,
    answer_id: uuid.UUID,
    content_version: int,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """确认回答指定版本：快照缺失如实标注，绝不回填."""
    await scope_session(session, identity, require_ops=True, require_content=True)
    tenant = tenant_id_of(identity)
    if limit < 1 or limit > CONTENT_PAGE_CHARS:
        raise ConsoleError(f"每页最多 {CONTENT_PAGE_CHARS} 字符", code="invalid_limit")
    if offset < 0:
        raise ConsoleError("offset 不能为负", code="invalid_offset")
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT a.id::text AS answer_id, a.attempt_id::text AS attempt_id,
                       a.message_id, a.content_version, a.confirmed, a.partial,
                       t.request_id::text AS request_id, r.thread_id,
                       COALESCE(r.business_code, r.graph_id) AS business_code,
                       a.confirmed_at
                FROM agent_answers a
                JOIN agent_run_attempts t
                  ON t.id=a.attempt_id AND t.tenant_id=a.tenant_id
                JOIN agent_requests r
                  ON r.id=t.request_id AND r.tenant_id=t.tenant_id
                WHERE a.tenant_id=:tenant AND a.id=:answer
                  AND a.content_version=:version
                  AND a.confirmed=true AND a.partial=false
                  AND """
                    + DELETED_GUARD.format(alias="r")
                ),
                {"tenant": tenant, "answer": str(answer_id), "version": content_version},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("回答不存在或不属于当前企业", code="not_found")
    snapshot = (
        (
            await session.execute(
                text(
                    """
                SELECT id::text AS snapshot_id, content_sha256, content_text, summary,
                       total_chars, truncated, capture_status, captured_at,
                       retention_until
                FROM agent_answer_contents
                WHERE tenant_id=:tenant AND answer_id=:answer
                  AND content_version=:version
                """
                ),
                {"tenant": tenant, "answer": str(answer_id), "version": content_version},
            )
        )
        .mappings()
        .first()
    )
    now = dt.datetime.now(dt.UTC)
    base = {
        "answer_id": row["answer_id"],
        "attempt_id": row["attempt_id"],
        "request_id": row["request_id"],
        "thread_id": row["thread_id"],
        "business_code": row["business_code"],
        "message_id": row["message_id"],
        "content_version": content_version,
        "confirmed_at": row["confirmed_at"].isoformat() if row["confirmed_at"] else None,
    }
    if snapshot is None:
        return {
            "status": "not_collected",
            "offset": offset,
            "next_offset": None,
            "total_chars": 0,
            "text": None,
            "content_type": "text/plain",
            "cache_control": "no-store",
            **base,
            "content_sha256": None,
            "summary": None,
            "truncated": False,
            "capture_status": "not_collected",
            "message": "该回答版本未采集正文；历史回答不从事后 Trace/checkpoint 回填",
        }
    settings = get_settings()
    policy_days = settings.ledger_content_retention_days_by_tenant.get(
        tenant, settings.ledger_content_retention_days
    )
    if (snapshot["retention_until"] is not None and snapshot["retention_until"] <= now) or (
        snapshot["captured_at"] is not None
        and snapshot["captured_at"] <= now - dt.timedelta(days=policy_days)
    ):
        return {
            "status": "expired",
            "offset": offset,
            "next_offset": None,
            "total_chars": 0,
            "text": None,
            "content_type": "text/plain",
            "cache_control": "no-store",
            **base,
            "content_sha256": None,
            "summary": None,
            "truncated": snapshot["truncated"],
            "capture_status": snapshot["capture_status"],
            "retention_until": snapshot["retention_until"].isoformat(),
            "message": "该回答快照已过保留期",
        }
    if snapshot["capture_status"] == "capture_failed":
        return {
            "status": "capture_failed",
            "offset": offset,
            "next_offset": None,
            "total_chars": 0,
            "text": None,
            "content_type": "text/plain",
            "cache_control": "no-store",
            **base,
            "content_sha256": snapshot["content_sha256"],
            "summary": snapshot["summary"],
            "truncated": snapshot["truncated"],
            "capture_status": "capture_failed",
            "retention_until": snapshot["retention_until"].isoformat()
            if snapshot["retention_until"]
            else None,
            "message": "终答采集失败；回答与确认事实保持不变",
        }
    raw = snapshot["content_text"] or ""
    status = "truncated" if snapshot["truncated"] else "available"
    return {
        **content_page(
            text_value=raw,
            offset=offset,
            limit=limit,
            status=status,
            **base,
            content_sha256=snapshot["content_sha256"],
            summary=snapshot["summary"],
            truncated=snapshot["truncated"],
            capture_status=snapshot["capture_status"],
            captured_at=snapshot["captured_at"].isoformat() if snapshot["captured_at"] else None,
            retention_until=snapshot["retention_until"].isoformat()
            if snapshot["retention_until"]
            else None,
            message=(
                "回答超过单条采集上限，当前为受控截断文本；不宣称全文"
                if snapshot["truncated"]
                else None
            ),
        )
    }


async def read_feedback_content(
    session: AsyncSession,
    identity: Identity,
    *,
    answer_id: uuid.UUID,
    kind: str,
    ref: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """意见/处理说明正文：精确引用可读历史；意见默认当前版，说明默认只列引用元数据."""
    await scope_session(session, identity, require_ops=True, require_content=True)
    tenant = tenant_id_of(identity)
    if kind not in ("comment", "note"):
        raise ConsoleError("kind 必须是 comment 或 note", code="invalid_kind")
    if limit < 1 or limit > CONTENT_PAGE_CHARS:
        raise ConsoleError(f"每页最多 {CONTENT_PAGE_CHARS} 字符", code="invalid_limit")
    if offset < 0:
        raise ConsoleError("offset 不能为负", code="invalid_offset")
    answer = (
        (
            await session.execute(
                text(
                    """
                SELECT a.id::text AS answer_id, t.request_id::text AS request_id,
                       r.thread_id
                FROM agent_answers a
                JOIN agent_run_attempts t ON t.id=a.attempt_id AND t.tenant_id=a.tenant_id
                JOIN agent_requests r ON r.id=t.request_id AND r.tenant_id=t.tenant_id
                WHERE a.tenant_id=:tenant AND a.id=:answer
                  AND """
                    + DELETED_GUARD.format(alias="r")
                ),
                {"tenant": tenant, "answer": str(answer_id)},
            )
        )
        .mappings()
        .first()
    )
    if answer is None:
        raise NotFound("回答不存在或不属于当前企业", code="not_found")
    if ref is not None:
        ref = str(parse_uuid(ref, label="正文引用"))
    now = dt.datetime.now(dt.UTC)
    if kind == "comment":
        current = (
            (
                await session.execute(
                    text(
                        """
                    SELECT c.id::text AS ref, c.comment_text, c.created_at, c.deleted_at
                    FROM agent_feedback_comments c
                    WHERE c.tenant_id=:tenant AND c.answer_id=:answer
                      AND ((CAST(:ref AS uuid) IS NOT NULL AND c.id=CAST(:ref AS uuid))
                        OR (CAST(:ref AS uuid) IS NULL AND EXISTS (
                          SELECT 1 FROM agent_feedback_current f
                          WHERE f.tenant_id=c.tenant_id AND f.answer_id=c.answer_id
                            AND f.comment_id=c.id)))
                    """
                    ),
                    {"tenant": tenant, "answer": str(answer_id), "ref": ref},
                )
            )
            .mappings()
            .first()
        )
        if ref is not None and current is None:
            raise NotFound("正文引用不存在", code="not_found")
        if current is None or current["ref"] is None or current["deleted_at"] is not None:
            return {
                "status": "not_collected",
                "kind": kind,
                "answer_id": str(answer_id),
                "items": [],
                "text": None,
                "offset": offset,
                "next_offset": None,
                "total_chars": 0,
                "cache_control": "no-store",
            }
        if ref and ref != current["ref"]:
            raise NotFound("正文引用不存在", code="not_found")
        days = get_settings().ledger_content_retention_days_by_tenant.get(
            tenant, get_settings().ledger_content_retention_days
        )
        if current["created_at"] <= now - dt.timedelta(days=days):
            return content_page(text_value=None, offset=offset, limit=limit, status="expired")
        raw = current["comment_text"] or ""
        return {
            **content_page(
                text_value=raw,
                offset=offset,
                limit=limit,
                status="available",
                kind=kind,
                answer_id=str(answer_id),
                ref=current["ref"],
                created_at=current["created_at"].isoformat() if current["created_at"] else None,
            )
        }
    notes = (
        (
            await session.execute(
                text(
                    """
                SELECT n.id::text AS ref, n.note_text, n.created_at, n.deleted_at,
                       n.retention_until, n.actor_user_id::text AS actor_user_id
                FROM agent_feedback_case_notes n
                JOIN agent_feedback_cases c
                  ON c.id=n.case_id AND c.tenant_id=n.tenant_id
                WHERE n.tenant_id=:tenant AND n.answer_id=:answer
                  AND (CAST(:ref AS uuid) IS NULL OR n.id=CAST(:ref AS uuid))
                ORDER BY n.created_at DESC, n.id DESC LIMIT 51
                """
                ),
                {"tenant": tenant, "answer": str(answer_id), "ref": ref},
            )
        )
        .mappings()
        .all()
    )
    if ref is None:
        # 只返回引用元数据，不预取正文；前端按选中备注再请求具体页。
        return {
            "status": "available",
            "kind": kind,
            "answer_id": str(answer_id),
            "items": [
                {
                    "ref": row["ref"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "actor_user_id": row["actor_user_id"],
                    "deleted": row["deleted_at"] is not None
                    or (row["retention_until"] is not None and row["retention_until"] <= now),
                    "content_included": False,
                }
                for row in notes[:50]
            ],
            "has_more": len(notes) > 50,
            "text": None,
            "offset": 0,
            "next_offset": None,
            "total_chars": 0,
            "cache_control": "no-store",
        }
    selected = next((row for row in notes if row["ref"] == ref), None)
    if selected is None:
        raise NotFound("正文引用不存在", code="not_found")
    if selected["deleted_at"] is not None:
        raise NotFound("正文引用不存在", code="not_found")
    if (selected["retention_until"] is not None and selected["retention_until"] <= now) or selected[
        "created_at"
    ] <= now - dt.timedelta(
        days=get_settings().ledger_content_retention_days_by_tenant.get(
            tenant, get_settings().ledger_content_retention_days
        )
    ):
        return {
            "status": "expired",
            "kind": kind,
            "answer_id": str(answer_id),
            "ref": ref,
            "text": None,
            "offset": offset,
            "next_offset": None,
            "total_chars": 0,
            "cache_control": "no-store",
        }
    raw = selected["note_text"] or ""
    return {
        **content_page(
            text_value=raw,
            offset=offset,
            limit=limit,
            status="available",
            kind=kind,
            answer_id=str(answer_id),
            ref=ref,
            created_at=selected["created_at"].isoformat() if selected["created_at"] else None,
            actor_user_id=selected["actor_user_id"],
        )
    }
