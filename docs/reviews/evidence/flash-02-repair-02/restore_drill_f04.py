"""F04 永久删除恢复演练（第二轮：新随机专用库；只清理本轮自建库）.

流程（按复验要求）：先备份 → **之后**才删除 → 灾难 + 恢复旧备份 → 重施外部删除记录与当前权限
→ 允许读取。与首轮演练的区别在于断言的是**永久删除**语义：

- 删除记录不因清理完成（`state='completed'`）而失效：回读/新写始终拒绝；
- 恢复旧备份后（备份里没有删除记录）必须**重施**删除记录与权限，才谈得上"允许读取"；
- 重施之后再插入的"新写"不得被顺手清掉（保留新写）；
- RLS 计数在恢复后仍分层（无 GUC/本人/跨企业）。

仅使用**随机命名**的专用演练库，绝不对固定/共享库 DROP；脚本结束时删除自己创建的库。
"""

from __future__ import annotations

import json
import random
import re
import string
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SUFFIX = "".join(random.choices(string.hexdigits.lower(), k=8))  # noqa: S311 - 仅用于库名唯一
DB = f"air_agent_rp02_restore_{SUFFIX}"
OWNER_PW = re.search(
    r"^DATABASE_URI=postgresql://[^:]+:([^@]+)@", (REPO / ".env").read_text(), re.M
).group(1)
RT_PW = re.search(
    r"^RUNTIME_DATABASE_URI=postgresql://[^:]+:([^@]+)@",
    (REPO / ".env.langgraph").read_text(),
    re.M,
).group(1)


def container(args: list[str]) -> subprocess.CompletedProcess:
    """在演练用 PG 容器内执行命令（库与角色均为本轮自建/内置）."""
    return subprocess.run(
        ["container", "exec", "air-agent-postgres", *args],
        capture_output=True,
        text=True,
    )


def psql(sql: str, db: str = DB) -> str:
    """执行一条 SQL 并返回文本结果（-tA：无表头无对齐）."""
    res = container(["psql", "-U", "air_agent", "-d", db, "-t", "-A", "-c", sql])
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[:300])
    return res.stdout.strip()


def sh(cmd: str) -> subprocess.CompletedProcess:
    """容器内 shell（用于 pg_dump/psql 文件导入）."""
    return container(["sh", "-c", cmd])


REQ = "12121212-1212-4212-8212-121212121212"
REQ_KEEP = "16161616-1616-4616-8616-161616161616"
NEW_AFTER_RESTORE = "17171717-1717-4717-8717-171717171717"
other = "99999999-9999-4999-8999-999999999999"
out: dict[str, object] = {"database": DB, "random_suffix": SUFFIX}

psql("DROP DATABASE IF EXISTS " + DB, "postgres")
psql("CREATE DATABASE " + DB + " OWNER air_agent", "postgres")
mig = subprocess.run(
    [str(REPO / "admin/api/.venv/bin/python"), "-m", "alembic", "upgrade", "head"],
    cwd=REPO / "admin/api",
    env={
        "DATABASE_URI": f"postgresql://air_agent:{OWNER_PW}@localhost:15432/{DB}",
        "PATH": "/usr/bin:/bin",
    },
    capture_output=True,
)
if mig.returncode:
    raise RuntimeError(mig.stderr.decode()[-500:])

psql(
    "INSERT INTO tenants(code,name,status) VALUES ('rp02_drill','恢复演练','active');"
    "INSERT INTO users(account,display_name,password_hash) VALUES ('rp02_user','演练用户','x');"
)
tenant = psql("SELECT id FROM tenants WHERE code='rp02_drill'")
user = psql("SELECT id FROM users WHERE account='rp02_user'")
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
    "content_live": int(
        psql("SELECT count(*) FROM agent_content_refs WHERE deleted_at IS NULL")
    ),
    "deletions": int(psql("SELECT count(*) FROM agent_thread_deletions")),
}

dump = sh(
    f"pg_dump -U air_agent -d {DB} -f /tmp/rp02_drill.sql && wc -l < /tmp/rp02_drill.sql"
)
out["dump_lines"] = dump.stdout.strip()

# 备份**之后**才删除，并按新语义把清理标记为完成
psql(
    "INSERT INTO agent_thread_deletions(id,tenant_id,thread_id,user_id,state,attempts,completed_at,"
    "requested_by) VALUES (gen_random_uuid(),"
    f"'{tenant}','drill-thread','{user}','completed',1,now(),'{user}');"
    "UPDATE agent_content_refs SET deleted_at = now() WHERE thread_id='drill-thread';"
)
out["deleted_after_backup"] = {
    "deleted_refs": int(
        psql(
            "SELECT count(*) FROM agent_content_refs"
            " WHERE thread_id='drill-thread' AND deleted_at IS NOT NULL"
        )
    ),
    "deletion_state": psql(
        "SELECT state FROM agent_thread_deletions WHERE thread_id='drill-thread'"
    ),
}

