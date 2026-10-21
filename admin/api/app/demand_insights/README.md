# 需求洞察（Change 7）运维与接口说明

本域只在后台管理进程与独立 worker 中运行，不在 Agent 图热路径执行；不访问共享
RAG/Qdrant，不导入根 `common.config`，不改图、MCP 或根依赖锁。

## 目录

| 文件 | 职责 |
|---|---|
| `app/demand_insights/config.py` | 独立 `INSIGHTS_*` 配置与阈值/profile 版本 |
| `normalization.py` | 确定性清洗、脱敏、动作/否定/slots、规则拆分 |
| `matching.py` | 有界 cosine 候选、动作/对象/时间/否定约束、容量边界 |
| `model_gateway.py` | OpenAI 兼容 Embedding/辅助模型；维度/范数/usage/超时 |
| `source_reader.py` | 已确认受理 request + 精确 content_version 来源、缺口重试 |
| `classifier.py` | 单来源标准化 → Embedding → 候选 → 复核 → 原子写入 |
| `storage.py` | profile/簇/意图/归属/操作持久化 |
| `jobs.py` | 作业、租约/fence、进度 CAS、幂等动作 |
| `delegations.py` | 有限人类委托与 IAM 实时内省 |
| `statistics.py` | 标准问题/主题榜、覆盖、增长、请求级负反馈 |
| `governance.py` | 改名/指派/合并/拆分/重算/激活 |
| `privacy.py` | 删除/到期清理、恢复后收敛 |
| `worker.py` | 显式启动的异步 worker（默认 off） |
| `app/routers/demand_insights.py` | `/agent/insights/**` 路由 |
| `app/schemas_demand.py` | 响应/写操作 DTO |

## 启动前置

1. 先按下述命令创建运行/worker角色，再执行当前迁移 head（本域起始为 `0028_demand_insights`，保留期修复head为 `0029_insight_retention`）：
   `PYTHONPATH=../../src .venv/bin/python -m alembic upgrade head`（在 `admin/api` 目录）。
2. 创建运行角色与独立 worker 角色：

```bash
export TENANT_RUNTIME_DB_PASSWORD='<runtime-password>'
export TENANT_INSIGHTS_WORKER_DB_PASSWORD='<insights-worker-password>'
PYTHONPATH=../../src .venv/bin/python scripts/bootstrap_tenant_db.py
```

   运行角色 `air_agent_runtime` 只有本域最小读写；worker 角色
   `air_agent_insights_worker` 可跨企业领取作业/读取委托，但必须按任务设置
   `air_agent.tenant_id` 与 `air_agent.insight_worker`。缺 worker 角色时迁移仍可建表，
   只是不会创建 worker 策略，worker 不能运行；若角色是在迁移之后补建的，执行
   `restore_ledger_permissions.py` 重施本域策略后再开 worker。

3. 在真实 IAM 正常管理面登记 `agent:insights:manage`（精确按钮权限，**不**加入
   `agent:business:*:use`），由企业角色授予操作人；本域另要求
   `agent:operations:read` + `agent:content:read`。未登记时不得用本地目录冒充
   remote 授权。

## 配置（仅部署配置，不接受浏览器传入）

| 变量 | 说明 |
|---|---|
| `INSIGHTS_ENABLED` | 页面/域启用标记（默认 false） |
| `INSIGHTS_WORKER_ENABLED` | worker 显式开关（默认 false） |
| `INSIGHTS_WORKER_DATABASE_URI` | worker 专用非 owner 连接 |
| `INSIGHTS_WORKER_POLL_SECONDS` | 无作业轮询间隔（默认 60s） |
| `INSIGHTS_EMBEDDING_BASE_URL/API_KEY/MODEL/PROFILE/DIMENSION` | 受保护 Embedding 端点；维度必须与端点一致且 ≤4096 |
| `INSIGHTS_LLM_BASE_URL/API_KEY/MODEL/PROFILE` | 可选辅助模型（拆分/临界复核）；未配置时只走确定性路径 |
| `INSIGHTS_SIMILARITY_THRESHOLD/MARGIN_THRESHOLD/REVIEW_LOW` | 新建 profile 的默认阈值；生产须用校准报告覆盖 |
| `INSIGHTS_MAX_CLUSTERS` | 首版有界候选上限（默认 2000） |
| `INSIGHTS_MAX_SOURCES_PER_JOB` / `INSIGHTS_MAX_TOKENS_PER_JOB` | 单作业预算上限 |
| `INSIGHTS_ALLOW_INSECURE_HTTP` | 仅本地回环可放宽；远程端点必须 https |
| `INTERNAL_API_TOKEN` | 游标/HMAC 预览签名（服务端内部凭据） |

模型故障时作业暂停并显示安全错误码；不静默换未批准供应商。usage 缺失记
`usage_missing=true`、不记 0。

## 校准（上线前必做）

```bash
cd admin/api
export INSIGHTS_EMBEDDING_BASE_URL=... INSIGHTS_EMBEDDING_API_KEY=... \
       INSIGHTS_EMBEDDING_MODEL=... INSIGHTS_EMBEDDING_DIMENSION=4096 \
       INSIGHTS_EMBEDDING_PROFILE=siliconflow-qwen3-8b-v1
PYTHONPATH=../../src .venv/bin/python scripts/evaluate_insights_calibration.py \
  --provider real --output ../../docs/reviews/evidence/run/insights-<time>/calibration-real.json
```

- 60 条校准用于留一法阈值选择；100 条冻结留出只做最终门槛；
- 门槛：precision ≥98%、清晰意图覆盖 ≥60%、自动归类 ≥50、关键误合并 0；
- `fake` 只验证算法管线，报告中标记 fixture，不能代替真实结果；
- 通过后把阈值写入目标 profile（默认只允许 draft/ready；首次 active 引导需显式
  `--allow-active`）：

```bash
PYTHONPATH=../../src .venv/bin/python scripts/apply_insight_calibration.py \
  --tenant-id <tenant-uuid> --report .../calibration-real.json [--allow-active]
```

## 作业与委托

- `POST /agent/insights/jobs` 需要 IAM remote 会话；本地身份模式返回
  `remote_identity_required`。委托只保存应用会话 handle 摘要，最长 12h，且不超过
  会话有效期；job 不保存 Bearer/cookie。
- worker 每批、每次 Embedding/LLM物理重试发送前、提交前重新内省；撤权/切企业/过期立即
  `paused_authorization`，IAM 故障同样暂停但保留委托。
- 启动：`INSIGHTS_WORKER_ENABLED=true INSIGHTS_WORKER_DATABASE_URI=... \
  python -m app.demand_insights.worker`；停止：SIGTERM/SIGINT（清理进行中租约后退出）。
- 第 4 项（真实 IAM 授权/委派）未通过时，不要以本地 fixture 或 mock 令牌声称
  worker 已上线。

## 删除与恢复

- 删除/到期/墓碑由 `agent_content_refs.deleted_at`、`agent_thread_deletions` 与
  source `retention_until` 触发；本域RLS在到期/墓碑后立即禁读派生来源及正文；worker按企业有界轮转清理，即使人类委托已过期也不等待新分析作业；脚本可用于停机维护。清理覆盖文本、
  向量、归属、原型与缺口。
- 备份恢复顺序：`restore_ledger_permissions.py` → `insights_restore_guard.py` →
  再开 worker/页面。恢复后旧委托/旧向量/旧摘要不得复活。
- 回退：先关 worker 与页面入口，保留旧 active profile；不盲目 downgrade
  （0028 不提供 downgrade，删除/到期清理必须继续运行）。
