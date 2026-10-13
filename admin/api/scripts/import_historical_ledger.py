"""历史台账导入（迁移工具，owner 连接）：dry-run 归属报告 + 幂等导入。

对应 change add-agent-event-ledger 任务 7.2 / design §Migration：

- **显式归属**：只导入 `thread_ownership` 中 `source in ('native','migrated')` 且
  tenant/user 明确的历史线程；`native_pending` 与未知来源一律列为**不可归属**，
  不补造企业归属。
- **不伪造**：只建 `agent_requests`（带 `identity_revision.historical=true` 标记），
  **不建 attempt**、不补造反馈/Token/耗时/模型时间；`admitted_at` 取归属行建立时间。
- **不与真实采集混算**：`agent_metrics/summary` 默认口径排除 historical 请求，另行
  单列，避免历史线程抬高提问量。
- **幂等**：按 (tenant, thread, historical 标记) 去重，重复执行不新增。

用法：

    DATABASE_URI=postgresql://air_agent:...@host:15432/air_agent \\
      .venv/bin/python scripts/import_historical_ledger.py [--apply] [--tenant <uuid>] [--limit N]

默认 dry-run；`--apply` 才写入。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db import OwnerSessionLocal  # noqa: E402

HISTORICAL_MARKER = "historical"

_REPORT_SQL = """
SELECT
  count(*) FILTER (WHERE source IN ('native','migrated')
                     AND tenant_id IS NOT NULL AND user_id IS NOT NULL) AS attributable,
  count(*) FILTER (WHERE source NOT IN ('native','migrated')) AS unattributable,
  count(*) FILTER (WHERE source IN ('native','migrated')
                     AND (tenant_id IS NULL OR user_id IS NULL)) AS missing_attribution,
  count(*) AS total
FROM thread_ownership
WHERE (CAST(:tenant AS uuid) IS NULL OR tenant_id = CAST(:tenant AS uuid))
"""

_ALREADY_SQL = """
SELECT count(*) FROM agent_requests r
WHERE r.identity_revision ->> 'historical' = 'true'
  AND (CAST(:tenant AS uuid) IS NULL OR r.tenant_id = CAST(:tenant AS uuid))
"""

_IMPORT_SQL = """
INSERT INTO agent_requests
  (id, tenant_id, user_id, membership_id, thread_id, graph_id, operation_kind,
   identity_revision, admitted_at, created_at)
SELECT gen_random_uuid(), o.tenant_id, o.user_id, o.user_id, o.thread_id,
       'historical', 'initial',
       jsonb_build_object('historical', true, 'attribution_source', 'thread_ownership',
                          'imported_at', now()::text),
       o.created_at, now()
FROM thread_ownership o
WHERE o.source IN ('native', 'migrated')
  AND o.tenant_id IS NOT NULL AND o.user_id IS NOT NULL
  AND (CAST(:tenant AS uuid) IS NULL OR o.tenant_id = CAST(:tenant AS uuid))
  AND NOT EXISTS (
    SELECT 1 FROM agent_requests r
    WHERE r.tenant_id = o.tenant_id AND r.thread_id = o.thread_id
      AND r.identity_revision ->> 'historical' = 'true')
LIMIT :limit
"""


async def _run(apply: bool, tenant: str | None, limit: int) -> int:
    async with OwnerSessionLocal() as session:
        report = (await session.execute(text(_REPORT_SQL), {"tenant": tenant})).mappings().first()
        already = (await session.execute(text(_ALREADY_SQL), {"tenant": tenant})).scalar()
        print(
            "归属报告: "
            f"可归属={report['attributable']} 不可归属(pending/未知)={report['unattributable']} "
            f"缺归属={report['missing_attribution']} 合计={report['total']} "
            f"已导入历史请求={already}"
        )
        if not apply:
            print("dry-run：未写入。确认后加 --apply。")
            return 0
        result = await session.execute(text(_IMPORT_SQL), {"tenant": tenant, "limit": limit})
        await session.commit()
        print(f"已导入历史请求 {result.rowcount} 条（不建 attempt、不补造反馈/Token/耗时）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    parser.add_argument("--tenant", default=None, help="仅处理指定企业")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    return asyncio.run(_run(args.apply, args.tenant, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
