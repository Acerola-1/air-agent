"""运营后台 v1 路由（`/agent/console/**`）.

浏览器只走同源 BFF；本路由不暴露数据库/IAM 凭据。读接口使用独立的
REPEATABLE READ READ ONLY 事务，保证同一响应看到固定截止；写接口使用请求
事务并在提交前完成权限、CAS、幂等与审计。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas_console as dto
from app.db import SessionLocal
from app.deps import (
    IdentityDep,
    SessionDep,
    require_active_tenant,
    require_tenant_context,
    verify_write_origin,
)
from app.schemas_console import FeedbackCaseActionIn
from app.services.console import content as content_service
from app.services.console import feedback_triage, metrics, queries
from app.services.console.scope import (
    CONTENT_PAGE_CHARS,
    MAX_LIMIT,
    ConsoleError,
)
from app.services.ledger import LedgerError, NotFound


async def validate_query(request: Request) -> None:
    allowed = {field.alias for field in request.scope["route"].dependant.query_params}
    unknown = set(request.query_params) - allowed
    repeated = [key for key in request.query_params if len(request.query_params.getlist(key)) > 1]
    if unknown or repeated:
        raise HTTPException(
            status_code=422,
            detail={"code": "unsupported_filter", "error": "存在不支持或重复的查询条件"},
        )


CONSOLE_REQUEST_TIMEOUT_SECONDS = 5


class ConsoleRoute(APIRoute):
    def __init__(self, *args, **kwargs):
        kwargs["response_model_exclude_unset"] = True
        super().__init__(*args, **kwargs)

    def get_route_handler(self):
        original = super().get_route_handler()

        async def bounded(request: Request):
            try:
                async with asyncio.timeout(CONSOLE_REQUEST_TIMEOUT_SECONDS):
                    return await original(request)
            except TimeoutError:
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": {"code": "query_timeout", "error": "查询超时，请缩小范围后重试"}
                    },
                    headers={"Cache-Control": "no-store"},
                )

        return bounded


router = APIRouter(
    prefix="/console",
    dependencies=[Depends(require_active_tenant), Depends(validate_query)],
    route_class=ConsoleRoute,
    responses={
        code: {"model": dto.ConsoleErrorResponse} for code in (400, 401, 403, 404, 409, 422, 503)
    },
)


async def get_console_session(
    identity: IdentityDep, identity_session: SessionDep
) -> AsyncIterator[AsyncSession]:
    """完成实时身份解析后释放连接，再建立独立只读快照。

    身份引用投影先提交，避免读快照看不到新引用；不同时占用两条池连接。
    只读快照中的企业/权限GUC仍由scope_session按该次可信身份设置。
    """
    await identity_session.commit()
    async with SessionLocal() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await session.execute(text("SET LOCAL statement_timeout = '5s'"))
        try:
            yield session
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "57014":
                raise HTTPException(
                    status_code=503,
                    detail={"code": "query_timeout", "error": "统计查询超时，请缩小范围后重试"},
                ) from exc
            raise


ConsoleSessionDep = Annotated[AsyncSession, Depends(get_console_session)]


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail={"code": exc.code, "error": exc.message})
    if isinstance(exc, ConsoleError):
        code = exc.code
        if code in {
            "bad_cursor",
            "cursor_filter_mismatch",
            "invalid_limit",
        }:
            http_status = 400
        elif code in {
            "permission_denied",
            "user_scope_requires_operations",
            "no_tenant_context",
            "membership_inactive",
            "tenant_inactive",
        }:
            http_status = 403
        elif code in {
            "invalid_window",
            "invalid_group_by",
            "invalid_metric",
            "invalid_source",
            "invalid_kind",
            "invalid_id",
            "invalid_offset",
            "invalid_action",
            "invalid_operation_id",
            "invalid_revision",
            "note_too_long",
            "note_required",
            "unsupported_filter",
        }:
            http_status = 422
        else:
            http_status = getattr(exc, "status_code", 400)
        return HTTPException(
            status_code=http_status,
            detail={"code": exc.code, "error": exc.message},
        )
    if isinstance(exc, LedgerError):
        return HTTPException(
            status_code=getattr(exc, "status_code", 503),
            detail={"code": exc.code, "error": exc.message},
        )
    raise exc


def _reject(filters: dict[str, object]) -> None:
    present = [name for name, value in filters.items() if value is not None]
    if present:
        raise ConsoleError(f"不支持的过滤条件: {', '.join(present)}", code="unsupported_filter")


@router.get("/overview", response_model=dto.ConsoleOverview, summary="运营总览（统计权限）")
async def overview(
    session: ConsoleSessionDep,
    identity: IdentityDep,
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    business_code: str | None = Query(default=None, max_length=64),
    graph_id: str | None = Query(default=None, max_length=64),
    user_id: uuid.UUID | None = Query(default=None),
    model: str | None = Query(default=None, max_length=128),
    skill_id: str | None = Query(default=None, max_length=128),
    tool_id: str | None = Query(default=None, max_length=128),
) -> dict:
    try:
        _reject({"user_id": user_id, "model": model, "skill_id": skill_id, "tool_id": tool_id})
        return await metrics.overview(
            session,
            identity,
            window=window,
            start=start,
            end=end,
            business_code=business_code,
            graph_id=graph_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get("/trends", response_model=dto.ConsoleTrends, summary="日趋势（统计权限）")
async def trends(
    session: ConsoleSessionDep,
    identity: IdentityDep,
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    business_code: str | None = Query(default=None, max_length=64),
    graph_id: str | None = Query(default=None, max_length=64),
    metric: str | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    model: str | None = Query(default=None, max_length=128),
    skill_id: str | None = Query(default=None, max_length=128),
    tool_id: str | None = Query(default=None, max_length=128),
) -> dict:
    try:
        _reject({"user_id": user_id, "model": model, "skill_id": skill_id, "tool_id": tool_id})
        return await metrics.trends(
            session,
            identity,
            window=window,
            start=start,
            end=end,
            business_code=business_code,
            graph_id=graph_id,
            metric=metric,
        )
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get(
    "/users",
    response_model=dto.ConsoleList[dto.ConsoleUserUsage],
    summary="用户用量（统计+运营权限）",
)
async def users(
    session: ConsoleSessionDep,
    identity: IdentityDep,
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    business_code: str | None = Query(default=None, max_length=64),
    graph_id: str | None = Query(default=None, max_length=64),
    user_id: uuid.UUID | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=4096),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> dict:
    try:
        return await queries.list_users(
            session,
            identity,
            window=window,
            start=start,
            end=end,
            business_code=business_code,
            graph_id=graph_id,
            user_id=user_id,
            cursor=cursor,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get("/runs", response_model=dto.ConsoleList[dto.ConsoleRun], summary="运行列表（运营权限）")
async def runs(
    session: ConsoleSessionDep,
    identity: IdentityDep,
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    business_code: str | None = Query(default=None, max_length=64),
    graph_id: str | None = Query(default=None, max_length=64),
    user_id: uuid.UUID | None = Query(default=None),
    state: str | None = Query(default=None, max_length=32),
    thread_id: str | None = Query(default=None, max_length=128),
    cursor: str | None = Query(default=None, max_length=4096),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> dict:
    allowed_states = {
        "admitting",
        "admission_unknown",
        "queued",
        "running",
        "waiting_input",
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
        "status_unknown",
    }
    try:
        if state is not None and state not in allowed_states:
            raise ConsoleError("不支持的运行状态", code="invalid_kind")
        return await queries.list_runs(
            session,
            identity,
            window=window,
            start=start,
            end=end,
            business_code=business_code,
            graph_id=graph_id,
            user_id=user_id,
            state=state,
            thread_id=thread_id,
            cursor=cursor,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get(
    "/runs/{attempt_id}",
    response_model=dto.ConsoleRunDetail,
    summary="运行详情（运营权限；无正文）",
)
async def run_detail(
    attempt_id: uuid.UUID, session: ConsoleSessionDep, identity: IdentityDep
) -> dict:
    try:
        return await queries.get_run(session, identity, attempt_id)
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


def _no_store(payload: dict) -> JSONResponse:
    return JSONResponse(
        content=dto.ConsoleContentPage.model_validate(payload).model_dump(
            mode="json", exclude_unset=True
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/requests/{request_id}/content",
    response_model=dto.ConsoleContentPage,
    summary="原问题正文分页（运营+正文）",
)
async def request_content(
    request_id: uuid.UUID,
    session: ConsoleSessionDep,
    identity: IdentityDep,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=CONTENT_PAGE_CHARS, ge=1, le=CONTENT_PAGE_CHARS),
) -> JSONResponse:
    try:
        payload = await content_service.read_request_content(
            session, identity, request_id=request_id, offset=offset, limit=limit
        )
        return _no_store(payload)
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get(
    "/answers/{answer_id}/content",
    response_model=dto.ConsoleContentPage,
    summary="确认回答正文分页（运营+正文）",
)
async def answer_content(
    answer_id: uuid.UUID,
    session: ConsoleSessionDep,
    identity: IdentityDep,
    content_version: int = Query(ge=1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=CONTENT_PAGE_CHARS, ge=1, le=CONTENT_PAGE_CHARS),
) -> JSONResponse:
    try:
        payload = await content_service.read_answer_content(
            session,
            identity,
            answer_id=answer_id,
            content_version=content_version,
            offset=offset,
            limit=limit,
        )
        return _no_store(payload)
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get(
    "/feedback",
    response_model=dto.ConsoleList[dto.ConsoleFeedback],
    summary="当前反馈列表（运营权限；无正文）",
)
async def feedback_list(
    session: ConsoleSessionDep,
    identity: IdentityDep,
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    business_code: str | None = Query(default=None, max_length=64),
    graph_id: str | None = Query(default=None, max_length=64),
    rating: Literal["positive", "negative", "none"] | None = Query(default=None),
    reason: Literal["answer_incorrect", "irrelevant", "incomplete", "slow", "other"] | None = Query(
        default=None
    ),
    case_state: Literal["pending", "in_progress", "resolved"] | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=4096),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> dict:
    try:
        return await queries.list_feedback(
            session,
            identity,
            window=window,
            start=start,
            end=end,
            business_code=business_code,
            graph_id=graph_id,
            rating=rating,
            reason=reason,
            case_state=case_state,
            cursor=cursor,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get(
    "/feedback/{answer_id}/history",
    response_model=dto.ConsoleHistory,
    summary="反馈/处理修订历史（无正文）",
)
async def feedback_history(
    answer_id: uuid.UUID,
    session: ConsoleSessionDep,
    identity: IdentityDep,
    kind: Literal["rating", "case"] = Query(default="rating"),
    cursor: str | None = Query(default=None, max_length=4096),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> dict:
    try:
        return await queries.feedback_history(
            session,
            identity,
            answer_id=answer_id,
            kind=kind,
            limit=limit,
            cursor=cursor,
        )
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get(
    "/feedback/{answer_id}/content",
    response_model=dto.ConsoleContentPage,
    summary="意见/处理说明精确引用分页",
)
async def feedback_content(
    answer_id: uuid.UUID,
    session: ConsoleSessionDep,
    identity: IdentityDep,
    kind: Literal["comment", "note"] = Query(default="comment"),
    ref: str | None = Query(default=None, max_length=64),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=CONTENT_PAGE_CHARS, ge=1, le=CONTENT_PAGE_CHARS),
) -> JSONResponse:
    try:
        payload = await content_service.read_feedback_content(
            session,
            identity,
            answer_id=answer_id,
            kind=kind,
            ref=ref,
            offset=offset,
            limit=limit,
        )
        return _no_store(payload)
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.post(
    "/feedback/{answer_id}/actions",
    status_code=status.HTTP_200_OK,
    response_model=dto.FeedbackCaseActionOut,
    summary="反馈处理 start/resolve/reopen（运营+管理权限）",
)
async def feedback_action(
    answer_id: uuid.UUID,
    payload: FeedbackCaseActionIn,
    request: Request,
    session: SessionDep,
    identity: IdentityDep,
    _context: Annotated[object, Depends(require_tenant_context)],
) -> JSONResponse:
    verify_write_origin(request)
    try:
        result = await feedback_triage.submit_action(
            session,
            identity,
            answer_id=answer_id,
            operation_id=payload.operation_id,
            expected_revision=payload.expected_revision,
            expected_feedback_revision=payload.expected_feedback_revision,
            action=payload.action,
            note=payload.note,
        )
        await session.commit()
        return JSONResponse(
            content=dto.FeedbackCaseActionOut.model_validate(result).model_dump(mode="json"),
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        raise _error(exc) from exc


@router.get(
    "/capabilities",
    response_model=dto.ConsoleCapabilities,
    summary="业务/Skill/工具聚合（统计权限）",
)
async def capabilities(
    session: ConsoleSessionDep,
    identity: IdentityDep,
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    business_code: str | None = Query(default=None, max_length=64),
    graph_id: str | None = Query(default=None, max_length=64),
    group_by: Literal["business", "skill", "tool"] = Query(default="business"),
    cursor: str | None = Query(default=None, max_length=4096),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> dict:
    try:
        return await metrics.capabilities(
            session,
            identity,
            window=window,
            start=start,
            end=end,
            business_code=business_code,
            graph_id=graph_id,
            group_by=group_by,
            cursor=cursor,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get(
    "/capabilities/details",
    response_model=dto.ConsoleCapabilityDetails,
    summary="Skill/工具调用明细（运营权限）",
)
async def capability_details(
    session: ConsoleSessionDep,
    identity: IdentityDep,
    kind: Literal["skill_span", "tool_call"] = Query(default="skill_span"),
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    business_code: str | None = Query(default=None, max_length=64),
    graph_id: str | None = Query(default=None, max_length=64),
    skill_id: str | None = Query(default=None, max_length=128),
    service_key: str | None = Query(default=None, max_length=128),
    tool_id: str | None = Query(default=None, max_length=128),
    cursor: str | None = Query(default=None, max_length=4096),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> dict:
    try:
        return await metrics.capability_details(
            session,
            identity,
            kind=kind,
            window=window,
            start=start,
            end=end,
            business_code=business_code,
            graph_id=graph_id,
            skill_id=skill_id,
            service_key=service_key,
            tool_id=tool_id,
            cursor=cursor,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get(
    "/performance",
    response_model=dto.ConsolePerformance,
    summary="服务/浏览器/模型耗时（统计权限）",
)
async def performance(
    session: ConsoleSessionDep,
    identity: IdentityDep,
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    business_code: str | None = Query(default=None, max_length=64),
    graph_id: str | None = Query(default=None, max_length=64),
    model: str | None = Query(default=None, max_length=128),
    provider: str | None = Query(default=None, max_length=64),
    source: Literal["all", "service", "browser", "model"] = Query(default="all"),
) -> dict:
    try:
        return await metrics.performance(
            session,
            identity,
            window=window,
            start=start,
            end=end,
            business_code=business_code,
            graph_id=graph_id,
            model=model,
            provider=provider,
            source=source,
        )
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get("/costs", response_model=dto.ConsoleCosts, summary="费用按币种/维度分页（统计权限）")
async def costs(
    session: ConsoleSessionDep,
    identity: IdentityDep,
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    business_code: str | None = Query(default=None, max_length=64),
    graph_id: str | None = Query(default=None, max_length=64),
    model: str | None = Query(default=None, max_length=128),
    provider: str | None = Query(default=None, max_length=64),
    group_by: Literal["model", "user", "business", "currency"] = Query(default="model"),
    cursor: str | None = Query(default=None, max_length=4096),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> dict:
    try:
        return await metrics.costs(
            session,
            identity,
            window=window,
            start=start,
            end=end,
            business_code=business_code,
            graph_id=graph_id,
            model=model,
            provider=provider,
            group_by=group_by,
            cursor=cursor,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc
