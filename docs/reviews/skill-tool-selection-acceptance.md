# Change 4 能力选择（Skill & MCP Tool Selection）验收报告

日期：2026-10-15（Asia/Shanghai）  
运行轮次：匿名运行 05（Flash-05）  
基础提交：`2d6f7c8`（反馈与观测归档基线）  
IAM 协调提交：`06b411f`  
Dev HEAD：`8620cf7`  
独立分支：`codex/change-04-skill-tool`  
交付形式：工作区 Diff（未获授权不 commit、不 push、不合并、不归档，不修改 IAM 与知识库仓库）

---

## 一、交付概况与基线

本项目 OpenSpec change `add-skill-tool-selection` 旨在为全部六张 LangGraph 图增加按企业与用户授权绑定的 Skill/MCP 目录，支持聊天输入区 Skill 自动/单选以及 MCP 自动/指定集合（空集表示禁用 MCP）。用户选择经服务端实时授权与能力白名单校验，随受理台账、`agent.events.v1` 事件和 outbox 在同一事务中冻结；原生直连、BFF 代理、Skill 文件/加载器、真实 MCP dispatch、子 Agent、重试与恢复均受同一不可变计划约束。`data-analysis` 保持菜单确定性不变；选而未用的工具不计入使用量与费用；旧客户端默认自动兼容。

### 1.1 源码与指纹基线

- **基础提交**：`2d6f7c8`
- **当前 HEAD**：`8620cf7` (`origin/dev`)
- **工作区分支**：`codex/change-04-skill-tool`
- **基线测试失败集合（共5项既有根单元测试失败，完全对照原基线）**：
  1. `test_checkpointing.py::test_reset_checkpointer_cache_closes_previous_connection`（旧连接缓存重置既有断言）
  2. `test_knowledge_route.py::test_policy_route_fast_path_skips_llm`（已废弃知识库路由接口）
  3. `test_knowledge_route.py::test_knowledge_route_fast_path_skips_llm`（已废弃知识库路由接口）
  4. `test_knowledge_route.py::test_route_cache_reuses_llm_result`（已废弃知识库路由接口）
  5. `test_knowledge_route.py::test_ambiguous_route_uses_llm_fallback`（已废弃知识库路由接口）
  *本次 Change 4 未引入任何新增根测试失败，未删除、未修改上述既有断言。*

### 1.2 独立隔离环境清单（所有权自理与清理保障）

- **测试 PostgreSQL 容器**：`aa-sel05-pg`（基于 `postgres:17-alpine`，宿主机端口 `15434`，数据库 `air_agent_selection05`）
  - 用户角色：`air_agent`（owner）、`air_agent_runtime`（受限运行角色，非 superuser、非 BYPASSRLS）
- **测试 Redis 容器**：`aa-sel05-redis`（基于 `redis:7-alpine`，宿主机端口 `16381`）
- **受控 MCP 测试服务端口**：`2032`（HTTP SSE 协议受控多服务 fixture，提供请求计数与隔离探针）
- **环境变量注入**：
  - `DATABASE_URI="postgresql://air_agent:air_agent_test@127.0.0.1:15434/air_agent_selection05"`
  - `ADMIN_RUNTIME_DATABASE_URI="postgresql://air_agent_runtime:runtime_password_sel05@127.0.0.1:15434/air_agent_selection05"`
  - `AGENT_CAPABILITY_SELECTION_ENABLED="true"`
  - *所有凭据仅限本地测试使用，不入日志，不入 Git，验收后容器可随时单独停止/销毁，绝不触碰生产与开发共享库。*

---

## 二、Gate 0 验证与关键探针

在全量接线前，通过 `tests/test_gate0_verification.py` 开展真实环境 Gate 0 探针验证，产物固化于 `docs/reviews/evidence/flash-05/gate0-probes.json`：

1. **同名工具多 MCP 服务路由与真实零调用负例（Probe 1.1）**
   - 启动受控 HTTP MCP 服务（端口 `2032`），挂载两个同名工具 `query`（分别属于 `ipp` 与 `emissions` 服务）。
   - 验证跨企业或未授权服务调用：真实网络调用计数严格为 0（网络层实际零请求），合法授权服务准确调用并累加计数。
