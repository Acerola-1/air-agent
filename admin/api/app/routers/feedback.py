"""回答反馈路由（change add-chat-feedback-interactions）。

权限分离（对应 contract-extension.md）：
- `PUT /agent/feedback/answers/{answer_id}`：本人（当前企业成员）评价/切换/撤销；
- `GET /agent/feedback/answers`：本人回读（最多 50 个回答）；
- `GET /agent/feedback/operations`：`agent:operations:read`，无意见正文；
- `GET /agent/feedback/operations/{operation_id}/comment`：运营权限 + `agent:content:read`；
- `GET /agent/feedback/metrics`：`agent:metrics:read`（统计口径见 services/feedback_metrics.py）。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.deps import (
    ActiveTenantIdentity,
    IdentityDep,
    SessionDep,
    require_active_tenant,
    require_permission,
    require_tenant_context,
    verify_write_origin,
)
from app.services import feedback as feedback_service
from app.services import feedback_metrics
from app.services.ledger import LedgerError

router = APIRouter(dependencies=[Depends(require_active_tenant)])


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, LedgerError):
        return HTTPException(
            status_code=exc.status_code, detail={"code": exc.code, "error": exc.message}
        )
    raise exc


class FeedbackPutIn(BaseModel):
    """专用评价端点：意见正文同事务保存；重试必须复用原 operation_id。"""

    event_id: uuid.UUID | None = None
    expected_revision: int = Field(strict=True, ge=0, le=1_000_000)
    rating: Literal["positive", "negative", "none"]
    reason_code: Literal["answer_incorrect", "irrelevant", "incomplete", "slow", "other"] | None = (
        None
    )
    comment: str | None = Field(default=None, max_length=1000)
    operation_id: str = Field(min_length=8, max_length=128)
    client_occurred_at: str | None = None


class FeedbackAnswerOut(BaseModel):
    answer_id: str
    operation_id: str
    rating: str
    reason_code: str | None
    revision: int
    replayed: bool
    comment_ref: str | None = None
    thread_id: str | None = None
    request_id: str | None = None
    attempt_id: str | None = None
    message_id: str | None = None
    content_version: int | None = None


@router.get("/feedback/policy", summary="当前采集开关及部署启用时间")
async def collection_policy(identity: ActiveTenantIdentity) -> dict:
    from app.services.interaction_policy import enabled, enabled_at

    at = enabled_at()
    return {"enabled": enabled(), "enabled_at": at.isoformat() if at else None}


@router.get("/feedback/attempts/resolve", summary="本人平台运行对应的可信台账尝试")
async def resolve_timing_attempt(
    session: SessionDep,
    identity: ActiveTenantIdentity,
    thread_id: str = Query(min_length=1, max_length=128),
    platform_run_id: str | None = Query(default=None, min_length=1, max_length=128),
) -> dict:
    from sqlalchemy import text

    row = (
        (
            await session.execute(
                text(
                    "SELECT s.attempt_id::text,s.id::text AS submission_id, "
                    "q.id::text AS request_id, "
                    "c.id::text AS selection_id,c.selection_digest,c.digest_version, "
                    "c.mcp_mode,c.tool_ids,c.catalog_version,c.validation_time "
                    "FROM agent_submissions s JOIN agent_requests q "
                    "ON q.tenant_id=s.tenant_id AND q.id=s.request_id "
                    "LEFT JOIN agent_capability_selections c "
                    "ON c.tenant_id=s.tenant_id AND c.id=s.capability_selection_id "
                    "WHERE s.tenant_id=:tenant AND s.user_id=:user "
                    "AND s.thread_id=:thread "
                    "AND (CAST(:run AS text) IS NULL OR s.platform_run_id=CAST(:run AS text)) "
                    "AND s.admission_status='confirmed' AND NOT EXISTS "
                    "(SELECT 1 FROM agent_thread_deletions d WHERE d.tenant_id=q.tenant_id "
                    "AND d.thread_id=q.thread_id) ORDER BY s.created_at DESC LIMIT 1"
                ),
                {
                    "tenant": identity.tenant.id,
                    "user": identity.user.id,
                    "thread": thread_id,
                    "run": platform_run_id,
                },
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="运行关联不可访问")
    selection = None
    if row["selection_id"]:
        selection = {
            "selection_id": row["selection_id"],
            "selection_status": "collected",
            "schema_version": "agent.capability-selection.v2",
            "selection_digest": row["selection_digest"],
            "digest_version": row["digest_version"],
            "mcp": {"mode": row["mcp_mode"], "tool_ids": row["tool_ids"]},
            "catalog_version": row["catalog_version"],
            "selection_source": "user_explicit" if row["mcp_mode"] == "allowlist" else "automatic",
            "validation_time": row["validation_time"].isoformat()
            if row["validation_time"]
            else None,
        }
        selection["id"] = row["selection_id"]
    return {
        "attempt_id": row["attempt_id"],
        "submission_id": row["submission_id"],
        "request_id": row["request_id"],
        "capability_selection": selection,
    }


@router.put(
    "/feedback/answers/{answer_id}",
    response_model=FeedbackAnswerOut,
    summary="本人回答评价（点赞/点踩/原因/意见/撤销；幂等 + CAS）",
)
async def put_feedback(
    answer_id: uuid.UUID,
    payload: FeedbackPutIn,
    request: Request,
    session: SessionDep,
    identity: ActiveTenantIdentity,
    _context: Annotated[object, Depends(require_tenant_context)],
) -> FeedbackAnswerOut:
    verify_write_origin(request)
    try:
        result = await feedback_service.submit_feedback(
            session,
            identity,
            answer_id=answer_id,
            operation_id=payload.operation_id,
            rating=payload.rating,
            reason_code=payload.reason_code,
            comment=payload.comment,
            expected_revision=payload.expected_revision,
            client_occurred_at=payload.client_occurred_at,
            accepted_via="feedback_put",
            event_id=payload.event_id,
        )
    except LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return FeedbackAnswerOut(**result.as_dict())


@router.get("/feedback/answers", summary="本人反馈回读（权威映射 + 当前评价）")
async def get_feedback_answers(
    session: SessionDep,
    identity: IdentityDep,
    thread_id: str | None = Query(default=None, max_length=128),
    answer_ids: str | None = Query(default=None, max_length=64 * 50),
    message_ids: str | None = Query(default=None, max_length=200 * 50),
) -> dict:
    """按 answer_id（UUID 逗号分隔）或 message_id（UI 消息 ID 逗号分隔）回读。"""
    ids: list[uuid.UUID] = []
    if answer_ids:
        for raw in answer_ids.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ids.append(uuid.UUID(raw))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="answer_ids 含非法 UUID") from exc
    msg_ids = (
        [raw.strip() for raw in message_ids.split(",") if raw.strip()] if message_ids else None
    )
    if msg_ids and any(len(m) > 128 for m in msg_ids):
        raise HTTPException(status_code=422, detail="message_ids 含超长标识")
    try:
        result = await feedback_service.read_feedback_answers(
            session,
            identity,
            answer_ids=ids or None,
            message_ids=msg_ids,
            thread_id=thread_id,
        )
    except LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


@router.get(
    "/feedback/operations",
    summary="企业反馈操作历史（运营权限；无意见正文）",
)
async def list_operations(
    session: SessionDep,
    identity: Annotated[object, Depends(require_permission("agent:operations:read"))],
    answer_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> dict:
    try:
        result = await feedback_service.list_feedback_operations(
            session,
            identity,  # type: ignore[arg-type]
            answer_id=answer_id,
            user_id=user_id,
            limit=limit,
            cursor=cursor,
        )
    except LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


@router.get(
    "/feedback/operations/{operation_id}/comment",
    summary="意见正文（运营权限 + 原文权限）",
)
async def get_operation_comment(
    operation_id: str,
    session: SessionDep,
    identity: Annotated[object, Depends(require_permission("agent:operations:read"))],
    _content: Annotated[object, Depends(require_permission("agent:content:read"))],
) -> dict:
    try:
        result = await feedback_service.read_operation_comment(
            session,
            identity,
            operation_id,  # type: ignore[arg-type]
        )
    except LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


@router.get(
    "/feedback/metrics",
    summary="反馈/复制/浏览器延迟统计（统计权限）",
)
async def feedback_metrics_view(
    session: SessionDep,
    identity: Annotated[object, Depends(require_permission("agent:metrics:read"))],
    start: str | None = None,
    end: str | None = None,
) -> dict:
    import datetime as dt

    def _parse(value: str | None, *, is_end: bool) -> dt.datetime | None:
        if value is None:
            return None
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="时间格式非法") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.UTC)
        return parsed

    try:
        result = await feedback_metrics.feedback_metrics(
            session,
            identity,  # type: ignore[arg-type]
            start=_parse(start, is_end=False),
            end=_parse(end, is_end=True),
        )
    except LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result
