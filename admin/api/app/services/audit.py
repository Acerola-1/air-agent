"""审计日志写入。

后台的所有写操作都应留痕：谁、对什么、做了什么、什么时候、来源 IP。

`actor_id` 与 `actor_account` 同时记录：前者是外键（用户删除后会置 NULL），后者是
冗余的账号快照。账号是登录标识，即使之后改名，历史记录保留当时取值也是审计应有的
语义——如实记录"当时是谁做的"。

调用方在业务事务里 `session.add`，随事务一起提交；审计失败会让业务事务一起回滚，
这是刻意的：宁可写入失败，也不要留下没有痕迹的管理操作。
"""

from __future__ import annotations

import ipaddress
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


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
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            actor_account=actor_account,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail or {},
            ip=_client_ip(request),
        )
    )
