"""幂等创建企业身份运行角色（必须在 alembic upgrade 到 0003 之前执行）。

0003 迁移的 RLS 策略与授权都 `TO air_agent_runtime`；角色不存在时迁移会失败
（这是刻意的顺序约束：先角色后策略，避免“建了表忘了授权”导致运行期 503）。

用法（宿主机，owner 连接）：

    export TENANT_RUNTIME_DB_PASSWORD='<与 RUNTIME_DATABASE_URI 一致的密码>'
    .venv/bin/python scripts/bootstrap_tenant_db.py

环境变量：
    DATABASE_URI                 owner/迁移连接（默认取 app.config，同 admin API）
    TENANT_RUNTIME_DB_PASSWORD   运行角色密码（必需；不落仓库、不打印）
    TENANT_RUNTIME_ROLE          角色名（默认 air_agent_runtime）
    TENANT_LEDGER_WORKER_DB_PASSWORD
                                 可选：台账 outbox 的**受限调度角色**密码；提供即幂等
                                 创建/同步 `air_agent_ledger_worker`（非 BYPASSRLS、
                                 无正文列权限）。未提供则不创建——worker 默认不启用。
    TENANT_LEDGER_WORKER_ROLE    调度角色名（默认 air_agent_ledger_worker）
    TENANT_INSIGHTS_WORKER_DB_PASSWORD
                                 可选：需求洞察分析 worker 的受限角色密码；提供即
                                 幂等创建/同步 `air_agent_insights_worker`（非
                                 BYPASSRLS、只读来源/反馈/墓碑，仅写本域表）。
    TENANT_INSIGHTS_WORKER_ROLE  分析角色名（默认 air_agent_insights_worker）
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.config import get_settings  # noqa: E402


def _valid_role(role: str) -> bool:
    return bool(role) and all(c.isalnum() or c == "_" for c in role)


async def _sync_role(conn, role: str, password: str) -> str:  # noqa: ANN001
    """幂等创建/同步一个 NOSUPERUSER/NOBYPASSRLS 登录角色；返回动作描述。"""
    exists = await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
    # 角色名已校验为 [A-Za-z0-9_]；密码走转义字面量（PG utility 语句不支持 $1 参数）
    literal = "'" + password.replace("'", "''") + "'"
    if exists:
        await conn.execute(text(f"ALTER ROLE {role} LOGIN PASSWORD {literal}"))
        return f"角色 {role} 已存在，密码已同步"
    await conn.execute(
        text(
            f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB "
            f"NOCREATEROLE NOREPLICATION PASSWORD {literal}"
        )
    )
    return f"角色 {role} 已创建（NOSUPERUSER/NOBYPASSRLS）"


async def main() -> int:
    password = os.environ.get("TENANT_RUNTIME_DB_PASSWORD", "")
    if not password:
        print("缺少 TENANT_RUNTIME_DB_PASSWORD（运行角色密码），中止", file=sys.stderr)
        return 2
    role = os.environ.get("TENANT_RUNTIME_ROLE", "air_agent_runtime")
    if not _valid_role(role):
        print("TENANT_RUNTIME_ROLE 含非法字符", file=sys.stderr)
        return 2
    worker_role = os.environ.get("TENANT_LEDGER_WORKER_ROLE", "air_agent_ledger_worker")
    worker_password = os.environ.get("TENANT_LEDGER_WORKER_DB_PASSWORD", "")
    if worker_password and not _valid_role(worker_role):
        print("TENANT_LEDGER_WORKER_ROLE 含非法字符", file=sys.stderr)
        return 2
    insights_worker_role = os.environ.get(
        "TENANT_INSIGHTS_WORKER_ROLE", "air_agent_insights_worker"
    )
    insights_worker_password = os.environ.get("TENANT_INSIGHTS_WORKER_DB_PASSWORD", "")
    if insights_worker_password and not _valid_role(insights_worker_role):
        print("TENANT_INSIGHTS_WORKER_ROLE 含非法字符", file=sys.stderr)
        return 2

    uri = get_settings().sqlalchemy_uri
    engine = create_async_engine(uri)
    try:
        async with engine.connect() as conn:
            print(await _sync_role(conn, role, password))
            await conn.commit()
            if worker_password:
                print(await _sync_role(conn, worker_role, worker_password))
                await conn.commit()
            else:
                print(
                    f"未提供 TENANT_LEDGER_WORKER_DB_PASSWORD：跳过 {worker_role}"
                    "（台账投递 worker 保持未启用）"
                )
            if insights_worker_password:
                print(await _sync_role(conn, insights_worker_role, insights_worker_password))
                await conn.commit()
            else:
                print(
                    f"未提供 TENANT_INSIGHTS_WORKER_DB_PASSWORD：跳过 {insights_worker_role}"
                    "（需求洞察 worker 保持未启用）"
                )
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
