"""Agent 侧身份数据库访问（psycopg 直连运行角色）.

- 连接 RUNTIME_DATABASE_URI（非 owner、非 BYPASSRLS 的 air_agent_runtime）；
  与 admin API 同一角色：身份状态读取同样受 RLS 约束.
- 所有查询在事务内先 `SET LOCAL` 票据中的可信标识作为 GUC（票据已验签），
  再按当前作用域查状态——查询条件与 RLS 双重限定.
- 连接池故障/缺配置一律抛 IdentityBackendUnavailable：调用方失败关闭（503）.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool

_IDENTITY_SQL = """
SELECT
    s.revoked_at IS NULL                          AS session_active,
    s.expires_at > now()                          AS session_unexpired,
    s.ctx_version                                 AS session_ctx_version,
    u.is_active                                   AS user_active,
    u.account                                     AS account,
    u.auth_revision                               AS user_auth_revision,
    t.status                                      AS tenant_status,
    t.auth_revision                               AS tenant_auth_revision,
    t.code                                        AS tenant_code,
    m.status                                      AS membership_status,
    m.revision                                    AS membership_revision
FROM auth_sessions s
JOIN users u ON u.id = s.user_id
LEFT JOIN tenants t ON t.id = s.active_tenant_id
LEFT JOIN tenant_memberships m
    ON m.tenant_id = s.active_tenant_id AND m.user_id = s.user_id
WHERE s.id = %(session_id)s
"""

_OWNERSHIP_FETCH = """
SELECT tenant_id::text AS tenant_id, user_id::text AS user_id, source
FROM thread_ownership WHERE thread_id = %(thread_id)s
"""

# R01 认领协议：新声明先落 `native_pending`，只有聊天代理确认平台真实创建
# 成功后才升级为 `native`；未确认的归属对一切读取路径不可见（未知历史归属
# 不可访问原则同样覆盖"认领失败残留"）。
# claim_session_id 绑定"哪一次会话创建产生了这条 pending 认领"：确认端点只
# 能升级**同一已验票据会话**的 pending 行（F01：内部凭据 + 身份匹配 + pending
# 存在都不构成创建事实，作用域与会话绑定才是可核验的最小凭证）。
_OWNERSHIP_CLAIM = """
INSERT INTO thread_ownership
    (thread_id, tenant_id, user_id, source, claim_session_id)
VALUES (%(thread_id)s, %(tenant_id)s, %(user_id)s, 'native_pending',
        NULLIF(%(session_id)s, '')::uuid)
ON CONFLICT (thread_id) DO NOTHING
RETURNING thread_id
"""

_OWNERSHIP_CONFIRM = """
UPDATE thread_ownership SET source = 'native'
WHERE thread_id = %(thread_id)s AND tenant_id = %(tenant_id)s
  AND user_id = %(user_id)s AND source = 'native_pending'
  AND claim_session_id = NULLIF(%(session_id)s, '')::uuid
RETURNING thread_id
"""

_OWNERSHIP_CONFIRMED_SAME = """
SELECT 1 AS x FROM thread_ownership
WHERE thread_id = %(thread_id)s AND tenant_id = %(tenant_id)s
  AND user_id = %(user_id)s AND source = 'native'
"""

_BINDING_SQL = """
SELECT b.tenant_id::text AS tenant_id, b.membership_id::text AS membership_id,
       b.server_key, b.external_subject, b.credential_ref, b.protocol_version,
       b.auth_revision, b.status
FROM mcp_identity_bindings b
WHERE b.tenant_id = %(tenant_id)s AND b.server_key = %(server_key)s
  AND (b.membership_id = %(membership_id)s
       OR (b.membership_id IS NULL AND NOT EXISTS (
             SELECT 1 FROM mcp_identity_bindings o
             WHERE o.tenant_id = b.tenant_id AND o.server_key = b.server_key
               AND o.membership_id = %(membership_id)s)))
