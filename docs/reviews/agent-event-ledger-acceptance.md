# Agent 事件台账验收报告（change `add-agent-event-ledger`）

- 实施者：**匿名02**
- 日期：2026-10-13
- 状态：**待独立验收**（保留工作树 diff；未 commit / push / merge / archive）
- 起点基线：HEAD `7b82c04`（规划提交，非已验收实现提交）+ 工作树指纹
  `docs/reviews/evidence/flash-01-repair-02/change2-source-baseline.json`
  （393 个非秘密文件 sha256 **全部逐字节匹配**，无偏差、未覆盖他人改动）
- 身份迁移链：change 1 已执行 head `0006` → 本项新增 `0007`（**不改写已执行 revision**）

## 1. 结论摘要

已完成核心链路并**在真实栈上验证**：可信受理 → 提交幂等 → 平台真实运行受理 →
关联回读 → 六图生命周期落账 → 停止意图 → 事件/outbox → 分级读取与首期指标 →
删除标记 → 契约包与运维 CLI。

未完成（均为**已明确记录**的缺口，不是"计划"）：浏览器端 UI 实测（8.2）、
分位数指标（6.4 部分）、历史幂等导入与备份恢复演练（7.2/7.3）、
真实企业 MCP 与完整长运行矩阵（继承 change 1 的生产门禁）。

## 2. 实现与配置影响

### 2.1 新增/修改文件

| 区域 | 文件 | 说明 |
|---|---|---|
| 迁移 | `admin/api/migrations/versions/0007_agent_event_ledger.py` | 12 张台账表 + 复合外键 + 13 条 RLS 策略 + 受限调度角色策略 |
| 模型 | `admin/api/app/models_ledger.py` | 独立台账域模型（7 个新类）；`app/models.py` 末尾一行导入注册 metadata |
| 服务 | `admin/api/app/services/ledger.py` | 受理/幂等/确认/状态/停止/恢复/回答/Trace/删除/查询/指标 |
| 服务 | `admin/api/app/services/event_contract.py` | 事件契约校验（与 Agent 侧同规则） |
| 契约 | `admin/api/app/schemas_ledger.py`、`app/routers/ledger.py` | 15 个台账端点 |
| 权限 | `admin/api/scripts/seed.py`、`app/services/tenant_seed.py` | 新增 5 个权限码；`tenant_admin` 只含运营/统计，**不含**原文与运维 |
| 引导 | `admin/api/scripts/bootstrap_tenant_db.py` | 可选创建受限调度角色 `air_agent_ledger_worker`（缺省不建） |
| Agent 侧 | `src/common/event_ledger/{contract,store,lifecycle,reconcile,worker,__main__}.py` + `contract_data/` | 直连 PG 的最小异步适配、六图生命周期、补偿校准、outbox worker、CLI、契约副本 |
| 六图接入 | `src/common/business_graph/builder.py`、`src/data_analysis/graph.py`、`src/intelligent_{report,tracing}/graph.py`、`src/deep_research/graph.py` | 五图+第六图用图内 `ledger_finalize` 节点；三个 deepagents 图用 `LedgerLifecycleMiddleware` |
| 前端 | `frontend/lib/agent-ledger.ts`、`lib/admin-api.ts`、`lib/langgraph-api.ts`、`lib/chat-store.ts`、`app/api/[..._path]/route.ts`、`app/api/agent-runs/{confirm,stop-intent}/route.ts` | 受理前置、关联注入、run 确认、停止意图、稳定提交键 |
| 契约包 | `docs/contracts/agent-events-v1/` | schema、来源/类型登记、状态/指标字典、样本、责任表 |
| 配置 | `.env.example` | 台账 worker 受限连接、调度角色、只读报告连接、契约目录 |

### 2.2 配置（全部非秘密、默认不启用未知凭据）

```
LEDGER_WORKER_DATABASE_URI   # 受限调度角色连接；未配置 = worker 未启用（不降级跨租户领取）
TENANT_LEDGER_WORKER_DB_PASSWORD / TENANT_LEDGER_WORKER_ROLE
LEDGER_REPORT_OWNER_URI      # 历史只读报告（缺省回退 DATABASE_URI）
AGENT_EVENTS_CONTRACT_DIR    # 契约目录（仅路径）
```

权限码：`agent:operations:read`、`agent:metrics:read`、`agent:content:read`、
`agent:replay:manage`（平台专属）、`business:runs`（菜单）。

### 2.3 迁移与角色影响

- `alembic upgrade head`：`0006 → 0007`，业务库与专用测试库均已执行；head 唯一。
- 新表全部启用 RLS；运行角色 `air_agent_runtime`（非 BYPASSRLS）按
  `tenant_id = GUC AND (user_id = GUC OR agent_operations='on')` 单一 AND 策略限定。
- 新增受限调度角色 `air_agent_ledger_worker`：只授 outbox 租约列 +
  事件非秘密列（`event_id/tenant_id/event_type/source/occurred_at/thread_id`）+
  删除标记最小列；**看不到 payload/正文/关联 ID**。
- 不触碰 LangGraph 平台内部表（checkpoints 等）。
- 未重建、未删除任何既有卷；专用库 `air_agent_ledger_test` 单独创建用于演练。

## 3. 可复跑命令

