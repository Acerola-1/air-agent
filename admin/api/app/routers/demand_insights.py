"""需求洞察路由（`/agent/insights/**`）.

读接口使用独立 REPEATABLE READ READ ONLY 快照，写接口使用请求事务并在提交前
完成权限、CAS、幂等与操作审计。所有响应 no-store；metrics 响应不返回来源
原句、向量或用户标识。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas_demand as dto
from app.db import SessionLocal
from app.demand_insights import delegations, governance, jobs, statistics, storage
from app.demand_insights.config import get_insights_settings
from app.demand_insights.errors import InsightsConflict, InsightsError
from app.demand_insights.scope import (
    MAX_LIMIT,
    InsightsBadCursor,
    build_meta,
    business_day_start,
    cursor_window,
    decode_cursor,
    filter_fingerprint,
    parse_window,
    previous_window,
    require_write_permissions,
    scope_session,
    tenant_id_of,
    window_key,
)
from app.deps import (
    IdentityDep,
    SessionDep,
    require_active_tenant,
    require_tenant_context,
    verify_write_origin,
)
from app.services.identity import Identity
from app.services.remote_identity import request_cookie
from app.services.remote_mode import identity_backend_mode


async def validate_query(request: Request) -> None:
    allowed = {field.alias for field in request.scope["route"].dependant.query_params}
    unknown = set(request.query_params) - allowed
    repeated = [key for key in request.query_params if len(request.query_params.getlist(key)) > 1]
    if unknown or repeated:
        raise HTTPException(
            status_code=422,
            detail={"code": "unsupported_filter", "error": "存在不支持或重复的查询条件"},
        )


INSIGHTS_READ_TIMEOUT_SECONDS = 5


class InsightsRoute(APIRoute):
    def __init__(self, *args, **kwargs):
        kwargs["response_model_exclude_unset"] = True
        super().__init__(*args, **kwargs)

    def get_route_handler(self):
        original = super().get_route_handler()

        async def bounded(request: Request):
            try:
                async with asyncio.timeout(INSIGHTS_READ_TIMEOUT_SECONDS):
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
    prefix="/insights",
    dependencies=[Depends(require_active_tenant), Depends(validate_query)],
    route_class=InsightsRoute,
    responses={
        code: {"model": dto.InsightsErrorResponse} for code in (400, 401, 403, 404, 409, 422, 503)
    },
)


async def get_insights_session(
    identity: IdentityDep, identity_session: SessionDep
) -> AsyncIterator[AsyncSession]:
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


InsightsSessionDep = Annotated[AsyncSession, Depends(get_insights_session)]


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, InsightsError):
        return HTTPException(
            status_code=exc.status_code, detail={"code": exc.code, "error": exc.message}
        )
    raise exc


def _jsonable(value):
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _empty_counts() -> dict:
    return {
        "eligible_requests": 0,
        "processed_requests": 0,
        "pending_requests": 0,
        "failed_requests": 0,
        "missing_content_requests": 0,
        "pending_confirmation_requests": 0,
        "expired_or_deleted_requests": 0,
        "review_required_requests": 0,
        "classified_intents": 0,
        "unclassified_intents": 0,
        "total_intents": 0,
        "negative_feedback_requests": 0,
        "evaluated_requests": 0,
        "feedback_coverage": None,
        "technical_failure_requests": 0,
        "stopped_requests": 0,
        "source_cutoff": None,
        "coverage": None,
    }


def _meta(
    *,
    identity: Identity,
    state: dict | None,
    window,
    filters: dict,
    counts: dict | None = None,
) -> dict:
    settings = get_insights_settings()
    if state is None:
        counts = counts or _empty_counts()
        return build_meta(
            identity=identity,
            window=window,
            filters=filters,
            analysis_version="uninitialized",
            assignment_revision=0,
            embedding_profile="uninitialized",
            profile_id=None,
            profile_state_revision=0,
            source_cutoff=None,
            counts=counts,
            comparable=False,
            limitations=["尚未启用需求洞察分析版本；请先由管理员创建分析作业"],
            status="not_initialized",
        )
    counts = counts or _empty_counts()
    comparable = statistics.is_comparable(counts, window, settings)
    limitations: list[str] = []
    if (
        counts.get("coverage") is not None
        and counts["coverage"] < settings.coverage_comparable_threshold
    ):
        limitations.append("当前窗口处理覆盖不足；未处理问题未计入零需求")
    if any(filters.get(key) for key in ("action", "pollutant", "city_code", "topic_id")):
        limitations.append("维度过滤作用于榜单与占比分母；meta 覆盖数为同企业窗口总量")
    return build_meta(
        identity=identity,
        window=window,
        filters=filters,
        analysis_version=state["analysis_version"],
        assignment_revision=int(state["assignment_revision"]),
        embedding_profile=state["embedding_profile"],
        profile_id=state["active_profile_id"],
        profile_state_revision=int(state["revision"]),
        source_cutoff=counts.get("source_cutoff"),
        counts=counts,
        comparable=comparable,
        limitations=limitations,
        status="ok" if comparable else "partial",
    )


def _filters(
    *,
    business_code: str | None,
    graph_id: str | None,
    action: str | None,
    pollutant: str | None,
    city_code: str | None,
    topic_id: uuid.UUID | None,
    rank: str | None = None,
) -> dict:
    result = {
        key: value
        for key, value in {
            "business_code": business_code,
            "graph_id": graph_id,
            "action": action,
            "pollutant": pollutant,
            "city_code": city_code,
            "topic_id": str(topic_id) if topic_id else None,
            "rank": rank,
        }.items()
        if value
    }
    return result


@router.get("/overview", response_model=dto.OverviewResponse, summary="需求洞察总览（统计权限）")
async def overview(
    session: InsightsSessionDep,
    identity: IdentityDep,
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    business_code: str | None = Query(default=None, max_length=64),
    graph_id: str | None = Query(default=None, max_length=64),
) -> dict:
    try:
        await scope_session(session, identity, require_metrics=True)
        tenant = tenant_id_of(identity)
        state = await storage.get_profile_state(session, tenant_id=tenant)
        parsed = parse_window(window=window, start=start, end=end, as_of=dt.datetime.now(dt.UTC))
        filters = _filters(
            business_code=business_code,
            graph_id=graph_id,
            action=None,
            pollutant=None,
            city_code=None,
            topic_id=None,
        )
        if state is None:
            return {
                "meta": _meta(identity=identity, state=None, window=parsed, filters=filters),
                "counts": _empty_counts(),
                "topics": [],
                "unclassified_reasons": [],
            }
        data = await statistics.overview(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            window=parsed,
            settings=get_insights_settings(),
        )
        return {
            "meta": _meta(
                identity=identity,
                state=state,
                window=parsed,
                filters=filters,
                counts=data["counts"],
            ),
            "counts": _jsonable(data["counts"]),
            "topics": data["topics"],
            "unclassified_reasons": data["unclassified_reasons"],
        }
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get(
    "/questions",
    response_model=dto.QuestionsResponse,
    summary="标准问题榜（统计权限）",
)
async def questions(
    session: InsightsSessionDep,
    identity: IdentityDep,
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    business_code: str | None = Query(default=None, max_length=64),
    graph_id: str | None = Query(default=None, max_length=64),
    action: str | None = Query(default=None, max_length=32),
    pollutant: str | None = Query(default=None, max_length=20),
    city_code: str | None = Query(default=None, max_length=40),
    topic_id: uuid.UUID | None = Query(default=None),
    analysis_version: str | None = Query(default=None, max_length=128),
    assignment_revision: int | None = Query(default=None, ge=0),
    rank: Literal["intents", "users", "requests"] = Query(default="intents"),
    cursor: str | None = Query(default=None, max_length=4096),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> dict:
    try:
        await scope_session(session, identity, require_metrics=True)
        tenant = tenant_id_of(identity)
        state = await storage.get_profile_state(session, tenant_id=tenant)
        filters = _filters(
            business_code=business_code,
            graph_id=graph_id,
            action=action,
            pollutant=pollutant,
            city_code=city_code,
            topic_id=topic_id,
            rank=rank,
        )
        if state is None:
            parsed = parse_window(
                window=window, start=start, end=end, as_of=dt.datetime.now(dt.UTC)
            )
            return {
                "meta": _meta(identity=identity, state=None, window=parsed, filters=filters),
                "items": [],
                "next_cursor": None,
                "has_more": False,
                "limit": limit,
                "sort": rank,
                "filters": filters,
                "previous_comparable": False,
            }
        _check_version_filters(state, analysis_version, assignment_revision)
        parsed = parse_window(window=window, start=start, end=end, as_of=dt.datetime.now(dt.UTC))
        filter_key = filter_fingerprint(filters, window_key=window_key(window, start, end))
        cursor_key = None
        if cursor:
            decoded = decode_cursor(
                cursor,
                tenant_id=tenant,
                context_version=identity.context_version,
                filter_key=filter_key,
                rank=rank,
                analysis_version=state["analysis_version"],
                assignment_revision=int(state["assignment_revision"]),
            )
            parsed = cursor_window(decoded, parsed.kind)
            cursor_key = decoded.key
        counts = await statistics.load_counts(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            window=parsed,
        )
        total = await statistics.filtered_intent_total(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            window=parsed,
            filters=filters,
        )
        result = await statistics.question_ranking(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            assignment_revision=int(state["assignment_revision"]),
            context_version=identity.context_version,
            filter_key=filter_key,
            window=parsed,
            previous=previous_window(parsed),
            rank=rank,
            total_intents=total,
            settings=get_insights_settings(),
            cursor_key=cursor_key,
            limit=limit,
            filters=filters,
        )
        return {
            "meta": _meta(
                identity=identity, state=state, window=parsed, filters=filters, counts=counts
            ),
            **result,
        }
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get("/topics", response_model=dto.TopicsResponse, summary="主题榜（统计权限）")
async def topics(
    session: InsightsSessionDep,
    identity: IdentityDep,
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    business_code: str | None = Query(default=None, max_length=64),
    graph_id: str | None = Query(default=None, max_length=64),
    action: str | None = Query(default=None, max_length=32),
    pollutant: str | None = Query(default=None, max_length=20),
    city_code: str | None = Query(default=None, max_length=40),
    topic_id: uuid.UUID | None = Query(default=None),
    analysis_version: str | None = Query(default=None, max_length=128),
    assignment_revision: int | None = Query(default=None, ge=0),
    rank: Literal["intents", "users", "requests"] = Query(default="intents"),
    cursor: str | None = Query(default=None, max_length=4096),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> dict:
    try:
        await scope_session(session, identity, require_metrics=True)
        tenant = tenant_id_of(identity)
        state = await storage.get_profile_state(session, tenant_id=tenant)
        filters = _filters(
            business_code=business_code,
            graph_id=graph_id,
            action=action,
            pollutant=pollutant,
            city_code=city_code,
            topic_id=topic_id,
            rank=rank,
        )
        if state is None:
            parsed = parse_window(
                window=window, start=start, end=end, as_of=dt.datetime.now(dt.UTC)
            )
            return {
                "meta": _meta(identity=identity, state=None, window=parsed, filters=filters),
                "items": [],
                "next_cursor": None,
                "has_more": False,
                "limit": limit,
                "sort": rank,
                "filters": filters,
            }
        _check_version_filters(state, analysis_version, assignment_revision)
        parsed = parse_window(window=window, start=start, end=end, as_of=dt.datetime.now(dt.UTC))
        filter_key = filter_fingerprint(filters, window_key=window_key(window, start, end))
        cursor_key = None
        if cursor:
            decoded = decode_cursor(
                cursor,
                tenant_id=tenant,
                context_version=identity.context_version,
                filter_key=filter_key,
                rank=rank,
                analysis_version=state["analysis_version"],
                assignment_revision=int(state["assignment_revision"]),
            )
            parsed = cursor_window(decoded, parsed.kind)
            cursor_key = decoded.key
        counts = await statistics.load_counts(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            window=parsed,
        )
        total = await statistics.filtered_intent_total(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            window=parsed,
            filters=filters,
        )
        result = await statistics.topic_ranking(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            assignment_revision=int(state["assignment_revision"]),
            context_version=identity.context_version,
            filter_key=filter_key,
            window=parsed,
            total_intents=total,
            rank=rank,
            cursor_key=cursor_key,
            limit=limit,
            filters=filters,
        )
        return {
            "meta": _meta(
                identity=identity, state=state, window=parsed, filters=filters, counts=counts
            ),
            **result,
        }
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


def _check_version_filters(
    state: dict, analysis_version: str | None, assignment_revision: int | None
) -> None:
    if analysis_version and analysis_version != state["analysis_version"]:
        raise InsightsError("只能查询当前分析版本", code="analysis_version_not_active")
    if assignment_revision is not None and assignment_revision != int(state["assignment_revision"]):
        raise InsightsBadCursor("归属版本已变化，请刷新后重试", code="assignment_revision_changed")


@router.get(
    "/questions/{cluster_id}",
    response_model=dto.QuestionDetailResponse,
    summary="标准问题详情（统计权限；无原文）",
)
async def question_detail(
    cluster_id: uuid.UUID,
    session: InsightsSessionDep,
    identity: IdentityDep,
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> dict:
    try:
        await scope_session(session, identity, require_metrics=True)
        tenant = tenant_id_of(identity)
        state = await storage.get_profile_state(session, tenant_id=tenant)
        if state is None:
            raise InsightsError("需求洞察尚未启用", code="not_initialized")
        parsed = parse_window(window=window, start=start, end=end, as_of=dt.datetime.now(dt.UTC))
        counts = await statistics.load_counts(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            window=parsed,
        )
        detail = await statistics.question_detail(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            assignment_revision=int(state["assignment_revision"]),
            window=parsed,
            cluster_id=cluster_id,
            settings=get_insights_settings(),
        )
        return {
            "meta": _meta(identity=identity, state=state, window=parsed, filters={}, counts=counts),
            **detail,
        }
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


def _content_path(attempt_id: str | None) -> str:
    if attempt_id:
        return f"/operations/runs/{attempt_id}"
    return "/operations/runs"


@router.get(
    "/questions/{cluster_id}/sources",
    response_model=dto.SourcesResponse,
    summary="标准问题授权来源与脱敏意图（运营+正文）",
)
async def question_sources(
    cluster_id: uuid.UUID,
    session: InsightsSessionDep,
    identity: IdentityDep,
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> dict:
    try:
        await scope_session(session, identity, require_ops=True, require_content=True)
        tenant = tenant_id_of(identity)
        state = await storage.get_profile_state(session, tenant_id=tenant)
        if state is None:
            raise InsightsError("需求洞察尚未启用", code="not_initialized")
        parsed = parse_window(
            window="last_30_days", start=None, end=None, as_of=dt.datetime.now(dt.UTC)
        )
        counts = await statistics.load_counts(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            window=parsed,
        )
        rows = await statistics.list_question_sources(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            cluster_id=cluster_id,
            limit=limit,
            offset=offset,
        )
        for row in rows:
            row["content_path"] = _content_path(row.get("attempt_id"))
        return {
            "meta": _meta(identity=identity, state=state, window=parsed, filters={}, counts=counts),
            "items": rows,
            "has_more": len(rows) >= limit,
            "limit": limit,
            "offset": offset,
        }
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get(
    "/unclassified",
    response_model=dto.UnclassifiedResponse,
    summary="未归类意图与缺源（运营+正文）",
)
async def unclassified(
    session: InsightsSessionDep,
    identity: IdentityDep,
    window: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> dict:
    try:
        await scope_session(session, identity, require_ops=True, require_content=True)
        tenant = tenant_id_of(identity)
        state = await storage.get_profile_state(session, tenant_id=tenant)
        if state is None:
            raise InsightsError("需求洞察尚未启用", code="not_initialized")
        parsed = parse_window(window=window, start=start, end=end, as_of=dt.datetime.now(dt.UTC))
        counts = await statistics.load_counts(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            window=parsed,
        )
        rows = await statistics.list_unclassified(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            window=parsed,
            limit=limit,
            offset=offset,
        )
        for row in rows:
            row["content_path"] = _content_path(row.get("attempt_id"))
        gaps = await statistics.list_gaps(
            session,
            tenant_id=tenant,
            profile_id=state["active_profile_id"],
            analysis_version=state["analysis_version"],
            window=parsed,
            limit=limit,
            offset=offset,
        )
        return {
            "meta": _meta(identity=identity, state=state, window=parsed, filters={}, counts=counts),
            "items": rows,
            "gaps": gaps,
            "has_more": len(rows) >= limit,
            "limit": limit,
            "offset": offset,
        }
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


# ------------------------------------------------------------------ 作业


@router.get("/jobs", response_model=dto.JobsResponse, summary="分析作业列表（运营）")
async def job_list(
    session: InsightsSessionDep,
    identity: IdentityDep,
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> dict:
    try:
        await scope_session(session, identity, require_ops=True)
        tenant = tenant_id_of(identity)
        state = await storage.get_profile_state(session, tenant_id=tenant)
        rows = await jobs.list_jobs(session, tenant_id=tenant, limit=limit)
        parsed = parse_window(
            window="last_7_days", start=None, end=None, as_of=dt.datetime.now(dt.UTC)
        )
        return {
            "meta": _meta(identity=identity, state=state, window=parsed, filters={}),
            "items": [jobs.encode_job(row) for row in rows],
        }
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


@router.get("/jobs/{job_id}", response_model=dto.JobItem, summary="分析作业详情（运营）")
async def job_detail(job_id: uuid.UUID, session: InsightsSessionDep, identity: IdentityDep) -> dict:
    try:
        await scope_session(session, identity, require_ops=True)
        tenant = tenant_id_of(identity)
        row = await jobs.get_job(session, tenant_id=tenant, job_id=job_id)
        return jobs.encode_job(row)
    except Exception as exc:  # noqa: BLE001
        raise _error(exc) from exc


def _delegation_fields(identity: Identity, request: Request) -> tuple[str, dt.datetime]:
    if identity_backend_mode() != "remote":
        raise InsightsConflict("需求洞察分析需要 IAM remote 会话", code="remote_identity_required")
    handle_hash = delegations.handle_hash_from_cookie(request_cookie(request))
    if not handle_hash:
        raise InsightsConflict("无法解析应用会话，请重新登录", code="session_required")
    expires_at = getattr(identity.session, "expires_at", None)
    if expires_at is None:
        raise InsightsConflict("会话有效期不可用", code="session_required")
    return handle_hash, expires_at


async def _create_delegation(
    session: AsyncSession,
    *,
    request: Request,
    identity: Identity,
    tenant: str,
    profile_id: uuid.UUID,
    ttl_hours: int,
) -> dict:
    handle_hash, expires_at = _delegation_fields(identity, request)
    return await delegations.create_delegation(
        session,
        tenant_id=tenant,
        user_id=identity.user.id,
        membership_id=identity.membership.id,
        handle_hash=handle_hash,
        ctx_version=identity.context_version,
        user_auth_revision=int(identity.user.auth_revision),
        tenant_auth_revision=int(identity.tenant.auth_revision),
        membership_revision=int(identity.membership.revision),
        profile_id=profile_id,
        session_expires_at=expires_at,
        ttl_hours=ttl_hours,
        scope={
            "permissions": ["agent:operations:read", "agent:content:read", "agent:insights:manage"],
        },
    )


@router.post(
    "/jobs", status_code=status.HTTP_202_ACCEPTED, response_model=dto.InsightsActionResponse
)
async def create_job(
    payload: dto.JobCreateIn,
    request: Request,
    session: SessionDep,
    identity: IdentityDep,
    _context: Annotated[object, Depends(require_tenant_context)],
) -> JSONResponse:
    verify_write_origin(request)
    try:
        require_write_permissions(identity)
        await scope_session(
            session, identity, require_ops=True, require_content=True, require_manage=True
        )
        tenant = tenant_id_of(identity)
        settings = get_insights_settings()
        state = await storage.ensure_default_profile(session, tenant_id=tenant, settings=settings)
        profile_id = state["active_profile_id"]
        now = dt.datetime.now(dt.UTC)
        if payload.job_kind == "backfill":
            if payload.window_start is None or payload.window_end is None:
                raise InsightsConflict("回填必须提供起止时间", code="backfill_window_required")
            window_start = payload.window_start.astimezone(dt.UTC)
            window_end = payload.window_end.astimezone(dt.UTC)
        else:
            window_start = (
                payload.window_start.astimezone(dt.UTC)
                if payload.window_start
                else business_day_start(now)
            )
            window_end = payload.window_end.astimezone(dt.UTC) if payload.window_end else None
        delegation = await _create_delegation(
            session,
            request=request,
            identity=identity,
            tenant=tenant,
            profile_id=profile_id,
            ttl_hours=payload.delegation_ttl_hours,
        )
        result = await jobs.create_job(
            session,
            identity=identity,
            tenant_id=tenant,
            profile_id=profile_id,
            delegation_id=delegation["id"],
            job_kind=payload.job_kind,
            window_start=window_start,
            window_end=window_end,
            as_of=now,
            batch_size=min(settings.batch_size, 500),
            max_sources=min(payload.max_sources, settings.max_sources_per_job),
            max_tokens=min(payload.max_tokens, settings.max_tokens_per_job),
            continuous=payload.continuous,
            acknowledge_backfill=payload.acknowledge_backfill,
            authorization_expires_at=delegation["expires_at"],
            operation_id=payload.operation_id,
        )
        await session.commit()
        return JSONResponse(
            content=dto.InsightsActionResponse.model_validate(
                {"result": _jsonable(result), "replayed": result.get("replayed", False)}
            ).model_dump(mode="json"),
            status_code=202,
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        raise _error(exc) from exc


@router.post(
    "/jobs/{job_id}/actions",
    response_model=dto.InsightsActionResponse,
    summary="作业 retry/pause/renew/cancel",
)
async def job_action(
    job_id: uuid.UUID,
    payload: dto.JobActionIn,
    request: Request,
    session: SessionDep,
    identity: IdentityDep,
    _context: Annotated[object, Depends(require_tenant_context)],
) -> JSONResponse:
    verify_write_origin(request)
    try:
        require_write_permissions(identity)
        await scope_session(
            session, identity, require_ops=True, require_content=True, require_manage=True
        )
        tenant = tenant_id_of(identity)
        state = await storage.get_profile_state(session, tenant_id=tenant)
        if state is None:
            raise InsightsConflict("需求洞察尚未启用", code="not_initialized")
        renewal_id = None
        renewal_expires = None
        if payload.action == "renew":
            delegation = await _create_delegation(
                session,
                request=request,
                identity=identity,
                tenant=tenant,
                profile_id=state["active_profile_id"],
                ttl_hours=payload.renew_ttl_hours,
            )
            renewal_id = delegation["id"]
            renewal_expires = delegation["expires_at"]
        result = await jobs.submit_action(
            session,
            identity=identity,
            tenant_id=tenant,
            job_id=job_id,
            operation_id=payload.operation_id,
            expected_revision=payload.expected_revision,
            action=payload.action,
            renewal_delegation_id=renewal_id,
            renewal_expires_at=renewal_expires,
        )
        await session.commit()
        return JSONResponse(
            content=dto.InsightsActionResponse.model_validate(
                {"result": _jsonable(result), "replayed": result.get("replayed", False)}
            ).model_dump(mode="json"),
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        raise _error(exc) from exc


@router.post(
    "/questions/{cluster_id}/actions",
    response_model=dto.InsightsActionResponse,
    summary="标准问题 rename/assign/merge/split",
)
async def question_action(
    cluster_id: uuid.UUID,
    payload: dto.QuestionActionIn,
    request: Request,
    session: SessionDep,
    identity: IdentityDep,
    _context: Annotated[object, Depends(require_tenant_context)],
) -> JSONResponse:
    verify_write_origin(request)
    try:
        require_write_permissions(identity)
        await scope_session(
            session, identity, require_ops=True, require_content=True, require_manage=True
        )
        tenant = tenant_id_of(identity)
        state = await storage.get_profile_state(session, tenant_id=tenant)
        if state is None:
            raise InsightsConflict("需求洞察尚未启用", code="not_initialized")
        result = await governance.question_action(
            session,
            identity=identity,
            tenant_id=tenant,
            profile=state,
            cluster_id=cluster_id,
            payload=payload.model_dump(mode="json"),
            settings=get_insights_settings(),
        )
        await session.commit()
        return JSONResponse(
            content=dto.InsightsActionResponse.model_validate(
                {"result": _jsonable(result), "replayed": result.get("replayed", False)}
            ).model_dump(mode="json"),
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        raise _error(exc) from exc


@router.post(
    "/profiles/{profile_id}/rebuild",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=dto.InsightsActionResponse,
)
async def rebuild_profile(
    profile_id: uuid.UUID,
    payload: dto.ProfileActionIn,
    request: Request,
    session: SessionDep,
    identity: IdentityDep,
    _context: Annotated[object, Depends(require_tenant_context)],
) -> JSONResponse:
    verify_write_origin(request)
    try:
        require_write_permissions(identity)
        await scope_session(
            session, identity, require_ops=True, require_content=True, require_manage=True
        )
        tenant = tenant_id_of(identity)
        profile = await storage.get_profile(session, tenant_id=tenant, profile_id=profile_id)
        state = await storage.get_profile_state(session, tenant_id=tenant)
        if state is None:
            raise InsightsConflict("需求洞察尚未启用", code="not_initialized")
        new_profile = await governance.rebuild_profile(
            session,
            identity=identity,
            tenant_id=tenant,
            profile=profile,
            operation_id=payload.operation_id,
            expected_revision=payload.expected_revision,
            settings=get_insights_settings(),
        )
        if new_profile.get("replayed"):
            existing_job = await session.scalar(
                text(
                    "SELECT result_snapshot->>'job_id' FROM agent_insight_operations"
                    " WHERE tenant_id=:tenant AND operation_id=:operation"
                ),
                {"tenant": tenant, "operation": payload.operation_id},
            )
            if existing_job:
                await session.commit()
                return JSONResponse(
                    content=dto.InsightsActionResponse.model_validate(
                        {
                            "result": _jsonable({**new_profile, "job_id": existing_job}),
                            "replayed": True,
                        }
                    ).model_dump(mode="json"),
                    status_code=202,
                    headers={"Cache-Control": "no-store"},
                )
        delegation = await _create_delegation(
            session,
            request=request,
            identity=identity,
            tenant=tenant,
            profile_id=uuid.UUID(new_profile["profile_id"]),
            ttl_hours=12,
        )
        now = dt.datetime.now(dt.UTC)
        job = await jobs.create_job(
            session,
            identity=identity,
            tenant_id=tenant,
            profile_id=uuid.UUID(new_profile["profile_id"]),
            delegation_id=delegation["id"],
            job_kind="rebuild",
            window_start=dt.datetime(1970, 1, 1, tzinfo=dt.UTC),
            window_end=now,
            as_of=now,
            batch_size=min(get_insights_settings().batch_size, 500),
            max_sources=get_insights_settings().max_sources_per_job,
            max_tokens=get_insights_settings().max_tokens_per_job,
            continuous=False,
            acknowledge_backfill=True,
            authorization_expires_at=delegation["expires_at"],
            operation_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"insight-rebuild-job:{tenant}:{payload.operation_id}",
                )
            ),
        )
        await session.execute(
            text(
                "UPDATE agent_insight_operations"
                " SET result_snapshot=COALESCE(result_snapshot,'{}'::jsonb)"
                "     || jsonb_build_object('job_id', :job_id)"
                " WHERE tenant_id=:tenant AND operation_id=:operation"
            ),
            {"job_id": job["job_id"], "tenant": tenant, "operation": payload.operation_id},
        )
        await session.commit()
        result = {**new_profile, "job_id": job["job_id"]}
        return JSONResponse(
            content=dto.InsightsActionResponse.model_validate(
                {"result": _jsonable(result), "replayed": bool(new_profile.get("replayed"))}
            ).model_dump(mode="json"),
            status_code=202,
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        raise _error(exc) from exc


@router.post(
    "/profiles/{profile_id}/activate",
    response_model=dto.InsightsActionResponse,
)
async def activate_profile(
    profile_id: uuid.UUID,
    payload: dto.ProfileActionIn,
    request: Request,
    session: SessionDep,
    identity: IdentityDep,
    _context: Annotated[object, Depends(require_tenant_context)],
) -> JSONResponse:
    verify_write_origin(request)
    try:
        require_write_permissions(identity)
        await scope_session(
            session, identity, require_ops=True, require_content=True, require_manage=True
        )
        tenant = tenant_id_of(identity)
        profile = await storage.get_profile(session, tenant_id=tenant, profile_id=profile_id)
        result = await governance.activate_profile(
            session,
            identity=identity,
            tenant_id=tenant,
            profile=profile,
            operation_id=payload.operation_id,
            expected_revision=payload.expected_revision,
            settings=get_insights_settings(),
        )
        await session.commit()
        return JSONResponse(
            content=dto.InsightsActionResponse.model_validate(
                {"result": _jsonable(result), "replayed": result.get("replayed", False)}
            ).model_dump(mode="json"),
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        raise _error(exc) from exc
