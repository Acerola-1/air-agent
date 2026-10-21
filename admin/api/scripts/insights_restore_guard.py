"""备份恢复后、开放读取前：让过期委托/作业与失效来源先收敛.

顺序（必须）：
1. `restore_ledger_permissions.py` 重施台账/本域授权与 RLS；
2. 本脚本按企业重施墓碑/有效期并暂停无效委托作业；
3. 再启动 insights worker 与页面入口。

脚本使用 owner 连接（恢复期显式授权），但只做本域收敛；不重放模型调用。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.demand_insights import privacy  # noqa: E402


async def main() -> int:
    engine = create_async_engine(get_settings().sqlalchemy_uri)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            tenants = (
                (
                    await session.execute(
                        text(
                            "SELECT tenant_id FROM agent_insight_delegations"
                            " UNION SELECT tenant_id FROM agent_insight_jobs"
                            " UNION SELECT tenant_id FROM agent_question_sources"
                        )
                    )
                )
                .scalars()
                .all()
            )
        totals = {"delegations_expired": 0, "jobs_paused": 0, "sources": 0}
        for tenant_id in tenants:
            async with factory() as session:
                await session.execute(
                    text("SELECT set_config('air_agent.tenant_id', :t, true)"),
                    {"t": str(tenant_id)},
                )
                result = await privacy.restore_guards(session, tenant_id=str(tenant_id))
                await session.commit()
                for key in totals:
                    totals[key] += int(result.get(key, 0))
        print(
            f"恢复收敛完成：tenants={len(tenants)} "
            f"delegations_expired={totals['delegations_expired']} "
            f"jobs_paused={totals['jobs_paused']} sources_purged={totals['sources']}"
        )
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
