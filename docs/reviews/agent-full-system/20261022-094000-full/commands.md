# 复跑命令（占位值不包含任何真实密钥）

下列命令用于在**新建的空隔离资源**上复跑；不要指向正式 15432/19132，不要复用本轮已删除的容器。

## 0. 基线与工作区

```bash
cd /Users/acerola/Dev/Python/air_agent
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
git status --porcelain
```

## 1. 隔离资源（名称/端口需本轮重新分配）

```bash
# 示例：本轮使用 25541–25551，容器名 aa-full-20261022-*
container run -d --name <own>-iam-pg --cpus 2 --memory 1024m \
  -p 127.0.0.1:<port>:5432 -e POSTGRES_PASSWORD -e POSTGRES_DB=iam_identity postgres:17-alpine
container run -d --name <own>-biz-pg --cpus 4 --memory 2048m \
  -p 127.0.0.1:<port>:5432 -e POSTGRES_PASSWORD -e POSTGRES_DB=postgres postgres:17-alpine
container run -d --name <own>-iam-redis --cpus 1 --memory 512m \
  -p 127.0.0.1:<port>:6379 redis:7-alpine redis-server --requirepass <password> --save ''
container run -d --name <own>-graph-redis --cpus 1 --memory 512m \
  -p 127.0.0.1:<port>:6379 redis:7-alpine redis-server --requirepass <password> --save ''
```

IAM 空库先建 `iam_migrate`/`iam_app` 角色（参考 IAM `scripts/test-db.sh create` 的 schema/默认权限），再启动 IAM jar；业务空库先跑：

```bash
cd admin/api
DATABASE_URI=postgresql://postgres:<owner>@127.0.0.1:<port>/<db> \
TENANT_RUNTIME_DB_PASSWORD=<runtime> \
TENANT_LEDGER_WORKER_DB_PASSWORD=<worker> \
TENANT_INSIGHTS_WORKER_DB_PASSWORD=<insights> \
../.venv/bin/python scripts/bootstrap_tenant_db.py
DATABASE_URI=postgresql://postgres:<owner>@127.0.0.1:<port>/<db> \
../.venv/bin/python -m alembic upgrade head
```

## 2. 受控模型与桥

```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
import time
from scripts.unified_campaign.controlled import ControlledDependencyServer
srv=ControlledDependencyServer(first_delay_s=0.4, chunk_interval_s=0.05, port=<port>)
srv.start(); print('controlled ready')
while True: time.sleep(3600)
PY

.venv/bin/python deployment/local_host_bridge.py --config <bridge.json>
```

`bridge.json` 只允许回环/私有地址，`allowed_source_ips` 必须填当前 LangGraph 容器实际 IP；模型 URL 在 P 层指受控服务，E 层指 127.0.0.1:7863。

## 3. 启动 BFF/Admin/官方 API

```bash
# Admin API
cd admin/api
DATABASE_URI=... ADMIN_RUNTIME_DATABASE_URI=... RUNTIME_DATABASE_URI=... \
IAM_BASE_URL=http://127.0.0.1:<iam-port> AGENT_IDENTITY_BACKEND=remote \
INTERNAL_API_TOKEN=... RUNTIME_TICKET_KEYS=... LEDGER_RECONCILE_KEYS=... \
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port <admin-port>

# Chat BFF
cd ../..
LANGGRAPH_API_URL=http://127.0.0.1:<lg-port> ADMIN_API_URL=http://127.0.0.1:<admin-port> \
AGENT_IDENTITY_BACKEND=remote INTERNAL_API_TOKEN=... ARCHIVE_SCOPE_KEYS=... \
npm --prefix frontend run start -- -p <bff-port>
```

LangGraph 容器使用 `air-agent/langgraph-api:tracing-merge-20261020`，挂载 `src` 到 `/deps/air_agent/src`，`LANGGRAPH_AUTH` 指 `src/common/identity/langgraph_auth.py:auth`，`LANGGRAPH_HTTP` 指 `artifact_http.py:app`。