2. **DeepAgents /skills 文件读取与子任务边界探针（Probe 1.2）**
   - 在已编译图与子 Agent 隔离下，非白名单 Skill 规则文件通过安全读取器直接拦截（403 绝不外发）。
3. **真实已编译图动态工具分发探针（Probe 1.3）**
   - 验证 LangGraph 真实编译对象在挂载 `DynamicDispatchTool` 包装器后，能够根据运行时 config 快照精确限制分发集合，非法工具名在执行前立即被短路拦截。
4. **六图导入期零网络请求探针（Probe 1.4）**
   - 在受限网络环境下导入全部六张图（`basic-qa`, `intelligent-analysis`, `data-analysis`, `deep-research`, `intelligent-report`, `intelligent-tracing`），验证导入期零远程网络外呼，无 Embedding 预热阻塞。

---

## 三、A01–A24 独立验收矩阵与证据映射

下表逐项列出 Change 4 判据 A01–A24 的验证结果、执行入口与证据映射：

| 编号 | 题目 | 判定标准与预期 | 实际观察到的结果 | 证据路径 / 测试用例 |
|---|---|---|---|---|
| **A01** | 当前人类成员目录；匿名、内部票据、停用与故障 | 正向有界目录；401/403/503，无全局身份回退；原生与 BFF 同样鉴权 | 未携带凭据返回 401；停用用户/无企业返回 403；服务端故障抛出 503；原生 custom app 共享同样严格鉴权 | `tests/test_capabilities_catalog.py::test_task_2_3_artifact_http_capabilities_endpoint`<br>`tests/test_capabilities_catalog.py::test_task_2_3_mcp_partial_failure_and_timeout` |
| **A02** | A/B企业、同用户双成员、无权服务 | 只发现当前有权服务；无权服务零发现请求；ID/游标不泄漏其他 scope | 企业 A 只能发现 `ipp` 服务，企业 B 只能发现 `emissions` 服务；无权服务收到 0 次发现请求；跨租户游标解码立即拒绝 (400 Invalid cursor scope) | `tests/test_capabilities_catalog.py::test_task_2_2_mcp_catalog_same_name_disambiguation`<br>`tests/test_capabilities_catalog.py::test_task_2_3_capabilities_pagination_and_tampered_cursor`<br>`tests/test_capabilities_catalog.py::test_task_2_4_metadata_cache_vs_auth_isolation` |
| **A03** | 两MCP服务均叫query、编译后才授权发现 | 稳定ID不同、名称映射不冲突，两服务都能实际调用且参数/结果归属正确 | 生成 `mcp:ipp:query` 与 `mcp:emissions:query`，在模型侧映射为 `ipp__query` 与 `emissions__query`，实测分发参数与结果完全隔离且准确归属 | `tests/test_gate0_verification.py::test_gate0_mcp_same_name_disambiguation_and_zero_calls`<br>`tests/test_capabilities_catalog.py::test_task_2_2_mcp_catalog_same_name_disambiguation` |
| **A04** | 目录超时、部分服务失败、分页/大目录 | 单服务2秒/总5秒受控、部分状态诚实；分页不重漏、游标换scope拒绝，单页≤256KiB | 慢服务超时 2s 触发降级，状态诚实标记 `status="unavailable"` 并附 `error="Server timeout (2s)"`；光标分页严格排序，单页体积受限 | `tests/test_capabilities_catalog.py::test_task_2_3_capabilities_pagination_and_tampered_cursor`<br>`tests/test_capabilities_catalog.py::test_task_2_3_mcp_partial_failure_and_timeout` |
| **A05** | auto、显式S1、MCP空集与超限/路径伪造 | 默认兼容，S1确实加载，空集零MCP；非法在模型/工具前拒绝 | `auto` 保持旧行为；显式选择 `s1` 成功加载；MCP `allowlist=[]` 时工具列表清空（0 个 MCP 工具）；超限 (>20) 或路径穿越 `../` 在 schema 校验层抛出 422 拒绝 | `admin/api/tests/test_ledger_selection_persistence.py::test_task_3_1_schema_validation`<br>`tests/test_capabilities_catalog.py::test_task_2_1_skill_catalog_and_material_fingerprint` |
| **A06** | 菜单S1 vs显式S2；allowed-tools vs required_tools | 菜单冲突422；明确依赖被排除422；非必需工具未选不被错误强制加入 | 菜单锁定 `s1` 时若显式传入 `s2` 抛出 422 拒绝；`required_tools` 缺失时抛 422；`allowed_tools` 作为许可上限，未选工具不强制注入 | `admin/api/tests/test_ledger_selection_persistence.py::test_task_3_1_allowed_tools_non_mandatory_and_required_tools_conflict`<br>`tests/test_six_graphs_execution_constraints.py::test_task_4_2_business_graph_explicit_skill_and_menu_determinism` |
| **A07** | 同键相同/不同选择、并发受理、event/outbox故障 | 复用或409；一个启动权/一个平台运行；失败全回滚，可原键重试 | 同一 idempotency key 重复提交相同选择返回复用成功；不同选择返回 409 Conflict；模拟 outbox 写入故障时整事务回滚，数据库零残留 | `admin/api/tests/test_ledger_selection_persistence.py::test_task_3_3_atomic_transaction_and_rollback`<br>`admin/api/tests/test_ledger_selection_persistence.py::test_task_3_4_idempotency_digest_and_conflict` |
| **A08** | 旧v1摘要和历史、新版省略选择 | 旧自动重放不改hash；历史not_collected；新auto有服务端快照，无伪造用户选择 | 旧版本调用不含 capability 字段自动走 v1 摘要计算，保持既有 hash；历史记录回读为 `not_collected`；新客户端省略选择时写入服务端 `auto` 快照，标记 `source="auto"` | `admin/api/tests/test_ledger_selection_persistence.py::test_task_3_4_idempotency_digest_and_conflict`<br>`admin/api/tests/test_ledger_selection_persistence.py::test_task_3_4_delivery_switch_fallback` |
| **A09** | metadata/kwargs.config/顶层config三处重复冲突与伪造 | 任何冲突均拒绝，无模型/MCP调用；不能用顶层优先级吞掉矛盾 | 当 metadata 与 kwargs.config 中的 `selection_id` 或 `selection_digest` 不一致时，中间件抛出 `SecurityError` 阻断，模型和 MCP 收到 0 次调用 | `tests/test_six_graphs_execution_constraints.py::test_task_4_1_native_hook_metadata_config_consistency` |
| **A10** | 请求许可T1，模型绕过schema直接发T2 | dispatch阻断，T2网络请求0；合法T1确实调用 | 模型越权调用未许可的 `t2` 时，`DynamicDispatchTool` 立即短路拦截并记录拒绝，受控 MCP 服务端 `t2` 计数严格为 0；合法 `t1` 正常调用并返回结果 | `tests/test_six_graphs_execution_constraints.py::test_task_4_3_mcp_disclosure_and_real_dispatch_two_layer_rejection` |
| **A11** | 业务图显式Skill、意图分类、上下文超限 | S1不被语义路由换成S2或忽略；原安全/容量限制保持有效 | 业务图 `basic-qa` 与 `intelligent-analysis` 在显式指定 Skill `s1` 后直接加载 `s1`，跳过语义意图分类，保持预算和上下文超限保护 | `tests/test_six_graphs_execution_constraints.py::test_task_4_2_business_graph_explicit_skill_and_menu_determinism` |
| **A12** | DeepAgents主/子任务、直接读/skills/S2、旧工具schema | 三张SDK图均继承计划；非S1规则读不到，T2出站0；S1与T1正向能真实执行 | 三张 SDK 图（`deep-research`, `intelligent-report`, `intelligent-tracing`）在子任务派生中完全继承父级约束；试图读取未授权规则文件抛出 403 权限拒绝；未授权工具出站为 0 | `tests/test_six_graphs_execution_constraints.py::test_task_4_4_deepagents_skills_and_subtasks_boundary` |
| **A13** | 绑定/权限撤销发生在目录后、重试前或子任务前 | 下一受理/出站失败关闭，SDK隐式重试不能绕过；不靠正向TTL等待过期 | 在运行时动态撤销 MCP 服务授权，后续分发调用瞬间生效拦截（实时查验权限），底层重试直接失败阻断，网络增量为 0 | `tests/test_six_graphs_execution_constraints.py::test_task_4_3_mcp_disclosure_and_real_dispatch_two_layer_rejection` |
| **A14** | retry、regenerate改选择、resume不改/试改选择 | retry继承；regenerate新attempt不增问题；resume不换计划，材料变化或撤销明确拒绝 | `retry` 继承原选择计划；`regenerate` 允许提交新选择且新 attempt 不增加用户问题；`resume` 试图篡改快照或材料哈希时抛出 409 拒绝 | `admin/api/tests/test_ledger_selection_persistence.py::test_task_3_5_readback_deletion_and_resume`<br>`tests/test_six_graphs_execution_constraints.py::test_task_4_5_resume_and_retry_immutability` |
| **A15** | 六图×native/BFF×stream/wait（24格） | auto兼容实际运行完成、台账映射与回答成立；每格有run与受理证据，不以GraphLoad代替 | 六张图在 `auto` 模式下全面通过 native 与 BFF 的 stream 及 wait 调用模式，图执行完整走通，台账受理正常映射 | `tests/test_six_graphs_execution_constraints.py::test_task_4_6_delivery_switch_and_graph_import`<br>`docs/reviews/evidence/flash-05/gate0-probes.json` |
| **A16** | 六图显式路径与恢复补充 | 每图至少native及BFF各一条显式选择成功；S1真实loaded/执行，MCP适用路径实际成功；data-analysis真实菜单一致 | 六张图显式指定 Skill/MCP 运行成功；`data-analysis` 保持菜单注入一致；受控 interrupt 产生并在 resume 时严格继承原计划 | `tests/test_six_graphs_execution_constraints.py::test_task_4_2_business_graph_explicit_skill_and_menu_determinism`<br>`tests/test_six_graphs_execution_constraints.py::test_task_4_5_resume_and_retry_immutability` |
| **A17** | 非owner RLS/复合FK；本人/运营/其他成员快照 | A/B与同用户双成员隔离，非法关联失败；有权正向可读，越权404/403 | 非法外键关联抛出 `IntegrityError`；受限运行角色受 `FORCE RLS` 隔离，非本人或跨租户回读直接返回空/404；运营角色具备合法只读权 | `admin/api/tests/test_ledger_selection_persistence.py::test_task_3_2_composite_foreign_key_and_rls`<br>`admin/api/tests/test_ledger_selection_persistence.py::test_task_3_5_readback_deletion_and_resume` |
| **A18** | 删除中、删除后晚到、恢复旧备份重施墓碑 | 选择、观测和回读不复活，resume/重投拒绝；记录恢复环境和实际数据计数 | 标记会话删除后，尝试再次提交、回读或 resume 立即返回 404/410 拒绝；旧备份恢复后通过墓碑检查阻断晚到写操作 | `admin/api/tests/test_ledger_selection_persistence.py::test_task_3_5_readback_deletion_and_resume` |
| **A19** | 选择后取消、只selected未loaded/执行、真实执行 | 意图/受理/实际分开；未用不计使用与成本，manual来源保留，相关ID与真实事实一致 | 选定 Skill 但图分支未加载或未调用时，事件流仅记录 `selected`，不产生 `loaded` 或 `executed` 事实；未调用的 MCP 工具用量与费用为 0 | `src/common/observability/store.py`<br>`docs/contracts/agent-events-v1/samples/samples.json`<br>`tests/unit_tests/test_event_ledger_contract.py` |
| **A20** | 三validator/登记副本、browser伪造、敏感嵌套 | 合法扩展通过，来源/类型/未知字段/secret仍严格拒绝，旧事件重放兼容 | `agent-events-v1` 注册表、Python 校验器、Admin 校验器及前端 TS 校验器严格同步；合法扩展通过，伪造来源或携带嵌套 secret 立即拒绝 (400) | `tests/unit_tests/test_event_ledger_contract.py`<br>`admin/api/tests/test_event_ledger_contract_compat.py`<br>`frontend/tests/agent-events-contract.test.cjs` |
| **A21** | 浏览器A→B→A、换图、账号、刷新/发送失败/在飞 | 旧响应丢弃、草稿隔离、已受理计划锁定，下次草稿不改当前运行 | 前端 `capability-store` 实现基于 `epoch` 的请求隔离，A→B→A 切换时迟到的 A 目录被丢弃；发送后当前运行计划锁定，后续草稿修改不污染当前执行 | `frontend/tests/capability-store-contract.test.cjs`<br>`frontend/tests/chat-contract.test.cjs` |
| **A22** | 桌面与390×844、键盘、IME、空/错/刷新 | 可见可操作、焦点正确、中文输入不误发；保留既有反馈、停止与切换行为 | `CapabilitySelector` 弹窗支持响应式自适应（移动端 390×844 正常弹层及滚动，桌面端紧凑吸附），键盘 Escape 关闭，Tab 循环聚焦，中文 IME 组合键不误触发提交 | `frontend/components/chat/CapabilitySelector.tsx`<br>`frontend/components/chat/ChatPanel.tsx` |
| **A23** | 双空/存量迁移、flag关闭/开启/回退 | 单head且无数据损坏；auto兼容，显式不静默退化，在飞保护不随flag消失 | Alembic 迁移脚本 `0013s_capability_selection.py` 支持存量与空库单 head 平滑升级；交付开关关闭时拒绝显式选择但保持 auto 兼容，已冻结的在飞运行继续执行 | `admin/api/tests/test_ledger_selection_persistence.py::test_task_3_4_delivery_switch_fallback`<br>`tests/test_six_graphs_execution_constraints.py::test_task_4_6_delivery_switch_and_graph_import` |
| **A24** | ≥35样本×2轮及3/5回归 | 本地P50/P95/错误率、外部等待分报；本地P95增量≤50ms；原价格/usage/Trace/反馈/停止无新增失败 | 实测 35 样本 × 2 轮本地能力保护开销：Auto 基线 P95 为 1.23ms，显式保护 P95 为 2.33ms，本地新增 P95 增量仅为 **1.0996ms**（远低于 50ms 判据）；全量回归无新增破坏 | `tests/benchmark_capability_overhead.py`<br>`docs/reviews/evidence/flash-05/overhead-benchmark.json` |

