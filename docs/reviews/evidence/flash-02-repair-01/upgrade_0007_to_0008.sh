#!/usr/bin/env bash
# R04 迁移验证：空库 0001→0008；0007 旧库升级 + 冲突预检（违规时不静默通过）。
set -u
cd "$(dirname "$0")/../../../.."
DB=air_agent_ledger_upgrade_test
OWNER_PW=$(grep -E '^DATABASE_URI=' .env | head -1 | sed -E 's#.*://[^:]+:([^@]+)@.*#\1#')
export DATABASE_URI="postgresql://air_agent:${OWNER_PW}@localhost:15432/$DB"
PG="container exec air-agent-postgres"
Q() { $PG psql -U air_agent -d "$DB" -t -A -c "$1"; }

$PG psql -U air_agent -d postgres -c "DROP DATABASE IF EXISTS $DB;" >/dev/null
$PG psql -U air_agent -d postgres -c "CREATE DATABASE $DB OWNER air_agent;" >/dev/null

echo "== 1) 先迁到 0007（旧库状态）=="
(cd admin/api && .venv/bin/alembic upgrade 0007 >/dev/null 2>&1)
echo "head=$(Q "SELECT version_num FROM alembic_version")"

echo "== 2) 造历史数据：合法事件 + 一条跨租户关联（模拟历史违规）=="
$PG psql -U air_agent -d "$DB" -q -c "
INSERT INTO tenants(code,name,status) VALUES ('upg_t','升级测试','active') RETURNING id" >/dev/null 2>&1
T=$($PG psql -U air_agent -d "$DB" -t -A -c "SELECT id FROM tenants WHERE code='upg_t'" | tr -d '[:space:]')
$PG psql -U air_agent -d "$DB" -q -c "INSERT INTO tenants(code,name,status) VALUES ('upg_other','他租户','active')" >/dev/null 2>&1
T2=$($PG psql -U air_agent -d "$DB" -t -A -c "SELECT id FROM tenants WHERE code='upg_other'" | tr -d '[:space:]')
$PG psql -U air_agent -d "$DB" -q -c "
INSERT INTO agent_requests(id,tenant_id,user_id,membership_id,thread_id,graph_id,operation_kind) VALUES
 ('11111111-1111-4111-8111-111111111111','$T','22222222-2222-4222-8222-222222222222','22222222-2222-4222-8222-222222222222','t-a','basic-qa','initial'),
 ('33333333-3333-4333-8333-333333333333','$T2','22222222-2222-4222-8222-222222222222','22222222-2222-4222-8222-222222222222','t-b','basic-qa','initial');
INSERT INTO agent_run_attempts(id,tenant_id,user_id,membership_id,request_id,graph_id,attempt_index,state) VALUES
 ('44444444-4444-4444-8444-444444444444','$T','22222222-2222-4222-8222-222222222222','22222222-2222-4222-8222-222222222222','11111111-1111-4111-8111-111111111111','basic-qa',1,'running'),
 ('55555555-5555-4555-8555-555555555555','$T2','22222222-2222-4222-8222-222222222222','22222222-2222-4222-8222-222222222222','33333333-3333-4333-8333-333333333333','basic-qa',1,'running');
-- 合法事件（A 租户 + A 尝试）
INSERT INTO agent_events(event_id,tenant_id,user_id,membership_id,event_type,source,occurred_at,request_id,attempt_id,payload,payload_digest)
 VALUES ('66666666-6666-4666-8666-666666666666','$T','22222222-2222-4222-8222-222222222222','22222222-2222-4222-8222-222222222222','run.state_observed','runtime',now(),'11111111-1111-4111-8111-111111111111','44444444-4444-4444-8444-444444444444','{}'::jsonb,'d1');
-- 历史违规：A 租户事件引用 B 租户尝试
INSERT INTO agent_events(event_id,tenant_id,user_id,membership_id,event_type,source,occurred_at,request_id,attempt_id,payload,payload_digest)
 VALUES ('77777777-7777-4777-8777-777777777777','$T','22222222-2222-4222-8222-222222222222','22222222-2222-4222-8222-222222222222','run.state_observed','runtime',now(),'33333333-3333-4333-8333-333333333333','55555555-5555-4555-8555-555555555555','{}'::jsonb,'d2');
" >/dev/null
echo "events_before=$(Q "SELECT count(*) FROM agent_events")"

echo "== 3) 升级 0008：预检必须拒绝（不能静默通过）=="
(cd admin/api && .venv/bin/alembic upgrade head 2>&1 | grep -oE "RuntimeError.*|事件与尝试关联跨租户[^']*" | head -2)
echo "head_after_failed_upgrade=$(Q "SELECT version_num FROM alembic_version")"

echo "== 4) 人工修正违规行后再升级 =="
$PG psql -U air_agent -d "$DB" -q -c "DELETE FROM agent_events WHERE event_id='77777777-7777-4777-8777-777777777777'"
(cd admin/api && .venv/bin/alembic upgrade head >/dev/null 2>&1)
echo "head=$(Q "SELECT version_num FROM alembic_version")"
echo "fk_constraints=$(Q "SELECT count(*) FROM pg_constraint WHERE conname IN ('fk_agent_events_request_same_tenant','fk_agent_events_attempt_same_tenant','fk_agent_events_answer_same_tenant','fk_agent_events_attempt_request')")"
echo "submission_attempt_fk=$(Q "SELECT count(*) FROM pg_constraint WHERE conname='fk_agent_submissions_attempt_same_tenant'")"

echo "== 5) 升级后：跨租户关联必须被数据库拒绝 =="
$PG psql -U air_agent -d "$DB" -t -A -c "
INSERT INTO agent_events(event_id,tenant_id,user_id,membership_id,event_type,source,occurred_at,request_id,attempt_id,payload,payload_digest)
 VALUES ('88888888-8888-4888-8888-888888888888','$T','22222222-2222-4222-8222-222222222222','22222222-2222-4222-8222-222222222222','run.state_observed','runtime',now(),'33333333-3333-4333-8333-333333333333','55555555-5555-4555-8555-555555555555','{}'::jsonb,'d3');
" 2>&1 | grep -oE "ERROR:.*|INSERT 0 1" | head -2