```bash
# 契约测试（双语言，同一组样本）
PYTHONPATH=src .venv/bin/python -m pytest tests/unit_tests/test_event_ledger_contract.py -q
cd frontend && node --test tests/agent-events-contract.test.cjs

# 台账迁移 + 角色（专用库）
cd admin/api && DATABASE_URI=...air_agent_ledger_test .venv/bin/alembic upgrade head
TENANT_RUNTIME_DB_PASSWORD=... TENANT_LEDGER_WORKER_DB_PASSWORD=... \
  .venv/bin/python scripts/bootstrap_tenant_db.py

# Agent 侧存储/worker/校准（需专用 PG）
LEDGER_TEST_TENANT_ID=... LEDGER_TEST_USER_ID=... LEDGER_TEST_OWNER_URI=... \
RUNTIME_DATABASE_URI=... LEDGER_WORKER_DATABASE_URI=... PYTHONPATH=src \
  .venv/bin/python -m pytest tests/unit_tests/test_event_ledger_store.py -q

# 管理端套件（含台账 API 与跨实现契约一致性）
cd admin/api && .venv/bin/python -m pytest tests/ -q

# 运维 CLI
PYTHONPATH=src python -m common.event_ledger {worker|replay|reconcile|history-report}

# 真实联调（admin:8100 + native:2024 已运行当前代码）
.venv/bin/python docs/reviews/evidence/flash-02/probe_run_admission.py
.venv/bin/python docs/reviews/evidence/flash-02/probe_run_metadata_resume.py
.venv/bin/python docs/reviews/evidence/flash-02/e2e_ledger_chain.py
.venv/bin/python docs/reviews/evidence/flash-02/e2e_proxy_chain.py
```

## 4. spec 场景 → 证据映射

证据类型：`U`=单元/集成测试（专用 PG 或 mock），`L`=真实栈联调，`C`=契约样本。

### 4.1 `agent-request-identity`

| 场景 | 证据 |
|---|---|
| 合法提问（唯一 request_id + 归属快照） | U `test_admit_creates_request_attempt_and_event`；L `e2e_ledger_chain.admit` |
| 伪造身份/旧企业上下文 | U `test_admit_rejects_cross_tenant_thread`（403）；`test_admit_rejects_unconfirmed_thread`（pending 归属拒绝） |
| 并发重放（同键同内容） | L `replay_reused=true` + `replay_same_attempt=true`；U `test_submission_idempotent_and_conflict` |
| 同键不同内容 → 冲突 | U `test_submission_idempotent_and_conflict`（409，2 提问/2 尝试） |
| 上游已受理但响应丢失 | U `test_admission_unknown_not_success_and_not_retried`；L `unknown_state=admission_unknown`、`unknown_not_retried=true` |
| 重新生成/失败重试（独立 attempt、原 request） | U（`operation_kind` + `source_attempt_id` + `attempt_index` 唯一约束）；前端已发 `x-operation-kind` |
| 工具内部重试不增加尝试 | U `test_event_ledger_contract`（`runtime.tool_call` 预留，不计提问）；U worker 用例 |
| 人工恢复（不新建提问） | U `test_resume_keeps_attempt_and_adds_mapping`（resume_index 1→2，同 attempt）；L 平台实测 resume 新建 run（见 5.1） |
| 确认最终正文 / 停止后部分正文 / 多可见版本 | U `test_visible_final_message_excludes_internal_and_drafts`、`test_visible_final_message_none_when_only_internal`；L 六图 `answer.confirmed` |
| 辅助 Trace 晚到 / Trace 未启用 | U `link_trace` 幂等 + 主 Trace 不被替换（`test_event_ledger_store` 系列）；L 六图 `trace_coverage` 字段 |

### 4.2 `agent-run-lifecycle`

| 场景 | 证据 |
|---|---|
| 六图合法调用（流式/非流式均可受理） | **L**：六图逐个 `runs/wait` 真实运行，全部 `state=succeeded`、`evidence_source=runtime_callback`、`answers=1/confirmed` |
| 绕过聊天代理（缺台账受理上下文） | U `test_missing_ledger_context_raises_for_unregistered_entry`；L `admit` 前置（未受理不启动） |
| 浏览器流正常结束 ≠ 完成 | L `readback_state=running`（仅确认受理未校准）；U 状态秩用例 |
| 晚到运行开始事件不回退 | U `test_observe_state_terminal_not_regressed`（`applied=false`）；U `test_state_evidence_rank_and_no_terminal_regression` |
| 人工处理等待（不计已结束分母） | U `test_states_dictionary_flags_terminal_and_ended`；`business_state_from_native("interrupted")=="waiting_input"` |
| 状态证据冲突 | U `test_observe_state_same_rank_conflict_kept`（保留原事实并记录修订） |
| 停止先于运行标识 | U `test_stop_intent_independent_of_terminal`（只给 thread_id 也关联成功）；L `proxy_stop_recorded=true` |
| 停止与完成竞争 | U 终态秩用例（终态保持，意图另存） |
| 单纯网络断连 | 契约登记 `transport_disconnect` 与 `stop_requested/cancel_confirmed` 分离；前端 `stopPending` 语义保留 |
| 运行进程崩溃 / 平台不可用 / 成员已撤销 | U `test_reconciler_keeps_unknown_when_platform_unavailable`（`kept_unknown`，状态不变）、`test_reconciler_calibrates_real_terminal_from_platform`、`test_reconciler_timeout_requires_platform_evidence` |
| 治理状态与正文并存 | 根单元套件全量回归无新增失败（消息治理/正文确认用例保持通过） |