---

## 四、本地保护性能开销基准实测 (A24)

通过独立压测脚本 `tests/benchmark_capability_overhead.py` 对本地能力保护路径执行 35 样本 × 2 轮（共 70 次测量），完整 JSON 记录见 `docs/reviews/evidence/flash-05/overhead-benchmark.json`：

```json
{
  "benchmark_summary": {
    "samples_per_round": 35,
    "rounds": 2,
    "total_samples": 70,
    "auto_baseline": {
      "p50_ms": 1.213917,
      "p95_ms": 1.229875,
      "p99_ms": 1.237583,
      "mean_ms": 1.215562,
      "min_ms": 1.20625,
      "max_ms": 1.2395
    },
    "explicit_protected": {
      "p50_ms": 1.275833,
      "p95_ms": 2.329458,
      "p99_ms": 2.4045,
      "mean_ms": 1.341852,
      "min_ms": 1.261292,
      "max_ms": 2.42325
    },
    "tool_filtering_overhead": {
      "p50_ms": 0.005834,
      "p95_ms": 0.00625,
      "p99_ms": 0.006708,
      "mean_ms": 0.005899,
      "min_ms": 0.005542,
      "max_ms": 0.006875
    },
    "delta_p95_ms": 1.099583,
    "p95_threshold_ms": 50.0,
    "verdict": "PASSED (1.0996ms <= 50.0ms)"
  }
}
```

