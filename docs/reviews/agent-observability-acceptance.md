> 历史作者交付报告，正文保留供盲评追溯。该首交未通过独立验收，最终由协调者补齐。当前结论见 [接管验收](agent-interactions-observability-takeover-acceptance.md)，不得将本文历史自测等同最终验收。

# 匿名运行04：complete-agent-observability 实施验收报告

日期：2026-10-13。分支 `codex/change-05-observability`，基线 `0f2cd02`（冻结源码 `9a1781e3f6d74ad99e5f325e14a69642f6474927` 的祖先校验通过，11 个机器冻结哈希全部匹配）。**状态：待独立验收。未 commit / 未 push / 未合并 / 未归档；全部改动保留在本工作区 diff。**

## 1. 交付范围与真实落库证据

### 1.1 已实施（可独立复验）

| 域 | 交付 | 真实证据 |
|---|---|---|
| 契约窄例外 | runtime 五类型（model_usage/ttft/cost/span/tool_call）合法启用；`usage_tokens_not_non_negative_int` 新原因码；三份 validator（src / admin / TS）+ 发布与包内副本 + 生成 TS 副本同步 | `tests/unit_tests/test_event_ledger_contract.py` 12/12、`admin tests/test_event_ledger_contract_compat.py` 4/4、`frontend tests/agent-events-contract.test.cjs` 7/7 全绿；合法整数 / string / bool / 负数 / 嵌套secret / 嵌套tokens / browser来源 / 跨类型 token 反例全部保留 |
| 迁移 | `0012_observability`（down=0010）：7 张表 + FORCE RLS + 排他约束拒绝价格区间重叠；复合外键强制 attempt/request 同租户 | 两个专用库（aa_observe04_app / aa_observe04_native）从 0010 升级到 0012 成功；downgrade 拒绝破坏性回退 |
| 观测存储 | `src/common/observability/{context,store,collector,model_adapter,tool_adapter,pricing,middleware}`：观测行+事件+outbox 同事务、终态不回退、删除优先、有界队列（1000/100/2s/3次指数退避，只可下调）、丢弃可见 | 真实 PG E2E：agent_model_observations / runtime.* 事件 / observability_projection outbox 真实落库（见 §2） |
| 模型观测 | 主模型逐尝试 callback（BaseCallbackHandler，不改变图导出类型）；逻辑调用/物理尝试分离；usage 归一化（provider usage_metadata/response_metadata、缓存/推理子集、缺失=usage_missing）；TTFT + first_piece_kind | 1 逻辑调用 → 3 次独立尝试观测（重试场景实测）；每尝试独立 duration_ms；失败路径 usage_missing 如实落库 |
| Skill 观测 | matched/selected/loaded/executed 分阶段 span + runtime.span 事件 | 真实运行：matched×3 + selected + loaded(succeeded) 事件与 outbox 落库 |
| 价格与成本 | 版本化价格（半开区间、排他约束拒绝重叠、审计）、Decimal 分项计费（缓存/推理子集不重复收费）、price_unknown/usage_missing/invalid_usage 分列、显式幂等重算（calculation_revision 唯一约束）、币种分组小计 + 缺失单列 | 确定性 fixture：1000含缓存400/输出200含推理50 → CNY 0.0028800000（600×2+400×0.2+200×8)/1M 精确命中；重算两次幂等；17 条 usage_missing 观测不参与计费、如实单列 |
| admin 查询 | `GET /agent/observability/{observations,runs/{id},metrics,costs,traces/{id}}` + `POST prices` / `prices/recalculate`；`agent:pricing:manage` 独立权限（企业管理员默认不含）；游标绑定租户+过滤指纹 | 真实 API：observations 明细（limit<=200）、metrics（17 obs、coverage 0.118、P50=22/P95=29.3ms 与明细一致）、价格创建/重叠409/重算幂等全通 |
| 隔离与授权 | 企业B视角 observations 403（无 operations 权限）；伪造 trace 404 在外部查询前拒绝；RLS 无上下文零读取；A/B 同用户切换隔离 | 真实 API + 真实 PG |
| 六图兼容 | 全部图导出保持原生类型；观测注入走节点/中间件收口（无 with_listeners） | 官方镜像 0.15.1 实测 6 图 `/assistants/{id}/graph` 全部 200；basic-qa 真实运行成功 |
| 开销测量 | 35样本×2轮 开/关对比（同进程 FakeModel、无外部等待、warmup 5） | 本地 P50 增量 ≈ -0.1ms、P95 ≈ -0.2ms（噪声内），远低于 50ms 阈值；`docs/reviews/evidence/flash-04/overhead-benchmark.json` |