### 4.3 `agent-event-delivery`

| 场景 | 证据 |
|---|---|
| 合法服务端事实 | U/L `request.accepted`、`run.admission_confirmed`、`run.state_observed`、`answer.confirmed` 实际落库 |
| 浏览器伪造完成/成本 | U `test_browser_events_all_reserved_types_rejected`（`source_not_allowed`，`written=0`）；C `browser-terminal-claim-invalid` |
| 未来版本/超限载荷 | C `future-major-version-invalid`、`payload_too_large`（契约测试）、`test_validate_batch_rejects_browser_oversize_batch` |
| 业务事务回滚 | U 事实与 outbox 同事务（同 `session`/同 `conn.transaction()`）；回滚用例覆盖于 admin 服务测试 |
| 消费提交后确认丢失 | U `test_worker_claims_and_completes_with_receipt`（receipt 唯一键幂等短路） |
| 相同事件重放（三次同内容） | U `test_write_event_dedup_and_conflict`（`duplicate`） |
| 同 ID 不同载荷 → 冲突 | U `test_write_event_dedup_and_conflict`（`payload_conflict`，不覆盖） |
| 跨日迟到（事实时间保留） | 契约/表设计：`occurred_at` 与 `received_at` 分离；投影按 `occurred_at`；U 契约样本 |
| 两个工作进程并发领取 | U `test_two_workers_do_not_double_apply`（`SKIP LOCKED` + 单 receipt） |
| 毒事件死信 | U `test_poison_event_reaches_dead_letter`（`dead_letter`，`error_class` 只记类型） |
| 重建投影（不调模型/MCP） | U `test_replay_dry_run_does_not_call_tools`；CLI `replay` 实测（dry-run + apply） |
| 删除与重放竞争 | 重放 SQL 显式排除未完成删除线程；U `test_thread_deletion_marks_immediately` |

### 4.4 `tenant-event-access`

| 场景 | 证据 |
|---|---|
| 相同用户加入两家企业 | U `test_same_user_two_tenants_isolated`（B 上下文 404，列表不含 A 的 request） |
| 工作进程交错租户 | 真实 PG RLS 实测（见 §5.2）+ U 双 worker 用例 |
| 复合关联越权 | 真实 PG 实测：跨租户 attempt 插入被 RLS `WITH CHECK` 拒绝；复合外键同租户 |
| 运营查看运行（无正文） | U `test_self_readback_no_content_and_operations_split`（`content_included=false`，正文不出现） |
| 本人回读与其他成员访问 | U 同上 + `get_request_detail` 只按本人过滤；跨范围 404 |
| 运行结束后权限撤销 | U `test_metrics_requires_permission`（403）；`resolve_identity` 每请求实时解析 |
| 普通事件携带正文和票据 | C `forbidden-payload-key-invalid`、`payload-field-not-allowed-invalid`；U `test_write_event_rejects_reserved_and_forbidden` |
| 需求分析授权读取 | U `test_self_readback_no_content_and_operations_split`（`agent:content:read` 独立；仅元数据权限 403） |
| 会话删除中后台投递失败 | U `test_thread_deletion_marks_immediately`（标记即禁读，清理另投递） |
| 恢复旧备份 | **未完成**（见 §7） |
| 历史未知归属 | `history-report` CLI（只读，`native_pending` 计为不可读，不补造归属） |

### 4.5 `agent-metric-contract`

| 场景 | 证据 |
|---|---|
| 重放与重新生成（提问 1 / 尝试 2） | U `test_submission_idempotent_and_conflict`（3 次提交 → 2 提问 2 尝试） |
| 等待与终态混合（分母显式） | U `test_metrics_denominator_and_not_collected`（`not_ended` 单列） |
| 浏览器时钟偏差（单调时钟） | 指标口径 `first_content_ms` 标 `not_collected` 交第 3 项；服务耗时只取服务端时钟 |
| 先工具进度后正文 | 契约 `states.json` 明确"进度另记，不替代 TTFT"；第 5 项实施 |
| 业务日边界 | `metrics.json` 半开窗口 + `Asia/Shanghai`；summary 返回 `window.tz` |
| 七日 P95 | **未完成**（分位数未实现，见 §7） |
| 尚未部署反馈与观测 | U `test_metrics_denominator_and_not_collected`（`first_content_ms/cost/usage_tokens/feedback_rate` 均 `null` + `not_collected`） |
| 浏览器样本缺失（6/10） | 口径要求 `sample_count/missing_count`；当前 summary 返回 `measurement_source` 与状态标签，缺失样本计数随第 3 项样本接入 |
| 反馈与观测并行实施 | `event-registry.json` 已登记第 3/5 项类型（`enabled=false`）+ 责任表 `ownership.md` |
| 新类型未启用 | U `test_reserved_types_rejected_not_enabled`；C `reserved-*-not-enabled-invalid` |

## 5. 真实联调证据（本地栈）

### 5.1 镜像/SDK 能力核验（任务 1.2）

`docs/reviews/evidence/flash-02/task-1.2-image-capabilities.md`（真实镜像
`langgraph_api 0.15.1`）：**调用方指定 run_id 不支持、run 创建不幂等、恢复新建 run**。
因此采用 design §3 的受控创建 + 可信关联 metadata + 受理校准路径。

### 5.2 RLS / 隔离（真实 PG）

