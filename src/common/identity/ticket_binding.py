"""票据摘要 → 执行引用绑定：rt-v1 与 IAM verification_ref 的服务端桥接（I2 G2.1）.

contract（运行票据和执行引用）落地：
- 签票门面（BFF/admin api 进程，`facade_issue_and_bind`）：
  1. 以当前人类 subject_token + runtime 受众内省（include_execution_context=true）
     取得 IAM 登记的真实执行引用（verification_ref + 引用快照修订）；
  2. 调 IAM /v1/internal/runtime-tickets 签发 rt-v1 运行票据；
  3. 本地验签票据并比较票据身份/四修订与引用快照**完全一致**——竞态不一致
     即 409，不登记可用绑定；
  4. 以票据 SHA-256 摘要为主键登记绑定（原始票据/token 不落存储）。
- native 复核（`resolve_binding_for_ticket`）：native Auth 验签票据后，按摘要找
  绑定，并用带内部 client 认证的 execution_context **实时**复查 IAM；修订/上下文
  不匹配或主体无效即拒绝。绑定只提供"非秘密引用 + 修订基线"，引用本身不能
  签票/发运行/换 subject_token（IAM 侧裁决）。
- 两个进程必须共享同一业务库（agent_iam_ticket_bindings，0015）；登记与复查
  使用同一登记的 Agent internal client（不同进程可共享受控凭据）。
- 生命周期：入口绑定按票据 exp 到期（≤60s）；运行中引用复查按 IAM 侧
  valid_until（TTL 43200s 或会话绝对到期）。过期清理有界（惰性 DELETE）。
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass

from common.identity.db import IdentityBackendUnavailable
from common.identity.remote import (
    RemoteIdentityRejected,
    RemoteProtocolError,
    recheck_execution_context,
    require_remote_config,
)
from common.identity.ticket import TicketClaims, TicketError, parse_keys, verify_ticket

BINDING_AUDIENCE_ENV = "IAM_RUNTIME_AUDIENCE"
_TICKET_AUDIENCE_DEFAULT = "langgraph-runtime"


class TicketBindingError(Exception):
    """签票/绑定流程错误（竞态不一致、IAM 拒绝、配置缺失）。调用方映射 409/503."""


@dataclass(frozen=True)
class TicketBinding:
    """已验证绑定（native 复核产物；仅非秘密引用与修订，无凭据）."""

    verification_ref: str
    user_id: str
    tenant_id: str
    membership_id: str
    session_id: str
    audience: str
    issuer: str
    user_auth_revision: int
    tenant_auth_revision: int
    membership_revision: int
    session_ctx_version: int
    # 入站内省时冻结的 IAM 精确权限集合（非秘密）；native create_run 用它求交
    # 业务使用权限。逐发送仍以执行引用复查修订，不缓存正向授权到业务存储。
    permissions: frozenset[str] = frozenset()


def _audience() -> str:
    return os.environ.get(BINDING_AUDIENCE_ENV, _TICKET_AUDIENCE_DEFAULT)


def _runtime_audience() -> str:
    return os.environ.get("RUNTIME_TICKET_AUDIENCE", _TICKET_AUDIENCE_DEFAULT)


def _keys() -> list[tuple[str, str]]:
    return parse_keys(os.environ.get("RUNTIME_TICKET_KEYS", ""))


def ticket_digest(ticket: str) -> str:
    """票据 SHA-256 摘要（存储主键；原始票据不落盘/不进 Trace）。."""
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


def _iam_post(
    config: dict[str, str],
    path: str,
    body: dict,
    *,
    timeout: float,
) -> dict:
    from common.identity.remote import RemoteContextMismatch, request_iam, response_json

    response = request_iam("POST", path, config=config, body=body)
    if response.status_code == 409:
        raise RemoteContextMismatch("签票上下文已变更")
    return response_json(response)


# ---------------------------------------------------------------- 签票门面（BFF 进程）


def facade_issue_and_bind(
    subject_token: str,
    *,
    target_tenant_id: str,
    expected_ctx_version: int,
    deadline_seconds: float = 5.0,
    config: dict[str, str] | None = None,
) -> tuple[str, int, TicketBinding]:
    """BFF 签票门面：subject_token → 引用 + 票据 + 绑定登记；返回 (ticket, expires_in, binding).

    - 受众固定 runtime（IAM runtime-tickets 契约）；
    - 票据身份/修订与引用快照竞态不一致 → TicketBindingError（409 语义）；
    - IAM 故障 → IdentityBackendUnavailable（503）。
    """
    cfg = config or require_remote_config()
    audience = _runtime_audience()

    # 1) 内省（含执行引用）
    payload = _iam_post(
        cfg,
        "/v1/internal/identity/introspect",
        {
            "subject_token": subject_token,
            "audience": audience,
            "include_execution_context": True,
        },
        timeout=deadline_seconds,
    )
    if not isinstance(payload, dict) or payload.get("active") is not True:
        raise RemoteIdentityRejected("主体无效（签票门面）")
    ec = payload.get("execution_context")
    if not isinstance(ec, dict) or not ec.get("verification_ref"):
        raise RemoteProtocolError("签票门面缺少执行引用（include_execution_context）")
    from common.identity.remote import parse_human_identity

    ident = parse_human_identity(
        payload, expected_issuer=cfg["IAM_ISSUER"], expected_audience=audience
    )
    if not ident.context_usable:
        raise TicketBindingError("尚未选择有效企业")
    if (
        ident.tenant_id != target_tenant_id
        or ident.context_version != expected_ctx_version
    ):
        raise TicketBindingError("签票上下文已变更")
    if ec.get("session_id") != ident.session_id or ec.get("audience") != audience:
        raise RemoteProtocolError("执行引用会话或受众不一致")
    ref_revisions = ec.get("revisions")
    if not isinstance(ref_revisions, dict):
        raise RemoteProtocolError("执行引用缺少修订基线")
    if ref_revisions != payload["revisions"]:
        raise TicketBindingError("执行引用与身份修订不一致")

    # 2) IAM 签票（rt-v1）
    ticket_resp = _iam_post(
        cfg,
        "/v1/internal/runtime-tickets",
        {
            "subject_token": subject_token,
            "audience": audience,
            "target_tenant_id": target_tenant_id,
            "expected_ctx_version": expected_ctx_version,
        },
        timeout=deadline_seconds,
    )
    ticket = str(ticket_resp.get("ticket") or "")
    expires_in = int(ticket_resp.get("expires_in") or 60)
    if not ticket:
        raise RemoteProtocolError("签票响应缺 ticket")

    # 3) 本地验签 + 与引用快照一致（竞态不一致即 409，不登记）
    claims = verify_ticket(ticket, _keys(), expected_audience=audience)
    _assert_claims_match_identity(claims, payload)
    _assert_revisions_match(claims, ref_revisions)
    _assert_revisions_match(claims, payload["revisions"])

    binding = TicketBinding(
        verification_ref=str(ec["verification_ref"]),
        user_id=claims.user_id,
        tenant_id=claims.tenant_id,
        membership_id=claims.membership_id,
        session_id=claims.session_id,
        audience=audience,
        issuer=cfg["IAM_ISSUER"],
        user_auth_revision=claims.user_auth_revision,
        tenant_auth_revision=claims.tenant_auth_revision,
        membership_revision=claims.membership_revision,
        session_ctx_version=claims.session_ctx_version,
        permissions=ident.permissions,
    )
    return ticket, expires_in, binding


def _assert_claims_match_identity(claims: TicketClaims, payload: dict) -> None:
    """票据身份与内省身份一致；任何不一致即绑定拒绝（A06 伪造/错配面）。."""
    principal = payload.get("principal") or {}
    if claims.user_id != str(principal.get("id") or ""):
        raise TicketBindingError("票据身份与内省主体不一致")
    tenant = payload.get("current_tenant") or {}
    if claims.tenant_id != str(tenant.get("id") or ""):
        raise TicketBindingError("票据企业与内省上下文不一致")
    membership = payload.get("membership") or {}
    if claims.membership_id != str(membership.get("id") or ""):
        raise TicketBindingError("票据成员与内省上下文不一致")
    session = payload.get("session") or {}
    if claims.session_id != str(session.get("id") or ""):
        raise TicketBindingError("票据会话与内省会话不一致")


def _assert_revisions_match(claims: TicketClaims, revisions: dict) -> None:
    expected = (
        ("user_auth_revision", claims.user_auth_revision),
        ("tenant_auth_revision", claims.tenant_auth_revision),
        ("membership_revision", claims.membership_revision),
        ("session_ctx_version", claims.session_ctx_version),
    )
    for key, value in expected:
        raw = revisions.get(key)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw != value:
            raise TicketBindingError(f"票据修订与引用快照不一致: {key}")


# ---------------------------------------------------------------- 绑定存储（两进程共享）


async def register_binding(
    binding: TicketBinding, ticket: str, expires_in: int
) -> None:
    """按票据摘要登记绑定（事务内幂等 upsert；入口到期 = 签发时刻 + expires_in）。."""
    import datetime as dt

    from common.identity.db import get_pool

    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('air_agent.iam_ticket_hash', %s, true)",
                (ticket_digest(ticket),),
            )
            await conn.execute(
                "INSERT INTO agent_iam_ticket_bindings"
                " (ticket_hash, verification_ref, user_id, tenant_id, membership_id,"
                "  session_id, audience, iam_issuer, user_auth_revision,"
                "  tenant_auth_revision, membership_revision, session_ctx_version,"
                "  entry_expires_at, ref_revisions, permissions)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (ticket_hash) DO NOTHING",
                (
                    ticket_digest(ticket),
                    uuid.UUID(binding.verification_ref),
                    uuid.UUID(binding.user_id),
                    uuid.UUID(binding.tenant_id),
                    uuid.UUID(binding.membership_id),
                    uuid.UUID(binding.session_id),
                    binding.audience,
                    binding.issuer,
                    binding.user_auth_revision,
                    binding.tenant_auth_revision,
                    binding.membership_revision,
                    binding.session_ctx_version,
                    dt.datetime.fromtimestamp(
                        verify_ticket(
                            ticket, _keys(), expected_audience=binding.audience
                        ).expires_at,
                        dt.UTC,
                    ),
                    json.dumps(
                        {
                            "user_auth_revision": binding.user_auth_revision,
                            "tenant_auth_revision": binding.tenant_auth_revision,
                            "membership_revision": binding.membership_revision,
                            "session_ctx_version": binding.session_ctx_version,
                        },
                        separators=(",", ":"),
                    ),
                    json.dumps(sorted(binding.permissions), separators=(",", ":")),
                ),
            )


async def load_binding(ticket_hash: str) -> dict | None:
    """按摘要读绑定行（过期返回 None）."""
    import datetime as dt

    from common.identity.db import get_pool

    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "SELECT set_config('air_agent.iam_ticket_hash', %s, true)", (ticket_hash,)
        )
        cursor = await conn.execute(
            "SELECT * FROM agent_iam_ticket_bindings WHERE ticket_hash = %s",
            (ticket_hash,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    record = dict(row)
    if record["entry_expires_at"] <= dt.datetime.now(dt.UTC):
        return None
    return record


async def purge_expired_bindings(limit: int = 200) -> int:
    """有界过期清理（入口绑定到期即删）。返回删除行数。."""
    import datetime as dt

    from common.identity.db import get_pool

    pool = await get_pool()
    async with pool.connection() as conn:
        result = await conn.execute(
            "DELETE FROM agent_iam_ticket_bindings WHERE ticket_hash IN ("
            "SELECT ticket_hash FROM agent_iam_ticket_bindings WHERE entry_expires_at < %s LIMIT %s)",
            (dt.datetime.now(dt.UTC), limit),
        )
    return result.rowcount


# ---------------------------------------------------------------- native 复核（native 进程）


def resolve_binding_for_ticket(
    claims: TicketClaims,
    raw_ticket: str,
    *,
    config: dict[str, str] | None = None,
) -> TicketBinding:
    """Native Auth：验签后的票据 → 绑定查找 → IAM 实时引用复查 → 可信绑定.

    - 找不到绑定 / 摘要不匹配 / 身份或修订与票据不一致 → TicketBindingError（401/409）；
    - IAM 复查 active=false → RemoteIdentityRejected；context_matches=false →
      RemoteContextMismatch；网络故障 → IdentityBackendUnavailable（503）。
    """
    cfg = config or require_remote_config()
    # 同步客户端（native 请求线程内；deadline 0.5s 复用）
    record = _load_binding_sync(ticket_digest(raw_ticket))
    if record is None:
        raise TicketBindingError("票据没有服务端绑定（未经理票门面）")
    if (
        record["audience"] != claims.audience
        or record["iam_issuer"] != cfg["IAM_ISSUER"]
    ):
        raise TicketBindingError("绑定签发者或受众不一致")
    _assert_record_matches_claims(record, claims)
    recheck_execution_context(
        str(record["verification_ref"]),
        {
            "user_auth_revision": claims.user_auth_revision,
            "tenant_auth_revision": claims.tenant_auth_revision,
            "membership_revision": claims.membership_revision,
            "session_ctx_version": claims.session_ctx_version,
        },
        expected_audience=claims.audience,
        config=cfg,
    )
    return _binding_from_record(record)


def _assert_record_matches_claims(record: dict, claims: TicketClaims) -> None:
    """绑定行与票据身份/四修订逐一比对；任何不一致即拒绝（A06 伪造面）。."""
    for column, value in (
        ("user_id", claims.user_id),
        ("tenant_id", claims.tenant_id),
        ("membership_id", claims.membership_id),
        ("session_id", claims.session_id),
    ):
        stored = record.get(column)
        if stored is not None and not isinstance(stored, str):
            stored = str(stored)
        if stored != value:
            raise TicketBindingError(f"绑定与票据不一致: {column}")
    for column, value in (
        ("user_auth_revision", claims.user_auth_revision),
        ("tenant_auth_revision", claims.tenant_auth_revision),
        ("membership_revision", claims.membership_revision),
        ("session_ctx_version", claims.session_ctx_version),
    ):
        if int(record[column]) != value:
            raise TicketBindingError(f"绑定修订与票据不一致: {column}")


def _binding_from_record(record: dict) -> TicketBinding:
    return TicketBinding(
        verification_ref=str(record["verification_ref"]),
        user_id=str(record["user_id"]),
        tenant_id=str(record["tenant_id"]),
        membership_id=str(record["membership_id"]),
        session_id=str(record["session_id"]),
        audience=str(record["audience"]),
        issuer=str(record["iam_issuer"]),
        user_auth_revision=int(record["user_auth_revision"]),
        tenant_auth_revision=int(record["tenant_auth_revision"]),
        membership_revision=int(record["membership_revision"]),
        session_ctx_version=int(record["session_ctx_version"]),
        permissions=_record_permissions(record.get("permissions")),
    )


def _record_permissions(raw) -> frozenset[str]:  # noqa: ANN001 - JSONB 列
    """绑定行 permissions（psycopg 可能给 str 或 list）→ 权限集合."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return frozenset()
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(code) for code in raw if code)


