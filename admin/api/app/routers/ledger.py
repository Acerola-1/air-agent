"""运行台账路由（change add-agent-event-ledger）。

权限分离：
- 受理/停止意图/浏览器事件：当前活跃成员 + 企业上下文（`require_tenant_context`）。
- 本人回读：会话身份，查询只返回本人对象（跨范围统一 404）。
- 企业运营明细/统计：`agent:operations:read` / `agent:metrics:read`。
- 原文：`agent:content:read`（独立权限，不随运营元数据自动获得）。
- 内部 runtime 写入与受理确认：`x-internal-token` + 会话身份（浏览器不可直接调用）。
- 重放/清理：`agent:replay:manage`（平台专属）。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from app.config import get_settings
from app.db import TenantContext, apply_tenant_context
from app.deps import (
    ActiveTenantIdentity,
    IdentityDep,
    SessionDep,
    TenantContextIdentity,
    require_internal_token,
    require_permission,
    verify_write_origin,
)
from app.models import Tenant
from app.schemas_ledger import (
    AdmissionConfirmIn,
    AdmissionResultOut,
    AdmitRequestIn,
    AnswerIn,
    BrowserEventIn,
    BrowserEventOut,
    ReconcileTicketIn,
    ReconcileTicketOut,
    ResumeIn,
    RunListOut,
    StartClaimIn,
    StartClaimOut,
    StateObservedIn,
    StopIntentIn,
    ThreadDeletionIn,
    TraceLinkIn,
)
from app.security import sign_reconcile_ticket
from app.services import ledger as ledger_service

router = APIRouter()


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, ledger_service.LedgerError):
        return HTTPException(status_code=exc.status_code, detail=exc.message)
    raise exc


# ------------------------------------------------------------------ 受理与回读


@router.post("/requests", response_model=AdmissionResultOut, summary="可信受理提问")
async def admit_request(
    payload: AdmitRequestIn,
    session: SessionDep,
    identity: TenantContextIdentity,
) -> AdmissionResultOut:
    """受理：幂等创建/复用 request+attempt，同事务写事件与 outbox。

    台账事务失败 → 503（调用方不得启动平台运行）。
    """
    try:
        result = await ledger_service.admit_request(
            session,
            identity,
            thread_id=payload.thread_id,
            submission_key=payload.submission_key,
            graph_id=payload.graph_id,
            operation_kind=payload.operation_kind,
            content=payload.content,
            user_message_id=payload.user_message_id,
            source_attempt_id=payload.source_attempt_id,
            content_snapshot=payload.content if payload.store_content else None,
            retention_days=payload.retention_days,
            menu_name=payload.menu_name,
            capability_selection=payload.capability_selection,
        )
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return AdmissionResultOut(**result.as_dict())


@router.get("/requests/{request_id}", summary="本人请求与回答版本回读（无正文）")
async def get_request(request_id: uuid.UUID, session: SessionDep, identity: IdentityDep) -> dict:
    try:
        detail = await ledger_service.get_request_detail(session, identity, request_id)
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return detail


@router.get("/requests/{request_id}/content", summary="原问题原文（独立权限）")
async def get_request_content(
    request_id: uuid.UUID,
    session: SessionDep,
    identity: Annotated[object, Depends(require_permission("agent:content:read"))],
) -> dict:
    """原文读取独立于运营元数据权限；普通运营账号不具备该权限。"""
    try:
        detail = await ledger_service.read_request_content(session, identity, request_id)
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return detail


# ------------------------------------------------------- 内部 runtime 写入面


@router.post(
    "/runs/start-claim",
    response_model=StartClaimOut,
    summary="启动权原子裁决（内部）：一次业务提交只能启动一次平台运行",
    dependencies=[Depends(require_internal_token)],
)
async def start_claim(
    payload: StartClaimIn, session: SessionDep, identity: IdentityDep
) -> StartClaimOut:
    """启动权裁决的**管理侧视图**（内部/运维用；与原生入口共用同一裁决规则）。

    F01 起**权威裁决点在原生入口**（平台 auth hook，唯一不可绕过），聊天代理不再预先
    消费启动权；本端点保留给内部工具与校准，语义与原生一致：

    - `claimed`：本次胜出；
    - `already_started` / `already_confirmed`：不得重复创建，应复用既有运行或如实报告"待确认"；
    - `mismatch`：换图/换操作/换正文，拒绝。
    """
    try:
        result = await ledger_service.claim_start(
            session,
            identity,
            submission_id=payload.submission_id,
            attempt_id=payload.attempt_id,
            thread_id=payload.thread_id,
            graph_id=payload.graph_id,
            operation_kind=payload.operation_kind,
            content=payload.content,
            source="bff",
        )
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return StartClaimOut(**result)


@router.post(
    "/runs/admission-confirmed",
    summary="平台受理结果确认（内部）",
    dependencies=[Depends(require_internal_token)],
)
async def admission_confirmed(
    payload: AdmissionConfirmIn, session: SessionDep, identity: IdentityDep
) -> dict:
    try:
        result = await ledger_service.confirm_admission(
            session,
            identity,
            request_id=payload.request_id,
            attempt_id=payload.attempt_id,
            submission_id=payload.submission_id,
            platform_run_id=payload.platform_run_id,
            outcome=payload.outcome,
            reason=payload.reason,
        )
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


@router.post(
    "/runs/{attempt_id}/state",
    summary="运行状态观测（内部）",
    dependencies=[Depends(require_internal_token)],
)
async def observe_state(
    attempt_id: uuid.UUID, payload: StateObservedIn, session: SessionDep, identity: IdentityDep
) -> dict:
    try:
        result = await ledger_service.observe_state(
            session,
            identity,
            attempt_id=attempt_id,
            state=payload.state,
            evidence_source=payload.evidence_source,
            platform_status=payload.platform_status,
            state_revision=payload.state_revision,
        )
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


@router.post(
    "/runs/{attempt_id}/resume",
    summary="人工恢复记录（内部）",
    dependencies=[Depends(require_internal_token)],
)
async def record_resume(
    attempt_id: uuid.UUID, payload: ResumeIn, session: SessionDep, identity: IdentityDep
) -> dict:
    try:
        result = await ledger_service.record_resume(
            session, identity, attempt_id=attempt_id, platform_run_id=payload.platform_run_id
        )
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


@router.post(
    "/runs/{attempt_id}/answers",
    summary="可见回答版本记录（内部）",
    dependencies=[Depends(require_internal_token)],
)
async def record_answer(
    attempt_id: uuid.UUID, payload: AnswerIn, session: SessionDep, identity: IdentityDep
) -> dict:
    try:
        result = await ledger_service.record_answer(
            session,
            identity,
            attempt_id=attempt_id,
            message_id=payload.message_id,
            content_version=payload.content_version,
            confirmed=payload.confirmed,
        )
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


@router.post(
    "/runs/{attempt_id}/traces",
    summary="Trace 映射（内部）",
    dependencies=[Depends(require_internal_token)],
)
async def link_trace(
    attempt_id: uuid.UUID, payload: TraceLinkIn, session: SessionDep, identity: IdentityDep
) -> dict:
    try:
        result = await ledger_service.link_trace(
            session,
            identity,
            attempt_id=attempt_id,
            provider=payload.provider,
            trace_id=payload.trace_id,
            project_scope=payload.project_scope,
            primary=payload.primary,
            link_source=payload.link_source,
        )
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


# ----------------------------------------------------------------- 停止意图


@router.post("/runs/stop-intent", summary="停止意图（独立于实际终态）")
async def stop_intent(
    payload: StopIntentIn, request: Request, session: SessionDep, identity: ActiveTenantIdentity
) -> dict:
    verify_write_origin(request)
    try:
        result = await ledger_service.record_stop_intent(
            session,
            identity,
            attempt_id=payload.attempt_id,
            thread_id=payload.thread_id,
            origin=payload.origin,
            phase=payload.phase,
            platform_run_id=payload.platform_run_id,
        )
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


# ----------------------------------------------------------------- 企业查询


@router.get("/runs", response_model=RunListOut, summary="企业运行明细（运营权限）")
async def list_runs(
    session: SessionDep,
    identity: Annotated[object, Depends(require_permission("agent:operations:read"))],
    state: str | None = None,
    graph_id: str | None = None,
    thread_id: str | None = None,
    started_after: dt.datetime | None = None,
    started_before: dt.datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> RunListOut:
    try:
        result = await ledger_service.list_runs(
            session,
            identity,
            state=state,
            graph_id=graph_id,
            thread_id=thread_id,
            started_after=started_after,
            started_before=started_before,
            cursor=cursor,
            limit=limit,
        )
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return RunListOut(**result)


@router.get("/runs/pending-attempts", summary="线程上待确认平台映射的本人尝试")
async def pending_attempts(
    session: SessionDep,
    identity: IdentityDep,
    thread_id: str = Query(min_length=1, max_length=128),
) -> dict:
    """流式运行的 run_id 确认候选（调用方须再用平台 run metadata 校验）。"""
    try:
        rows = await ledger_service.list_pending_attempts(session, identity, thread_id=thread_id)
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return {"items": rows}


@router.get("/runs/{attempt_id}", summary="运行详情（运营权限，无正文）")
async def get_run(
    attempt_id: uuid.UUID,
    session: SessionDep,
    identity: Annotated[object, Depends(require_permission("agent:operations:read"))],
) -> dict:
    try:
        result = await ledger_service.get_run_detail(session, identity, attempt_id)
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


@router.get(
    "/deletions/due",
    summary="待清理的删除标记（内部，有界调度）",
    dependencies=[Depends(require_internal_token)],
)
async def deletions_due(
    session: SessionDep,
    identity: IdentityDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    """清理 worker 的有界输入：非完成态删除标记（不恢复任何业务权限）."""
    rows = await ledger_service.list_deletions_due(session, limit=limit)
    await session.commit()
    return {"items": rows}


@router.get("/metrics/summary", summary="首期运行统计（统计权限）")
async def metrics_summary(
    session: SessionDep,
    identity: Annotated[object, Depends(require_permission("agent:metrics:read"))],
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> dict:
    now = dt.datetime.now(dt.UTC)
    window_end = end or now
    window_start = start or (window_end - dt.timedelta(days=7))
    try:
        result = await ledger_service.summary(session, identity, start=window_start, end=window_end)
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


# --------------------------------------------------------- 浏览器事件与删除


@router.post("/events/browser", response_model=BrowserEventOut, summary="浏览器事件（逐项结果）")
async def browser_events(
    payload: BrowserEventIn, request: Request, session: SessionDep, identity: ActiveTenantIdentity
) -> BrowserEventOut:
    """浏览器来源：类型白名单 + 服务端身份覆盖；浏览器不能上报运行终态或模型用量。"""
    verify_write_origin(request)
    result = await ledger_service.ingest_browser_events(session, identity, events=payload.events)
    await session.commit()
    return BrowserEventOut(**result)


@router.post("/threads/deletion", summary="请求会话删除（立即禁读 + 异步清理）")
async def request_deletion(
    payload: ThreadDeletionIn, request: Request, session: SessionDep, identity: ActiveTenantIdentity
) -> dict:
    verify_write_origin(request)
    try:
        result = await ledger_service.request_thread_deletion(
            session, identity, thread_id=payload.thread_id
        )
    except ledger_service.LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


@router.post(
    "/reconcile/ticket",
    response_model=ReconcileTicketOut,
    summary="签发服务级只读校准票据（内部）",
    dependencies=[Depends(require_internal_token)],
)
async def issue_reconcile_ticket(
    payload: ReconcileTicketIn,
    session: SessionDep,
) -> ReconcileTicketOut:
    """F05：签发**服务主体**的窄范围只读校准凭据（rs-v1）.

    与用户会话票据完全分离的密钥与受众：原成员被撤销后，运维校准仍可按租户只读运行状态，
    但该凭据不能用于创建运行、读取消息或任何业务写入。租户必须真实存在且可用。

    凭据门槛是**部署内部令牌**（与其它内部端点同级），不要求任何用户会话——否则
    "原成员被撤销后仍可校准"这一目标就无法成立。
    """
    settings = get_settings()
    keys = settings.ledger_reconcile_key_pairs
    if not keys:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "reconcile_credential_unconfigured",
                "error": "服务校准密钥未配置，拒绝签发",
            },
        )
    # 内部令牌门控的运维端点：显式开启平台作用域读租户（RLS 仍生效，不绕过）。
    await apply_tenant_context(session, TenantContext(platform=True))
    tenant = (
        await session.execute(select(Tenant).where(Tenant.id == payload.tenant_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail={"code": "tenant_not_found"})
    if str(tenant.status) != "active":
        raise HTTPException(status_code=403, detail={"code": "tenant_inactive"})
    ticket = sign_reconcile_ticket(
        str(payload.tenant_id),
        keys,
        audience=settings.ledger_reconcile_audience,
        ttl_seconds=payload.ttl_seconds,
        thread_ids=[str(item) for item in payload.thread_ids],
    )
    return ReconcileTicketOut(
        ticket=ticket,
        scope="ledger:reconcile",
        tenant_id=str(payload.tenant_id),
        expires_in=payload.ttl_seconds,
        thread_ids=[str(item) for item in payload.thread_ids],
    )


@router.get("/health", summary="台账健康（积压可见）")
async def ledger_health(
    session: SessionDep,
    identity: Annotated[object, Depends(require_permission("agent:replay:manage"))],
) -> dict:
    return await ledger_service.outbox_health(session, identity)
