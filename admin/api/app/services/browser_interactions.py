"""浏览器事实：关联、逐项事务、持久幂等与永久删除。"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models_ledger import AgentEvent, AgentRequest, AgentRunAttempt
from app.services import feedback
from app.services.event_contract import canonical_digest, validate_batch
from app.services.ledger import emit_fact, is_thread_deletion_pending


async def accept(session, identity, events: list[dict[str, Any]]) -> dict[str, Any]:
    if identity.tenant is None or identity.membership is None:
        raise feedback.AnswerNotFeedbackable()
    tenant, user, membership = identity.tenant.id, identity.user.id, identity.membership.id
    trusted = dict(
        source="browser", tenant_id=str(tenant), user_id=str(user), membership_id=str(membership)
    )
    overridden = [{**e, **trusted} if isinstance(e, dict) else e for e in events]
    result = validate_batch(overridden, source="browser")
    if result["batch_reason"] not in ("ok", "partial_invalid"):
        return {**result, "written": 0}
    from app.services.interaction_policy import enabled

    if not enabled():
        for item in result["items"]:
            item.update(valid=False, reason="collection_disabled")
        return {**result, "accepted": False, "written": 0, "batch_reason": "collection_disabled"}
    written = 0
    now = dt.datetime.now(dt.UTC)
    for event, verdict in zip(overridden, result["items"], strict=False):
        if not verdict["valid"]:
            continue
        try:
            async with session.begin_nested():
                occurred = dt.datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
                if occurred > now + dt.timedelta(minutes=5):
                    raise feedback.PayloadInvalid("操作时间超前", code="invalid_occurred_at")
                from app.config import get_settings

                settings = get_settings()

                days = settings.ledger_content_retention_days_by_tenant.get(
                    str(tenant), settings.ledger_content_retention_days
                )
                if occurred < now - dt.timedelta(days=days):
                    raise feedback.PayloadInvalid("操作超出保留期", code="invalid_occurred_at")
                kind, payload = event["event_type"], dict(event["payload"])
                eid = uuid.UUID(event["event_id"])
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                    {"k": f"event:{tenant}:{eid}"},
                )
                if kind == "browser.feedback":
                    await feedback.lock_operation(
                        session,
                        identity,
                        str(
                            payload.get("client_operation_id") or payload.get("operation_id") or ""
                        ),
                    )
                answer = None
                if kind in ("browser.feedback", "browser.copy"):
                    aid = uuid.UUID(str(payload.get("answer_id") or event.get("answer_id")))
                    answer, request, attempt = await feedback._confirmed_answer(
                        session, identity, aid
                    )
                    if kind == "browser.copy" and payload.get("copy_scope", "full") != "full":
                        raise feedback.PayloadInvalid("复制范围非法", code="invalid_payload")
                else:
                    raw = str(payload.get("attempt_id") or event.get("attempt_id") or "")
                    try:
                        attempt_id = uuid.UUID(raw)
                    except ValueError:
                        attempt_id = None
                    query = select(AgentRunAttempt).where(
                        AgentRunAttempt.tenant_id == tenant, AgentRunAttempt.user_id == user
                    )
                    query = (
                        query.where(AgentRunAttempt.id == attempt_id)
                        if attempt_id
                        else query.where(AgentRunAttempt.platform_run_id == raw)
                    )
                    attempt = (await session.execute(query)).scalar_one_or_none()
                    if attempt is None:
                        raise feedback.AnswerNotFeedbackable()
                    request = await session.get(AgentRequest, attempt.request_id)
                    if request is None or await is_thread_deletion_pending(
                        session, tenant_id=tenant, thread_id=request.thread_id
                    ):
                        raise feedback.AnswerNotFeedbackable()
                    if payload.get("clock") != "monotonic":
                        raise feedback.PayloadInvalid("时钟非法", code="invalid_timing")
                    values = [
                        payload.get(k) for k in ("first_visible_answer_ms", "display_complete_ms")
                    ]
                    for v in values:
                        if v is not None and (
                            isinstance(v, bool)
                            or not isinstance(v, (int, float))
                            or not math.isfinite(v)
                            or v < 0
                            or v > days * 86400000
                        ):
                            raise feedback.PayloadInvalid("耗时非法", code="invalid_timing")
                    if all(v is not None for v in values) and values[1] < values[0]:
                        raise feedback.PayloadInvalid("耗时顺序非法", code="invalid_timing")
                    if payload.get("completion_state") not in (
                        None,
                        "complete",
                        "incomplete",
                        "abandoned",
                    ):
                        raise feedback.PayloadInvalid("完成状态非法", code="invalid_timing")
                    if payload.get("completion_state") == "complete" and values[1] is None:
                        raise feedback.PayloadInvalid("完成测量缺失", code="invalid_timing")
                    if occurred < request.admitted_at - dt.timedelta(minutes=5):
                        raise feedback.PayloadInvalid("测量早于运行", code="invalid_occurred_at")
                    payload["attempt_id"] = str(attempt.id)
                expected = {
                    "thread_id": request.thread_id,
                    "request_id": str(request.id),
                    "attempt_id": str(attempt.id),
                    "run_id": attempt.platform_run_id,
                }
                if answer is not None:
                    expected.update(answer_id=str(answer.id), message_id=answer.message_id)
                for key, value in expected.items():
                    if event.get(key) is not None and str(event[key]) != str(value):
                        raise feedback.PayloadInvalid("信封关联非法", code="invalid_association")
                old = (
                    await session.execute(
                        select(AgentEvent).where(
                            AgentEvent.tenant_id == tenant, AgentEvent.event_id == eid
                        )
                    )
                ).scalar_one_or_none()
                if old:
                    if (
                        old.event_type != kind
                        or old.user_id != user
                        or old.payload_digest != canonical_digest(payload)
                        or old.occurred_at != occurred
                    ):
                        raise feedback.OperationConflict("事件内容不一致", code="event_conflict")
                    verdict["replayed"] = True
                    continue
                if kind == "browser.feedback":
                    if payload.get("comment_ref") is not None:
                        raise feedback.PayloadInvalid(
                            "意见须经专用端点保存", code="invalid_payload"
                        )
                    reply = await feedback.submit_feedback(
                        session,
                        identity,
                        answer_id=answer.id,
                        operation_id=str(payload.get("client_operation_id") or f"bev:{eid}"),
                        rating=str(payload.get("rating") or ""),
                        reason_code=payload.get("reason_code"),
                        expected_revision=payload.get("expected_revision"),
                        client_occurred_at=event["occurred_at"],
                        accepted_via="browser_event",
                        event_id=eid,
                    )
                    verdict["replayed"] = reply.replayed
                    if not reply.replayed:
                        written += 1
                else:
                    await emit_fact(
                        session,
                        tenant_id=tenant,
                        user_id=user,
                        membership_id=membership,
                        event_type=kind,
                        source="browser",
                        payload=payload,
                        occurred_at=occurred,
                        event_id=eid,
                        thread_id=request.thread_id,
                        request_id=request.id,
                        attempt_id=attempt.id,
                        run_id=attempt.platform_run_id,
                        answer_id=answer.id if answer else None,
                        message_id=answer.message_id if answer else None,
                    )
                    written += 1
                await session.flush()
        except (feedback.FeedbackError, IntegrityError, ValueError, TypeError) as exc:
            verdict["valid"] = False
            verdict["reason"] = getattr(exc, "code", "invalid_association")
            if verdict["reason"] == "not_found":
                verdict["reason"] = "invalid_association"
            if verdict["reason"] == "invalid_rating":
                verdict["reason"] = "invalid_enum_value"
    result["accepted"] = all(i["valid"] for i in result["items"])
    result["batch_reason"] = "ok" if result["accepted"] else "partial_invalid"
    return {**result, "written": written}