## 4. IAM 主体（2 企业 × 2 用户）

使用 IAM 正常管理 API：`platform_boot` 登录 → 创建企业 → `PUT /v1/admin/tenants/{id}/entitlements/{resource}` 开通三项业务 → 企业上下文创建角色（含三项 business:use + operations/metrics/content/feedback）→ 创建用户 → `POST /v1/admin/members` 并授予角色。注意每次写操作使用最新 `expected_context`/`expected_tenant_revision`。无授权用户不授业务角色。

## 5. P/S 冒烟

```bash
export CAMPAIGN_COOKIE='air_agent_iam_handle=<value>'
.venv/bin/python -m scripts.unified_campaign smoke \
  --endpoints <endpoints-p.json> \
  --performance-out docs/reviews/basic-qa-performance/<new-run-id> \
  --evidence-out .local/<new-evidence-dir>
```

endpoints 需 `layer=P, profile=S, allow_real_models=false, allow_load=false`，`controlled_stats_url` 指受控服务 `/stats`。

## 6. E/S 真实模型

```bash
export CAMPAIGN_COOKIE='air_agent_iam_handle=<value>'
.venv/bin/python -m scripts.product_chat_perf.run_baseline \
  --endpoints <endpoints-e.json> \
  --performance-out docs/reviews/basic-qa-performance/<new-run-id> \
  --evidence-out .local/<new-evidence-dir> --enable-real-models
```

运行前必须设置 `PERF_PG_CONTAINER=<own-biz-pg>`、`PERF_PG_DATABASE=<own-db>`，否则 `calls.jsonl` 会按未采集处理。E 层 `allow_real_models=true`；Q4 历史 50 轮与 40 题分母分开。

## 7. 重新生成 E/S 汇总

```bash
PERF_PG_CONTAINER=<own-biz-pg> PERF_PG_DATABASE=<own-db> \
.venv/bin/python -m scripts.product_chat_perf.finalize \
  --run-dir docs/reviews/basic-qa-performance/<run-id> \
  --evidence-dir .local/<evidence-dir>
```

## 8. P/R 受控并发（本轮未执行；工具已提供）

```bash
# 端点必须 layer=P, profile=R, allow_load=true, allow_real_models=false
export CAMPAIGN_COOKIE='air_agent_iam_handle=<value>'
.venv/bin/python -m scripts.product_chat_perf.load \
  --endpoints <endpoints-pr.json> \
  --performance-out docs/reviews/basic-qa-performance/<new-pr-run-id> \
  --evidence-out .local/<new-pr-evidence> --enable-load \
  --concurrencies 1,5,10 --rounds 3 \
  --warmup-seconds 120 --measure-seconds 180
```

运行器按 60 秒窗口、至少 20 条已结束、非成功 >10% 停止升压；每轮预热 ≥2 分钟且 ≥8 题，计量 ≥3 分钟且 ≥40 题。未执行原因见总报告 §6。

## 9. 工具校准单测

```bash
.venv/bin/python -m pytest \
  tests/unit_tests/test_perf_tool_calibration.py \
  tests/unit_tests/test_unified_campaign.py -q
npm --prefix frontend run test:contracts
```

## 10. 本轮资源清理（执行后端口应全部释放）

```bash
for n in aa-full-20261022-admin-web aa-full-20261022-bff aa-full-20261022-admin-api aa-full-20261022-iam aa-full-20261022-insights-worker aa-full-20261022-controlled aa-full-20261022-bridge; do
  p=".local/full-20261022/${n#aa-full-20261022-}.pid"; [ -f "$p" ] && kill "$(cat "$p")" 2>/dev/null || true
done
for c in aa-full-20261022-langgraph aa-full-20261022-biz-pg aa-full-20261022-graph-redis aa-full-20261022-iam-pg aa-full-20261022-iam-redis; do
  container stop "$c" 2>/dev/null || true
  container rm "$c" 2>/dev/null || true
done
```

只允许删除本任务自建名称；不要全局 prune，不要删除共享卷/镜像/正式资源。