"""


async def fetch_mcp_binding(
    tenant_id: str, membership_id: str, server_key: str
) -> dict | None:
    """解析企业默认绑定或成员覆盖绑定（覆盖优先）。GUC 企业作用域 + 显式条件."""
    return await _fetch_row(
        _BINDING_SQL,
        {
            "tenant_id": tenant_id,
            "membership_id": membership_id,
            "server_key": server_key,
        },
        {"air_agent.tenant_id": tenant_id, "air_agent.user_id": ""},
    )


async def binding_exists(tenant_id: str, server_key: str) -> bool:
    """该企业在该服务上是否存在任意绑定（供懒可用判定，不解析凭据）."""
    row = await _fetch_row(
        "SELECT 1 AS x FROM mcp_identity_bindings WHERE tenant_id = %(t)s AND server_key = %(k)s LIMIT 1",
        {"t": tenant_id, "k": server_key},
        {"air_agent.tenant_id": tenant_id},
    )
    return row is not None


_POOL: AsyncConnectionPool | None = None


class IdentityBackendUnavailable(Exception):
    """身份后端不可用：必须失败关闭，绝不放行匿名/降级身份."""


def _uri() -> str:
    uri = os.environ.get("RUNTIME_DATABASE_URI", "")
    if not uri:
        raise IdentityBackendUnavailable("RUNTIME_DATABASE_URI not configured")
    return uri


async def get_pool() -> AsyncConnectionPool:
    """返回共享连接池（首次调用打开）."""
    global _POOL
    if _POOL is None:
        _POOL = AsyncConnectionPool(
            conninfo=_uri(),
            min_size=1,
            max_size=8,
            open=False,
            timeout=10,
            kwargs={"row_factory": psycopg.rows.dict_row, "autocommit": False},
        )
        await _POOL.open(wait=True, timeout=10)
    return _POOL


async def close_pool() -> None:
    """关闭并释放连接池."""
    global _POOL
    if _POOL is not None:
        await _POOL.close()
        _POOL = None


async def _execute(sql: str, params: dict[str, Any], guc: dict[str, str]) -> None:
    """执行不返回行的语句（INSERT/DELETE）。同样失败关闭."""
    await _run(sql, params, guc, fetch=False)


async def _fetch_row(
    sql: str, params: dict[str, Any], guc: dict[str, str]
) -> dict | None:
    return await _run(sql, params, guc, fetch=True)


async def _run(
    sql: str, params: dict[str, Any], guc: dict[str, str], *, fetch: bool
) -> dict | None:
    try:
        pool = await get_pool()
    except IdentityBackendUnavailable:
        raise
    except Exception as exc:  # 池打开失败同样失败关闭
        raise IdentityBackendUnavailable(
            f"pool unavailable: {type(exc).__name__}"
        ) from exc
    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                for key, value in guc.items():
                    await conn.execute(
                        "SELECT set_config(%s, %s, true)", (key, value or "")
                    )
                cur = await conn.execute(sql, params)
                if not fetch:
                    return None
                row = await cur.fetchone()
                return dict(row) if row is not None else None
    except psycopg.Error as exc:
        # 权限/连接错误：失败关闭，让调用方返回不可用；不区分细节避免探测.
        # 服务端日志记录真实原因（不外泄给客户端）.
        from loguru import logger

        logger.bind(identity_db=True).exception(
            "identity query failed: {}", type(exc).__name__
        )
        raise IdentityBackendUnavailable("identity query rejected or failed") from exc


@dataclass(frozen=True)
class IdentityState:
    """会话/账号/企业/成员的实时状态快照（来自身份库）."""

    session_active: bool
    session_unexpired: bool
    session_ctx_version: int
    user_active: bool
    account: str
    user_auth_revision: int
    tenant_status: str | None
    tenant_auth_revision: int | None
    tenant_code: str | None
    membership_status: str | None
    membership_revision: int | None


async def fetch_identity_state(
    session_id: str,
    *,
    user_id: str,
    tenant_id: str,
) -> IdentityState | None:
    """按票据标识查实时状态。GUC 用票据内已验签值，查询本身再按主键匹配."""
    row = await _fetch_row(
        _IDENTITY_SQL,
        {"session_id": session_id},
        {
            "air_agent.session_id": session_id,
            "air_agent.user_id": user_id,
            "air_agent.tenant_id": tenant_id,
        },
    )
    if row is None:
        return None
    return IdentityState(
        session_active=bool(row["session_active"]),
        session_unexpired=bool(row["session_unexpired"]),
        session_ctx_version=int(row["session_ctx_version"]),
        user_active=bool(row["user_active"]),
        account=str(row["account"]),
        user_auth_revision=int(row["user_auth_revision"]),
        tenant_status=row["tenant_status"],
        tenant_auth_revision=row["tenant_auth_revision"],
        tenant_code=row["tenant_code"],
        membership_status=row["membership_status"],
        membership_revision=row["membership_revision"],
    )


_OWNERSHIP_FETCH_TENANT = """
SELECT tenant_id::text AS tenant_id, user_id::text AS user_id, source
FROM thread_ownership WHERE thread_id = %(thread_id)s
"""


async def fetch_thread_in_tenant(thread_id: str, *, tenant_id: str) -> dict | None:
    """租户作用域的线程归属查询（服务级只读校准用；不绑定用户）.

    F05：校准主体不是任何用户，因此只按**票据绑定的租户**限定可见性
    （`thread_ownership` 的 RLS 本就是租户级策略），失败关闭。
    """
    row = await _fetch_row(
        _OWNERSHIP_FETCH_TENANT,
        {"thread_id": thread_id},
        {"air_agent.tenant_id": tenant_id},
    )
    return row


async def fetch_thread_owner(
    thread_id: str, *, tenant_id: str, user_id: str
) -> dict | None:
    """线程归属；仅当归属行在当前企业+用户作用域内才返回（RLS + WHERE 双限）."""
    row = await _fetch_row(
        _OWNERSHIP_FETCH,
        {"thread_id": thread_id},
        {"air_agent.tenant_id": tenant_id, "air_agent.user_id": user_id},
    )
    return row


_OWNERSHIP_DELETE = """
DELETE FROM thread_ownership
WHERE thread_id = %(thread_id)s AND tenant_id = %(tenant_id)s AND user_id = %(user_id)s
"""


async def delete_thread_ownership(
    thread_id: str, *, tenant_id: str, user_id: str
) -> None:
    """删除本人本企业的归属行（幂等）."""
    await _execute(
        _OWNERSHIP_DELETE,
        {"thread_id": thread_id, "tenant_id": tenant_id, "user_id": user_id},
        {"air_agent.tenant_id": tenant_id, "air_agent.user_id": user_id},
    )


async def claim_thread_ownership(
    thread_id: str, *, tenant_id: str, user_id: str, session_id: str = ""
) -> str:
    """threads.create 前的原子认领（R01）.

    返回值：
    - ``claimed``  新建待确认归属（平台创建成功后由代理确认，否则保持不可见）
    - ``same``     已存在本人本企业的归属（严格创建重试路径，交由平台裁决）
    - ``conflict`` 归属已存在且不属于本人本企业——包括"他人/他企业的历史
      线程"与"RLS 不可见的他企业行"；绝不吞并未知历史归属。

    数据库故障抛 IdentityBackendUnavailable（创建不放行无归属线程）。
    """
    inserted = await _fetch_row(
        _OWNERSHIP_CLAIM,
        {
            "thread_id": thread_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "session_id": session_id,
        },
        {"air_agent.tenant_id": tenant_id, "air_agent.user_id": user_id},
    )
    if inserted is not None:
        return "claimed"
    existing = await fetch_thread_owner(thread_id, tenant_id=tenant_id, user_id=user_id)
    if existing is None:
        # 行存在但 RLS 不可见 = 其他企业归属；或同企业他人——一律按冲突拒绝
        return "conflict"
    if str(existing["user_id"]) == user_id:
        return "same"
    return "conflict"


async def confirm_thread_claim(
    thread_id: str, *, tenant_id: str, user_id: str, session_id: str
) -> bool:
    """平台真实创建成功后的归属确认：pending → native（会话绑定，幂等）.

    F01：只升级**同一已验票据会话**建立的 pending 认领；已确认且同归属的
    重复确认也返回 True（代理重试安全）；他人/未知归属/他会话认领返回
    False，不泄露任何存在性信息。
    """
    guc = {"air_agent.tenant_id": tenant_id, "air_agent.user_id": user_id}
    params = {
        "thread_id": thread_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "session_id": session_id,
    }
    row = await _fetch_row(_OWNERSHIP_CONFIRM, params, guc)
    if row is not None:
        return True
    already = await _fetch_row(_OWNERSHIP_CONFIRMED_SAME, params, guc)
    return already is not None