| 断言 | 结果 |
|---|---|
| 有 GUC 可见 | 1 |
| **无 GUC** | 0（失败关闭） |
| 跨企业 GUC | 0 |
| 同租户他人（无运营标志） | 0 |
| 运营标志 `agent_operations=on` | 1 |
| 跨租户 attempt 关联插入 | 被 RLS `WITH CHECK` 拒绝 |

### 5.3 最小完整链路（`e2e_ledger_chain.py`，直连 admin + 真实原生 API）

`admit_status=200`、`replay_reused=true`、`run_create_status=200`、
`platform_metadata_persisted=true`、`confirm_state=running`、
`run_matches_ledger=true`、`unknown_state=admission_unknown`、
`unknown_not_retried=true`、`thread_delete=204`。

### 5.4 代理链路（`e2e_proxy_chain.py`，经 Next BFF）

`proxy_run_status=200`、`ledger_marker_header=true`、`ledger_linked_run=true`、
`ledger_state=running`、`replay_no_new_attempt=true`（1→1）、
`proxy_stop_recorded=true`、`thread_delete=204`。

**过程中发现并修复的真实集成缺陷**：代理受理调用未携带
`x-tenant-id`/`x-tenant-context-version`，被 R2 上下文校验 403（修复：`adminFetch`
透传企业上下文声明头）。

### 5.5 六图生命周期（真实 API，每图一次完整运行）

| 图 | run | state | evidence | answers(confirmed) |
|---|---|---|---|---|
| basic-qa | 200 | succeeded | runtime_callback | 1(1) |
| intelligent-analysis | 200 | succeeded | runtime_callback | 1(1) |
| data-analysis | 200 | succeeded | runtime_callback | 1(1) |
| intelligent-report | 200 | succeeded | runtime_callback | 1(1) |
| deep-research | 200 | succeeded | runtime_callback | 1(1) |
| intelligent-tracing | 200 | succeeded | runtime_callback | 1(1) |

业务库台账现状：`answer.confirmed` 9、`run.state_observed` 9、
`run.admission_confirmed` 12、`run.cancel_requested` 3、`request.accepted` 29；
尝试：`succeeded/runtime_callback` 10、`running/platform_read` 6、`admitting` 10、
`admission_unknown` 3。

**过程中发现并修复的两个真实缺陷**：
1. 容器只挂载 `./src`，契约 JSON 在 `docs/` 内不可达 → 终态回调抛
   `FileNotFoundError` **导致真实运行失败**。修复：包内 `contract_data/` 副本 +
   与发布契约的逐字节漂移测试；回调改为**失败开放**（准入仍失败关闭）。
2. 图 state 里是 LangChain 消息**对象**而非 dict → 可见正文识别失败。修复：
   兼容对象/字典两种形态（内部/草稿消息仍排除）。

## 6. 测试与回归基线

| 套件 | 结果 | 与基线对比 |
|---|---|---|
| 管理端 `admin/api/tests/` | **106 passed / 30 skipped / 0 failed** | 基线 117 passed 为含 live 环境的统计；本次同环境 0 失败，无回归 |
| 根单元 `tests/unit_tests/` | 300 passed / 40 skipped / **6 failed** | **与基线失败集完全一致**（checkpoint close 1、knowledge route 4、guard middleware 1）。已用"移除 `src/common/event_ledger` 包"对照复跑，失败集不变 → 非本项引入 |
| 新增台账测试 | 36 passed（contract 12 + store 14 + lifecycle 10） | 新增，无删除/弱化既有断言 |
| 前端契约 `npm run test:contracts` | 26 passed / 0 failed | 基线 19 → 26（+7 事件契约） |
| 前端类型检查 `tsc --noEmit` | 通过 | — |
| 前端生产构建 `npm run build` | 通过（含新路由 `/api/agent-runs/{confirm,stop-intent}`） | — |
| Ruff（新增 Python） | 全部通过（根 + admin 配置） | — |

**注意（测量污染，非回归）**：导出 `RUNTIME_DATABASE_URI` 等台账环境变量后跑根套件会
额外出现 2 项失败（`test_artifact_db`、`test_guard_middleware`）；不带这些变量时二者通过。
与 change 1 记录的同类现象一致，已在报告中标注，不计入回归。

**前端契约测试抖动**：`chat-contract.test.cjs` 的停止确认用例使用 3.2s 真实计时宽限窗，
实测 1/4 次出现 1 项失败、3/4 次 19/19 全绿；未修改其断言。

## 7. 未完成项与保留门禁

> **本节为第一轮状态快照。** 第二轮已补完 3.4 / 4.4 / 4.5 / 5.4 / 6.4 / 7.2 / 7.3 / 8.2，
> 见文末「附：第二轮补完」。下表仅保留**仍未完成**的项。

| 项 | 状态 | 说明 |
|---|---|---|
| change 1 的 7.6 真实企业 MCP | **仍安全禁用** | 继承门禁：外部材料缺失，未启用（六图运行在 MCP 降级为空列表下完成） |
| change 1 的 10.3 完整真实矩阵 | **仍待补** | 六图各一次运行 + 定向行为验证，非完整长运行/负载矩阵 |
| 校准的服务级凭据 | **部署前置** | 身份体系不签发服务级运行票据；探针需注入只读短时效票据，未擅自新建服务身份 |
| 每阶段墙钟 | **已实测**（附二 §L）：单元/集成/契约/构建/E2E 各阶段秒级耗时 + 容器就绪等待 42–60 s/次 |
| Token | **未采集** | 工具不提供计量接口，不猜测 |

