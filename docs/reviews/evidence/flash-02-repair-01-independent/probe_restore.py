"""R05 恢复演练（独立验收反例，Python 版避免 shell 引号问题）.

流程：先备份 → **之后**才删除 → 灾难 + 恢复旧备份 → 重施外部删除记录与当前权限
→ 允许读取。仅使用新建专用演练库，绝不对共享库 DROP。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO = Path("/Users/acerola/Dev/Python/air_agent")
DB = "air_agent_ivrp01_restore_70f85d2a"
OWNER_PW = re.search(
    r"^DATABASE_URI=postgresql://[^:]+:([^@]+)@", (REPO / ".env").read_text(), re.M
).group(1)
RT_PW = re.search(
    r"^RUNTIME_DATABASE_URI=postgresql://[^:]+:([^@]+)@",
    (REPO / ".env.langgraph").read_text(),
    re.M,
).group(1)


def podman(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def psql(sql: str, db: str = DB) -> str:
    res = podman(
        ["container", "exec", "air-agent-postgres", "psql", "-U", "air_agent", "-d", db, "-t", "-A", "-c", sql]
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[:300])
    return res.stdout.strip()


def sh(cmd: str) -> subprocess.CompletedProcess:
    return podman(["container", "exec", "air-agent-postgres", "sh", "-c", cmd])


REQ = "12121212-1212-4212-8212-121212121212"
REQ_KEEP = "16161616-1616-4616-8616-161616161616"
other = "99999999-9999-4999-8999-999999999999"
out: dict[str, object] = {}

psql("DROP DATABASE IF EXISTS " + DB, "postgres")
psql("CREATE DATABASE " + DB + " OWNER air_agent", "postgres")
subprocess.run(
    [str(REPO / "admin/api/.venv/bin/alembic"), "upgrade", "head"],
    cwd=REPO / "admin/api",
    env={
        "DATABASE_URI": f"postgresql://air_agent:{OWNER_PW}@localhost:15432/{DB}",
        "PATH": "/usr/bin:/bin",
    },
    capture_output=True,
)
psql(
    "INSERT INTO tenants(code,name,status) VALUES ('r05_drill','恢复演练','active');"
    "INSERT INTO users(account,display_name,password_hash) VALUES ('r05_user','演练用户','x');"
)
tenant = psql("SELECT id FROM tenants WHERE code='r05_drill'")
user = psql("SELECT id FROM users WHERE account='r05_user'")
psql(
    "INSERT INTO agent_requests(id,tenant_id,user_id,membership_id,thread_id,graph_id,operation_kind)"
    f" VALUES ('{REQ}','{tenant}','{user}','{user}','drill-thread','basic-qa','initial');"
    "INSERT INTO agent_requests(id,tenant_id,user_id,membership_id,thread_id,graph_id,operation_kind)"
    f" VALUES ('{REQ_KEEP}','{tenant}','{user}','{user}','keep-thread','basic-qa','initial');"
    "INSERT INTO agent_content_refs(id,tenant_id,user_id,membership_id,thread_id,request_id,content_snapshot)"
    f" VALUES ('13131313-1313-4313-8313-131313131313','{tenant}','{user}','{user}','drill-thread','{REQ}','SECRET');"
    "INSERT INTO agent_content_refs(id,tenant_id,user_id,membership_id,thread_id,request_id,content_snapshot)"
    f" VALUES ('14141414-1414-4414-8414-141414141414','{tenant}','{user}','{user}','keep-thread','{REQ_KEEP}','KEEP');"
)

out["before_backup"] = {
    "content_live": int(psql("SELECT count(*) FROM agent_content_refs WHERE deleted_at IS NULL")),
    "deletions": int(psql("SELECT count(*) FROM agent_thread_deletions")),
}

dump = sh(f"pg_dump -U air_agent -d {DB} -f /tmp/air_agent_ivrp01_restore_70f85d2a.sql && wc -l < /tmp/air_agent_ivrp01_restore_70f85d2a.sql")
out["dump_lines"] = dump.stdout.strip()

# 备份**之后**才删除：旧备份里该原文仍是"未删除"
psql(
    "INSERT INTO agent_thread_deletions(id,tenant_id,thread_id,user_id,state,requested_by)"
    f" VALUES (gen_random_uuid(),'{tenant}','drill-thread','{user}','requested','{user}');"
    "UPDATE agent_content_refs SET deleted_at = now() WHERE thread_id='drill-thread';"
)
out["deleted_after_backup"] = int(
    psql("SELECT count(*) FROM agent_content_refs WHERE thread_id='drill-thread' AND deleted_at IS NOT NULL")
)

# 灾难 + 恢复
psql("DROP DATABASE " + DB, "postgres")
psql("CREATE DATABASE " + DB + " OWNER air_agent", "postgres")
sh(f"psql -U air_agent -d {DB} -q -f /tmp/air_agent_ivrp01_restore_70f85d2a.sql")
out["restored"] = {
    "head": psql("SELECT version_num FROM alembic_version"),
    "open_deletions": int(psql("SELECT count(*) FROM agent_thread_deletions WHERE state <> 'completed'")),
    # 旧备份里没有删除记录 → 恢复出来是"未删除"（正是泄露风险）
    "content_not_yet_deleted": int(
        psql("SELECT count(*) FROM agent_content_refs WHERE thread_id='drill-thread' AND deleted_at IS NULL")
    ),
}

# 重施外部删除记录 + 当前权限，然后才开放读取
psql(
    "INSERT INTO agent_thread_deletions(id,tenant_id,thread_id,user_id,state,requested_by)"
    f" VALUES (gen_random_uuid(),'{tenant}','drill-thread','{user}','requested','{user}')"
    " ON CONFLICT (tenant_id, thread_id) DO NOTHING;"
    "UPDATE agent_content_refs SET deleted_at = now() WHERE thread_id='drill-thread' AND deleted_at IS NULL;"
)
perms = subprocess.run(
    [str(REPO / "admin/api/.venv/bin/python"), "admin/api/scripts/restore_ledger_permissions.py"],
    cwd=REPO,
    env={
        "DATABASE_URI": f"postgresql://air_agent:{OWNER_PW}@localhost:15432/{DB}",
        "PATH": "/usr/bin:/bin",
    },
    capture_output=True,
    text=True,
)
out["permission_regrant"] = perms.stdout.strip().splitlines()[-1] if perms.stdout else perms.stderr[:200]
out["after_reapply"] = {
    "deletion_recorded": int(
        psql("SELECT count(*) FROM agent_thread_deletions WHERE thread_id='drill-thread' AND state <> 'completed'")
    ),
    "deleted_thread_content_blocked": int(
        psql("SELECT count(*) FROM agent_content_refs WHERE thread_id='drill-thread' AND deleted_at IS NOT NULL")
    ),
    "other_content_preserved": int(
        psql("SELECT count(*) FROM agent_content_refs WHERE thread_id='keep-thread' AND deleted_at IS NULL")
    ),
}

import psycopg  # noqa: E402

uri = f"postgresql://air_agent_runtime:{RT_PW}@localhost:15432/{DB}"


def scoped(guc: dict[str, str], sql: str) -> int:
    with psycopg.connect(uri) as conn:
        with conn.transaction():
            for key, value in guc.items():
                conn.execute("SELECT set_config(%s,%s,true)", (key, value))
            return int(conn.execute(sql).fetchone()[0])


out["rls_after_restore"] = {
    "no_guc": scoped({}, "SELECT count(*) FROM agent_content_refs"),
    "own_scope": scoped(
        {"air_agent.tenant_id": tenant, "air_agent.user_id": user},
        "SELECT count(*) FROM agent_content_refs",
    ),
    "cross_tenant": scoped(
        {"air_agent.tenant_id": other, "air_agent.user_id": user},
        "SELECT count(*) FROM agent_content_refs",
    ),
}

print(json.dumps(out, ensure_ascii=False, indent=2))
(REPO / "docs/reviews/evidence/flash-02-repair-01-independent/restore-drill.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2) + "\n"
)
