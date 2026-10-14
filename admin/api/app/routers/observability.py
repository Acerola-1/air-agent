"""观测查询与价格管理路由（change complete-agent-observability）.

授权分级（设计 §6）：
- `GET /agent/observability/observations`：agent:operations:read（运营明细）
- `GET /agent/observability/runs/{attempt_id}`：本人或 agent:operations:read
- `GET /agent/observability/metrics` / `/costs`：agent:metrics:read
- `GET /agent/observability/traces/{trace_id}`：归属核验先行（运营读）
- 价格管理：`agent:pricing:manage`（独立配置权限，不默认授予企业管理员）

响应无原文、无工具参数/结果正文；平台账号不隐式拥有企业数据。
写操作带 Origin/CSRF 与企业上下文版本校验（复用全局依赖）。
"""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.deps import (
    ActiveTenantIdentity,
    IdentityDep,
    SessionDep,
    require_active_tenant,
    require_permission,
    require_tenant_context,
    verify_write_origin,
)
from app.services import observability as obs_service
from app.services.ledger import LedgerError


class PriceCreate(BaseModel):
    """价格版本创建（不可变；重叠 409）."""

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    currency: str = Field(min_length=3, max_length=8)
    input_per_unit: Decimal = Field(ge=0, allow_inf_nan=False)
    output_per_unit: Decimal = Field(ge=0, allow_inf_nan=False)
    cache_read_per_unit: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_write_per_unit: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    reasoning_mode: Literal["included_in_output", "independent"] = "included_in_output"
    reasoning_per_unit: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    effective_from: AwareDatetime
    effective_to: AwareDatetime | None = None
    supersedes_price_ref: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def price_bounds(self):
        if self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError("价格结束时间必须晚于开始时间")
        if self.reasoning_mode == "independent" and self.reasoning_per_unit is None:
            raise ValueError("独立推理计费须提供费率")
        return self


class RecalculateIn(BaseModel):
    """显式幂等重算窗口（attempt 或时间范围二选一）。"""

    attempt_id: str | None = None
    since: dt.datetime | None = None
    until: dt.datetime | None = None


PriceCreate.model_rebuild()
RecalculateIn.model_rebuild()

router = APIRouter(prefix="/observability", dependencies=[Depends(require_active_tenant)])


def _error(exc: LedgerError) -> HTTPException:
    code = getattr(exc, "code", None)
    if code in {"bad_limit", "bad_cursor", "cursor_filter_mismatch"}:
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if code == "price_overlap":
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=getattr(exc, "status_code", 503), detail=str(exc))


# ------------------------------------------------------------------ 明细


@router.get(
    "/observations",
    summary="逐调用模型观测（运营明细；limit<=200，游标绑定过滤+租户）",
)
async def list_observations(
    session: SessionDep,
    identity: ActiveTenantIdentity,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    graph_id: str | None = None,
    model: str | None = None,
    role: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    user_id: uuid.UUID | None = None,
    attempt_id: uuid.UUID | None = None,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> dict:
    try:
        result = await obs_service.list_observations(
            session,
            identity,  # type: ignore[arg-type]
            limit=limit,
            cursor=cursor,
            graph_id=graph_id,
            model=model,
            role=role,
            status=status_filter,
            user_id=user_id,
            attempt_id=attempt_id,
            since=since,
            until=until,
        )
    except LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


@router.get("/runs/{attempt_id}", summary="单次运行观测聚合（本人/运营；无正文）")
async def get_run(attempt_id: uuid.UUID, session: SessionDep, identity: IdentityDep) -> dict:
    try:
        result = await obs_service.get_run_observations(
            session, identity, attempt_id=str(attempt_id)
        )
    except LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


# ------------------------------------------------------------------ 统计


@router.get("/metrics", summary="观测统计（Token/耗时/覆盖/缺失；统计权限）")
async def metrics(
    session: SessionDep,
    identity: Annotated[object, Depends(require_permission("agent:metrics:read"))],
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> dict:
    try:
        result = await obs_service.metrics_summary(
            session,
            identity,  # type: ignore[arg-type]
            since=since,
            until=until,
        )
    except LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


@router.get("/costs", summary="费用小计（按币种分组；缺失单列；统计权限）")
async def costs(
    session: SessionDep,
    identity: Annotated[object, Depends(require_permission("agent:metrics:read"))],
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> dict:
    try:
        result = await obs_service.cost_summary(
            session,
            identity,  # type: ignore[arg-type]
            since=since,
            until=until,
        )
    except LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


# ------------------------------------------------------------------ Trace


@router.get("/traces/{trace_id}", summary="Trace 归属核验（伪造/跨企业在外部查询前拒绝）")
async def get_trace(
    trace_id: str,
    session: SessionDep,
    identity: Annotated[object, Depends(require_permission("agent:operations:read"))],
) -> dict:
    if len(trace_id) > 128:
        raise HTTPException(status_code=400, detail="trace_id 形状非法")
    mapping = await obs_service.get_trace_mapping(
        session,
        identity,  # type: ignore[arg-type]
        trace_id=trace_id,
    )
    await session.commit()
    if mapping is None:
        # 与台账一致的 404 语义：不暴露其他企业记录的存在性
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="trace 不可访问")
    return mapping


# ------------------------------------------------------------------ 价格管理


@router.post(
    "/prices",
    summary="发布价格版本（agent:pricing:manage；重叠拒绝并审计）",
    status_code=status.HTTP_201_CREATED,
)
async def create_price(
    payload: PriceCreate,
    request: Request,
    session: SessionDep,
    identity: Annotated[object, Depends(require_permission("agent:pricing:manage"))],
    _: None = Depends(require_tenant_context),
) -> dict:
    verify_write_origin(request)
    try:
        result = await obs_service.create_price_version(
            session,
            identity,  # type: ignore[arg-type]
            provider=payload.provider,
            model=payload.model,
            currency=payload.currency,
            input_per_unit=payload.input_per_unit,
            output_per_unit=payload.output_per_unit,
            cache_read_per_unit=payload.cache_read_per_unit,
            cache_write_per_unit=payload.cache_write_per_unit,
            reasoning_mode=payload.reasoning_mode,
            reasoning_per_unit=payload.reasoning_per_unit,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            supersedes_price_ref=payload.supersedes_price_ref,
        )
    except LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result


@router.post(
    "/prices/recalculate",
    summary="显式幂等重算（agent:pricing:manage；原 usage 不可变）",
)
async def recalculate(
    payload: RecalculateIn,
    request: Request,
    session: SessionDep,
    identity: Annotated[object, Depends(require_permission("agent:pricing:manage"))],
    _: None = Depends(require_tenant_context),
) -> dict:
    verify_write_origin(request)
    if not payload.attempt_id and not payload.since and not payload.until:
        raise HTTPException(status_code=400, detail="重算必须限定 attempt_id 或时间窗口")
    try:
        result = await obs_service.recalculate_costs(
            session,
            identity,  # type: ignore[arg-type]
            attempt_id=payload.attempt_id,
            since=payload.since,
            until=payload.until,
        )
    except LedgerError as exc:
        raise _error(exc) from exc
    await session.commit()
    return result
