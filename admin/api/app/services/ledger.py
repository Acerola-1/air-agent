"""企业事件台账服务（可信受理、幂等、尝试/回答/Trace 关联、事件与 outbox）。

设计要点（change add-agent-event-ledger）：
- **受理顺序**：可信授权 → 同事务持久化 request/submission/attempt + 事件 + outbox
  → 由受控入口启动平台运行 → 回填平台 run 关联。台账事务失败时调用方返回 503，
  不启动新运行（spec: 业务请求在台账受理事务失败时不启动新运行）。
- **提交幂等**：同成员+线程+提交键 + 同规范摘要 → 复用已有 request/attempt；
  同键不同摘要 → 409 冲突（不覆盖已有内容）。
- **受理歧义**：平台不提供调用方指定 run_id 与幂等创建（见 task-1.2 实测），因此
  上游响应丢失时置 `admission_unknown`，由校准流程按注入的 metadata 标记匹配，
  **不自动重发**。
- 正文只进 `agent_content_refs`（独立授权）；事件/outbox 禁带正文与票据。
- 明细/状态查询：普通成员仅本人；企业运营需独立权限（服务层在该权限下显式设置
  `air_agent.agent_operations` GUC，与数据库策略共同限定）。
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import TenantContext, apply_tenant_context
from app.models_ledger import (
    AgentAnswer,
    AgentContentRef,
    AgentEvent,
    AgentOutbox,
    AgentProjectionState,
    AgentRequest,
    AgentRunAttempt,
    AgentRunResume,
    AgentSubmission,
    AgentThreadDeletion,
    AgentTraceLink,
)
from app.services.identity import Identity

# 与 docs/contracts/agent-events-v1 保持一致的最小子集（避免 admin venv 依赖 src/）
ACTIVE_ATTEMPT_STATES = ("admitting", "admission_unknown", "queued", "running", "waiting_input")
ENDED_STATES = ("succeeded", "failed", "cancelled", "timed_out")
OPERATION_KINDS = ("initial", "retry", "regenerate", "resume")
# F02：人工恢复只允许来自**等待输入**的可恢复状态；其它状态（运行中/已终态/未知）
# 说明"没有什么可恢复的"，如实拒绝而不是受理出一个假的恢复操作。
RESUMABLE_ATTEMPT_STATES = ("waiting_input",)
TERMINAL_STATES = ENDED_STATES
# 证据来源秩：数值越大越权威（states.json.evidence_sources）
EVIDENCE_RANK = {"platform_read": 3, "runtime_callback": 2, "browser_observed": 1}
DEFAULT_CONSUMERS = ("run_projection",)
METRIC_VERSION = "v1"
BUSINESS_TZ = "Asia/Shanghai"


class LedgerError(Exception):
    """台账域错误基类：调用方映射为明确 HTTP 状态。"""

    status_code = 400

    def __init__(self, message: str, *, code: str = "ledger_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ThreadDeletedError(LedgerError):
    """已删除会话（永久语义）：拒绝新的运行事实/事件写入.

    F04：删除后仍可能有**晚到回调**（内部状态/回答/恢复/受理确认端点）到达；一律拒绝，
    不复活已删会话的事实，也不产生新的运行事件。
    """

    status_code = 409


class SubmissionConflict(LedgerError):
    """同提交键不同内容。"""

    status_code = 409


class AdmissionRejected(LedgerError):
    """受理前置条件不满足（线程归属/图/上下文）。"""

    status_code = 403


class NotFound(LedgerError):
    status_code = 404


@dataclass(frozen=True)
class AdmissionResult:
    request_id: uuid.UUID
    attempt_id: uuid.UUID
    submission_id: uuid.UUID
    admission_status: str
    operation_kind: str
    reused: bool
    thread_id: str
    graph_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": str(self.request_id),
            "attempt_id": str(self.attempt_id),
            "submission_id": str(self.submission_id),
            "admission_status": self.admission_status,
            "operation_kind": self.operation_kind,
            "reused": self.reused,
            "thread_id": self.thread_id,
            "graph_id": self.graph_id,
            "ledger_marker": ledger_marker(self.request_id, self.attempt_id),
        }


def ledger_marker(request_id: uuid.UUID | str, attempt_id: uuid.UUID | str) -> str:
    """注入平台 run metadata 的非秘密关联标记（供受理校准匹配）。"""
    return f"lr:{request_id}:{attempt_id}"


def parse_ledger_marker(marker: str) -> tuple[str, str] | None:
    try:
        _, request_id, attempt_id = marker.split(":", 2)
    except ValueError:
        return None
    if not request_id or not attempt_id:
        return None
    return request_id, attempt_id


# --------------------------------------------------------------------- 规范摘要


def canonical_digest(payload: Any) -> str:
    """规范 JSON 摘要：sha256(sort_keys, 紧凑分隔符, 非 ASCII 保留)。

    与 `src/common/event_ledger/contract.py::canonical_digest` 及
    `docs/contracts/agent-events-v1/README.md` 的定义字节一致（跨项目契约测试保证）。
    """
    import hashlib
    import json

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_content_digest(*, graph_id: str, operation_kind: str, content: Any) -> str:
    """提交内容摘要：图 + 操作类型 + 规范化文本/消息。"""
    normalized = content.strip() if isinstance(content, str) else content
    return canonical_digest(
        {"graph_id": graph_id, "operation_kind": operation_kind, "content": normalized}
    )


# ---------------------------------------------------------------- 事件与 outbox


def _event_row(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
    event_type: str,
    source: str,
    occurred_at: dt.datetime,
    payload: dict[str, Any],
    thread_id: str | None = None,
    request_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID | None = None,
    run_id: str | None = None,
    answer_id: uuid.UUID | None = None,
    message_id: str | None = None,
    state_revision: int | None = None,
    event_id: uuid.UUID | None = None,
) -> AgentEvent:
    return AgentEvent(
        event_id=event_id or uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        membership_id=membership_id,
        schema_version="1.0",
        event_type=event_type,
        source=source,
        occurred_at=occurred_at,
        received_at=dt.datetime.now(dt.UTC),
        thread_id=thread_id,
        request_id=request_id,
        attempt_id=attempt_id,
        run_id=run_id,
        answer_id=answer_id,
        message_id=message_id,
        payload=payload,
        payload_digest=canonical_digest(payload),
        state_revision=state_revision,
        created_at=dt.datetime.now(dt.UTC),
    )


async def emit_fact(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
    event_type: str,
    source: str,
    payload: dict[str, Any],
    consumers: tuple[str, ...] = DEFAULT_CONSUMERS,
    occurred_at: dt.datetime | None = None,
    thread_id: str | None = None,
    request_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID | None = None,
    run_id: str | None = None,
    answer_id: uuid.UUID | None = None,
    message_id: str | None = None,
    state_revision: int | None = None,
) -> AgentEvent:
    """事实 + outbox 同事务写入（调用方事务提交后二者一起可见）。"""
    event = _event_row(
        tenant_id=tenant_id,
        user_id=user_id,
        membership_id=membership_id,
        event_type=event_type,
        source=source,
        occurred_at=occurred_at or dt.datetime.now(dt.UTC),
        payload=payload,
        thread_id=thread_id,
        request_id=request_id,
        attempt_id=attempt_id,
        run_id=run_id,
        answer_id=answer_id,
        message_id=message_id,
        state_revision=state_revision,
    )
    session.add(event)
    await session.flush()
    now = dt.datetime.now(dt.UTC)
    for consumer in consumers:
        session.add(
            AgentOutbox(
                tenant_id=tenant_id,
                event_id=event.event_id,
                consumer=consumer,
                available_at=now,
                status="pending",
                attempt_count=0,
                created_at=now,
                updated_at=now,
            )
        )
    await session.flush()
    return event


# --------------------------------------------------------------------- 线程归属


async def _assert_thread_owned(
    session: AsyncSession, *, thread_id: str, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """线程必须已有**已确认**归属（native/migrated），pending 视为未知归属。"""
    row = (
        await session.execute(
            text(
                "SELECT source FROM thread_ownership"
                " WHERE thread_id = :t AND tenant_id = :ten AND user_id = :u"
            ),
            {"t": thread_id, "ten": str(tenant_id), "u": str(user_id)},
        )
    ).first()
    if row is None or str(row[0]) not in ("native", "migrated"):
        raise AdmissionRejected("会话归属未确认或不属于当前企业", code="thread_not_owned")


# ---------------------------------------------------------------------- 受理


async def admit_request(
    session: AsyncSession,
    identity: Identity,
    *,
    thread_id: str,
    submission_key: str,
    graph_id: str,
    operation_kind: str,
    content: Any,
    user_message_id: str | None = None,
    source_attempt_id: uuid.UUID | None = None,
    content_snapshot: str | None = None,
    retention_days: int | None = None,
) -> AdmissionResult:
    """可信受理：幂等创建/复用 request + attempt。

    调用方必须在同一事务内提交；失败则整个受理回滚（不启动平台运行）。
    """
    if identity.tenant is None or identity.membership is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    if operation_kind not in OPERATION_KINDS:
        raise AdmissionRejected(f"未知操作类型: {operation_kind}", code="bad_operation_kind")
    tenant_id = identity.tenant.id
    user_id = identity.user.id
    membership_id = identity.membership.id
    digest = canonical_content_digest(
        graph_id=graph_id, operation_kind=operation_kind, content=content
    )

    await _assert_thread_owned(session, thread_id=thread_id, tenant_id=tenant_id, user_id=user_id)
    # R05：删除中的线程**禁止新写**（新提问/新事件都不允许落在已删除会话上）
    if await is_thread_deletion_pending(session, tenant_id=tenant_id, thread_id=thread_id):
        raise AdmissionRejected("会话正在删除，无法提交新问题", code="thread_deleting")

    existing = (
        await session.execute(
            select(AgentSubmission).where(
                AgentSubmission.tenant_id == tenant_id,
                AgentSubmission.user_id == user_id,
                AgentSubmission.thread_id == thread_id,
                AgentSubmission.submission_key == submission_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        normalized_fallback = (
            existing.operation_kind == "initial"
            and operation_kind in ("retry", "regenerate")
            and source_attempt_id is None
        )
        if existing.content_digest != digest or (
            existing.operation_kind != operation_kind and not normalized_fallback
        ):
            raise SubmissionConflict(
                "同一提交键已用于不同内容或操作类型", code="submission_conflict"
            )
        # R03：重放必须回到**该提交自己的尝试**，而不是请求下最新的尝试
        # （initial→regenerate 后重放原 initial 键不得漂到 regenerate 的尝试）。
        bound_attempt_id = existing.attempt_id
        if bound_attempt_id is None:
            # 0008 前的历史行：仅当该请求只有一个尝试时可唯一确定，否则如实拒绝。
            rows = (
                (
                    await session.execute(
                        select(AgentRunAttempt.id).where(
                            AgentRunAttempt.tenant_id == tenant_id,
                            AgentRunAttempt.request_id == existing.request_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(rows) != 1:
                raise LedgerError(
                    "该提交未绑定唯一尝试，无法安全重放", code="submission_attempt_ambiguous"
                )
            bound_attempt_id = rows[0]
            existing.attempt_id = bound_attempt_id
        attempt = await session.get(AgentRunAttempt, bound_attempt_id)
        if attempt is None or attempt.tenant_id != tenant_id:
            raise LedgerError("提交记录缺少关联尝试", code="inconsistent_submission")
        if normalized_fallback:
            attempt.content_digest = canonical_content_digest(
                graph_id=graph_id, operation_kind="initial", content=content
            )
        return AdmissionResult(
            request_id=existing.request_id,
            attempt_id=attempt.id,
            submission_id=existing.id,
            admission_status=existing.admission_status,
            operation_kind=existing.operation_kind,
            reused=True,
            thread_id=thread_id,
            graph_id=graph_id,
        )

    now = dt.datetime.now(dt.UTC)
    # 失败重试 / 重新生成：关联**原 request**，只新增独立 attempt（不增加提问量）。
    # source_attempt_id 必须属于本租户本人，否则拒绝（不静默新建提问）。
    source_attempt: AgentRunAttempt | None = None
    if source_attempt_id is None and operation_kind in ("retry", "regenerate", "resume"):
        # 服务端解析来源尝试（客户端只声明"这是重试/重新生成"）：取本人本线程最近一次
        # 尝试作为来源。解析不到（例如线程刚切换）则按新提问受理，不猜测关联。
        source_attempt = (
            await session.execute(
                select(AgentRunAttempt)
                .join(
                    AgentRequest,
                    and_(
                        AgentRequest.id == AgentRunAttempt.request_id,
                        AgentRequest.tenant_id == AgentRunAttempt.tenant_id,
                    ),
                )
                .where(
                    AgentRunAttempt.tenant_id == tenant_id,
                    AgentRunAttempt.user_id == user_id,
                    AgentRequest.thread_id == thread_id,
                )
                .order_by(AgentRunAttempt.admitted_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if source_attempt_id is not None:
        if operation_kind not in ("retry", "regenerate", "resume"):
            raise AdmissionRejected(
                "source_attempt_id 仅用于 retry/regenerate/resume",
                code="source_attempt_not_allowed",
            )
        source_attempt = (
            await session.execute(
                select(AgentRunAttempt).where(
                    AgentRunAttempt.id == source_attempt_id,
                    AgentRunAttempt.tenant_id == tenant_id,
                    AgentRunAttempt.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if source_attempt is None:
            raise NotFound("原尝试不存在或不属于当前用户", code="source_attempt_not_found")

    if source_attempt is not None:
        source_thread = await session.scalar(
            select(AgentRequest.thread_id).where(
                AgentRequest.id == source_attempt.request_id, AgentRequest.tenant_id == tenant_id
            )
        )
        if str(source_thread or "") != thread_id:
            raise AdmissionRejected("操作来源必须属于同一会话", code="source_thread_mismatch")
    if operation_kind == "resume" and source_attempt is not None:
        # F02：恢复来源必须是**该用户本企业本线程**上处于可恢复等待状态的尝试。
        # 两条来源解析路径（显式 source_attempt_id / 服务端按线程就近解析）统一适用：
        # 只按用户过滤会让"另一线程的尝试"成为恢复来源，也会把运行中/已终态的尝试
        # 当作可恢复对象；非等待态说明"没有可恢复的东西"，如实拒绝而不是受理假恢复。
        source_thread = await session.scalar(
            select(AgentRequest.thread_id).where(
                AgentRequest.id == source_attempt.request_id,
                AgentRequest.tenant_id == tenant_id,
            )
        )
        if str(source_thread or "") != thread_id:
            raise AdmissionRejected(
                "恢复来源必须属于同一会话", code="resume_source_thread_mismatch"
            )
        if str(source_attempt.state) not in RESUMABLE_ATTEMPT_STATES:
            raise AdmissionRejected(
                f"当前没有可恢复的等待状态（{source_attempt.state}）",
                code="resume_source_not_waiting",
            )

    if source_attempt is None and operation_kind == "resume":
        raise AdmissionRejected("当前没有可恢复的来源", code="resume_source_missing")
    if source_attempt is None and operation_kind in ("retry", "regenerate", "resume"):
        # 解析不到来源尝试（例如线程刚切换/无历史尝试）：按**新提问**如实受理，
        # 不把这次运行标成 regenerate/resume（避免指标与审计记成无来源的重新生成/恢复）。
        operation_kind = "initial"

    if source_attempt is not None:
        request_id = source_attempt.request_id
        request = (
            await session.execute(
                select(AgentRequest)
                .where(AgentRequest.id == request_id, AgentRequest.tenant_id == tenant_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if request is None:  # pragma: no cover - 复合外键保证不会发生
            raise LedgerError("原提问不存在", code="source_request_not_found")
        max_index = (
            await session.execute(
                select(func.max(AgentRunAttempt.attempt_index)).where(
                    AgentRunAttempt.tenant_id == tenant_id,
                    AgentRunAttempt.request_id == request_id,
                )
            )
        ).scalar() or 0
        attempt_index = int(max_index) + 1
    else:
        request_id = uuid.uuid4()
        attempt_index = 1
        request = AgentRequest(
            id=request_id,
            tenant_id=tenant_id,
            user_id=user_id,
            membership_id=membership_id,
            thread_id=thread_id,
            user_message_id=user_message_id,
            graph_id=graph_id,
            operation_kind=operation_kind,
            department_id=identity.membership.department_id,
            role_snapshot=list(identity.membership_role_codes),
            identity_revision={
                "session_ctx_version": identity.session.ctx_version,
                "membership_revision": identity.membership.revision,
                "user_auth_revision": identity.user.auth_revision,
                "tenant_auth_revision": identity.tenant.auth_revision,
            },
            admitted_at=now,
            created_at=now,
        )
        session.add(request)
        # 0008 起 content_refs 通过复合外键引用 request；ORM 未声明该关系，
        # flush 顺序不确定 → 显式先落 request 行。
        await session.flush()

    content_ref_id: uuid.UUID | None = None
    if content_snapshot:
        # 同一逻辑提问的原文不可因重生成而覆盖、重复插入或延长存活时间。
        existing_ref = await session.scalar(
            select(AgentContentRef.id)
            .where(AgentContentRef.tenant_id == tenant_id, AgentContentRef.request_id == request_id)
            .limit(1)
        )
        if existing_ref is not None:
            content_ref_id = existing_ref
        else:
            settings = get_settings()
            policy_days = settings.ledger_content_retention_days_by_tenant.get(
                str(tenant_id), settings.ledger_content_retention_days
            )
            effective_days = min(retention_days or policy_days, policy_days)
            content_ref_id = uuid.uuid4()
            session.add(
                AgentContentRef(
                    id=content_ref_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    membership_id=membership_id,
                    thread_id=thread_id,
                    request_id=request_id,
                    message_id=user_message_id,
                    content_version=1,
                    content_snapshot=content_snapshot,
                    retention_until=now + dt.timedelta(days=effective_days),
                    created_at=now,
                )
            )

    if operation_kind == "resume" and source_attempt is not None:
        # 人工恢复：**保留原 request/attempt**，只追加恢复操作（不新建逻辑提问或尝试）。
        # 平台侧 resume 会新建运行，由 record_resume 追加"恢复映射"。
        attempt_id = source_attempt.id
    else:
        attempt_id = uuid.uuid4()
        session.add(
            AgentRunAttempt(
                id=attempt_id,
                tenant_id=tenant_id,
                user_id=user_id,
                membership_id=membership_id,
                request_id=request_id,
                attempt_index=attempt_index,
                source_attempt_id=source_attempt.id if source_attempt else source_attempt_id,
                graph_id=graph_id,
                state="admitting",
                state_revision=1,
                operation_kind=operation_kind,
                content_digest=canonical_content_digest(
                    graph_id=graph_id, operation_kind=operation_kind, content=content
                ),
                admitted_at=now,
                last_progress_at=now,
                updated_at=now,
            )
        )
    # 新约束（0008）要求 submission→attempt / event→(request,attempt) 的引用真实存在，
    # 而 ORM 没有声明这些复合外键关系，flush 顺序不确定 → 显式先落 request/attempt。
    await session.flush()
    submission_id = uuid.uuid4()
    session.add(
        AgentSubmission(
            id=submission_id,
            tenant_id=tenant_id,
            user_id=user_id,
            membership_id=membership_id,
            thread_id=thread_id,
            submission_key=submission_key,
            operation_kind=operation_kind,
            content_digest=digest,
            admission_status="admitting",
            request_id=request_id,
            attempt_id=attempt_id,
            created_at=now,
            updated_at=now,
        )
    )
    try:
        await emit_fact(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            membership_id=membership_id,
            event_type="request.accepted",
            source="server",
            payload={
                "graph_id": graph_id,
                "operation_kind": operation_kind,
                "submission_digest_prefix": digest[:16],
                "user_message_id": user_message_id,
                "admitted_at": now.isoformat(),
            },
            thread_id=thread_id,
            request_id=request_id,
            attempt_id=attempt_id,
            occurred_at=now,
        )
        await session.flush()
    except IntegrityError as exc:
        # 并发同键：回滚由调用方负责；转为冲突语义
        raise SubmissionConflict("并发提交冲突，请重试", code="submission_conflict") from exc

    return AdmissionResult(
        request_id=request_id,
        attempt_id=attempt_id,
        submission_id=submission_id,
        admission_status="admitting",
        operation_kind=operation_kind,
        reused=False,
        thread_id=thread_id,
        graph_id=graph_id,
    )


async def confirm_admission(
    session: AsyncSession,
    identity: Identity,
    *,
    request_id: uuid.UUID,
    attempt_id: uuid.UUID,
    submission_id: uuid.UUID | None = None,
    platform_run_id: str | None,
    outcome: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """回填平台受理结果：confirmed（有 run_id）或 admission_unknown（不确定）.

    F02：确认**只作用于本次提交**。恢复与初次提交共享同一 attempt，若按 attempt
    批量改写，会把其它操作（retry/regenerate/初次）的受理状态一起改掉；因此优先按
    `submission_id` 定位，缺省时按 attempt 下"未被确认的最新一次启动"回退（兼容旧调用）。
    恢复确认同时落**恢复映射**（原 run 关系 + 新 run），不新建提问/尝试。
    """
    if outcome not in ("confirmed", "admission_unknown"):
        raise LedgerError("outcome 必须是 confirmed 或 admission_unknown", code="bad_outcome")
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    attempt = await _load_attempt(session, attempt_id, tenant_id)
    # F04：删除后到达的"受理确认"属晚到回调 → 拒绝写入
    await assert_thread_live(
        session,
        tenant_id=tenant_id,
        thread_id=str(
            await session.scalar(
                select(AgentRequest.thread_id).where(
                    AgentRequest.id == request_id,
                    AgentRequest.tenant_id == tenant_id,
                )
            )
            or ""
        ),
    )
    submission = None
    if submission_id is not None:
        submission = (
            await session.execute(
                select(AgentSubmission).where(
                    AgentSubmission.id == submission_id,
                    AgentSubmission.tenant_id == tenant_id,
                    AgentSubmission.user_id == identity.user.id,
                )
            )
        ).scalar_one_or_none()
        if submission is None:
            raise NotFound("提交不存在或不属于当前用户", code="submission_not_found")
        if str(submission.attempt_id) != str(attempt_id) or str(submission.request_id) != str(
            request_id
        ):
            raise AdmissionRejected("提交与目标尝试/请求不一致", code="submission_mismatch")
    if str(attempt.request_id) != str(request_id):
        raise AdmissionRejected("尝试不属于该请求", code="attempt_request_mismatch")
    now = dt.datetime.now(dt.UTC)
    prior_run_id = attempt.platform_run_id
    resume_index: int | None = None
    if outcome == "confirmed":
        if not platform_run_id:
            raise LedgerError("confirmed 必须提供 platform_run_id", code="missing_run_id")
        if submission is not None:
            if submission.platform_run_id and submission.platform_run_id != platform_run_id:
                raise AdmissionRejected("提交运行映射冲突", code="submission_run_conflict")
            if (
                submission.platform_run_id == platform_run_id
                and submission.admission_status == "confirmed"
            ):
                return {"outcome": "confirmed", "platform_run_id": platform_run_id, "reused": True}
            submission.platform_run_id = platform_run_id
        is_resume = str(submission.operation_kind) == "resume" if submission is not None else False
        if prior_run_id is not None and str(prior_run_id) != platform_run_id and not is_resume:
            raise AdmissionRejected("旧操作不能覆盖当前运行", code="stale_submission")
        attempt.platform_run_id = platform_run_id
        if attempt.state in ("admitting", "admission_unknown", "waiting_input"):
            attempt.state = "running"
        attempt.evidence_source = "platform_read"
        attempt.state_revision += 1
        attempt.started_at = attempt.started_at or now
        if is_resume:
            # 恢复映射：保留原 run 关系，追加本次 resume 的 run（幂等，不新建尝试）
            prior = await session.scalar(
                text(
                    "SELECT resume_index FROM agent_run_resumes"
                    " WHERE tenant_id = :t AND attempt_id = :a AND platform_run_id = :r"
                ),
                {
                    "t": str(tenant_id),
                    "a": str(attempt_id),
                    "r": str(platform_run_id),
                },
            )
            if prior is not None:
                resume_index = int(prior)
            else:
                max_index = await session.scalar(
                    text(
                        "SELECT COALESCE(max(resume_index), 0) FROM agent_run_resumes"
                        " WHERE tenant_id = :t AND attempt_id = :a"
                    ),
                    {"t": str(tenant_id), "a": str(attempt_id)},
                )
                resume_index = int(max_index or 0) + 1
                await session.execute(
                    text(
                        "INSERT INTO agent_run_resumes (id, tenant_id, user_id, attempt_id,"
                        " resume_index, original_run_id, platform_run_id, created_at)"
                        " VALUES (gen_random_uuid(), CAST(:t AS uuid), CAST(:u AS uuid),"
                        " CAST(:a AS uuid), :idx, :orig, :run, now())"
                    ),
                    {
                        "t": str(tenant_id),
                        "u": str(identity.user.id),
                        "a": str(attempt_id),
                        "idx": resume_index,
                        "orig": prior_run_id,
                        "run": str(platform_run_id),
                    },
                )
    else:
        if attempt.platform_run_id is None:
            attempt.state = "admission_unknown"
            attempt.state_revision += 1
    attempt.last_progress_at = now
    attempt.updated_at = now
    # 只改写目标提交：显式指定 submission_id 时按提交；否则取该尝试下**最近一次已取
    # 启动权且尚未终态判定**的提交（兼容旧调用），绝不再批量改写全部提交。
    target = (
        select(AgentSubmission.id)
        .where(
            AgentSubmission.tenant_id == tenant_id,
            AgentSubmission.attempt_id == attempt_id,
            AgentSubmission.start_claimed_at.is_not(None),
        )
        .order_by(AgentSubmission.start_claimed_at.desc())
        .limit(1)
    )
    if submission is not None:
        await session.execute(
            update(AgentSubmission)
            .where(
                AgentSubmission.tenant_id == tenant_id,
                AgentSubmission.id == submission.id,
            )
            .values(
                admission_status=("confirmed" if outcome == "confirmed" else "admission_unknown"),
                **({"platform_run_id": platform_run_id} if outcome == "confirmed" else {}),
                updated_at=now,
            )
        )
    else:
        await session.execute(
            update(AgentSubmission)
            .where(AgentSubmission.id.in_(target.scalar_subquery()))
            .values(
                admission_status=("confirmed" if outcome == "confirmed" else "admission_unknown"),
                **({"platform_run_id": platform_run_id} if outcome == "confirmed" else {}),
                updated_at=now,
            )
        )
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        membership_id=attempt.membership_id,
        event_type="run.admission_confirmed",
        source="runtime",
        payload={
            "platform_run_id": platform_run_id or "",
            "admission_outcome": outcome,
            "submission_id": str(submission.id) if submission is not None else "",
            **({"resume_index": resume_index} if resume_index else {}),
            **({"original_platform_run_id": prior_run_id} if resume_index else {}),
            **({"admission_unknown_reason": reason} if reason else {}),
            "admitted_at": now.isoformat(),
        },
        thread_id=None,
        request_id=request_id,
        attempt_id=attempt_id,
        run_id=platform_run_id,
        state_revision=attempt.state_revision,
        occurred_at=now,
    )
    await session.flush()
    return {
        "attempt_id": str(attempt_id),
        "state": attempt.state,
        "outcome": outcome,
        **({"submission_id": str(submission.id)} if submission is not None else {}),
        **({"resume_index": resume_index} if resume_index else {}),
    }


# ------------------------------------------------------------------- 停止意图


async def claim_start(
    session: AsyncSession,
    identity: Identity,
    *,
    submission_id: uuid.UUID,
    attempt_id: uuid.UUID,
    thread_id: str,
    graph_id: str,
    operation_kind: str,
    content: str,
    source: str,
) -> dict[str, Any]:
    """受控入口的启动权原子裁决（R01）：一次业务提交只能启动一次平台运行.

    与 Agent 侧 `store.claim_start` 语义一致（原生与 BFF 共用同一裁决规则）：
    并发只有一个胜出；已有平台映射直接复用；换图/换操作/换正文拒绝。
    """
    import hashlib
    import json

    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    digest = hashlib.sha256(
        json.dumps(
            {
                "graph_id": graph_id,
                "operation_kind": operation_kind,
                "content": (content or "").strip(),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    await _load_attempt(session, attempt_id, tenant_id)
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT s.id, s.attempt_id, s.operation_kind AS submission_kind,
                       s.start_claimed_at, COALESCE(s.platform_run_id,
                       CASE WHEN s.operation_kind <> 'resume' THEN
                         CASE WHEN EXISTS (SELECT 1 FROM agent_run_resumes x
                              WHERE x.attempt_id=a.id AND x.tenant_id=a.tenant_id)
                           THEN (SELECT x.original_run_id FROM agent_run_resumes x
                                 WHERE x.attempt_id=a.id AND x.tenant_id=a.tenant_id
                                 ORDER BY x.resume_index LIMIT 1)
                           ELSE a.platform_run_id END END) AS submission_run_id,
                       a.graph_id, a.state,
                       CASE WHEN s.operation_kind = 'initial' THEN a.content_digest
                            ELSE s.content_digest END AS content_digest,
                       a.platform_run_id, r.thread_id
                FROM agent_submissions s
                JOIN agent_run_attempts a
                  ON a.id = s.attempt_id AND a.tenant_id = s.tenant_id
                JOIN agent_requests r
                  ON r.id = s.request_id AND r.tenant_id = s.tenant_id
                WHERE s.id = CAST(:sid AS uuid) AND s.tenant_id = CAST(:t AS uuid)
                  AND s.user_id = CAST(:u AS uuid) FOR UPDATE OF a, s
                """
                ),
                {"sid": str(submission_id), "t": str(tenant_id), "u": str(identity.user.id)},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound("提交不存在或不属于当前用户", code="submission_not_found")
    if str(row["thread_id"]) != thread_id or str(row["attempt_id"]) != str(attempt_id):
        raise NotFound("提交与目标尝试/会话不一致", code="submission_mismatch")
    if str(row["graph_id"]) != graph_id:
        return {"status": "mismatch", "detail": "graph", "reserved_graph_id": str(row["graph_id"])}
    if str(row["submission_kind"]) != operation_kind:
        return {
            "status": "mismatch",
            "detail": "operation_kind",
            "reserved_operation_kind": str(row["submission_kind"]),
        }
    if row["content_digest"] and str(row["content_digest"]) != digest:
        return {"status": "mismatch", "detail": "content"}
    if row["submission_run_id"]:
        return {
            "status": "already_confirmed",
            "platform_run_id": str(row["submission_run_id"]),
            "state": str(row["state"]),
        }
    if str(row["state"]) in TERMINAL_STATES:
        return {"status": "terminal", "state": str(row["state"])}
    if str(row["submission_kind"]) == "resume" and row["start_claimed_at"] is None:
        if str(row["state"]) != "waiting_input":
            return {"status": "mismatch", "detail": "resume_not_waiting"}
    token = uuid.uuid4()
    claimed = (
        await session.execute(
            text(
                "UPDATE agent_submissions SET start_claimed_at = now(),"
                " start_claim_token = CAST(:token AS uuid), start_claim_source = :src,"
                " updated_at = now()"
                " WHERE id = CAST(:sid AS uuid) AND tenant_id = CAST(:t AS uuid)"
                "   AND start_claimed_at IS NULL"
                " RETURNING id"
            ),
            {"token": str(token), "src": source, "sid": str(submission_id), "t": str(tenant_id)},
        )
    ).first()
    if claimed is None:
        return {"status": "already_started"}
    if operation_kind == "resume":
        await session.execute(
            text(
                "UPDATE agent_run_attempts SET state='queued',"
                " state_revision=state_revision+1, last_progress_at=now(), updated_at=now()"
                " WHERE id=CAST(:a AS uuid) AND tenant_id=CAST(:t AS uuid)"
            ),
            {"a": str(attempt_id), "t": str(tenant_id)},
        )
    return {"status": "claimed", "start_claim_token": str(token)}


