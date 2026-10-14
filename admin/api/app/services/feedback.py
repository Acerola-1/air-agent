"""回答反馈服务（change add-chat-feedback-interactions）。

核心协议（design §2）：
- **幂等优先**：相同 operation_id 的重复投递先查操作历史并复用原受理结果（当前
  评价/历史/outbox 都不增加）；同 operation_id 不同载荷 → 409 event_conflict。
- **CAS**：每次新操作携带 expected_revision（首次 0）；当前修订号不一致 →
  409 revision_conflict，另一侧回读后重试，不按到达顺序静默覆盖。
- **同事务**：当前评价 upsert + 操作历史插入 + browser.feedback 事件/outbox 写入
  在一个事务内完成；任一写入点失败整体回滚，调用方以原 operation_id 重试。
- **意见正文**：独立存储，同事务写入；不进入事件载荷（载荷只有 comment_ref）。
- **授权**：可信会话身份覆盖载荷；本人只能写本人已确认回答；跨企业/未确认/
  已删除回答 404（不泄露存在性）。
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_feedback import (
    COMMENT_MAX_CHARS,
    RATINGS,
    REASON_CODES,
    AgentFeedbackComment,
    AgentFeedbackCurrent,
    AgentFeedbackOperation,
)
from app.models_ledger import (
    AgentAnswer,
    AgentRequest,
    AgentRunAttempt,
)
from app.services.event_contract import canonical_digest
from app.services.identity import Identity
from app.services.ledger import (
    LedgerError,
    _enable_operations_read,
    emit_fact,
    is_thread_deletion_pending,
)

MAX_ANSWER_IDS = 50
MAX_PAGE = 200
FUTURE_DRIFT = dt.timedelta(minutes=5)


class FeedbackError(LedgerError):
    """反馈域错误（复用台账错误基类的 HTTP 映射）。"""


class AnswerNotFeedbackable(FeedbackError):
    """回答不存在/未确认/跨范围/已删除：统一 404，不泄露对象存在性。"""

    status_code = 404

    def __init__(self, message: str = "回答不存在或不可评价", *, code: str = "not_found") -> None:
        super().__init__(message, code=code)


class OperationConflict(FeedbackError):
    """同 operation_id 不同载荷。"""

    status_code = 409


class RevisionConflict(FeedbackError):
    """expected_revision 与当前修订号不一致（另一页面已修改）。"""

    status_code = 409


class PayloadInvalid(FeedbackError):
    """载荷校验失败（评级/原因/意见长度/时间漂移）。"""

    status_code = 422


@dataclass(frozen=True)
class FeedbackResult:
    answer_id: uuid.UUID
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer_id": str(self.answer_id),
            "operation_id": self.operation_id,
            "rating": self.rating,
            "reason_code": self.reason_code,
            "revision": self.revision,
            "replayed": self.replayed,
            "comment_ref": self.comment_ref,
            "thread_id": self.thread_id,
            "request_id": self.request_id,
            "attempt_id": self.attempt_id,
            "message_id": self.message_id,
            "content_version": self.content_version,
        }


def _validate_rating(rating: str) -> None:
    if rating not in RATINGS:
        raise PayloadInvalid(f"非法评级: {rating}", code="invalid_rating")


def _validate_reason(reason_code: str | None) -> None:
    if reason_code is not None and reason_code not in REASON_CODES:
        raise PayloadInvalid(f"未知原因: {reason_code}", code="invalid_reason_code")


def _validate_comment(comment: str | None) -> None:
    if comment is not None and len(comment) > COMMENT_MAX_CHARS:
        raise PayloadInvalid(f"意见最多 {COMMENT_MAX_CHARS} 字符", code="comment_too_long")


def _parse_client_time(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise PayloadInvalid("操作时间格式非法", code="invalid_occurred_at") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    if parsed > dt.datetime.now(dt.UTC) + FUTURE_DRIFT:
        raise PayloadInvalid("操作时间未来漂移超过 5 分钟", code="invalid_occurred_at")
    return parsed


async def _confirmed_answer(
    session: AsyncSession, identity: Identity, answer_id: uuid.UUID
) -> tuple[AgentAnswer, AgentRequest, AgentRunAttempt]:
    """本人 + 当前企业 + 已确认 + 线程未删除的回答；否则 404（不泄露存在性）。

    临时 ID、部分回答（partial=true）、跨线程/attempt/用户/企业在这里统一拒绝。
    """
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AnswerNotFeedbackable()
    row = (
        await session.execute(
            select(AgentAnswer, AgentRunAttempt, AgentRequest)
            .join(
                AgentRunAttempt,
                and_(
                    AgentRunAttempt.id == AgentAnswer.attempt_id,
                    AgentRunAttempt.tenant_id == AgentAnswer.tenant_id,
                ),
            )
            .join(
                AgentRequest,
                and_(
                    AgentRequest.id == AgentRunAttempt.request_id,
                    AgentRequest.tenant_id == AgentRunAttempt.tenant_id,
                ),
            )
            .with_for_update(of=AgentAnswer)
            .where(
                AgentAnswer.id == answer_id,
                AgentAnswer.tenant_id == tenant_id,
                AgentAnswer.user_id == identity.user.id,
                AgentAnswer.confirmed.is_(True),
                AgentAnswer.partial.is_(False),
            )
        )
    ).first()
    if row is None:
        raise AnswerNotFeedbackable()
    answer, attempt, request = row
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"thread:{tenant_id}:{request.thread_id}"},
    )
    if await is_thread_deletion_pending(session, tenant_id=tenant_id, thread_id=request.thread_id):
        raise AnswerNotFeedbackable()
    return answer, request, attempt


async def lock_operation(session, identity, operation_id):
    """操作ID跨回答唯一；先锁操作，再锁线程/回答，两个入口保持一致顺序。"""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"feedback-operation:{identity.tenant.id}:{identity.user.id}:{operation_id}"},
    )


async def submit_feedback(
    session: AsyncSession,
    identity: Identity,
    *,
    answer_id: uuid.UUID,
    operation_id: str,
    rating: str,
    reason_code: str | None = None,
    comment: str | None = None,
    expected_revision: int | None = None,
    client_occurred_at: Any = None,
    accepted_via: str = "feedback_put",
    event_id: uuid.UUID | None = None,
) -> FeedbackResult:
    """反馈受理统一入口：幂等 → 校验 → CAS → 同事务落当前/历史/事件/outbox。

    两条受理途径（专用 PUT 与浏览器通用端点）都必须走这里，避免各写一次。
    """
    from app.services.interaction_policy import enabled

    if not enabled():
        raise PayloadInvalid("反馈采集尚未启用", code="collection_disabled")
    if identity.tenant is None or identity.membership is None:
        raise AnswerNotFeedbackable()
    if not operation_id or len(operation_id) > 128:
        raise PayloadInvalid("操作 ID 缺失或超长", code="invalid_operation_id")
    _validate_rating(rating)
    _validate_reason(reason_code)
    _validate_comment(comment)
    client_time = _parse_client_time(client_occurred_at)
    tenant_id = identity.tenant.id
    user_id = identity.user.id
    membership_id = identity.membership.id
    now = dt.datetime.now(dt.UTC)

    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise PayloadInvalid("必须提供非负整数修订号", code="invalid_revision")
    await lock_operation(session, identity, operation_id)
    answer, request, attempt = await _confirmed_answer(session, identity, answer_id)
    # 稳定父行锁也覆盖当前评价尚不存在的首次并发。
    intent_digest = canonical_digest(
        {
            "answer_id": str(answer_id),
            "rating": rating,
            "reason_code": reason_code,
            "comment": comment or None,
            "expected_revision": expected_revision,
        }
    )
    # ---- 幂等优先：同 operation_id 复用原结果（不增加历史/事件/outbox） ----
    existing = (
        await session.execute(
            select(AgentFeedbackOperation).where(
                AgentFeedbackOperation.tenant_id == tenant_id,
                AgentFeedbackOperation.user_id == user_id,
                AgentFeedbackOperation.operation_id == operation_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.intent_digest != intent_digest or existing.answer_id != answer_id:
            raise OperationConflict("同一操作 ID 携带不同内容", code="event_conflict")
        if existing.result_snapshot is None:
            raise OperationConflict("旧操作缺少完整受理快照，请使用新操作ID", code="event_conflict")
        snapshot = dict(existing.result_snapshot)
        snapshot["answer_id"] = uuid.UUID(snapshot["answer_id"])
        snapshot["replayed"] = True
        return FeedbackResult(**snapshot)

    # ---- CAS：仅对非撤销（或首次）比较修订；none 也算一次状态修改 ----
    current = (
        await session.execute(
            select(AgentFeedbackCurrent)
            .with_for_update()
            .where(
                AgentFeedbackCurrent.tenant_id == tenant_id,
                AgentFeedbackCurrent.user_id == user_id,
                AgentFeedbackCurrent.answer_id == answer.id,
            )
        )
    ).scalar_one_or_none()
    current_revision = current.revision if current else 0
    if expected_revision is not None and expected_revision != current_revision:
        raise RevisionConflict("评价已被其他页面修改，请刷新后重试", code="revision_conflict")

    # ---- 意见正文（同事务；仅在有正文且评级非 none 时保存） ----
    comment_id: uuid.UUID | None = None
    if comment is not None and comment.strip() and rating != "none":
        comment_id = uuid.uuid4()
        session.add(
            AgentFeedbackComment(
                id=comment_id,
                tenant_id=tenant_id,
                user_id=user_id,
                answer_id=answer.id,
                request_id=request.id,
                attempt_id=attempt.id,
                thread_id=request.thread_id,
                comment_text=comment,
                created_at=now,
            )
        )

    new_revision = current_revision + 1
    if current is None:
        session.add(
            AgentFeedbackCurrent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                answer_id=answer.id,
                request_id=request.id,
                attempt_id=attempt.id,
                thread_id=request.thread_id,
                rating=rating,
                reason_code=reason_code if rating == "negative" else None,
                comment_id=comment_id,
                revision=new_revision,
                updated_at=now,
                created_at=now,
            )
        )
    else:
        current.rating = rating
        current.reason_code = reason_code if rating == "negative" else None
        current.comment_id = comment_id
        current.revision = new_revision
        current.updated_at = now

    session.add(
        AgentFeedbackOperation(
            tenant_id=tenant_id,
            user_id=user_id,
            membership_id=membership_id,
            answer_id=answer.id,
            request_id=request.id,
            attempt_id=attempt.id,
            thread_id=request.thread_id,
            operation_id=operation_id,
            intent_digest=intent_digest,
            result_snapshot=FeedbackResult(
                answer_id=answer.id,
                operation_id=operation_id,
                rating=rating,
                reason_code=reason_code if rating == "negative" else None,
                revision=new_revision,
                replayed=False,
                comment_ref=str(comment_id) if comment_id else None,
                thread_id=request.thread_id,
                request_id=str(request.id),
                attempt_id=str(attempt.id),
                message_id=answer.message_id,
                content_version=answer.content_version,
            ).as_dict(),
            rating=rating,
            reason_code=reason_code if rating == "negative" else None,
            comment_id=comment_id,
            accepted_via=accepted_via,
            expected_revision=expected_revision,
            result_revision=new_revision,
            client_occurred_at=client_time,
            received_at=now,
            created_at=now,
        )
    )

    # ---- browser.feedback 事件 + outbox（同事务；意见正文不进载荷） ----
    payload: dict[str, Any] = {
        "answer_id": str(answer.id),
        "rating": rating,
        "expected_revision": expected_revision if expected_revision is not None else 0,
        "client_operation_id": operation_id,
    }
    if reason_code is not None:
        payload["reason_code"] = reason_code
    if comment_id is not None:
        payload["comment_ref"] = str(comment_id)
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        membership_id=membership_id,
        event_type="browser.feedback",
        source="browser",
        payload=payload,
        occurred_at=client_time or now,
        thread_id=request.thread_id,
        request_id=request.id,
        attempt_id=attempt.id,
        answer_id=answer.id,
        message_id=answer.message_id,
        event_id=event_id,
    )

    return FeedbackResult(
        answer_id=answer.id,
        operation_id=operation_id,
        rating=rating,
        reason_code=reason_code if rating == "negative" else None,
        revision=new_revision,
        replayed=False,
        comment_ref=str(comment_id) if comment_id else None,
        thread_id=request.thread_id,
        request_id=str(request.id),
        attempt_id=str(attempt.id),
        message_id=answer.message_id,
        content_version=answer.content_version,
    )


async def read_feedback_answers(
    session: AsyncSession,
    identity: Identity,
    *,
    answer_ids: list[uuid.UUID] | None = None,
    message_ids: list[str] | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """本人回读：最多 50 个回答的权威映射 + 当前评价（仅本人同租户已确认）。

    支持按 answer_id（UUID）或 message_id（UI 消息标识）查询；message_id 是
    聊天页的真实键（UI 消息没有 answer ID），映射由服务端权威给出，不猜测。
    """
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AnswerNotFeedbackable()
    if answer_ids is None and message_ids is None:
        raise PayloadInvalid("需要 answer_ids 或 message_ids", code="invalid_answer_ids")
    lookup_values: list[Any] = list(answer_ids or []) + list(message_ids or [])
    if not lookup_values or len(lookup_values) > MAX_ANSWER_IDS:
        raise PayloadInvalid(f"查询标识必须在 1..{MAX_ANSWER_IDS} 之间", code="invalid_answer_ids")
    by_answer = bool(answer_ids)
    rows = (
        await session.execute(
            select(AgentAnswer, AgentRunAttempt, AgentRequest, AgentFeedbackCurrent)
            .join(
                AgentRunAttempt,
                and_(
                    AgentRunAttempt.id == AgentAnswer.attempt_id,
                    AgentRunAttempt.tenant_id == AgentAnswer.tenant_id,
                ),
            )
            .join(
                AgentRequest,
                and_(
                    AgentRequest.id == AgentRunAttempt.request_id,
                    AgentRequest.tenant_id == AgentRunAttempt.tenant_id,
                ),
            )
            .outerjoin(
                AgentFeedbackCurrent,
                and_(
                    AgentFeedbackCurrent.answer_id == AgentAnswer.id,
                    AgentFeedbackCurrent.tenant_id == AgentAnswer.tenant_id,
                    AgentFeedbackCurrent.user_id == identity.user.id,
                ),
            )
            .where(
                AgentAnswer.id.in_(lookup_values)
                if by_answer
                else AgentAnswer.message_id.in_(lookup_values),
                AgentAnswer.tenant_id == tenant_id,
                AgentAnswer.user_id == identity.user.id,
                AgentAnswer.confirmed.is_(True),
                AgentAnswer.partial.is_(False),
                AgentRequest.thread_id == thread_id if thread_id else True,
            )
        )
    ).all()
    deleted_threads: set[str] = set()
    items: list[dict[str, Any]] = []
    for answer, _attempt, request, feedback in rows:
        deletion = (
            await session.execute(
                text(
                    "SELECT 1 FROM agent_thread_deletions WHERE tenant_id = :ten AND thread_id = :t"
                ),
                {"ten": str(tenant_id), "t": request.thread_id},
            )
        ).first()
        if deletion is not None:
            deleted_threads.add(request.thread_id)
            continue  # 已删除线程的回答不暴露（含不可评价状态）
        items.append(
            {
                "answer_id": str(answer.id),
                "message_id": answer.message_id,
                "attempt_id": str(answer.attempt_id),
                "request_id": str(request.id),
                "thread_id": request.thread_id,
                "content_version": answer.content_version,
                "confirmed": answer.confirmed,
                "feedbackable": __import__(
                    "app.services.interaction_policy", fromlist=["enabled"]
                ).enabled(),
                "rating": feedback.rating if feedback else None,
                "reason_code": feedback.reason_code if feedback else None,
                "revision": feedback.revision if feedback else 0,
                "updated_at": feedback.updated_at.isoformat() if feedback else None,
            }
        )
    return {"items": items, "requested": len(lookup_values), "found": len(items)}


async def list_feedback_operations(
    session: AsyncSession,
    identity: Identity,
    *,
    answer_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """企业运营分页（agent:operations:read）：无意见正文；游标绑定租户。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFoundErr()
    await _enable_operations_read(session, identity)
    effective_limit = max(1, min(limit, MAX_PAGE))
    conditions = [
        "tenant_id = :ten",
        (
            "NOT EXISTS (SELECT 1 FROM agent_thread_deletions d WHERE "
            "d.tenant_id=agent_feedback_operations.tenant_id AND "
            "d.thread_id=agent_feedback_operations.thread_id)"
        ),
    ]
    params: dict[str, Any] = {"ten": str(tenant_id), "lim": effective_limit + 1}
    if answer_id is not None:
        conditions.append("answer_id = :aid")
        params["aid"] = str(answer_id)
    if user_id is not None:
        conditions.append("user_id = :uid")
        params["uid"] = str(user_id)
    if cursor:
        try:
            cursor_id, cursor_tenant = cursor.split(":", 1)
            if cursor_tenant != str(tenant_id):
                raise ValueError
            conditions.append("id > :cur")
            params["cur"] = int(cursor_id)
        except ValueError as exc:
            raise PayloadInvalid("游标非法", code="invalid_cursor") from exc
    where = " AND ".join(conditions)
    rows = (
        await session.execute(
            text(
                "SELECT id, tenant_id, user_id, answer_id, request_id, attempt_id, thread_id,"
                " operation_id, rating, reason_code, accepted_via, result_revision,"
                " expected_revision, received_at"
                f" FROM agent_feedback_operations WHERE {where}"
                " ORDER BY id LIMIT :lim"
            ),
            params,
        )
    ).all()
    has_more = len(rows) > effective_limit
    rows = rows[:effective_limit]
    items = [
        {
            "operation_id": r.operation_id,
            "answer_id": str(r.answer_id),
            "request_id": str(r.request_id),
            "attempt_id": str(r.attempt_id),
            "thread_id": r.thread_id,
            "user_id": str(r.user_id),
            "rating": r.rating,
            "reason_code": r.reason_code,
            "comment_included": False,
            "accepted_via": r.accepted_via,
            "expected_revision": r.expected_revision,
            "result_revision": r.result_revision,
            "received_at": r.received_at.isoformat() if r.received_at else None,
        }
        for r in rows
    ]
    next_cursor = f"{rows[-1].id}:{tenant_id}" if has_more and rows else None
    return {"items": items, "next_cursor": next_cursor, "limit": effective_limit}