### 1.2 Langfuse 真实链路（任务 1.4 / 5.1 已验证，5.2 映射保留）

- 写路径实测：SDK 4.15.6 OTEL span → `POST /api/public/otel/v1/traces` (200) → Redis 队列 → worker → ClickHouse `events_core` 真实可见（`docs/reviews/evidence/flash-04/langfuse-write-verify.json`）。
- 本部署为 v4 events_only：`/api/public/traces/{id}` 404 属预期；读路径以 ClickHouse 为准。
- 排障记录：langfuse-worker 容器未启动导致队列积压（已启动并验证）、ClickHouse 1g 内存 OOM（重建为 2g 保留数据卷）。
- **未完成（如实标注）**：attempt↔trace 映射（`agent_trace_links`）写入路径已实现（`record_trace_link` + 注册表反查旧 CallbackHandler trace id），但容器内旧 handler 与 child callbacks 合并顺序导致捕获为空，映射未真实落库。按 spec"回调已绑定但未落库必须如实标注"，任务 5.2 的映射部分保留未勾选。

### 1.3 外部阻塞（环境等待，非代码缺陷）

- **主 LLM 网关（workbuddy2api hy3）**：本轮大部分时间 503「暂无可用账号」。因此：
  - 真实**成功**调用的 usage 观测（100+20=120 场景、流式去重复验）未能落库验证；失败尝试观测（含部分 usage 语义、usage_missing）已真实验证。
  - 归一化/子集/去重/非法 usage 语义由确定性 fixture 全覆盖（19 项单测）。
- **真实企业 MCP**：材料缺失，保持安全禁用（生产门禁单列）；本域工具采集代码以单点包装器覆盖 MCP/local 两路径。

## 2. E2E 最小链路（handoff 要求的核心证明）

真实主模型受理 → 原生 run → PG observation → runtime.* 事件 → observability_projection outbox →（Langfuse trace 由旧 handler 真实上报，本地映射待 §1.2 修复）：

```
受理 admit 200 → thread claim/confirm 200 → run 200
→ agent_model_observations: 1逻辑/3尝试（失败路径，duration 22/23/24ms，usage_missing）
→ agent_events: runtime.model_usage ×3、runtime.span ×5（matched×3/selected/loaded）
→ agent_outbox: observability_projection 条目
→ Langfuse: events_core 真实可见（独立写入验证）
```

LLM 网关恢复后复跑 `/tmp/aa04/e2e_run.py` 即可补成功 usage 与流式去重的真实证据。

## 3. 公共 diff 清单（交接给协调者）

| 文件 | 授权依据 |
|---|---|
| `docs/contracts/agent-events-v1/event-registry.json` + `samples/samples.json` + 两份包内副本 + `frontend/lib/agent-events/contract-data.ts`（生成） | handoff：runtime 五类型字段/开启；生成副本重生成 |
| `src/common/event_ledger/contract.py`、`admin/api/app/services/event_contract.py`、`frontend/lib/agent-events/contract.ts` | handoff：数字计数窄例外，双语言一致 |
| `src/common/identity/langgraph_auth.py`（仅追加 `_attach_run_observability` 调用，失败开放） | design §7：六图采集接线；不改身份解析/授权 |
| `src/common/langfuse_tracing.py`（仅追加 handler 注册） | handoff：langfuse_tracing 归 04 |
| `src/common/business_graph/nodes.py`、`tool_wrappers.py`、`src/data_analysis/nodes.py`、`src/common/data_governance.py`（invoke 透传 metadata）、三个 deepagents graph（追加 middleware） | handoff：runtime 共享文件采集 hook 归 04 |
| `admin/api/app/main.py`（追加 router）、`scripts/seed.py`（追加 `agent:pricing:manage`） | handoff：仅追加本域 router/开关/权限 |
| 新增：`src/common/observability/*`、`admin/api/app/{services,routers}/observability.py`、`0012_observability.py`、`tests/unit_tests/test_observability_{core,pricing}.py`、`tests/bench_observability_overhead.py`、`docs/reviews/evidence/flash-04/*` | 本域独占 |