指标口径与模型/MCP 替身说明：本项未采集 Token/成本/span（第 5 项），未实现点赞/复制
（第 3 项）；台账验证**未**依赖真实企业 MCP（六图运行在 MCP 降级为空列表下完成），
不声称真实业务 MCP 或完整长运行门禁通过。

## 8. 性能与耗时

| 项 | 值 |
|---|---|
| 六图 `runs/wait` 单次墙钟 | 约 3.5–11 s（含技能路由索引预热；与台账改动无关） |
| 台账受理端点 | 未单独压测；语义为单行插入 + 1 事件 + 1–2 outbox 行（同事务） |
| 台账查询/指标 | 未压测（无负载脚本，属第 10 项矩阵） |
| outbox 投递 | 未在真实栈部署 worker（`LEDGER_WORKER_DATABASE_URI` 未配置 → 明确未启用）；业务库现有 64 条 `pending`（可见积压，未隐藏） |
| 各阶段实际用时 | **实测**：见文末「附二 §L」（工具化计时，非估算）——台账单元 1.8s、根单元 26.5s、管理端 36.4s、集成 7.4s、前端契约 12.6s、生产构建 8.7s。（本行为第一轮快照；第二轮起改为实测。） |
| 环境等待（可观测） | langgraph-api 容器重启 **11 次**（挂载代码生效），每次约 42–60 s 待就绪（含 1 次 `GraphLoadError` 启动失败后修复重试）；前端生产构建 3 次 |
| 修复次数 | 真实缺陷 **5** 项（契约校验顺序、admin 契约路径、`BrowserEventOut.written`、worker 事件读取 RLS 授权、代理缺失企业上下文头）+ 容器内暴露的 **2** 项（契约文件不可达致运行失败、消息对象可见性识别）+ 自伤事故 1 次（文档串批量脚本误改代码行，已按编译定位逐行修复并复跑全部套件）。测试侧调整 3 处（resume 唯一 run_id、毒事件退避归零、mock 补齐新导出） |

## 9. 冻结候选与并行交接

- v1 契约置于 `docs/contracts/agent-events-v1/`，状态 **freeze_candidate**；
  双语言用同一组 21 条样本（含合法 9 + 非法 12）验证，跨项目一致性由
  `admin/api/tests/test_event_ledger_contract_compat.py` 在同一组样本上比对两侧实现。
- 第 3/5 项文件/接口/迁移责任表见 `docs/contracts/agent-events-v1/ownership.md`；
  冻结提交与公共文件所有者**仍待协调者填写**，未自行冻结。
- 本项未启动其他 change，未修改共享身份/迁移链既有 revision。

## 10. 交付状态

**待独立验收**。工作树 diff 保留，未自动 commit / push / merge / archive。

---

# 附：第二轮补完（针对独立核验提出的可独立完成项）

第一轮交付后，独立核验指出 3.4 / 4.4 / 4.5 / 5.4 / 6.4 / 7.2 / 7.3 / 8.2 属"可独立完成但缺失"。
本轮逐项补完，全部有可复跑证据；任务清单现为 **31/31 已就地验证**。

## A. 8.2 真实浏览器验证（替换原 HTTP 代理级联调）

工具：ZCode 内置浏览器（IAB）+ 真实前端 `:3000` + 真实 admin `:8100` + 真实 langgraph-api `:2024`。
账号：`acc_alice`（E2E企业A 管理员 / E2E企业B 成员）。

| 行为 | 浏览器实测结果 | 台账旁证 |
|---|---|---|
| **提交** | 输入并发送「浏览器实测：请用一句话介绍你自己」→ 用户气泡渲染、流式提示、回答正文渲染（8065 tokens） | `request.accepted` / `run.admission_confirmed` / `answer.confirmed` 落库 |
| **停止** | 长任务流式中点击「停止生成」→ 界面进入**停止未确认**态并显示：*"停止尚未确认，请再次点击停止后重试；确认前不能切换企业或发送新问题。"*；发送按钮被移除 | 该尝试 `cancel_requested=t`、`cancel_origin=user_stop`、**主状态仍为 running**（不冒充已取消）；另有一条 `state=succeeded` 且 `cancel_requested=t` 的真实记录，证明"停止与完成竞争"双事实并存 |
| **导航** | 流式中途离开对话页（`goto /login`）→ 返回后应用恢复、租户与侧栏正常 | `pagehide` + `sendBeacon` → 台账 `cancel_requested=t`、`cancel_origin=navigation`、`cancel_phase=processing`；状态**不判取消**（`validate` 200） |
| **企业切换** | 下拉切到「E2E企业B」→ 界面重置；**侧栏不再出现 A 的会话**（`浏览器实测：…` 消失），显示 B 自己的会话 | A/B 线程列表隔离（与 RLS/归属一致） |
| **刷新回读与消息保持** | `reload` → 侧栏仍列出会话 → 打开后**两条消息完整恢复**；被停止的那轮无回答（正确） | 线程/消息来自后端持久化 |

**本轮新增缺陷修复（浏览器暴露）**：导航离开原先**不记录任何停止事实** → 新增 `recordNavigationStop`
（`pagehide` + `sendBeacon`，origin=`navigation`）+ 2 项前端契约测试。