async def record_stop_intent(
    session: AsyncSession,
    identity: Identity,
    *,
    attempt_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
    thread_id: str | None = None,
    origin: str,
    phase: str,
    platform_run_id: str | None = None,
) -> dict[str, Any]:
    """停止意图独立保存：origin/phase 必须显式；未知不得猜测为 user_stop。"""
    if origin not in (
        "user_stop",
        "navigation",
        "tenant_switch",
        "transport_disconnect",
        "permission_revocation",
        "unknown",
    ):
        raise LedgerError(f"未知停止来源: {origin}", code="bad_cancel_origin")
    if phase not in ("queued", "processing", "output", "unknown"):
        raise LedgerError(f"未知停止阶段: {phase}", code="bad_cancel_phase")
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    attempt = await _load_attempt(session, attempt_id, tenant_id) if attempt_id else None
    if attempt is None and thread_id:
        attempt = (
            (
                await session.execute(
                    select(AgentRunAttempt)
                    .where(
                        AgentRunAttempt.tenant_id == tenant_id,
                        AgentRunAttempt.user_id == identity.user.id,
                        AgentRunAttempt.state.in_(ACTIVE_ATTEMPT_STATES),
                    )
                    .join(
                        AgentRequest,
                        and_(
                            AgentRequest.id == AgentRunAttempt.request_id,
                            AgentRequest.tenant_id == AgentRunAttempt.tenant_id,
                        ),
                    )
                    .where(AgentRequest.thread_id == thread_id)
                    .order_by(AgentRunAttempt.admitted_at.desc())
                )
            )
            .scalars()
            .first()
        )
    if attempt is None:
        raise NotFound("找不到可关联的运行尝试", code="attempt_not_found")

    now = dt.datetime.now(dt.UTC)
    attempt.cancel_requested = True
    attempt.cancel_origin = origin
    attempt.cancel_phase = phase
    if platform_run_id:
        attempt.platform_run_id = attempt.platform_run_id or platform_run_id
    attempt.state_revision += 1
    attempt.last_progress_at = now
    attempt.updated_at = now
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        membership_id=attempt.membership_id,
        event_type="run.cancel_requested",
        source="runtime",
        payload={
            "origin": origin,
            "phase": phase,
            **({"platform_run_id": platform_run_id} if platform_run_id else {}),
            "requested_at": now.isoformat(),
        },
        request_id=attempt.request_id,
        attempt_id=attempt.id,
        run_id=attempt.platform_run_id,
        state_revision=attempt.state_revision,
        occurred_at=now,
    )
    await session.flush()
    return {
        "attempt_id": str(attempt.id),
        "cancel_requested": True,
        "origin": origin,
        "phase": phase,
        "state": attempt.state,
    }