def _load_binding_sync(ticket_hash: str) -> dict | None:
    """同步读绑定行（native 线程；psycopg 直连，失败关闭）。."""
    import datetime as dt

    import psycopg
    from psycopg.rows import dict_row

    uri = os.environ.get("RUNTIME_DATABASE_URI", "")
    if not uri:
        raise IdentityBackendUnavailable("RUNTIME_DATABASE_URI not configured")
    try:
        with psycopg.connect(uri, row_factory=dict_row, connect_timeout=2) as conn:
            conn.execute(
                "SELECT set_config('air_agent.iam_ticket_hash', %s, true)",
                (ticket_hash,),
            )
            row = conn.execute(
                "SELECT * FROM agent_iam_ticket_bindings WHERE ticket_hash = %s",
                (ticket_hash,),
            ).fetchone()
    except psycopg.Error as exc:
        raise IdentityBackendUnavailable("绑定存储不可用") from exc
    if row is None:
        return None
    if row["entry_expires_at"] <= dt.datetime.now(dt.UTC):
        return None
    return dict(row)


__all__ = [
    "TicketBinding",
    "TicketBindingError",
    "TicketError",
    "facade_issue_and_bind",
    "load_binding",
    "purge_expired_bindings",
    "register_binding",
    "resolve_binding_for_ticket",
    "ticket_digest",
]