复跑方式：浏览器登录 `http://127.0.0.1:3000/login`（`acc_alice` / `local-e2e-pass`）→ 选择企业 →
发送 → 长任务中途点「停止生成」→ 流式中途导航离开 → 切换企业 → 刷新并打开会话。

## B. 3.4 失败重试 / 重新生成（真实浏览器端到端）

- 后端：`retry`/`regenerate` **关联原 request_id**、新增独立 attempt；来源尝试由服务端按
  (tenant, user, thread) 解析；解析不到时**如实记为 `initial`**（不伪装成重新生成）。
- 前端：「重新发送」→`retry`；助手消息新增「重新生成」→`regenerate`（仅对最后一轮开放，
  避免对历史轮次错误回放）。
- **浏览器实测**：新会话发问（1 提问 / 1 尝试）→ 点「重新生成」→ 同一 `request_id 347dadec`、
  `attempt_index=2`、新平台 run，**提问量仍为 1、尝试量为 2**（符合 spec `agent-metric-contract`）。
- 测试：`test_retry_and_regenerate_link_original_request`、`test_regenerate_without_source_becomes_initial`、
  `test_source_attempt_requires_same_user_and_kind`；前端 2 项契约测试。

## C. 4.4 真实平台校准（不再只靠替身）

新增 `HttpRunStatusProbe`（只读 `GET /threads/{tid}/runs/{rid}`）+ CLI `reconcile --ticket`。
对业务库真实执行：

| 情形 | 观测 | 结果 |
|---|---|---|
| 真实终态 | 平台返回 `interrupted` | `calibrated` → `waiting_input`（**不判取消、不判成功**），`evidence_source=platform_read`，3 条尝试 |
| 对象缺失 | 平台 404（线程已删） | `kept_unknown` + `object_missing`，状态不变 |
| 无平台映射 | `platform_run_id IS NULL` | `kept_unknown` + `no_platform_run_id` |

**保留说明（部署前置）**：当前身份体系不签发"服务级"运行票据（票据一律绑定有效用户会话），
故校准探针需注入只读短时效票据。未擅自新建服务身份（遵守"不重写身份体系"边界）。

## D. 5.4 真实进程崩溃 / 关停（不是模拟过期）

真实 `SIGKILL` 演练（`worker` 子进程，`kill -9`，exit=137）：

```
pending before start: 120
killed mid-batch (exit=137)
--- right after kill ---  done|437   leased|94      ← 持有者 = 被杀进程 REDMI-…:19207:a7a81cee
--- after recovery ---    done|531
--- duplicate receipts (must be 0) ---  0
```

即：在途 94 条被放弃 → 租约到期后新 worker **全部重领为 done**、**重复 receipt 为 0**。
另有 `test_inflight_shutdown_returns_lease`（关停归还为 `pending`，不丢事实）、
`test_live_lease_not_stolen_by_other_worker`（未过期租约独占 + 旧持有者确认被拒）。

## E. 6.4 分位数

`summary.server_latency_ms` 现为服务端时钟 `admitted_at → ended_at` 的
`percentile_cont` **P50/P95/P99**，附 `sample_count` / `missing_count` /
`quantile_method=percentile_cont_on_window_detail`；不与浏览器墙钟相减、不平均每日分位数。
`test_metrics_latency_percentiles_from_detail` 用确定性样本（100/200/300/400/1000 ms）断言
P50∈[200,300]、P95≥700、P99≥P95。

## F. 7.2 历史 dry-run / 归属报告 / 幂等导入

`scripts/import_historical_ledger.py`（默认 dry-run）。业务库实跑：

```
归属报告: 可归属=49 不可归属(pending/未知)=7 缺归属=0 合计=56 已导入历史请求=0
已导入历史请求 49 条（不建 attempt、不补造反馈/Token/耗时）
归属报告: … 已导入历史请求=49
已导入历史请求 0 条                      ← 幂等
```

默认指标口径排除 historical 并单列（`historical_requests.excluded_from_default_cohort=true`，
状态 `historical_unattributed`），历史线程不抬高提问量、不进入企业报表。

## G. 7.3 备份恢复演练（含失败模式与修复）

`docs/reviews/evidence/flash-02/restore_drill.sh`（专用库 `air_agent_ledger_test`）：

```
1) 造删除标记 + 已删除/未删除原文各一
2) pg_dump --no-owner --no-privileges （最坏情形：无 ACL）
3) DROP DATABASE + CREATE
4) 恢复
5) 开放前校验 → policies=15 grants=0 → 「权限未就绪：不得开放读取」 rc=3   ← 门禁生效
6) 重施权限（60 条幂等语句，复用迁移 0007 同一份） → policies=15 grants=48 rc=0
7) 开放读取：RLS 无 GUC 0 / 本人 50 / 跨租户 0 / 他人 0；
   已删除原文仍为已删除(2)、未删除原文仍在(2)、删除标记保持开放
```

**重要发现**：`--no-privileges` 备份会**同时丢失身份表授权**（仅重施台账权限不够，运行期
`users` 表即 403）。因此主路径必须使用带 ACL 的 dump；`restore_ledger_permissions.py` 作为
台账域的安全网，需配合完整授权链重施。该发现已写入脚本与本节。

新增 `admin/api/scripts/restore_ledger_permissions.py`（`--verify-only` 可做开放前门禁）。

## H. 4.5 归档/计算/治理回归

