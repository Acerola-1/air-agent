"""轻量反馈处理单：start / resolve / reopen（独立于用户评价）.

协议要点（design D5 / spec agent-feedback-triage）：

- 写权限 = 运营读 + `agent:feedback:manage`；带说明正文另需 `agent:content:read`。
- 用户评价修订（`agent_feedback_current.revision`）与运营处理修订（case.revision）
  分开；首次操作/重复操作都必须携带两者期望值。
- `operation_id` 幂等：同 ID 同载荷复用原结果；同 ID 异载荷 409。
- 状态与审计同事务；审计不含说明正文。
- 会话删除优先：删除后不可读、不可晚到写入。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.console.scope import ConsoleError, scope_session, tenant_id_of
from app.services.event_contract import canonical_digest
from app.services.identity import Identity
from app.services.ledger import NotFound, is_thread_deletion_pending

ACTIONS = ("start", "resolve", "reopen")
NOTE_MAX_CHARS = 1000


class TransitionConflict(ConsoleError):
    status_code = 409


class TriagePayloadInvalid(ConsoleError):
    status_code = 422


class TriageForbidden(ConsoleError):
    status_code = 403


def _payload_digest(payload: dict[str, Any]) -> str:
    return canonical_digest(payload)


def _snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (value.isoformat() if isinstance(value, dt.datetime) else value)
        for key, value in row.items()
    }


async def submit_action(
    session: AsyncSession,
    identity: Identity,
    *,
    answer_id: uuid.UUID,
    operation_id: str,
    expected_revision: int,
    expected_feedback_revision: int,
    action: str,
    note: str | None,
) -> dict[str, Any]:
    if action not in ACTIONS:
        raise TriagePayloadInvalid("未知处理动作", code="invalid_action")
    try:
        operation_uuid = uuid.UUID(operation_id)
    except (ValueError, AttributeError) as exc:
        raise TriagePayloadInvalid("operation_id 必须是 UUID", code="invalid_operation_id") from exc
    if isinstance(expected_revision, bool) or expected_revision < 0:
        raise TriagePayloadInvalid("expected_revision 必须是非负整数", code="invalid_revision")
    if isinstance(expected_feedback_revision, bool) or expected_feedback_revision < 0:
        raise TriagePayloadInvalid(
            "expected_feedback_revision 必须是非负整数", code="invalid_revision"
        )
    if note is not None and len(note) > NOTE_MAX_CHARS:
        raise TriagePayloadInvalid(f"处理说明最多 {NOTE_MAX_CHARS} 字符", code="note_too_long")
    if action == "resolve" and not (note and note.strip()):
        raise TriagePayloadInvalid("处理完成必须填写结果说明", code="note_required")

    has_note = bool(note and note.strip())
    await scope_session(
        session,
        identity,
        require_ops=True,
        require_manage=True,
        require_content=has_note,
    )
    tenant = tenant_id_of(identity)
    now = dt.datetime.now(dt.UTC)
    payload = {
        "answer_id": str(answer_id),
        "operation_id": str(operation_uuid),
        "action": action,
        "expected_revision": expected_revision,
        "expected_feedback_revision": expected_feedback_revision,
        "note": note or None,
    }
    digest = _payload_digest(payload)

    # 操作 ID 与答案分别加事务级 advisory lock：跨答案同 ID 也不会并发穿透。
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"case-operation:{tenant}:{operation_uuid}"},
    )
    answer = (
        (
            await session.execute(
                text(
                    """
                SELECT a.id::text AS answer_id, a.user_id::text AS answer_user_id,
                       t.request_id::text AS request_id, t.id::text AS attempt_id,
                       r.thread_id, a.confirmed_at
                FROM agent_answers a
                JOIN agent_run_attempts t ON t.id=a.attempt_id AND t.tenant_id=a.tenant_id
                JOIN agent_requests r ON r.id=t.request_id AND r.tenant_id=t.tenant_id
                WHERE a.tenant_id=:tenant AND a.id=:answer
                  AND a.confirmed=true AND a.partial=false
                  AND NOT EXISTS (SELECT 1 FROM agent_thread_deletions d
                                  WHERE d.tenant_id=r.tenant_id AND d.thread_id=r.thread_id)
                """
                ),
                {"tenant": tenant, "answer": str(answer_id)},
            )
        )
        .mappings()
        .first()
    )
    if answer is None:
        raise NotFound("回答不存在或不属于当前企业", code="not_found")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"feedback-case:{tenant}:{answer_id}"},
    )
    if await is_thread_deletion_pending(
        session, tenant_id=identity.tenant.id, thread_id=answer["thread_id"]
    ):
        raise NotFound("对象不存在", code="not_found")

    existing_action = (
        (
            await session.execute(
                text(
                    """
                SELECT id::text AS action_id, answer_id::text AS answer_id,
                       payload_digest, result_snapshot
                FROM agent_feedback_case_actions
                WHERE tenant_id=:tenant AND operation_id=:operation
                """
                ),
                {"tenant": tenant, "operation": str(operation_uuid)},
            )
        )
        .mappings()
        .first()
    )
    if existing_action is not None:
        if (
            existing_action["answer_id"] != str(answer_id)
            or existing_action["payload_digest"] != digest
            or existing_action["result_snapshot"] is None
        ):
            raise TransitionConflict("同一操作 ID 携带不同处理内容", code="event_conflict")
        snapshot = dict(existing_action["result_snapshot"])
        snapshot["replayed"] = True
        return snapshot

    feedback = (
        (
            await session.execute(
                text(
                    """
                SELECT f.id::text AS feedback_id, f.rating, f.reason_code, f.revision,
                       f.comment_id::text AS comment_id, f.updated_at
                FROM agent_feedback_current f
                WHERE f.tenant_id=:tenant AND f.answer_id=:answer
                FOR UPDATE
                """
                ),
                {"tenant": tenant, "answer": str(answer_id)},
            )
        )
        .mappings()
        .first()
    )
    if feedback is None:
        raise NotFound("该回答没有当前用户评价", code="feedback_not_found")
    if int(feedback["revision"]) != expected_feedback_revision:
        raise TransitionConflict("用户评价已变化，请先回读最新评价", code="feedback_changed")

    case = (
        (
            await session.execute(
                text(
                    """
                SELECT id::text AS case_id, state, revision, reviewed_feedback_revision,
                       handled_by::text AS handled_by, handled_at
                FROM agent_feedback_cases
                WHERE tenant_id=:tenant AND answer_id=:answer
                FOR UPDATE
                """
                ),
                {"tenant": tenant, "answer": str(answer_id)},
            )
        )
        .mappings()
        .first()
    )
    current_state = case["state"] if case else None
    current_revision = int(case["revision"]) if case else 0
    if current_revision != expected_revision:
        raise TransitionConflict(
            "处理状态已被其他运营人员修改，请刷新后重试", code="revision_conflict"
        )

    if action == "start":
        if current_state in ("in_progress", "resolved"):
            raise TransitionConflict("当前状态不能重新开始处理", code="invalid_transition")
        to_state = "in_progress"
    elif action == "resolve":
        if current_state == "resolved":
            raise TransitionConflict("已处理状态请使用重新打开", code="invalid_transition")
        to_state = "resolved"
    else:  # reopen
        if current_state != "resolved":
            raise TransitionConflict("只有已处理状态可以重新打开", code="invalid_transition")
        to_state = "in_progress"

    note_id: uuid.UUID | None = None
    if has_note:
        note_id = uuid.uuid4()
    new_revision = current_revision + 1
    if case is None:
        case_id = uuid.uuid4()
        await session.execute(
            text(
                """
                INSERT INTO agent_feedback_cases
                    (id, tenant_id, answer_id, request_id, attempt_id, thread_id, user_id,
                     state, revision, reviewed_feedback_revision, handled_by, handled_at,
                     last_note_id, created_at, updated_at)
                VALUES (:id, :tenant, :answer, :request, :attempt, :thread, :answer_user,
                        :state, :revision, :reviewed, :actor, :now, :note_id, :now, :now)
                """
            ),
            {
                "id": case_id,
                "tenant": tenant,
                "answer": str(answer_id),
                "request": answer["request_id"],
                "attempt": answer["attempt_id"],
                "thread": answer["thread_id"],
                "answer_user": answer["answer_user_id"],
                "state": to_state,
                "revision": new_revision,
                "reviewed": int(feedback["revision"]),
                "actor": str(identity.user.id),
                "now": now,
                "note_id": note_id,
            },
        )
    else:
        case_id = uuid.UUID(case["case_id"])
        await session.execute(
            text(
                """
                UPDATE agent_feedback_cases
                   SET state=:state, revision=:revision,
                       reviewed_feedback_revision=:reviewed,
                       handled_by=:actor, handled_at=:now,
                       last_note_id=COALESCE(:note_id, last_note_id),
                       updated_at=:now
                 WHERE tenant_id=:tenant AND id=:case_id
                """
            ),
            {
                "state": to_state,
                "revision": new_revision,
                "reviewed": int(feedback["revision"]),
                "actor": str(identity.user.id),
                "now": now,
                "note_id": note_id,
                "tenant": tenant,
                "case_id": case_id,
            },
        )
    if note_id is not None:
        await session.execute(
            text(
                """
                INSERT INTO agent_feedback_case_notes
                    (id, tenant_id, case_id, answer_id, thread_id, user_id,
                     actor_user_id, note_text, retention_until, created_at)
                VALUES (:id, :tenant, :case_id, :answer, :thread, :answer_user,
                        :actor, :text, :retention, :now)
                """
            ),
            {
                "id": note_id,
                "tenant": tenant,
                "case_id": case_id,
                "answer": str(answer_id),
                "thread": answer["thread_id"],
                "answer_user": answer["answer_user_id"],
                "actor": str(identity.user.id),
                "text": note,
                "retention": min(
                    now
                    + dt.timedelta(
                        days=get_settings().ledger_content_retention_days_by_tenant.get(
                            tenant, get_settings().ledger_content_retention_days
                        )
                    ),
                    answer["confirmed_at"]
                    + dt.timedelta(
                        days=get_settings().ledger_content_retention_days_by_tenant.get(
                            tenant, get_settings().ledger_content_retention_days
                        )
                    ),
                ),
                "now": now,
            },
        )

    snapshot = {
        "answer_id": str(answer_id),
        "case_id": str(case_id),
        "operation_id": str(operation_uuid),
        "action": action,
        "from_state": current_state,
        "state": to_state,
        "revision": new_revision,
        "feedback_revision": int(feedback["revision"]),
        "reviewed_feedback_revision": int(feedback["revision"]),
        "needs_review": False,
        "handled_by": str(identity.user.id),
        "handled_at": now.isoformat(),
        "note_ref": str(note_id) if note_id else None,
        "replayed": False,
    }
    await session.execute(
        text(
            """
            INSERT INTO agent_feedback_case_actions
                (id, tenant_id, case_id, answer_id, thread_id, operation_id, action,
                 from_state, to_state, expected_revision, result_revision,
                 actor_user_id, actor_account, payload_digest, note_id,
                 result_snapshot, created_at)
            VALUES (:id, :tenant, :case_id, :answer, :thread, :operation, :action,
                    :from_state, :to_state, :expected_revision, :result_revision,
                    :actor, :actor_account, :digest, :note_id,
                    CAST(:snapshot AS jsonb), :now)
            """
        ),
        {
            "id": uuid.uuid4(),
            "tenant": tenant,
            "case_id": case_id,
            "answer": str(answer_id),
            "thread": answer["thread_id"],
            "operation": str(operation_uuid),
            "action": action,
            "from_state": current_state,
            "to_state": to_state,
            "expected_revision": expected_revision,
            "result_revision": new_revision,
            "actor": str(identity.user.id),
            "actor_account": identity.user.account,
            "digest": digest,
            "note_id": note_id,
            "snapshot": json.dumps(snapshot, ensure_ascii=False),
            "now": now,
        },
    )
    from app.services.audit import record_audit

    record_audit(
        session,
        identity=identity,
        actor_id=identity.user.id,
        actor_account=identity.user.account,
        action=f"agent.feedback.case.{action}",
        target_type="agent_feedback_cases",
        target_id=str(case_id),
        detail={
            "answer_id": str(answer_id),
            "from_state": current_state,
            "to_state": to_state,
            "revision": new_revision,
            "feedback_revision": int(feedback["revision"]),
            "has_note": note_id is not None,
            # 说明正文只存独立表；审计只记是否存在。
        },
    )
    await session.flush()
    return snapshot


def note_digest(text_value: str) -> str:
    """给测试/诊断用的稳定摘要（不存正文）."""
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()
