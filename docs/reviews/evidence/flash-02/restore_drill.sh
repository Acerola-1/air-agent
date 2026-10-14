#!/usr/bin/env bash
# 7.3 专项：台账库备份恢复演练（专用库，不触碰共享业务卷）。
# 步骤：造删除标记+原文 → pg_dump → DROP/重建 → 恢复 → 重施权限 → 开放前校验。
set -u
cd "$(dirname "$0")/../../../.."
PG="container exec air-agent-postgres"
DB=air_agent_ledger_test
Q() { $PG psql -U air_agent -d $DB -t -A -c "$1"; }
read -r T U < /tmp/ledger_ids.txt

echo "== 1) 前置：删除标记 + 已删除/未删除原文各一 =="
$PG psql -U air_agent -d $DB -c "
INSERT INTO agent_thread_deletions (id, tenant_id, thread_id, state, attempts, requested_at)
VALUES (gen_random_uuid(), '$T', 'drill-thread-del', 'requested', 0, now())
ON CONFLICT (tenant_id, thread_id) DO NOTHING;
INSERT INTO agent_content_refs (id, tenant_id, user_id, membership_id, thread_id, message_id, content_version, content_snapshot, deleted_at)
VALUES (gen_random_uuid(), '$T', '$U', '$U', 'drill-thread-del', 'm-del', 1, 'DRILL_SHOULD_STAY_DELETED', now())
ON CONFLICT DO NOTHING;
INSERT INTO agent_content_refs (id, tenant_id, user_id, membership_id, thread_id, message_id, content_version, content_snapshot, deleted_at)
VALUES (gen_random_uuid(), '$T', '$U', '$U', 'drill-thread-live', 'm-live', 1, 'DRILL_LIVE_CONTENT', NULL)
ON CONFLICT DO NOTHING;
" >/dev/null
echo "deleted_refs=$(Q "SELECT count(*) FROM agent_content_refs WHERE deleted_at IS NOT NULL")"
echo "live_refs=$(Q "SELECT count(*) FROM agent_content_refs WHERE deleted_at IS NULL")"
echo "policies_before=$(Q "SELECT count(*) FROM pg_policies WHERE tablename LIKE 'agent_%'")"
echo "alembic_before=$(Q "SELECT version_num FROM alembic_version")"
echo "attempts_before=$(Q "SELECT count(*) FROM agent_run_attempts")"

echo "== 2) 备份（pg_dump，不含 ACL —— 最坏情形）=="
$PG sh -c "pg_dump -U air_agent -d $DB --no-owner --no-privileges -f /tmp/ledger_drill.sql && wc -l /tmp/ledger_drill.sql"

echo "== 3) 灾难：DROP + 重建 =="
$PG psql -U air_agent -d postgres -c "DROP DATABASE $DB;" >/dev/null
$PG psql -U air_agent -d postgres -c "CREATE DATABASE $DB OWNER air_agent;" >/dev/null

echo "== 4) 恢复备用数据 =="
$PG sh -c "psql -U air_agent -d $DB -q -f /tmp/ledger_drill.sql" 2>&1 | grep -iE "^ERROR" | head -3
echo "restored"

echo "== 5) 开放读取前的权限/删除状态校验（最坏情形：ACL 缺失，必须拒绝开放）=="
OWNER_PW=$(grep -E '^DATABASE_URI=' .env | head -1 | sed -E 's#.*://[^:]+:([^@]+)@.*#\1#')
DATABASE_URI="postgresql://air_agent:${OWNER_PW}@localhost:15432/$DB" \
  admin/api/.venv/bin/python admin/api/scripts/restore_ledger_permissions.py --verify-only
echo "verify_only_rc=$?"

echo "== 6) 重施权限（幂等，复用迁移 0007 同一份语句）=="
DATABASE_URI="postgresql://air_agent:${OWNER_PW}@localhost:15432/$DB" \
  admin/api/.venv/bin/python admin/api/scripts/restore_ledger_permissions.py
echo "apply_rc=$?"

echo "== 7) 开放读取（RLS 生效 + 删除状态保持）=="
.venv/bin/python - <<PY
import re, os, psycopg
pw=re.search(r'RUNTIME_DATABASE_URI=postgresql://[^:]+:([^@]+)@', open('.env.langgraph').read()).group(1)
uri=f"postgresql://air_agent_runtime:{pw}@localhost:15432/$DB"
T="$T"; U="$U"
def run(g, sql):
    with psycopg.connect(uri) as c:
        with c.transaction():
            for k,v in g.items(): c.execute("SELECT set_config(%s,%s,true)",(k,v))
            return c.execute(sql).fetchone()[0]
g={"air_agent.tenant_id":T,"air_agent.user_id":U}
print("RLS: no GUC ->", run({}, "SELECT count(*) FROM agent_run_attempts"))
print("RLS: own ->", run(g, "SELECT count(*) FROM agent_run_attempts"))
print("RLS: cross tenant ->", run({"air_agent.tenant_id":"99999999-9999-4999-8999-999999999999","air_agent.user_id":U}, "SELECT count(*) FROM agent_run_attempts"))
print("RLS: other member ->", run({"air_agent.tenant_id":T,"air_agent.user_id":"88888888-8888-4888-8888-888888888888"}, "SELECT count(*) FROM agent_run_attempts"))
print("deleted content still deleted ->", run(g, "SELECT count(*) FROM agent_content_refs WHERE content_snapshot='DRILL_SHOULD_STAY_DELETED' AND deleted_at IS NOT NULL"))
print("live content present ->", run(g, "SELECT count(*) FROM agent_content_refs WHERE content_snapshot='DRILL_LIVE_CONTENT' AND deleted_at IS NULL"))
print("open deletions ->", run(g, "SELECT count(*) FROM agent_thread_deletions WHERE state <> 'completed'"))
PY