async def observe_state(
    session: AsyncSession,
    identity: Identity,
    *,
    attempt_id: uuid.UUID,
    state: str,
    evidence_source: str,
    platform_status: str | None = None,
    state_revision: int | None = None,
) -> dict[str, Any]:
    """带证据/修订的状态转换：低秩证据不得覆盖高秩终态；乱序不回退。"""
    from app.models_ledger import ATTEMPT_STATES  # noqa: PLC0415

    if state not in ATTEMPT_STATES:
        raise LedgerError(f"未知状态: {state}", code="bad_state")
    if evidence_source not in EVIDENCE_RANK:
        raise LedgerError(f"未知证据来源: {evidence_source}", code="bad_evidence_source")
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    attempt = await _load_attempt(session, attempt_id, tenant_id)
    # F04：删除后的状态回调（含补偿校准）不得写入新事件
    await assert_thread_live(
        session,
        tenant_id=tenant_id,
        thread_id=str(
            await session.scalar(
                select(AgentRequest.thread_id).where(
                    AgentRequest.id == attempt.request_id,
                    AgentRequest.tenant_id == tenant_id,
                )
            )
            or ""
        ),
    )
    now = dt.datetime.now(dt.UTC)
    current_rank = EVIDENCE_RANK.get(attempt.evidence_source or "", 0)
    incoming_rank = EVIDENCE_RANK[evidence_source]
    applied = True
    rejection: str | None = None
    if attempt.state in TERMINAL_STATES:
        if incoming_rank < current_rank:
            applied = False
            rejection = "lower_rank_after_terminal"
        elif state != attempt.state:
            # 同秩冲突：保留冲突，用更强证据修订但不删除原事实
            rejection = (
                "terminal_conflict_repaired"
                if incoming_rank > current_rank
                else ("terminal_conflict_kept")
            )
    if applied and rejection != "terminal_conflict_kept":
        attempt.state = state
        attempt.evidence_source = evidence_source
        attempt.platform_status = platform_status or attempt.platform_status
        attempt.state_revision = max(attempt.state_revision + 1, state_revision or 0)
        if state == "running" and attempt.started_at is None:
            attempt.started_at = now
        if state in TERMINAL_STATES:
            attempt.ended_at = attempt.ended_at or now
    attempt.last_progress_at = now
    attempt.updated_at = now
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        membership_id=attempt.membership_id,
        event_type="run.state_observed",
        source="runtime",
        payload={
            "state": state,
            "evidence_source": evidence_source,
            "platform_status": platform_status or "",
            "state_revision": attempt.state_revision,
            "cancel_requested": attempt.cancel_requested,
            **({"waiting_kind": platform_status} if state == "waiting_input" else {}),
        },
        request_id=attempt.request_id,
        attempt_id=attempt.id,
        run_id=attempt.platform_run_id,
        state_revision=attempt.state_revision,
        occurred_at=now,
    )
    await session.flush()
    return {
        "attempt_id": str(attempt.id),
        "state": attempt.state,
        "state_revision": attempt.state_revision,
        "applied": applied,
        "note": rejection,
    }


