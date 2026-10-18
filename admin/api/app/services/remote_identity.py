"""remote 模式身份解析：IAM 内省 → 业务 Identity（I2，G1.2/G1.3）.

local 与 remote 是启动时显式选择的部署模式：remote 下**不再**解析本地
auth_sessions/角色 join，权限完全来自 IAM（A05：无本地角色放行）。会话权威是
IAM；Agent 侧只持有"服务端应用会话 handle → IAM token 摘要"的桥接存储
（agent_iam_sessions，0014），以及与 IAM 同 UUID 的最小业务引用投影
（iam_reference_*，只服务 FK/RLS/展示，不构成授权源）。

- 每请求实时 introspect（不缓存正向授权，撤销即时生效；deadline 500ms）；
- IAM 不可达/协议失败 → 503 失败关闭，不回退本地身份（A01/A04）；
- 主体无效 → 401；浏览器声明的企业/ctx 只是期望基线，以 IAM 答复为准。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from types import SimpleNamespace

from common.identity.db import IdentityBackendUnavailable
from common.identity.remote import (
    require_remote_config,
)
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SESSION_COOKIE = "air_agent_iam_handle"
ADMIN_SESSION_COOKIE = "air_agent_iam_admin_handle"


def request_cookie(request):
    """BFF 仅转发本前端 cookie，不能按端口判断隔离。"""
    return request.cookies.get(SESSION_COOKIE) or request.cookies.get(ADMIN_SESSION_COOKIE)


SESSION_TTL_SECONDS = 12 * 3600


@dataclass(frozen=True)
class RemoteSessionProbe:
    """cookie 拆出的应用会话标识（未验证前不可用于任何授权）。"""

    handle_hash: str


def parse_handle_cookie(cookie_value: str | None) -> RemoteSessionProbe | None:
    raw = (cookie_value or "").strip()
    if not raw or len(raw) < 32 or "." not in raw:
        return None
    # handle 形状：<random 32hex>.<checksum 8hex>；校验和防误传任意串
    handle, _, checksum = raw.rpartition(".")
    if not handle or len(checksum) != 8:
        return None
    if hashlib.sha256(handle.encode()).hexdigest()[:8] != checksum:
        return None
    return RemoteSessionProbe(handle_hash=hashlib.sha256(raw.encode()).hexdigest())


def new_handle() -> str:
    """生成一次性应用会话 handle（浏览器 HttpOnly cookie 值）。"""
    handle = secrets.token_hex(16)
    return f"{handle}.{hashlib.sha256(handle.encode()).hexdigest()[:8]}"


def compose_cookie(handle: str) -> str:
    return handle


def hash_cookie(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def load_session_token(session: AsyncSession, probe: RemoteSessionProbe) -> dict | None:
    """按 handle 摘要读应用会话行（含 IAM token 摘要与快照标识）。"""
    await session.execute(
        text("SELECT set_config('air_agent.iam_handle_hash', :h, true)"), {"h": probe.handle_hash}
    )
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, token_hash, user_id, tenant_id, membership_id, ctx_version,"
                    " user_auth_revision, tenant_auth_revision, membership_revision,"
                    " audience, iam_issuer, verification_ref, account, tenant_code,"
                    " tenant_name, permissions, iam_session_id, revoked_at,"
                    " expires_at, subject_token"
                    " FROM agent_iam_sessions WHERE handle_hash = :h"
                ),
                {"h": probe.handle_hash},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    if row["revoked_at"] is not None or row["expires_at"] <= dt.datetime.now(dt.UTC):
        return None
    return dict(row)


async def save_iam_session(
    session: AsyncSession,
    *,
    token_hash: str,
    user_id: str,
    account: str,
    subject_token: str | None,
    iam_session_id: str | None,
    tenant_id: str | None,
    tenant_code: str | None,
    tenant_name: str | None,
    membership_id: str | None,
    ctx_version: int,
    revisions: dict[str, int],
    permissions: list[str],
    audience: str,
    issuer: str,
    verification_ref: str | None,
    expires_at: dt.datetime,
) -> str:
    """登记新的应用会话（登录/SSO 兑换后调用）；返回浏览器 handle.

    verification_ref 来自登记进程内的 include_execution_context=true 内省
    原始 token 仅保存在受 RLS 会话键保护的服务端会话行，用于实时内省/切换；
    不进入浏览器 JS、图状态或日志。
    """
    handle = new_handle()
    await session.execute(
        text("SELECT set_config('air_agent.iam_handle_hash', :h, true)"), {"h": hash_cookie(handle)}
    )
    await session.execute(
        text(
            "INSERT INTO agent_iam_sessions"
            " (handle_hash, token_hash, subject_token, user_id, account, tenant_id, tenant_code,"
            "  tenant_name, membership_id, ctx_version, user_auth_revision,"
            "  tenant_auth_revision, membership_revision, permissions,"
            "  audience, iam_issuer, verification_ref, iam_session_id, expires_at)"
            " VALUES (:h, :t, :st, :u, :a, :tn, :tc, :tna, :m, :cv, :ur, :tr, :mr,"
            "         :perm, :aud, :iss, :vref, :sid, :exp)"
        ),
        {
            "h": hash_cookie(handle),
            "t": token_hash,
            "st": subject_token,
            "u": uuid.UUID(user_id),
            "a": account,
            "tn": uuid.UUID(tenant_id) if tenant_id else None,
            "tc": tenant_code,
            "tna": tenant_name,
            "m": uuid.UUID(membership_id) if membership_id else None,
            "cv": ctx_version,
            "ur": revisions["user_auth_revision"],
            "tr": revisions.get("tenant_auth_revision"),
            "mr": revisions.get("membership_revision"),
            "perm": json.dumps(sorted(permissions)),
            "aud": audience,
            "iss": issuer,
            "vref": verification_ref,
            "sid": uuid.UUID(iam_session_id) if iam_session_id else None,
            "exp": expires_at,
        },
    )
    return handle


async def revoke_session(session: AsyncSession, probe: RemoteSessionProbe) -> None:
    await session.execute(
        text("SELECT set_config('air_agent.iam_handle_hash', :h, true)"), {"h": probe.handle_hash}
    )
    await session.execute(
        text(
            "UPDATE agent_iam_sessions SET revoked_at = now(), subject_token = NULL"
            " WHERE handle_hash = :h"
        ),
        {"h": probe.handle_hash},
    )


async def revoke_sessions_for_user(session: AsyncSession, user_id: str) -> None:
    await session.execute(
        text(
            "UPDATE agent_iam_sessions SET revoked_at = now()"
            " WHERE user_id = :u AND revoked_at IS NULL"
        ),
        {"u": uuid.UUID(user_id)},
    )


async def upsert_reference_projection(
    session: AsyncSession,
    *,
    user_id: str,
    account: str,
    display_name: str,
    tenant_id: str | None,
    tenant_code: str | None,
    tenant_name: str | None,
    membership_id: str | None,
    user_auth_revision: int,
    tenant_auth_revision: int | None,
    membership_revision: int | None,
) -> None:
    """同步最小业务引用投影（同 UUID；不授权、不存会话/角色/口令材料）。

    remote 下业务 FK（thread_ownership、偏好、台账等）要求引用行存在；
    这里以 INSERT .. ON CONFLICT 幂等 upsert。任何失败抛
    IdentityBackendUnavailable（投影不可用 = 业务引用不可用，失败关闭）。
    """
    from app.db import TenantContext, apply_tenant_context

    try:
        # This helper is also called by the SSO callback before request middleware.
        # Apply the verified identity before the reference-table RLS policies run.
        await apply_tenant_context(session, TenantContext(user_id=user_id, tenant_id=tenant_id))
        await session.execute(
            text(
                "INSERT INTO iam_reference_users (id, account, display_name, auth_revision)"
                " VALUES (:i, :a, :d, :r)"
                " ON CONFLICT (id) DO UPDATE SET"
                "   account = EXCLUDED.account, display_name = EXCLUDED.display_name,"
                "   auth_revision = EXCLUDED.auth_revision,"
                "   synced_at = now()"
            ),
            {
                "i": uuid.UUID(user_id),
                "a": account,
                "d": display_name,
                "r": user_auth_revision,
            },
        )
        if tenant_id:
            await session.execute(
                text(
                    "INSERT INTO iam_reference_tenants (id, code, name, auth_revision)"
                    " VALUES (:i, :c, :n, :r)"
                    " ON CONFLICT (id) DO UPDATE SET"
                    "   code = EXCLUDED.code, name = EXCLUDED.name,"
                    "   auth_revision = EXCLUDED.auth_revision, synced_at = now()"
                ),
                {
                    "i": uuid.UUID(tenant_id),
                    "c": tenant_code or "",
                    "n": tenant_name or "",
                    "r": tenant_auth_revision or 1,
                },
            )
        if membership_id and tenant_id:
            result = await session.execute(
                text(
                    "INSERT INTO iam_reference_memberships (id, tenant_id, user_id, revision)"
                    " VALUES (:i, :t, :u, :r)"
                    " ON CONFLICT (id) DO UPDATE SET"
                    "   revision = EXCLUDED.revision, synced_at = now()"
                    " WHERE iam_reference_memberships.tenant_id = EXCLUDED.tenant_id"
                    "   AND iam_reference_memberships.user_id = EXCLUDED.user_id"
                ),
                {
                    "i": uuid.UUID(membership_id),
                    "t": uuid.UUID(tenant_id),
                    "u": uuid.UUID(user_id),
                    "r": membership_revision or 1,
                },
            )
            if result.rowcount != 1:
                raise ValueError("IAM 成员引用已绑定到其他企业或用户")
    except Exception as exc:  # noqa: BLE001 - 投影失败按后端不可用关闭
        raise IdentityBackendUnavailable("业务引用投影不可用") from exc


def _load_permissions(raw) -> list[str]:  # noqa: ANN001 - JSONB 列（str/list）
    """JSONB permissions 列 → 权限码列表（psycopg 可能给 str 或 list）."""
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    return [str(code) for code in raw if code]


def _identity_objects(
    user_id: str,
    account: str,
    tenant_id: str | None,
    tenant_code: str | None,
    tenant_name: str | None,
    membership_id: str | None,
    session_id: str | None,
    ctx_version: int,
    revisions: dict[str, int],
    permissions: frozenset[str],
    expires_at: dt.datetime,
):
    """构造与 Identity 消费面对齐的轻量对象（remote 不查询本地 ORM 行）。"""
    user = SimpleNamespace(
        id=uuid.UUID(user_id),
        account=account,
        display_name=account,
        auth_revision=revisions["user_auth_revision"],
        # remote 身份不在业务库保存口令或占位口令。
        password_hash=None,
        is_active=True,
        last_login_at=None,
        # IAM 当前身份响应不含用户创建时间，不伪造成本地时间。
        created_at=None,
        followed_regions=[],
    )
    tenant = (
        SimpleNamespace(
            id=uuid.UUID(tenant_id),
            code=tenant_code or "",
            name=tenant_name or "",
            status="active",
            auth_revision=revisions.get("tenant_auth_revision") or 1,
        )
        if tenant_id
        else None
    )
    membership = (
        SimpleNamespace(
            id=uuid.UUID(membership_id),
            tenant_id=uuid.UUID(tenant_id) if tenant_id else None,
            department_id=None,
            status="active",
            revision=revisions.get("membership_revision") or 1,
        )
        if membership_id
        else None
    )
    session_obj = SimpleNamespace(
        id=uuid.UUID(session_id) if session_id else None,  # IAM 会话 id（展示）
        active_tenant_id=uuid.UUID(tenant_id) if tenant_id else None,
        ctx_version=ctx_version,
        created_at=dt.datetime.now(dt.UTC),
        expires_at=expires_at,
    )
    from app.services.identity import Identity

    return Identity(
        user=user,
        session=session_obj,
        tenant=tenant,
        membership=membership,
        platform_admin=False,  # remote 平台管理走 IAM 管理台；业务侧不本地判定
        permissions=permissions,
        membership_role_codes=frozenset(),
        platform_roles=(),
        tenant_status="active" if tenant_id else None,
        membership_status="active" if membership_id else None,
    )


def raise_remote_error(exc: Exception) -> None:
    """将结构化 IAM 故障映射到旧业务 API 状态码，不匹配异常文本。"""
    from common.identity.remote import RemoteContextMismatch, RemoteIdentityRejected

    code = (
        409
        if isinstance(exc, RemoteContextMismatch)
        else 401
        if isinstance(exc, RemoteIdentityRejected)
        else 503
    )
    raise HTTPException(
        status_code=code,
        detail={
            401: "登录已失效，请重新登录",
            409: "企业上下文已变更，请刷新",
            503: "身份服务暂不可用",
        }[code],
    ) from exc


async def iam_user_request(token: str, method: str, path: str, body: dict | None = None):
    """异步请求仅在线程中等待 IAM；Bearer 只存在服务端。"""
    import asyncio

    from common.identity.remote import RemoteContextMismatch, RemoteIdentityRejected, request_iam

    config = require_remote_config()
    response = await asyncio.to_thread(
        request_iam, method, path, config=config, body=body, bearer=token
    )
    if response.status_code == 401:
        raise RemoteIdentityRejected("会话已失效")
    if response.status_code == 409:
        raise RemoteContextMismatch("会话上下文已变更")
    if response.status_code in (400, 403, 404):
        raise HTTPException(status_code=response.status_code, detail="身份请求被拒绝")
    if response.status_code not in (200, 204):
        raise IdentityBackendUnavailable("IAM 请求不可用")
    if response.status_code == 204:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise IdentityBackendUnavailable("IAM 响应协议异常") from exc


async def resolve_remote_identity(session: AsyncSession, cookie_value: str | None):
    """每请求以服务端 token 重新内省权限；投影只服务 FK，不能授权。"""
    import asyncio
    import hmac
    from dataclasses import replace

    from common.identity.remote import subject_payload

    from app.db import TenantContext, apply_tenant_context

    probe = parse_handle_cookie(cookie_value)
    row = await load_session_token(session, probe) if probe else None
    if row is None:
        raise HTTPException(status_code=401, detail="应用会话无效，请重新登录")
    token = row.get("subject_token")
    if not token or not hmac.compare_digest(hash_token(token), row["token_hash"]):
        raise HTTPException(status_code=401, detail="应用会话凭据无效")
    try:
        payload = await asyncio.to_thread(subject_payload, token, expected_audience=row["audience"])
        if payload["principal"]["id"] != str(row["user_id"]) or payload["session"]["id"] != str(
            row["iam_session_id"]
        ):
            raise IdentityBackendUnavailable("IAM 主体与应用会话不一致")
        tenants = await iam_user_request(token, "GET", "/v1/auth/tenants")
        if not isinstance(tenants, list):
            raise IdentityBackendUnavailable("IAM 企业列表协议异常")
    except HTTPException:
        raise
    except Exception as exc:
        raise_remote_error(exc)
    tenant = payload["current_tenant"] or {}
    member = payload["membership"] or {}
    revisions = payload["revisions"]
    fields = dict(
        user_id=payload["principal"]["id"],
        account=payload["user"]["account"],
        tenant_id=tenant.get("id"),
        tenant_code=tenant.get("code"),
        tenant_name=tenant.get("name"),
        membership_id=member.get("id"),
        user_auth_revision=revisions["user_auth_revision"],
        tenant_auth_revision=revisions["tenant_auth_revision"],
        membership_revision=revisions["membership_revision"],
    )
    await upsert_reference_projection(
        session,
        **fields,
        display_name=payload["user"].get("display_name") or fields["account"],
    )
    await apply_tenant_context(
        session,
        TenantContext(
            user_id=fields["user_id"],
            tenant_id=fields["tenant_id"],
            session_id=payload["session"]["id"],
        ),
    )
    verification_ref = None
    has_mcp = fields["tenant_id"] and await session.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM mcp_identity_bindings WHERE tenant_id=:t"
            " AND status='active' AND (membership_id IS NULL OR membership_id=:m))"
        ),
        {"t": uuid.UUID(fields["tenant_id"]), "m": uuid.UUID(fields["membership_id"])},
    )
    if has_mcp:
        try:
            with_ref = await asyncio.to_thread(
                subject_payload,
                token,
                expected_audience=row["audience"],
                include_execution_context=True,
            )
            if any(
                with_ref[key] != payload[key]
                for key in ("principal", "session", "current_tenant", "membership", "revisions")
            ):
                from common.identity.remote import RemoteContextMismatch

                raise RemoteContextMismatch("目录核验期间身份上下文已变化")
            ec = with_ref.get("execution_context") or {}
            verification_ref = ec.get("verification_ref")
            if (
                not isinstance(verification_ref, str)
                or not verification_ref
                or ec.get("revisions") != revisions
            ):
                raise IdentityBackendUnavailable("IAM执行引用协议异常")
        except Exception as exc:
            raise_remote_error(exc)
    identity = _identity_objects(
        **{key: value for key, value in fields.items() if not key.endswith("revision")},
        session_id=payload["session"]["id"],
        ctx_version=payload["context_version"],
        revisions=revisions,
        permissions=frozenset(payload["permissions"]),
        expires_at=row["expires_at"],
    )
    identity.user.display_name = payload["user"].get("display_name") or identity.user.account
    identity.session.created_at = dt.datetime.fromisoformat(
        payload["session"]["created_at"].replace("Z", "+00:00")
    )
    if identity.membership is not None:
        department = member.get("department_id")
        identity.membership.department_id = uuid.UUID(department) if department else None
    return replace(
        identity,
        remote_tenants=tuple(tenants),
        remote_verification_ref=verification_ref,
        membership_role_codes=frozenset(role["code"] for role in payload.get("tenant_roles", [])),
    )
