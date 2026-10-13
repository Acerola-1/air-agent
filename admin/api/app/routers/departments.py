"""部门管理（企业作用域）。

同企业父子关系由复合外键保证；无环校验由延迟约束触发器兜底（0003），服务层
在写入前先沿 parent 链显式检查并给出中文错误——数据库错误留给并发竞态。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import SessionDep, require_active_tenant, require_permission, require_tenant_context
from app.models import Department, TenantMembership
from app.schemas import DepartmentCreate, DepartmentOut, DepartmentUpdate
from app.services.audit import record_audit
from app.services.identity import Identity

# R02/R11：部门读写全部限定"有效当前企业"，写操作必须声明目标企业/版本并与
# 实时会话一致——旧标签页不得把部门落进切换后的企业，停用企业不得再写入。
router = APIRouter(
    tags=["departments"],
    dependencies=[Depends(require_active_tenant), Depends(require_tenant_context)],
)

CanRead = Annotated[Identity, Depends(require_permission("department:read"))]
CanManage = Annotated[Identity, Depends(require_permission("department:manage"))]


def _out(dept: Department) -> DepartmentOut:
    return DepartmentOut(
        id=dept.id,
        tenant_id=dept.tenant_id,
        dept_code=dept.dept_code,
        name=dept.name,
        parent_id=dept.parent_id,
        created_at=dept.created_at,
    )


def _require_tenant(actor: Identity) -> uuid.UUID:
    if actor.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="尚未选择企业，无法管理部门"
        )
    return actor.tenant_id


async def _check_parent_chain(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    node_id: uuid.UUID | None,
) -> None:
    """显式环检测：从 parent 向上走，遇到自身即拒绝；跨企业父部门拒绝。"""
    seen: set[uuid.UUID] = set()
    current = parent_id
    while current is not None:
        if node_id is not None and current == node_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="部门父子关系将形成环"
            )
        if current in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="部门父子关系存在环"
            )
        seen.add(current)
        parent = await session.get(Department, current)
        if parent is None or parent.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="父部门不存在或不属于本企业"
            )
        current = parent.parent_id


@router.get("", response_model=list[DepartmentOut], summary="部门列表（本企业）")
async def list_departments(session: SessionDep, actor: CanRead) -> list[DepartmentOut]:
    tenant_id = _require_tenant(actor)
    rows = (
        await session.scalars(
            select(Department)
            .where(Department.tenant_id == tenant_id)
            .order_by(Department.created_at, Department.dept_code)
        )
    ).all()
    return [_out(d) for d in rows]


@router.post(
    "", response_model=DepartmentOut, status_code=status.HTTP_201_CREATED, summary="新建部门"
)
async def create_department(
    payload: DepartmentCreate,
    request: Request,
    session: SessionDep,
    actor: CanManage,
) -> DepartmentOut:
    tenant_id = _require_tenant(actor)
    await _check_parent_chain(session, tenant_id, payload.parent_id, None)
    dept = Department(
        tenant_id=tenant_id,
        dept_code=payload.dept_code,
        name=payload.name,
        parent_id=payload.parent_id,
    )
    session.add(dept)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="部门编码在本企业内已存在或父子关系非法"
        ) from exc
    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="department.create",
        target_type="department",
        target_id=str(dept.id),
        detail={"dept_code": dept.dept_code, "name": dept.name},
        request=request,
    )
    await session.commit()
    return _out(dept)


@router.patch("/{department_id}", response_model=DepartmentOut, summary="修改部门")
async def update_department(
    department_id: uuid.UUID,
    payload: DepartmentUpdate,
    request: Request,
    session: SessionDep,
    actor: CanManage,
) -> DepartmentOut:
    tenant_id = _require_tenant(actor)
    dept = await session.get(Department, department_id)
    if dept is None or dept.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="部门不存在")
    changes = payload.model_dump(exclude_unset=True)
    if "parent_id" in changes:
        await _check_parent_chain(session, tenant_id, changes["parent_id"], dept.id)
        dept.parent_id = changes["parent_id"]
    if changes.get("name"):
        dept.name = changes["name"]
    await session.flush()
    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="department.update",
        target_type="department",
        target_id=str(dept.id),
        detail=changes,
        request=request,
    )
    await session.commit()
    return _out(dept)


@router.delete("/{department_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除部门")
async def delete_department(
    department_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    actor: CanManage,
) -> None:
    tenant_id = _require_tenant(actor)
    dept = await session.get(Department, department_id)
    if dept is None or dept.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="部门不存在")
    child_count = await session.scalar(
        select(func.count()).select_from(Department).where(Department.parent_id == dept.id)
    )
    if child_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="存在子部门，请先处理下级部门"
        )
    member_count = await session.scalar(
        select(func.count())
        .select_from(TenantMembership)
        .where(TenantMembership.department_id == dept.id)
    )
    if member_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="部门仍有成员，请先调整成员部门"
        )
    name = dept.name
    await session.delete(dept)
    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="department.delete",
        target_type="department",
        target_id=str(department_id),
        detail={"name": name},
        request=request,
    )
    await session.commit()