class NotFoundErr(FeedbackError):
    status_code = 404


async def read_operation_comment(
    session: AsyncSession, identity: Identity, operation_id: str
) -> dict[str, Any]:
    """意见正文读取（运营权限 + agent:content:read；本人读自己的走回读接口）。

    跨租户/已删除：404，不泄露存在性。
    """
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFoundErr("对象不存在", code="not_found")
    if not {"agent:operations:read", "agent:content:read"}.issubset(identity.permissions):
        raise PayloadInvalid("需要运营及原文权限", code="permission_denied")
    await _enable_operations_read(session, identity)
    await session.execute(text("SELECT set_config('air_agent.feedback_content', 'on', true)"))
    row = (
        await session.execute(
            select(AgentFeedbackOperation).where(
                AgentFeedbackOperation.tenant_id == tenant_id,
                AgentFeedbackOperation.operation_id == operation_id,
            )
        )
    ).scalar_one_or_none()
    if row is None or row.comment_id is None:
        raise NotFoundErr("对象不存在", code="not_found")
    comment = (
        await session.execute(
            select(AgentFeedbackComment).where(
                AgentFeedbackComment.id == row.comment_id,
                AgentFeedbackComment.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if comment is None or comment.deleted_at is not None:
        raise NotFoundErr("对象不存在", code="not_found")
    if await is_thread_deletion_pending(session, tenant_id=tenant_id, thread_id=row.thread_id):
        raise NotFoundErr("对象不存在", code="not_found")
    return {
        "operation_id": operation_id,
        "answer_id": str(row.answer_id),
        "comment": comment.comment_text,
        "created_at": comment.created_at.isoformat(),
    }
