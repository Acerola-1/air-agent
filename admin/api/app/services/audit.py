"""审计日志写入。

后台的所有写操作都应留痕：谁、对什么、做了什么、什么时候、来源 IP。

`actor_id` 与 `actor_account` 同时记录：前者是外键（用户删除后会置 NULL），后者是
冗余的账号快照。账号是登录标识，即使之后改名，历史记录保留当时取值也是审计应有的
语义——如实记录"当时是谁做的"。

调用方在业务事务里 `session.add`，随事务一起提交；审计失败会让业务事务一起回滚，
这是刻意的：宁可写入失败，也不要留下没有痕迹的管理操作。

0003 起带企业维度：传 `identity` 时写入 tenant/成员/平台范围；未传（登录/登出等
会话动作）保持 tenant NULL + actor 本人，由 RLS `audit_insert` 的
“tenant NULL 且 actor=本人”分支放行。管理写入与审计同一事务（任务 4.4）。
"""

from __future__ import annotations

import ipaddress
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

if TYPE_CHECKING:
    from app.services.identity import Identity


def _client_ip(request: Request | None) -> str | None:
    """取客户端 IP；非法值直接丢弃，避免污染 INET 列。"""
    if request is None or request.client is None:
        return None
    candidate = request.client.host
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def record_audit(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    actor_account: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
    request: Request | None = None,
    identity: Identity | None = None,
) -> None:
    """写入一条审计。绝不记录凭据、密码或 cookie（detail 由调用方负责脱敏）。"""
    tenant_id = identity.tenant_id if identity is not None else None
    actor_membership_id = (
        identity.membership.id if identity is not None and identity.membership else None
    )
    platform_scope = (
        bool(identity.platform_admin and identity.tenant is None) if identity else False
    )
    session.add(
        AuditLog(
            actor_id=actor_id,
            actor_account=actor_account,
            actor_membership_id=actor_membership_id,
            tenant_id=tenant_id,
            platform_scope=platform_scope,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail or {},
            ip=_client_ip(request),
        )
    )