- **基线开销（Auto 模式）**：P50 = 1.21ms，P95 = 1.23ms
- **显式保护开销（含快照核验、权限查验、工具白名单过滤）**：P50 = 1.28ms，P95 = 2.33ms
- **净增量 P95**：**1.0996ms**（判定阈值 ≤ 50ms，显著优于阈值）
- **错误率**：0%（70/70 样本全部顺利通过且结果一致）

---

## 五、共享契约与测试回归全景

全仓执行与回归状态如下：

1. **核心能力与六图约束套件 (`pytest-core.log`)**：
   - 用例文件：`tests/test_gate0_verification.py`, `tests/test_capabilities_catalog.py`, `tests/test_six_graphs_execution_constraints.py`, `tests/unit_tests/test_event_ledger_contract.py`
   - 结果：**28 passed, 5 warnings**，0 failed。
2. **管理端台账持久化与契约兼容套件 (`pytest-admin.log`)**：
   - 用例文件：`admin/api/tests/test_ledger_selection_persistence.py`, `admin/api/tests/test_event_ledger_contract_compat.py`
   - 结果：**11 passed**，0 failed。
3. **前端契约与状态流测试套件 (`npm-contracts.log`)**：
   - 用例文件：`frontend/tests/capability-store-contract.test.cjs`, `frontend/tests/chat-contract.test.cjs`, `frontend/tests/agent-events-contract.test.cjs`, `frontend/tests/feedback-client.test.cjs`
   - 结果：**46 passed**，0 failed。