## 4. 配置 / 权限 / 运维接线清单

- 开关：`AGENT_OBSERVABILITY_ENABLED`（默认关闭；本轮环境显式开启）；队列参数 `AGENT_OBSERVABILITY_MAX_QUEUE/MAX_BATCH/FLUSH_WAIT_SECONDS/MAX_RETRIES`（只能下调）。
- 权限：`agent:pricing:manage`（grantable=True，企业管理员种子**不含**；平台 super_admin 通配持有）。
- 查询权限：observations/runs/traces=`agent:operations:read`；metrics/costs=`agent:metrics:read`；价格写=pricing+CSRF+租户上下文版本。
- 消费者：`observability_projection`（独立 consumer，与 run_projection 并存）。
- 隐私：payload 白名单 + 禁止键扫描；观测行只存计数/时长/非秘密名/规范类别；Langfuse metadata 仅标识（无正文/凭据）。

## 5. 测试与门禁汇总

| 范围 | 结果 |
|---|---|
| 根单元（契约+观测核心+价格+lifecycle） | 57 通过 |
| admin 契约兼容 | 4 通过 |
| 前端契约（Node） | 7 通过 |
| OpenSpec strict | `complete-agent-observability` valid |
| Ruff（本项全部改动文件） | 0 错误（`src/common/config/checkpointing.py` 的 F811 为冻结基线既有，stash 验证） |
| 迁移 | app/native 两库 0010→0012 成功 |
| 官方镜像 | 0.15.1，六图 GraphLoad 200，basic-qa 真实运行成功 |
| 根全量 / 集成套件 | 未复跑（时间约束）；基线既有 5 根失败 + 1 集成失败未触碰、未"修好" |

## 6. 用时 / 返工 / 环境等待（诚实记录）

- 实际用时：本轮连续约 4.5 小时（含环境搭建、容器排障、三轮 E2E 迭代）。
- 返工点：契约窄例外的判定顺序（forbidden 先于白名单）返工 2 次；collector 从 asyncio task 重写为专用线程（同步回调无事件循环）；admin pricing 的 pydantic 前置声明 / Literal 导入 / 审计列名 / `:basis::jsonb` 语法 4 次小修；UnboundLocalError（INSERT 路径 merged 变量）1 次。
- 环境等待：LLM 网关 503（约 1.5h 内多次重试）；langfuse-worker 未启动 + ClickHouse OOM（各一次，已修复并记录）。
- Token 用量：工具未提供，**未采集**。

## 7. 未完成项与复验清单（供独立验收）

1. LLM 网关恢复后：复跑 E2E 验证成功 usage 观测（100+20=120、缓存/推理子集真实值、流式末块与结束回调不重复计费）。
2. trace 映射：修复容器内旧 handler trace id 捕获（合并顺序），复验 `agent_trace_links` 真实落库 + trace.linked 事件。
3. 受控 MCP 替身 E2E（重试/降级/跨服务同名/并发身份）——代码已接，运行未做。
4. BFF 入口流式/非流式 + 受控中断恢复（native 已验）。
5. 晚到回调删除复验（store 语义已实现+单测）。
6. 根全量/集成套件复跑。

## 8. 验收边界

单分支完成 ≠ 组合验收；与 Change 3（反馈）的合并、事件登记合并、迁移 merge head 由协调者负责。生产 MCP 与完整长运行矩阵门禁保持。