async def record_resume(
    session: AsyncSession,
    identity: Identity,
    *,
    attempt_id: uuid.UUID,
    platform_run_id: str,
) -> dict[str, Any]:
    """人工恢复：不新建提问/尝试，追加平台运行映射与恢复记录。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    attempt = await _load_attempt(session, attempt_id, tenant_id)
    # F04：删除后到达的回答/恢复/关联回调不得复活已删会话的事实
    await assert_thread_live(
        session,
        tenant_id=tenant_id,
        thread_id=str(
            await session.scalar(
                select(AgentRequest.thread_id).where(
                    AgentRequest.id == attempt.request_id,
                    AgentRequest.tenant_id == tenant_id,
                )
            )
            or ""
        ),
    )
    now = dt.datetime.now(dt.UTC)
    last_index = (
        await session.execute(
            select(func.max(AgentRunResume.resume_index)).where(
                AgentRunResume.tenant_id == tenant_id,
                AgentRunResume.attempt_id == attempt_id,
            )
        )
    ).scalar() or 0
    resume_index = int(last_index) + 1
    session.add(
        AgentRunResume(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=attempt.user_id,
            attempt_id=attempt_id,
            resume_index=resume_index,
            original_run_id=attempt.platform_run_id,
            platform_run_id=platform_run_id,
            created_at=now,
        )
    )
    attempt.platform_run_id = platform_run_id
    attempt.state = "running"
    attempt.evidence_source = "platform_read"
    attempt.state_revision += 1
    attempt.last_progress_at = now
    attempt.updated_at = now
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        membership_id=attempt.membership_id,
        event_type="run.resumed",
        source="runtime",
        payload={
            "resume_index": resume_index,
            "platform_run_id": platform_run_id,
            "resumed_at": now.isoformat(),
        },
        request_id=attempt.request_id,
        attempt_id=attempt_id,
        run_id=platform_run_id,
        state_revision=attempt.state_revision,
        occurred_at=now,
    )
    await session.flush()
    return {"attempt_id": str(attempt_id), "resume_index": resume_index, "state": attempt.state}


async def record_answer(
    session: AsyncSession,
    identity: Identity,
    *,
    attempt_id: uuid.UUID,
    message_id: str | None,
    content_version: int = 1,
    confirmed: bool,
) -> dict[str, Any]:
    """可见回答版本：同尝试同消息同版本幂等；草稿/推理不入表（由调用方保证）。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    attempt = await _load_attempt(session, attempt_id, tenant_id)
    # F04：删除后到达的回答/恢复/关联回调不得复活已删会话的事实
    await assert_thread_live(
        session,
        tenant_id=tenant_id,
        thread_id=str(
            await session.scalar(
                select(AgentRequest.thread_id).where(
                    AgentRequest.id == attempt.request_id,
                    AgentRequest.tenant_id == tenant_id,
                )
            )
            or ""
        ),
    )
    now = dt.datetime.now(dt.UTC)
    existing = None
    if message_id:
        existing = (
            await session.execute(
                select(AgentAnswer).where(
                    AgentAnswer.tenant_id == tenant_id,
                    AgentAnswer.attempt_id == attempt_id,
                    AgentAnswer.message_id == message_id,
                    AgentAnswer.content_version == content_version,
                )
            )
        ).scalar_one_or_none()
    if existing is None:
        existing = AgentAnswer(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=attempt.user_id,
            attempt_id=attempt_id,
            message_id=message_id,
            content_version=content_version,
            partial=not confirmed,
            confirmed=confirmed,
            confirmed_at=now if confirmed else None,
            created_at=now,
        )
        session.add(existing)
        await session.flush()
    else:
        existing.confirmed = existing.confirmed or confirmed
        existing.partial = existing.partial and not confirmed
        if confirmed and existing.confirmed_at is None:
            existing.confirmed_at = now
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        membership_id=attempt.membership_id,
        event_type="answer.confirmed" if confirmed else "answer.partial",
        source="runtime",
        payload={
            "answer_id": str(existing.id),
            "content_version": content_version,
            **({"message_id": message_id} if message_id else {}),
            **(
                {"confirmed_at": now.isoformat()} if confirmed else {"observed_at": now.isoformat()}
            ),
        },
        request_id=attempt.request_id,
        attempt_id=attempt_id,
        run_id=attempt.platform_run_id,
        answer_id=existing.id,
        message_id=message_id,
        occurred_at=now,
    )
    await session.flush()
    return {
        "answer_id": str(existing.id),
        "confirmed": existing.confirmed,
        "partial": existing.partial,
        "content_version": existing.content_version,
    }


