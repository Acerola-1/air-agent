"""Fail-closed startup checks for the explicitly selected database deployment mode."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_LEGACY_IDENTITY_TABLES = (
    "users",
    "tenants",
    "tenant_memberships",
    "roles",
    "role_permissions",
    "user_roles",
    "membership_roles",
    "permissions",
    "auth_sessions",
)


async def verify_database_mode(connection: AsyncConnection, configured_mode: str) -> None:
    """Reject local/remote mode mismatches and incomplete remote databases."""
    marker_exists = await connection.scalar(
        text("SELECT to_regclass('public.i4_separation_state') IS NOT NULL")
    )
    if not marker_exists:
        if configured_mode == "remote":
            raise RuntimeError(
                "remote 身份模式要求 Alembic 0019 标记；请先运行 I4 物理分离演练并核对目标库。"
            )
        return

    marker = (
        await connection.execute(
            text("SELECT mode, state FROM i4_separation_state WHERE singleton = 1")
        )
    ).first()
    if marker is None:
        raise RuntimeError("I4 数据库部署标记缺失；拒绝启动。")

    mode, state = marker
    if configured_mode == "local":
        if mode == "remote":
            raise RuntimeError("检测到已分离的 remote 业务库；local 模式连接已拒绝。")
        return

    if mode != "remote" or state != "ready":
        raise RuntimeError(f"remote 身份模式要求物理分离状态 remote/ready；当前为 {mode}/{state}。")

    legacy = (
        (
            await connection.execute(
                text(
                    "SELECT name FROM unnest(CAST(:names AS text[])) AS t(name) "
                    "WHERE to_regclass('public.' || quote_ident(name)) IS NOT NULL"
                ),
                {"names": list(_LEGACY_IDENTITY_TABLES)},
            )
        )
        .scalars()
        .all()
    )
    if legacy:
        raise RuntimeError("remote 业务库仍含旧身份表：" + ", ".join(sorted(legacy)))