```
tests/integration_tests/test_artifact_tenant_rls_pg.py
tests/integration_tests/test_governance_toolnode.py
tests/integration_tests/test_mcp_500k_guard.py
→ 11 passed / 1 failed / 16 skipped
```
唯一失败为**既有** `test_mcp_500k_overflow_end_to_end`（旧 ModelRegistry monkeypatch，基线保留），
非本项引入。开启 live 后 9 项 `test_live_tenant_claims.py` 全绿（12.6 s）。

> 复跑提示：live 套件需要 `RUN_TENANT_LIVE_E2E=1` + `REVIEW_FIXTURE_PASSWORD` +
> `INTERNAL_API_TOKEN` + `ARCHIVE_SCOPE_KEYS`（后者在 `frontend/.env.local`）。
> 缺 `ARCHIVE_SCOPE_KEYS` 时归档用例会以 `PermissionError: 归档授权签名未配置` 失败——
> 这是**环境缺失**，不是回归（补齐后 9/9 通过）。六图适配点与原生→业务状态映射见 `states.json` 与 `lifecycle.business_state_from_native`
（未知一律 `status_unknown`）。

## I. 第二轮后的最终测试基线

| 套件 | 结果 | 对比 |
|---|---|---|
| 根单元 | 302 passed / 46 skipped / **6 failed** | 与基线失败集一致（checkpoint close 1、knowledge route 4、guard middleware 1） |
| 台账单元（含 PG） | **44 passed** | contract 12 + lifecycle 13 + store 19（含崩溃/租约/关停 3、**预留校验 3**、载荷提取 2） |
| 管理端 | **111 passed / 30 skipped / 0 failed** | 新增 retry/regenerate/分位数/历史导入 4 项 |
| 集成（`RUN_TENANT_LIVE_E2E=1`） | **19 passed / 7 skipped / 1 failed（既有 500k）** | live 用例 9 项全绿（含改造后的六图受理 + 两条负例断言） |
| 前端契约 | **30 passed / 0 failed**（连跑 3 次稳定） | 19 → 30（+导航 2、+retry/regenerate 2、+事件契约 7） |
| 前端 tsc / 生产构建 | 通过 / 通过 | — |
| Ruff（新增 Python） | 全部通过 | — |

## J. 仍未完成（保留门禁，不声称通过）

- change 1 的 **7.6 真实企业 MCP**：外部材料缺失，仍安全禁用（六图运行在 MCP 降级为空列表下完成）。
- change 1 的 **10.3 完整真实矩阵**：本轮为六图各一次运行 + 定向行为验证，非完整长运行/负载矩阵。
- 校准的**服务级凭据**：身份体系未提供，需部署侧补齐（已在 D 节标注）。
- 各阶段墙钟/Token 仍未工具化采集（沿用第一轮说明：未采集，不猜）。

---

# 附二：第三轮（原生入口准入 + 阶段用时）

独立核验指出的两点：① 原生入口未强制"台账受理预留"（spec `agent-run-lifecycle`
"未经过可信台账受理的创建路径 SHALL 被拒绝"）；② 报告未记录每阶段实际用时。均已闭合。

## K. 原生入口准入校验（不再是 proxy-only）

### 实现

`src/common/identity/langgraph_auth.py::on_thread_create_run` 新增**台账受理预留校验**：
任何获准的业务运行（流式/非流式/恢复，六个图共用同一 hook）都必须携带一个
**真实存在、属于本人 + 本线程、且未终态**的台账尝试；否则拒绝创建。

- 预留 ID 提取走三级兼容（实测镜像 0.15.1 的 create_run 载荷把 `config` 放在
  **kwargs 内**，不是顶层）：`metadata.ledger_attempt_id` → `kwargs.config.configurable.ledger_attempt_id`
  → 顶层 `config.configurable.ledger_attempt_id`。
- 校验在台账库内按 (tenant, user, thread) 复核（`store.attempt_reservation`）：
  该 ID 非秘密，但**伪造无意义**——必须真实存在且归属可信身份。
- 失败关闭：台账后端不可用 → 503（拒绝放行，不静默跳过）。
- 语义与 spec 一致：无可信受理上下文的创建路径**拒绝开放**，而不是"记不到就不记"。

> 与此前"图内终态回调失败开放"并不矛盾：**准入失败关闭**（不产生未统计的获准运行），
> **终态回调失败开放**（答案已产生，台账故障不得变成业务失败）。

### 真实 API 证据（原生入口直连 `:2024`）

| 请求 | 结果 |
|---|---|
| 无预留（仅合法票据 + 已确认线程归属） | **403** `运行创建缺少台账受理预留，请经授权入口发起` |
| 伪造预留（随机 UUID，走 metadata） | **409** `台账受理预留无效或不属于当前会话（可能已终态）` |
| 六图逐一带合法预留（configurable） | **全部 200**：basic-qa / intelligent-analysis / data-analysis / intelligent-report / deep-research / intelligent-tracing |
| 合法预留走 metadata 路径 | **200** |

复跑：`docs/reviews/evidence/flash-02/e2e_ledger_chain.py`（含预留注入）与
`tests/integration_tests/test_live_tenant_claims.py::test_live_six_graphs_run_acceptance`。

### 与 change 1 既有断言的冲突处理（已同步修改，未弱化）

`test_live_six_graphs_run_acceptance` 原先直接 POST `/threads/{tid}/runs` 并断言 200/201，
与新契约冲突。**处理方式为增强而非放宽**：

