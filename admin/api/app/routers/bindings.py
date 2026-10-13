"""企业 MCP 授权绑定管理（任务 7.1 的管理面）。

权限边界：只有平台角色（mcp:binding:manage）可以创建/修改/停用绑定；
企业管理员无绑定管理权限时提交变更一律拒绝（spec：企业管理员修改平台凭据）。
读侧（mcp:binding:read）返回**脱敏**状态：credential_ref 值永不返回，只返回
是否已配置与协议/授权版本。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.deps import SessionDep, require_permission
from app.models import McpIdentityBinding, Tenant, TenantMembership
from app.schemas import BindingOut, BindingUpsert
from app.services.audit import record_audit
from app.services.identity import Identity

# 与 Agent 侧 src/common/mcp_authorization.py::VERIFIED_PROTOCOLS 同步维护。
# 真实平台协议（7.6）核验后在此与登记表同步添加；v1-fixture 仅限本地测试环境
# （Agent 侧还叠加 TENANT_MCP_FIXTURE_MODE 门禁）。
ALLOWED_PROTOCOL_VERSIONS = {"unverified", "v1-fixture"}

router = APIRouter(tags=["mcp-bindings"])

CanRead = Annotated[Identity, Depends(require_permission("mcp:binding:read"))]
CanManage = Annotated[Identity, Depends(require_permission("mcp:binding:manage"))]


def _out(binding: McpIdentityBinding) -> BindingOut:
    return BindingOut(
        id=binding.id,
        tenant_id=binding.tenant_id,
        membership_id=binding.membership_id,
        server_key=binding.server_key,
        external_subject=binding.external_subject,
        protocol_version=binding.protocol_version,
        auth_revision=binding.auth_revision,
        status=binding.status,
        credential_configured=bool(binding.credential_ref),
    )


@router.get("", response_model=list[BindingOut], summary="绑定列表（脱敏）")
async def list_bindings(session: SessionDep, actor: CanRead) -> list[BindingOut]:
    """平台可看全部；企业管理上下文只看到本企业绑定（RLS + 显式条件）。"""
    stmt = select(McpIdentityBinding)
    if not actor.platform_admin:
        if actor.tenant_id is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="尚未选择企业")
        stmt = stmt.where(McpIdentityBinding.tenant_id == actor.tenant_id)
    rows = (await session.scalars(stmt.order_by(McpIdentityBinding.server_key))).all()
    return [_out(b) for b in rows]


@router.put("", response_model=BindingOut, summary="创建/更新绑定（平台）")
async def upsert_binding(
    payload: BindingUpsert,
    request: Request,
    session: SessionDep,
    actor: CanManage,
) -> BindingOut:
    """按 (tenant, server, membership) upsert；任何变更递增 auth_revision。

    auth_revision 递增使进行中缓存/调用在下一次检查时失效（任务 7.1/7.4）。
    """
    if not actor.platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="外部授权绑定仅平台角色可管理；企业管理员无权修改平台凭据",
        )
    if await session.get(Tenant, payload.tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="企业不存在")
    if payload.membership_id is not None:
        membership = await session.get(TenantMembership, payload.membership_id)
        if membership is None or membership.tenant_id != payload.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="成员覆盖绑定必须属于同一企业",
            )

    stmt = select(McpIdentityBinding).where(
        McpIdentityBinding.tenant_id == payload.tenant_id,
        McpIdentityBinding.server_key == payload.server_key,
    )
    stmt = (
        stmt.where(McpIdentityBinding.membership_id.is_(None))
        if payload.membership_id is None
        else stmt.where(McpIdentityBinding.membership_id == payload.membership_id)
    )
    # R07：绑定只能落"待核验默认值"或已登记核验协议；未知协议名在管理入口
    # 即拒绝，防止绕过 Agent 侧白名单门禁写入可执行绑定。
    if payload.protocol_version not in ALLOWED_PROTOCOL_VERSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "协议版本未登记核验；仅允许 'unverified'（安全默认）或已核验协议清单: "
                + ", ".join(sorted(ALLOWED_PROTOCOL_VERSIONS - {"unverified"}))
            ),
        )
    binding = await session.scalar(stmt)
    created = binding is None
    if binding is None:
        binding = McpIdentityBinding(tenant_id=payload.tenant_id, server_key=payload.server_key)
        session.add(binding)
    binding.membership_id = payload.membership_id
    binding.external_subject = payload.external_subject
    binding.credential_ref = payload.credential_ref
    binding.protocol_version = payload.protocol_version
    binding.status = payload.status
    if not created:
        # 更新即轮换：auth_revision 递增使进行中缓存/调用在下一次检查时失效
        binding.auth_revision += 1
    else:
        binding.auth_revision = 1
    changed_revision = binding.auth_revision

    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="绑定唯一性冲突（默认/覆盖各限一条）"
        ) from exc

    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="mcp_binding.upsert",
        target_type="mcp_binding",
        target_id=str(binding.id),
        # 审计不落 credential_ref 值，只记录引用是否变化
        detail={
            "tenant_id": str(payload.tenant_id),
            "server_key": payload.server_key,
            "membership_id": str(payload.membership_id) if payload.membership_id else None,
            "external_subject": payload.external_subject,
            "protocol_version": payload.protocol_version,
            "status": payload.status,
            "created": created,
            "auth_revision": changed_revision,
        },
        request=request,
    )
    await session.commit()
    return _out(binding)


@router.delete(
    "/{binding_id}", status_code=status.HTTP_204_NO_CONTENT, summary="停用/删除绑定（平台）"
)
async def delete_binding(
    binding_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    actor: CanManage,
) -> None:
    binding = await session.get(McpIdentityBinding, binding_id)
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="绑定不存在")
    binding.auth_revision += 1
    await session.delete(binding)
    record_audit(
        session,
        identity=actor,
        actor_id=actor.user.id,
        actor_account=actor.user.account,
        action="mcp_binding.delete",
        target_type="mcp_binding",
        target_id=str(binding_id),
        detail={"tenant_id": str(binding.tenant_id), "server_key": binding.server_key},
        request=request,
    )
    await session.commit()