async def link_trace(
    session: AsyncSession,
    identity: Identity,
    *,
    attempt_id: uuid.UUID,
    provider: str,
    trace_id: str,
    project_scope: str = "default",
    primary: bool = False,
    link_source: str = "runtime_callback",
) -> dict[str, Any]:
    """Trace 多重映射：幂等补充；已有主 Trace 不被无规则替换。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    attempt = await _load_attempt(session, attempt_id, tenant_id)
    # F04：删除后到达的回答/恢复/关联回调不得复活已删会话的事实
    await assert_thread_live(
        session,
        tenant_id=tenant_id,
        thread_id=str(
            await session.scalar(
                select(AgentRequest.thread_id).where(
                    AgentRequest.id == attempt.request_id,
                    AgentRequest.tenant_id == tenant_id,
                )
            )
            or ""
        ),
    )
    now = dt.datetime.now(dt.UTC)
    existing = (
        await session.execute(
            select(AgentTraceLink).where(
                AgentTraceLink.tenant_id == tenant_id,
                AgentTraceLink.provider == provider,
                AgentTraceLink.project_scope == project_scope,
                AgentTraceLink.trace_id == trace_id,
                AgentTraceLink.attempt_id == attempt_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {
            "trace_link_id": str(existing.id),
            "created": False,
            "is_primary": existing.is_primary,
        }
    has_primary = (
        await session.execute(
            select(func.count())
            .select_from(AgentTraceLink)
            .where(
                AgentTraceLink.tenant_id == tenant_id,
                AgentTraceLink.attempt_id == attempt_id,
                AgentTraceLink.is_primary.is_(True),
            )
        )
    ).scalar() or 0
    is_primary = primary and not has_primary
    row = AgentTraceLink(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        attempt_id=attempt_id,
        provider=provider,
        project_scope=project_scope,
        trace_id=trace_id,
        is_primary=is_primary,
        link_source=link_source,
        created_at=now,
    )
    session.add(row)
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=attempt.user_id,
        membership_id=attempt.membership_id,
        event_type="trace.linked",
        source="runtime",
        payload={
            "provider": provider,
            "project_scope": project_scope,
            "trace_id": trace_id,
            "primary": is_primary,
            "link_source": link_source,
        },
        request_id=attempt.request_id,
        attempt_id=attempt_id,
        run_id=attempt.platform_run_id,
        occurred_at=now,
    )
    await session.flush()
    return {"trace_link_id": str(row.id), "created": True, "is_primary": is_primary}


async def _load_attempt(
    session: AsyncSession, attempt_id: uuid.UUID, tenant_id: uuid.UUID
) -> AgentRunAttempt:
    attempt = (
        await session.execute(
            select(AgentRunAttempt)
            .where(AgentRunAttempt.id == attempt_id, AgentRunAttempt.tenant_id == tenant_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if attempt is None:
        raise NotFound("运行尝试不存在或不属于当前企业", code="attempt_not_found")
    return attempt


# --------------------------------------------------------------------- 删除标记


async def request_thread_deletion(
    session: AsyncSession,
    identity: Identity,
    *,
    thread_id: str,
) -> dict[str, Any]:
    """会话删除：立即禁读（标记 requested）+ 事件 + outbox；清理异步可重试。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    now = dt.datetime.now(dt.UTC)
    # R05：删除入口必须**校验线程归属**（本人本企业），或调用方持有独立的
    # 线程删除管理权限。缺失即拒绝，且不泄露其他企业/他人线程是否存在（统一 404）。
    owner = (
        (
            await session.execute(
                text(
                    "SELECT user_id::text AS user_id, source FROM thread_ownership"
                    " WHERE thread_id = :t AND tenant_id = :ten"
                ),
                {"t": thread_id, "ten": str(tenant_id)},
            )
        )
        .mappings()
        .first()
    )
    if owner is None or str(owner["source"]) not in ("native", "migrated"):
        raise NotFound("对象不存在", code="not_found")
    owns = str(owner["user_id"]) == str(identity.user.id)
    if not owns and "agent:thread:delete" not in identity.permissions:
        raise NotFound("对象不存在", code="not_found")
    existing = (
        await session.execute(
            select(AgentThreadDeletion).where(
                AgentThreadDeletion.tenant_id == tenant_id,
                AgentThreadDeletion.thread_id == thread_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = AgentThreadDeletion(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            thread_id=thread_id,
            user_id=uuid.UUID(str(owner["user_id"])),
            requested_by=identity.user.id,
            state="requested",
            attempts=0,
            requested_at=now,
        )
        session.add(existing)
        await session.flush()
    await emit_fact(
        session,
        tenant_id=tenant_id,
        user_id=identity.user.id,
        membership_id=identity.membership.id if identity.membership else identity.user.id,
        event_type="resource.deleted",
        source="server",
        payload={
            "resource_type": "thread",
            "resource_id": thread_id,
            "deletion_state": existing.state,
            "deleted_at": now.isoformat(),
        },
        thread_id=thread_id,
        consumers=("deletion_cleanup",),
        occurred_at=now,
    )
    await session.flush()
    return {"thread_id": thread_id, "deletion_state": existing.state}


async def assert_thread_live(
    session: AsyncSession, *, tenant_id: uuid.UUID, thread_id: str
) -> None:
    """已删除会话的写入闸门：晚到回调/新事实一律拒绝（F04）."""
    if await is_thread_deletion_pending(session, tenant_id=tenant_id, thread_id=thread_id):
        raise ThreadDeletedError("该会话已删除，拒绝写入运行事实", code="thread_deleted")


async def is_thread_deletion_pending(
    session: AsyncSession, *, tenant_id: uuid.UUID, thread_id: str
) -> bool:
    """会话**已被删除**（含清理完成）→ 回读与新写一律拒绝.

    F04：删除是**永久**语义，不能因为清理把 state 置为 completed 就重新放行读取与新写
    ——那等于"删完又能读、又能继续写"，用户以为删掉的内容仍在被使用。只要存在删除记录，
    该会话即对外不可用（清理进度由 state/attempts 反映，仅用于运维可见性）。
    """
    row = (
        await session.execute(
            text("SELECT 1 FROM agent_thread_deletions WHERE tenant_id = :ten AND thread_id = :t"),
            {"ten": str(tenant_id), "t": thread_id},
        )
    ).first()
    return row is not None


# ----------------------------------------------------------------------- 查询


async def _enable_operations_read(session: AsyncSession, identity: Identity) -> None:
    """企业运营读：权限校验通过后显式开启 GUC（与数据库策略共同限定）。"""
    await apply_tenant_context(
        session,
        TenantContext(
            user_id=str(identity.user.id),
            tenant_id=str(identity.tenant.id) if identity.tenant else None,
            session_id=str(identity.session.id),
            platform=identity.platform_admin,
        ),
    )
    await session.execute(
        text("SELECT set_config('air_agent.agent_operations', :v, true)"),
        {"v": "on"},
    )


async def get_request_detail(
    session: AsyncSession, identity: Identity, request_id: uuid.UUID
) -> dict[str, Any]:
    """本人请求回读：含尝试、回答版本、Trace 映射；**不含正文**。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFound("对象不存在", code="not_found")
    request = (
        await session.execute(
            select(AgentRequest).where(
                AgentRequest.id == request_id,
                AgentRequest.tenant_id == tenant_id,
                AgentRequest.user_id == identity.user.id,
            )
        )
    ).scalar_one_or_none()
    if request is None:
        raise NotFound("对象不存在", code="not_found")
    # R05：删除中的线程立即禁读（本人回读同样受限）
    if await is_thread_deletion_pending(session, tenant_id=tenant_id, thread_id=request.thread_id):
        raise NotFound("对象不存在", code="not_found")
    attempts = (
        (
            await session.execute(
                select(AgentRunAttempt)
                .where(
                    AgentRunAttempt.tenant_id == tenant_id,
                    AgentRunAttempt.request_id == request_id,
                )
                .order_by(AgentRunAttempt.attempt_index)
            )
        )
        .scalars()
        .all()
    )
    attempt_ids = [a.id for a in attempts]
    answers = (
        (
            await session.execute(
                select(AgentAnswer)
                .where(
                    AgentAnswer.tenant_id == tenant_id,
                    AgentAnswer.attempt_id.in_(attempt_ids or [uuid.uuid4()]),
                )
                .order_by(AgentAnswer.created_at)
            )
        )
        .scalars()
        .all()
    )
    traces = (
        (
            await session.execute(
                select(AgentTraceLink).where(
                    AgentTraceLink.tenant_id == tenant_id,
                    AgentTraceLink.attempt_id.in_(attempt_ids or [uuid.uuid4()]),
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "request": {
            "request_id": str(request.id),
            "thread_id": request.thread_id,
            "graph_id": request.graph_id,
            "operation_kind": request.operation_kind,
            "admitted_at": request.admitted_at.isoformat(),
            "department_snapshot": request.department_snapshot,
            "role_snapshot": request.role_snapshot,
        },
        "attempts": [
            {
                "attempt_id": str(a.id),
                "attempt_index": a.attempt_index,
                "state": a.state,
                "state_revision": a.state_revision,
                "evidence_source": a.evidence_source,
                "platform_run_id": a.platform_run_id,
                "cancel_requested": a.cancel_requested,
                "cancel_origin": a.cancel_origin,
                "cancel_confirmed": a.cancel_confirmed,
                "admitted_at": a.admitted_at.isoformat(),
                "started_at": a.started_at.isoformat() if a.started_at else None,
                "ended_at": a.ended_at.isoformat() if a.ended_at else None,
            }
            for a in attempts
        ],
        "answers": [
            {
                "answer_id": str(x.id),
                "attempt_id": str(x.attempt_id),
                "message_id": x.message_id,
                "content_version": x.content_version,
                "partial": x.partial,
                "confirmed": x.confirmed,
            }
            for x in answers
        ],
        "traces": [
            {
                "attempt_id": str(t.attempt_id),
                "provider": t.provider,
                "project_scope": t.project_scope,
                "trace_id": t.trace_id,
                "primary": t.is_primary,
                "link_source": t.link_source,
            }
            for t in traces
        ],
        "content_included": False,
    }


async def list_runs(
    session: AsyncSession,
    identity: Identity,
    *,
    state: str | None = None,
    graph_id: str | None = None,
    thread_id: str | None = None,
    started_after: dt.datetime | None = None,
    started_before: dt.datetime | None = None,
    cursor: str | None = None,
    limit: int = 50,
    own_only: bool = False,
) -> dict[str, Any]:
    """企业运行明细：运营权限下可跨成员；own_only 时仅本人。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFound("对象不存在", code="not_found")
    if not own_only:
        await _enable_operations_read(session, identity)
    limit = max(1, min(int(limit), 200))
    stmt = (
        select(AgentRunAttempt, AgentRequest.thread_id, AgentRequest.graph_id)
        .join(
            AgentRequest,
            and_(
                AgentRequest.id == AgentRunAttempt.request_id,
                AgentRequest.tenant_id == AgentRunAttempt.tenant_id,
            ),
        )
        .where(AgentRunAttempt.tenant_id == tenant_id)
    )
    if own_only:
        stmt = stmt.where(AgentRunAttempt.user_id == identity.user.id)
    if state:
        stmt = stmt.where(AgentRunAttempt.state == state)
    if graph_id:
        stmt = stmt.where(AgentRunAttempt.graph_id == graph_id)
    if thread_id:
        stmt = stmt.where(AgentRequest.thread_id == thread_id)
    if started_after:
        stmt = stmt.where(AgentRunAttempt.admitted_at >= started_after)
    if started_before:
        stmt = stmt.where(AgentRunAttempt.admitted_at < started_before)
    if cursor:
        try:
            cursor_ts, cursor_id = cursor.split("|", 1)
            # 游标走查询串：ISO 时间里的 `+00:00` 会被解码成空格，必须先还原
            # （生成侧用 quote，兼容历史未编码形态）。
            cursor_dt = dt.datetime.fromisoformat(unquote(cursor_ts))
            stmt = stmt.where(
                (AgentRunAttempt.admitted_at, AgentRunAttempt.id)
                < (cursor_dt, uuid.UUID(cursor_id))
            )
        except (ValueError, TypeError) as exc:
            raise LedgerError("cursor 非法", code="bad_cursor") from exc
    stmt = stmt.order_by(AgentRunAttempt.admitted_at.desc(), AgentRunAttempt.id.desc()).limit(
        limit + 1
    )
    rows = (await session.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        {
            "attempt_id": str(a.id),
            "request_id": str(a.request_id),
            "thread_id": thread_id_value,
            "graph_id": graph_id_value,
            "state": a.state,
            "state_revision": a.state_revision,
            "evidence_source": a.evidence_source,
            "platform_run_id": a.platform_run_id,
            "cancel_requested": a.cancel_requested,
            "admitted_at": a.admitted_at.isoformat(),
            "ended_at": a.ended_at.isoformat() if a.ended_at else None,
            "content_included": False,
        }
        for a, thread_id_value, graph_id_value in rows
    ]
    next_cursor = (
        f"{quote(rows[-1][0].admitted_at.isoformat())}|{rows[-1][0].id}"
        if has_more and rows
        else None
    )
    return {"items": items, "next_cursor": next_cursor, "limit": limit, "content_included": False}


async def summary(
    session: AsyncSession,
    identity: Identity,
    *,
    start: dt.datetime,
    end: dt.datetime,
) -> dict[str, Any]:
    """首期指标：提问/活跃/尝试/恢复/状态/完成率/停止意图；缺失不补零。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFound("对象不存在", code="not_found")
    await _enable_operations_read(session, identity)
    base = (
        select(func.count(), AgentRunAttempt.state)
        .where(
            AgentRunAttempt.tenant_id == tenant_id,
            AgentRunAttempt.admitted_at >= start,
            AgentRunAttempt.admitted_at < end,
        )
        .group_by(AgentRunAttempt.state)
    )
    state_rows = (await session.execute(base)).all()
    state_counts = {str(state): int(count) for count, state in state_rows}
    attempts_total = sum(state_counts.values())

    # 默认 cohort **排除**历史导入请求（historical 标记）：历史线程不得抬高提问量，
    # 也不得与本次真实采集混算；另列 historical 计数供核对。
    is_historical = text("COALESCE(identity_revision ->> 'historical', 'false') = 'true'")
    not_historical = text("COALESCE(identity_revision ->> 'historical', 'false') <> 'true'")
    questions = (
        await session.execute(
            select(func.count(func.distinct(AgentRequest.id))).where(
                AgentRequest.tenant_id == tenant_id,
                AgentRequest.admitted_at >= start,
                AgentRequest.admitted_at < end,
                not_historical,
            )
        )
    ).scalar() or 0
    historical_requests = (
        await session.execute(
            select(func.count(func.distinct(AgentRequest.id))).where(
                AgentRequest.tenant_id == tenant_id,
                AgentRequest.admitted_at >= start,
                AgentRequest.admitted_at < end,
                is_historical,
            )
        )
    ).scalar() or 0
    active_users = (
        await session.execute(
            select(func.count(func.distinct(AgentRequest.user_id))).where(
                AgentRequest.tenant_id == tenant_id,
                AgentRequest.admitted_at >= start,
                AgentRequest.admitted_at < end,
                not_historical,
            )
        )
    ).scalar() or 0
    resumes = (
        await session.execute(
            select(func.count())
            .select_from(AgentRunResume)
            .where(
                AgentRunResume.tenant_id == tenant_id,
                AgentRunResume.created_at >= start,
                AgentRunResume.created_at < end,
            )
        )
    ).scalar() or 0
    stop_intents = (
        await session.execute(
            select(func.count())
            .select_from(AgentRunAttempt)
            .where(
                AgentRunAttempt.tenant_id == tenant_id,
                AgentRunAttempt.admitted_at >= start,
                AgentRunAttempt.admitted_at < end,
                AgentRunAttempt.cancel_requested.is_(True),
                AgentRunAttempt.cancel_origin == "user_stop",
            )
        )
    ).scalar() or 0
    latency = await _latency_metrics(session, tenant_id, start, end)
    ended = sum(int(state_counts.get(s, 0)) for s in ENDED_STATES)
    succeeded = int(state_counts.get("succeeded", 0))
    waiting = int(state_counts.get("waiting_input", 0))
    unknown = int(state_counts.get("status_unknown", 0))
    admitting = int(state_counts.get("admitting", 0)) + int(
        state_counts.get("admission_unknown", 0)
    )
    projected_at, last_event_at = await _projection_times(session, tenant_id)
    now = dt.datetime.now(dt.UTC)
    return {
        "metric_version": METRIC_VERSION,
        "as_of": (last_event_at or now).isoformat(),
        "projected_at": projected_at.isoformat() if projected_at else None,
        "data_lag_seconds": int((now - last_event_at).total_seconds()) if last_event_at else None,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "tz": BUSINESS_TZ},
        "cohort": "attempts_admitted_in_window",
        "questions": {"value": int(questions), "status": "measured"},
        # 历史导入单列（excluded_from_default_cohort），不补造反馈/Token/耗时
        "historical_requests": {
            "value": int(historical_requests),
            "status": "historical_unattributed" if historical_requests else "zero",
            "excluded_from_default_cohort": True,
        },
        "active_users": {"value": int(active_users), "status": "measured"},
        "attempts": {"value": attempts_total, "status": "measured"},
        "resumes": {"value": int(resumes), "status": "measured"},
        "states": state_counts,
        "technical_completion_rate": {
            "numerator": succeeded,
            "denominator": ended,
            "value": round(succeeded / ended, 4) if ended else None,
            "status": "measured" if ended else "not_collected",
            "not_ended": {
                "waiting_input": waiting,
                "status_unknown": unknown,
                "admitting_or_unknown": admitting,
            },
        },
        "user_stop_intent": {
            "value": int(stop_intents),
            "denominator": attempts_total,
            "status": "measured",
        },
        "actual_cancelled": {
            "value": int(state_counts.get("cancelled", 0)),
            "status": "measured",
        },
        # 未采集项显式 null + 标签，禁止伪零
        "first_content_ms": {"value": None, "status": "not_collected", "reserved_for": "change-3"},
        "model_ttft_ms": {"value": None, "status": "not_collected", "reserved_for": "change-5"},
        "feedback_rate": {"value": None, "status": "not_collected", "reserved_for": "change-3"},
        "copy_rate": {"value": None, "status": "not_collected", "reserved_for": "change-3"},
        "usage_tokens": {"value": None, "status": "not_collected", "reserved_for": "change-5"},
        "cost": {"value": None, "status": "not_collected", "reserved_for": "change-5"},
        "measurement_source": "ledger_facts",
        # 服务端耗时与分位数（口径见 docs/contracts/agent-events-v1/metrics.json）：
        # 只取服务端时钟（admitted_at → ended_at），不与浏览器墙钟混算；人工等待另列；
        # 缺失样本单列，不补零、不平均每日分位数。
        "server_latency_ms": latency,
        "payload_bytes": None,
    }


async def _latency_metrics(
    session: AsyncSession, tenant_id: uuid.UUID, start: dt.datetime, end: dt.datetime
) -> dict[str, Any]:
    """窗口内已结束尝试的服务耗时分位数（服务端时钟；明细计算，不平均每日分位数）."""
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT
                  percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (ended_at - admitted_at)) * 1000) AS p50,
                  percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (ended_at - admitted_at)) * 1000) AS p95,
                  percentile_cont(0.99) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (ended_at - admitted_at)) * 1000) AS p99,
                  count(*) AS sample_count
                FROM agent_run_attempts
                WHERE tenant_id = :t AND admitted_at >= :s AND admitted_at < :e
                  AND ended_at IS NOT NULL
                """
                ),
                {"t": str(tenant_id), "s": start, "e": end},
            )
        )
        .mappings()
        .first()
    )
    missing = (
        await session.execute(
            text(
                "SELECT count(*) FROM agent_run_attempts WHERE tenant_id = :t"
                " AND admitted_at >= :s AND admitted_at < :e AND ended_at IS NULL"
            ),
            {"t": str(tenant_id), "s": start, "e": end},
        )
    ).scalar() or 0
    samples = int(row["sample_count"] or 0) if row else 0
    p50 = float(row["p50"]) if row and row["p50"] is not None else None
    p95 = float(row["p95"]) if row and row["p95"] is not None else None
    p99 = float(row["p99"]) if row and row["p99"] is not None else None
    return {
        "p50_ms": round(p50, 1) if p50 is not None else None,
        "p95_ms": round(p95, 1) if p95 is not None else None,
        "p99_ms": round(p99, 1) if p99 is not None else None,
        "sample_count": samples,
        "missing_count": int(missing),
        "status": "measured" if samples else "not_collected",
        "measurement_source": "server_clock_admitted_to_ended",
        "quantile_method": "percentile_cont_on_window_detail",
    }