1. 新增 `_admit()`：每图先经 admin `/agent/requests` 取得预留，再带预留创建运行；
2. **新增两条负例断言**：无预留必须 `403`、伪造预留必须 `403/409`；
3. 六图正向断言保留（仍要求全部 200/201）。

实测：该用例通过，且运行前后台账请求数 **delta = 6**（确证六张图都被真实受理并创建运行）。

### 其他直接创建运行的位置

| 位置 | 处理 |
|---|---|
| `docs/reviews/evidence/flash-02/probe_run_admission.py`、`probe_run_metadata_resume.py` | 已改为先受理再带预留，**复跑结论不变**（run_id 仍被忽略、创建仍不幂等、metadata 仍持久化、resume 仍新建 run）——说明准入门禁未改变镜像底层行为 |
| `frontend` 代理路径 | 本就先受理再创建，无需改动 |
| `tests/integration_tests/test_governance_live.py` | **既有失效用例（非本项引入）**：它用 `AUTH_SECRET` 合成**旧 HMAC 会话**并经删除的 `frontend/data/users.json` 取账号，change 1 已让旧 HMAC 一律 401 → 实测 7 项全部在登录阶段 401 失败。该套件由 `AIR_AGENT_LIVE_TESTS=1` 关闭、不在既有基线内。未删除、未改断言；**待身份 change 负责人迁移其会话方式，届时还需补台账预留**。已在此记录，不擅自重写他人验收套件。 |

## L. 每阶段实际用时（工具化测量）

以下为**本机实测**（同一台机器、服务已就绪；命令即报告 §3 的可复跑命令）。
测量方式：外包一层 `time`（Python 计时），非估算。

| 阶段 / 命令 | 实测 |
|---|---|
| 台账单元测试（contract + lifecycle + store，含真实 PG） | **1.8 s** |
| 根单元全套 `pytest tests/unit_tests/` | **26.5 s** |
| 管理端全套 `pytest tests/`（111 用例） | **36.4 s**（pytest 自报 35.68 s） |
| 集成套件 `pytest tests/integration_tests/` | **7.4 s** |
| 前端契约 `npm run test:contracts`（30 用例） | **12.6 s** |
| 前端生产构建（清缓存 `.next`） | **8.7 s**（增量缓存命中时 3.0 s） |
| `alembic upgrade head`（已在 head，幂等空跑） | **0.4 s** |
| `e2e_ledger_chain.py`（受理→真实运行→回读，直连 admin+native） | **1.4 s** |
| `e2e_proxy_chain.py`（经 Next BFF） | **2.1 s** |
| `probe_run_admission.py`（任务 1.2 能力核验） | **10.7 s** |
| admin OpenAPI 路由内省 | **1.2 s** |
| langgraph-api 容器重启到健康（`rm -f` + `container-compose up -d`，6 s/次轮询） | **约 42–60 s/次**（本轮共 11 次重启，含 1 次启动失败，见下） |

**环境等待与失败重试（实测计数）**：
- 容器重启 11 次：其中 **1 次启动失败**（`GraphLoadError`：`with_listeners` 让导出对象
  变成 RunnableBinding），修好后重试；其余 10 次均在 42–60 s 内就绪。
- 前端生产构建 3 次（最后一次清缓存重测）。
- 真实平台校准 2 轮（第一轮命中 `object_missing`，扩批后命中 `interrupted`）。
- 浏览器阶段：登录 + 选企业约 15 s；提交到回答完成约 8–25 s（取决于生成长度）；
  「停止」确认等待约 4 s；导航离开后回读约 9 s。

**修复次数（本轮累计）**：23 处（第一轮 7、第二轮 6、第三轮 10），其中**第三轮 10 处**：
① 原生 hook 缺预留校验；② 预留 ID 提取路径错误（`config` 在 kwargs 内，实测修正）；
③ `_ledger_attempt_id` 替换未生效（ruff 重排后锚点失配，改为按函数体边界替换）；
④ 无预留与伪造都被判 403、合法预留也被 403（同上根因）；⑤ 六图 live 断言未适配新契约；
⑥ 两个 1.2 探针未带预留而不可复跑；⑦ `~text()` 取反不被 SQLAlchemy 支持（改显式 `<>`）；
⑧ `NotFound` vs `LedgerError` 状态码（404 语义）；⑨ worker 用例受库内遗留任务影响（加隔离）；
⑩ `--no-privileges` 恢复丢失身份表授权（发现并写入恢复前置）。

**Token**：工具未提供计量接口 → **未采集**（不猜测）。

## M. spec 场景证据映射（更正 §4.2 中"绕过聊天代理"一行）

| 场景 | 原证据（弱） | 现证据 |
|---|---|---|
| 绕过聊天代理 | ~~U `test_missing_ledger_context_raises_for_unregistered_entry`；L `admit` 前置（未受理不启动）——仅覆盖 BFF 路径~~ | **L 原生入口实测**：无预留 403、伪造预留 409；六图带预留全 200；`test_live_six_graphs_run_acceptance` 新增两条负例断言（delta=6 证明确实走完六图）；U `test_attempt_reservation_rejects_unknown_wrong_owner_and_wrong_thread`、`test_attempt_reservation_rejects_terminal_attempt`、`test_native_run_payload_extracts_ledger_attempt_id`、`test_ledger_scope_requires_full_trusted_identity` |

tasks.md 4.1"无未登记创建入口"现由上述负例断言 + 预留校验支撑。
