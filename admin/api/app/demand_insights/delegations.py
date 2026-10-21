"""有限人类委托：服务器生成引用 + 现有 IAM 会话桥实时内省.

- 作业只保存 delegation_id；委托行只保存应用会话 handle 的 SHA-256，不复制
  Bearer/cookie/token。
- worker 消费委托时通过 `load_session_token` 读取服务端会话行，再调用 IAM
  实时内省；用户/企业/成员/ctx/修订/权限逐项比较。
- 切企业、撤权、改密、会话过期或 IAM 故障都不能静默继续；前两者撤销委托，
  IAM 故障保留委托但暂停作业，便于恢复后续跑。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.demand_insights.config import InsightsSettings
from app.demand_insights.errors import InsightsConflict, InsightsUnavailable
from app.services.remote_identity import RemoteSessionProbe, load_session_token

REQUIRED_PERMISSIONS = frozenset(
    {"agent:operations:read", "agent:content:read", "agent:insights:manage"}
)
MAX_DELEGATION_HOURS = 12


@dataclass(frozen=True)
class DelegationCheck:
    ok: bool
    reason: str
    session_row: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None


def handle_hash_from_cookie(cookie_value: str | None) -> str | None:
    from app.services.remote_identity import parse_handle_cookie

    probe = parse_handle_cookie(cookie_value)
    return probe.handle_hash if probe else None


async def create_delegation(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: uuid.UUID,
    membership_id: uuid.UUID,
    handle_hash: str,
    ctx_version: int,
    user_auth_revision: int,
    tenant_auth_revision: int,
    membership_revision: int,
    profile_id: uuid.UUID,
    session_expires_at: dt.datetime,
    ttl_hours: int,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ttl = min(max(1, ttl_hours), MAX_DELEGATION_HOURS)
    now = dt.datetime.now(dt.UTC)
    expires = min(now + dt.timedelta(hours=ttl), session_expires_at)
    if expires <= now:
        raise InsightsConflict("会话即将过期，不能创建分析委托", code="session_expired")
    delegation_id = uuid.uuid4()
    # 不用 RETURNING：runtime 角色被刻意限制为只能 INSERT 委托、不能读
    # 委托行（RETURNING 会要求 SELECT 策略与列权限）。
    await session.execute(
        text(
            """
            INSERT INTO agent_insight_delegations
                (id, tenant_id, user_id, membership_id, session_handle_hash,
                 ctx_version, user_auth_revision, tenant_auth_revision,
                 membership_revision, profile_id, scope, status, revision,
                 issued_at, expires_at)
            VALUES (:id, :tenant, :user, :membership, :handle, :ctx, :user_rev,
                    :tenant_rev, :membership_rev, :profile, CAST(:scope AS jsonb),
                    'active', 0, :now, :expires)
            """
        ),
        {
            "id": delegation_id,
            "tenant": tenant_id,
            "user": user_id,
            "membership": membership_id,
            "handle": handle_hash,
            "ctx": ctx_version,
            "user_rev": user_auth_revision,
            "tenant_rev": tenant_auth_revision,
            "membership_rev": membership_revision,
            "profile": profile_id,
            "scope": __import__("json").dumps(scope or {}, ensure_ascii=False),
            "now": now,
            "expires": expires,
        },
    )
    return {"id": delegation_id, "expires_at": expires}


async def load_delegation(
    session: AsyncSession, *, tenant_id: str, delegation_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    """
                SELECT id, tenant_id, user_id, membership_id, session_handle_hash,
                       ctx_version, user_auth_revision, tenant_auth_revision,
                       membership_revision, profile_id, status, revision, issued_at,
                       expires_at, last_checked_at
                FROM agent_insight_delegations
                WHERE tenant_id=:tenant AND id=:id
                """
                ),
                {"tenant": tenant_id, "id": delegation_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def _revoke(session: AsyncSession, *, delegation_id: uuid.UUID) -> None:
    await session.execute(
        text(
            "UPDATE agent_insight_delegations SET status='revoked', revision=revision+1,"
            " last_checked_at=now() WHERE id=:id AND status='active'"
        ),
        {"id": delegation_id},
    )


async def check_delegation(
    session: AsyncSession,
    *,
    delegation: dict[str, Any],
    settings: InsightsSettings,
    touch: bool = True,
) -> DelegationCheck:
    """实时内省并逐项比较固定 scope；不信任数据库投影或浏览器声明."""
    now = dt.datetime.now(dt.UTC)
    if delegation["status"] != "active":
        return DelegationCheck(False, "delegation_not_active")
    if delegation["expires_at"] <= now:
        await _revoke(session, delegation_id=delegation["id"])
        return DelegationCheck(False, "delegation_expired")
    probe = RemoteSessionProbe(handle_hash=delegation["session_handle_hash"])
    row = await load_session_token(session, probe)
    if row is None:
        await _revoke(session, delegation_id=delegation["id"])
        return DelegationCheck(False, "session_expired_or_revoked")
    token = row.get("subject_token")
    if not token or hashlib.sha256(token.encode()).hexdigest() != row["token_hash"]:
        await _revoke(session, delegation_id=delegation["id"])
        return DelegationCheck(False, "session_credential_invalid")
    try:
        from common.identity.remote import subject_payload

        payload = await asyncio.to_thread(
            subject_payload,
            token,
            expected_audience=row["audience"],
            include_execution_context=True,
        )
    except Exception as exc:  # noqa: BLE001 - IAM 故障不是撤权
        raise InsightsUnavailable("身份服务暂不可用，作业暂停", code="iam_unavailable") from exc
    tenant = payload.get("current_tenant") or {}
    member = payload.get("membership") or {}
    revisions = payload.get("revisions") or {}
    permissions = set(payload.get("permissions") or [])
    mismatches: list[str] = []
    if payload.get("principal", {}).get("id") != str(delegation["user_id"]):
        mismatches.append("user")
    if payload.get("session", {}).get("id") != str(row.get("iam_session_id")):
        mismatches.append("session")
    if str(tenant.get("id")) != str(delegation["tenant_id"]):
        mismatches.append("tenant")
    if str(member.get("id")) != str(delegation["membership_id"]):
        mismatches.append("membership")
    if int(payload.get("context_version", -1)) != int(delegation["ctx_version"]):
        mismatches.append("context_version")
    if int(revisions.get("user_auth_revision", -1)) != int(delegation["user_auth_revision"]):
        mismatches.append("user_revision")
    if int(revisions.get("tenant_auth_revision", -1)) != int(delegation["tenant_auth_revision"]):
        mismatches.append("tenant_revision")
    if int(revisions.get("membership_revision", -1)) != int(delegation["membership_revision"]):
        mismatches.append("membership_revision")
    if not REQUIRED_PERMISSIONS <= permissions:
        mismatches.append("permissions")
    if payload.get("needs_tenant_selection"):
        mismatches.append("tenant_not_selected")
    if mismatches:
        await _revoke(session, delegation_id=delegation["id"])
        return DelegationCheck(False, ",".join(mismatches), payload=payload)
    if touch:
        await session.execute(
            text(
                "UPDATE agent_insight_delegations SET last_checked_at=:now"
                " WHERE id=:id AND status='active'"
            ),
            {"now": now, "id": delegation["id"]},
        )
    return DelegationCheck(True, "ok", session_row=row, payload=payload)