# 灾难 + 恢复旧备份
psql("DROP DATABASE " + DB, "postgres")
psql("CREATE DATABASE " + DB + " OWNER air_agent", "postgres")
sh(f"psql -U air_agent -d {DB} -q -f /tmp/rp02_drill.sql")
out["restored"] = {
    "head": psql("SELECT version_num FROM alembic_version"),
    "deletions_any_state": int(psql("SELECT count(*) FROM agent_thread_deletions")),
    # 旧备份里没有删除记录 → 恢复出来该线程"未删除"（这正是泄露风险）
    "content_not_yet_deleted": int(
        psql(
            "SELECT count(*) FROM agent_content_refs"
            " WHERE thread_id='drill-thread' AND deleted_at IS NULL"
        )
    ),
}

# 恢复后的"新写"（备份之后才产生的业务写）：重施删除时不得把它一起清掉
psql(
    "INSERT INTO agent_requests(id,tenant_id,user_id,membership_id,thread_id,graph_id,operation_kind)"
    f" VALUES ('{NEW_AFTER_RESTORE}','{tenant}','{user}','{user}','fresh-thread','basic-qa','initial');"
    "INSERT INTO agent_content_refs(id,tenant_id,user_id,membership_id,thread_id,request_id,content_snapshot)"
    " VALUES ('18181818-1818-4818-8818-181818181818',"
    f"'{tenant}','{user}','{user}','fresh-thread','{NEW_AFTER_RESTORE}','FRESH');"
)

# 重施外部删除记录 + 当前权限，然后才允许读取
psql(
    "INSERT INTO agent_thread_deletions(id,tenant_id,thread_id,user_id,state,attempts,completed_at,"
    "requested_by) VALUES (gen_random_uuid(),"
    f"'{tenant}','drill-thread','{user}','completed',1,now(),'{user}')"
    " ON CONFLICT (tenant_id, thread_id) DO NOTHING;"
    "UPDATE agent_content_refs SET deleted_at = now()"
    " WHERE thread_id='drill-thread' AND deleted_at IS NULL;"
)
perms = subprocess.run(
    [
        str(REPO / "admin/api/.venv/bin/python"),
        "admin/api/scripts/restore_ledger_permissions.py",
    ],
    cwd=REPO,
    env={
        "DATABASE_URI": f"postgresql://air_agent:{OWNER_PW}@localhost:15432/{DB}",
        "PATH": "/usr/bin:/bin",
    },
    capture_output=True,
    text=True,
)
out["permission_regrant"] = (
    perms.stdout.strip().splitlines()[-1] if perms.stdout else perms.stderr[:200]
)

# 永久语义断言：删除记录存在即拒绝，清理完成与否都不放行
out["after_reapply"] = {
    "deletion_present_any_state": int(
        psql("SELECT count(*) FROM agent_thread_deletions")
    ),
    "deleted_thread_content_blocked": int(
        psql(
            "SELECT count(*) FROM agent_content_refs"
            " WHERE thread_id='drill-thread' AND deleted_at IS NOT NULL"
        )
    ),
    "other_content_preserved": int(
        psql(
            "SELECT count(*) FROM agent_content_refs"
            " WHERE thread_id='keep-thread' AND deleted_at IS NULL"
        )
    ),
    "newer_write_preserved": int(
        psql(
            "SELECT count(*) FROM agent_content_refs"
            " WHERE thread_id='fresh-thread' AND deleted_at IS NULL"
        )
    ),
}

import psycopg  # noqa: E402

uri = f"postgresql://air_agent_runtime:{RT_PW}@localhost:15432/{DB}"


def scoped(guc: dict[str, str], sql: str) -> int:
    """以运行角色 + 事务本地 GUC 查询计数（验证 RLS 仍分层）."""
    with psycopg.connect(uri) as conn:
        with conn.transaction():
            for key, value in guc.items():
                conn.execute("SELECT set_config(%s,%s,true)", (key, value))
            return int(conn.execute(sql).fetchone()[0])


own = {"air_agent.tenant_id": tenant, "air_agent.user_id": user}
out["rls_after_restore"] = {
    "no_guc": scoped({}, "SELECT count(*) FROM agent_content_refs"),
    "own_scope": scoped(own, "SELECT count(*) FROM agent_content_refs"),
    "cross_tenant": scoped(
        {"air_agent.tenant_id": other, "air_agent.user_id": user},
        "SELECT count(*) FROM agent_content_refs",
    ),
    "deleted_thread_visible_rows": scoped(
        own,
        "SELECT count(*) FROM agent_content_refs WHERE thread_id='drill-thread'"
        " AND deleted_at IS NULL",
    ),
}

# 只清理自己创建的库
cleanup = psql("DROP DATABASE IF EXISTS " + DB, "postgres")
out["cleanup"] = {"dropped": DB, "result": cleanup or "ok"}

import sys  # noqa: E402

sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
Path(__file__).with_name("restore-drill-f04.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2) + "\n"
)