async def _projection_times(
    session: AsyncSession, tenant_id: uuid.UUID
) -> tuple[dt.datetime | None, dt.datetime | None]:
    row = (
        await session.execute(
            select(AgentProjectionState).where(
                AgentProjectionState.tenant_id == tenant_id,
                AgentProjectionState.consumer == "run_projection",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        last_event = (
            await session.execute(
                select(func.max(AgentEvent.occurred_at)).where(AgentEvent.tenant_id == tenant_id)
            )
        ).scalar()
        return None, last_event
    return row.projected_at, row.last_event_at


async def get_run_detail(
    session: AsyncSession, identity: Identity, attempt_id: uuid.UUID
) -> dict[str, Any]:
    """企业运行详情（运营权限）：元数据 + 回答版本关联 + Trace 映射；无正文。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFound("对象不存在", code="not_found")
    await _enable_operations_read(session, identity)
    attempt = await _load_attempt(session, attempt_id, tenant_id)
    request = (
        await session.execute(
            select(AgentRequest).where(
                AgentRequest.id == attempt.request_id,
                AgentRequest.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    answers = (
        (
            await session.execute(
                select(AgentAnswer).where(
                    AgentAnswer.tenant_id == tenant_id, AgentAnswer.attempt_id == attempt_id
                )
            )
        )
        .scalars()
        .all()
    )
    traces = (
        (
            await session.execute(
                select(AgentTraceLink).where(
                    AgentTraceLink.tenant_id == tenant_id, AgentTraceLink.attempt_id == attempt_id
                )
            )
        )
        .scalars()
        .all()
    )
    resumes = (
        await session.execute(
            select(func.count())
            .select_from(AgentRunResume)
            .where(AgentRunResume.tenant_id == tenant_id, AgentRunResume.attempt_id == attempt_id)
        )
    ).scalar() or 0
    return {
        "attempt": {
            "attempt_id": str(attempt.id),
            "request_id": str(attempt.request_id),
            "thread_id": request.thread_id if request else None,
            "graph_id": attempt.graph_id,
            "state": attempt.state,
            "state_revision": attempt.state_revision,
            "evidence_source": attempt.evidence_source,
            "platform_status": attempt.platform_status,
            "platform_run_id": attempt.platform_run_id,
            "cancel_requested": attempt.cancel_requested,
            "cancel_origin": attempt.cancel_origin,
            "cancel_phase": attempt.cancel_phase,
            "cancel_confirmed": attempt.cancel_confirmed,
            "admitted_at": attempt.admitted_at.isoformat(),
            "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
            "ended_at": attempt.ended_at.isoformat() if attempt.ended_at else None,
            "resume_count": int(resumes),
        },
        "answers": [
            {
                "answer_id": str(a.id),
                "message_id": a.message_id,
                "content_version": a.content_version,
                "partial": a.partial,
                "confirmed": a.confirmed,
            }
            for a in answers
        ],
        "traces": [
            {
                "provider": t.provider,
                "project_scope": t.project_scope,
                "trace_id": t.trace_id,
                "primary": t.is_primary,
                "link_source": t.link_source,
            }
            for t in traces
        ],
        "content_included": False,
    }


async def read_request_content(
    session: AsyncSession, identity: Identity, request_id: uuid.UUID
) -> dict[str, Any]:
    """原文读取：需 `agent:content:read`（路由层已校验）；仅本人企业范围内。"""
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFound("对象不存在", code="not_found")
    request = (
        await session.execute(
            select(AgentRequest).where(
                AgentRequest.id == request_id, AgentRequest.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if request is None:
        raise NotFound("对象不存在", code="not_found")
    # R05：删除中/已删除一律禁读（立即生效，不等后台清理完成）
    if await is_thread_deletion_pending(session, tenant_id=tenant_id, thread_id=request.thread_id):
        raise NotFound("对象不存在", code="not_found")
    now = dt.datetime.now(dt.UTC)
    # 交付缺口：按**请求**精确读取不可漂移快照（不按可变 thread 指针取全量）；
    # 保留期到期即拒绝。
    settings = get_settings()
    policy_days = settings.ledger_content_retention_days_by_tenant.get(
        str(tenant_id), settings.ledger_content_retention_days
    )
    refs = (
        (
            await session.execute(
                select(AgentContentRef).where(
                    AgentContentRef.tenant_id == tenant_id,
                    AgentContentRef.request_id == request.id,
                    AgentContentRef.deleted_at.is_(None),
                    AgentContentRef.created_at > now - dt.timedelta(days=policy_days),
                    AgentContentRef.retention_until.is_(None)
                    | (AgentContentRef.retention_until > now),
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "request_id": str(request.id),
        "thread_id": request.thread_id,
        "contents": [
            {
                "content_ref_id": str(r.id),
                "message_id": r.message_id,
                "content_version": r.content_version,
                "content": r.content_snapshot,
                "retention_until": r.retention_until.isoformat() if r.retention_until else None,
            }
            for r in refs
        ],
    }


async def ingest_browser_events(
    session: AsyncSession, identity: Identity, *, events: list[dict[str, Any]]
) -> dict[str, Any]:
    """浏览器事件受理：来源固定为 browser（服务端覆盖），逐项结果。

    v1 全部浏览器类型预留未启用 → 逐项 `type_not_enabled`；批量上限仍生效。
    """
    from app.services.event_contract import validate_batch  # noqa: PLC0415

    if identity.tenant is None or identity.membership is None:
        raise AdmissionRejected("缺少企业上下文", code="no_tenant_context")
    tenant_id = identity.tenant.id
    user_id = identity.user.id
    membership_id = identity.membership.id
    trusted = {
        "source": "browser",
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "membership_id": str(membership_id),
    }
    overridden = [{**ev, **trusted} if isinstance(ev, dict) else ev for ev in events]
    result = validate_batch(overridden, source="browser")
    # 仅写入服务端确认合法的项（v1 通常为空集），并保持逐项结果
    written = 0
    now = dt.datetime.now(dt.UTC)
    for event, item in zip(overridden, result["items"], strict=False):
        if not item["valid"] or not isinstance(event, dict):
            continue
        occurred = dt.datetime.fromisoformat(str(event["occurred_at"]).replace("Z", "+00:00"))
        event_id = uuid.UUID(str(event["event_id"]))
        session.add(
            AgentEvent(
                event_id=event_id,
                tenant_id=tenant_id,
                user_id=user_id,
                membership_id=membership_id,
                schema_version=str(event.get("schema_version", "1.0")),
                event_type=str(event["event_type"]),
                source="browser",
                occurred_at=occurred,
                received_at=now,
                thread_id=event.get("thread_id"),
                request_id=_opt_uuid(event.get("request_id")),
                attempt_id=_opt_uuid(event.get("attempt_id")),
                run_id=event.get("run_id"),
                answer_id=_opt_uuid(event.get("answer_id")),
                message_id=event.get("message_id"),
                payload=dict(event.get("payload") or {}),
                payload_digest=canonical_digest(event.get("payload") or {}),
                created_at=now,
            )
        )
        written += 1
    if written:
        await session.flush()
    return {**result, "written": written}


def _opt_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


async def list_pending_attempts(
    session: AsyncSession, identity: Identity, *, thread_id: str
) -> list[dict[str, Any]]:
    """线程上尚未确认平台映射的尝试（供流式运行的 run_id 确认候选）。

    只返回本人、且未持有平台 run 映射的尝试；调用方须再用平台 run metadata 校验。
    """
    tenant_id = identity.tenant.id if identity.tenant else None
    if tenant_id is None:
        raise NotFound("对象不存在", code="not_found")
    rows = (
        (
            await session.execute(
                select(AgentRunAttempt)
                .join(
                    AgentRequest,
                    and_(
                        AgentRequest.id == AgentRunAttempt.request_id,
                        AgentRequest.tenant_id == AgentRunAttempt.tenant_id,
                    ),
                )
                .where(
                    AgentRunAttempt.tenant_id == tenant_id,
                    AgentRunAttempt.user_id == identity.user.id,
                    AgentRequest.thread_id == thread_id,
                    AgentRunAttempt.platform_run_id.is_(None),
                    AgentRunAttempt.state.in_(("admitting", "admission_unknown")),
                )
                .order_by(AgentRunAttempt.admitted_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "request_id": str(a.request_id),
            "attempt_id": str(a.id),
            "state": a.state,
            "ledger_marker": ledger_marker(a.request_id, a.id),
        }
        for a in rows
    ]


async def outbox_health(session: AsyncSession, identity: Identity) -> dict[str, Any]:
    """积压可见：pending/oldest_age/retry/dead_letter（平台运维权限）。"""
    now = dt.datetime.now(dt.UTC)
    rows = (
        await session.execute(
            text(
                "SELECT status, count(*) AS n, min(available_at) AS oldest"
                " FROM agent_outbox GROUP BY status"
            )
        )
    ).all()
    by_status = {str(s): int(n) for s, n, _ in rows}
    oldest = min((r[2] for r in rows if r[2] is not None), default=None)
    return {
        "now": now.isoformat(),
        "pending": by_status.get("pending", 0),
        "leased": by_status.get("leased", 0),
        "dead_letter": by_status.get("dead_letter", 0),
        "done": by_status.get("done", 0),
        "oldest_available_at": oldest.isoformat() if oldest else None,
        "oldest_age_seconds": int((now - oldest).total_seconds()) if oldest else None,
    }