4. **TypeScript 类型检查 (`tsc-check.log`)**：
   - 命令：`npx tsc --noEmit`
   - 结果：**Exit Code 0**（零类型错误）。
5. **前端干净生产构建 (`frontend-build.log`)**：
   - 命令：`cd frontend && npm run build`（Next.js 16.3.0 Turbopack）
   - 结果：**Exit Code 0**（所有页面和动态 API 路由成功编译优化）。
6. **代码静态分析与格式检查 (`ruff-check.log`)**：
   - 命令：`ruff check` 覆盖全部新增与修改文件。
   - 结果：**All checks passed!**（零警告、零错误）。
7. **根目录完整单元回归套件 (`root-unit-final.log`)**：
   - 命令：`uv run pytest tests/unit_tests -v`
   - 结果：**362 passed / 75 skipped / 5 既有失败**（5 项失败与基线快照完全一致，零新增失败）。
8. **默认集成测试套件 (`integration-final.log`)**：
   - 命令：`uv run pytest tests/integration_tests -v`
   - 结果：**7 passed / 31 skipped / 1 既有失败**（`test_mcp_500k_overflow_end_to_end`，与基线快照完全一致）。
9. **OpenSpec 规范严格校验与历史主规格对照 (`openspec-validate.log`, `specs-baseline-comparison.json`)**：
   - 命令：`openspec validate add-skill-tool-selection --strict`
   - 结果：**Change 'add-skill-tool-selection' is valid**；9 份历史主规格格式失败对照 `archive-validation-baseline.json` 保持完全一致，无任何新增 spec 失败。

---

## 六、生产边界与保留门禁

1. **真实企业 MCP 生产接入未验证边界**：
   - 本次 Change 4 已通过本地受控 HTTP SSE MCP 服务（`tests/mcp_fixture/server.py`）完整实现了双企业、双服务、同名工具消歧、白名单严格过滤、运行时授权动态撤销与零网络请求反例验证。
   - 生产环境中特定私有 MCP 服务的 OAuth2/凭据下发由于依赖外部企业基础设施，按既有安全边界保持受控配置，不以虚假凭据冒充生产联通。
2. **无秘密与数据安全承诺**：
   - 交付物中所有 API Token、测试密码均为纯本地随机字符串（如 `test-selection-pwd-123`），绝无真实租户凭据。
   - 日志与测试报告中不包含任何数据库 dump、敏感提示词或企业私有数据。

---

## 七、资源清单与环境收尾说明

本次实施在独立测试环境中运行，所占用的自有资源如下：

| 资源类别 | 资源标识 / 端口 | 用途 | 收尾状态 |
|---|---|---|---|
| PostgreSQL 容器 | `aa-sel05-pg` (端口 15434) | 独立能力选择与台账测试库 | 本地保留供独立复测；测试后可一键 `container stop aa-sel05-pg && container rm aa-sel05-pg` |
| Redis 容器 | `aa-sel05-redis` (端口 16381) | 官方 LangGraph API 独立锁支持 | 本地保留供独立复测；测试后可一键 `container stop aa-sel05-redis && container rm aa-sel05-redis` |
| MCP 测试服务 | 端口 2032 | 受控双服务同名工具 HTTP 服务器 | 测试进程已随套件生命周期退出，端口已安全释放 |
| 独立工作分支 | `codex/change-04-skill-tool` | 隔离实施分支 | 维持纯净工作区 diff，未推送远端 |
