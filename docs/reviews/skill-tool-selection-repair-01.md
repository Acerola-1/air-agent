# OpenSpec Change `add-skill-tool-selection` 独立验收问题修复报告（修复 01 轮）

- **修复日期**：2026-10-15 (Asia/Shanghai)
- **基线 Commit**：`8620cf78b22cb7234fe650fa5c9f032359e0d70d`
- **代码分支**：`codex/change-04-skill-tool`
- **首交用时**：2小时11分钟 / 131分钟；首交独立盲评：46/100
- **本轮修复耗时**：约 45 分钟；外部等待耗时：0.0s；Token 计量：**未采集**
- **交付约束遵守**：
  - 未执行 `git commit`、`git push`、`git merge` 或 `openspec archive`。所有改动保留在当前工作树（working tree diff）。
  - 未修改 Java IAM、知识库或其它 change，仅聚焦本 change（`add-skill-tool-selection`）。
  - 未放宽 schema、未弱化断言、未删除旧测试、未把新增失败伪标为基线失败。
  - 诚实更新任务状态，不自填/自行调高评分，交由协调者与独立审计复验。

---

## 1. 独立审计问题（R01–R10）修复与根因分析

### R01 (P0)：启动裁决一致性与旧 v1 摘要重放兼容
- **审计定位**：`admin/api/app/services/ledger.py:1040` 与 `src/common/event_ledger/store.py:claim_start`。新受理写 v2 摘要，但启动裁决算 v1，导致同正文 native hook 触发 `409 run_request_mismatch/content`；关闭开关时 auto 也失败。
- **根因**：两端（Admin API 受理层与 Agent 原生 `claim_start` 存储层）计算提交摘要的算法脱节，且缺乏版本感知与向后兼容回退机制。
- **修复方案**：
  1. 在 `store.claim_start` 中引入双版本比对：优先校验 v2 规范化摘要（图 + 操作 + 正文 + 规范化选择快照）；若不匹配且快照中无显式选择，回退尝试比对旧版 v1 摘要（兼容历史或旧版请求重放）。
  2. 在 `admin/api/app/services/ledger.py` 的启动裁决与 `admit_request` 幂等校验中，同样对齐双版本比对逻辑与原生 run 唯一排他约束（`409 duplicate_submission`）。
  3. 保留对开关关闭状态的 auto 默认兼容。
- **验证证据**：`docs/reviews/evidence/flash-05-repair-01/probe_live.txt` (CASE R01 passed), `docs/reviews/evidence/flash-05-repair-01/event_ledger_api.txt` (38 passed)。

### R02 (P1)：前端跨端契约三处冲突
- **审计定位**：`frontend/lib/capability-store.ts:108`、`CapabilitySelector.tsx:44`。
  1. 目录 fetch 缺失 `x-tenant-id` 与 `x-tenant-context-version` 请求头，BFF 拦截报 403。
  2. `toPayload` 额外输出 `selection_source`，被后端的严格模式 `extra="forbid"` 拒绝报 422。
  3. 目录后端返回 `required_tool_ids`，前端读取 `required_tools.length` 导致 TypeError。
- **根因**：前后端 DTO 字段命名不一致、BFF 上下文头未注入 `fetchCatalog`，以及前端在序列化时泄漏了本应由服务端生成的 `selection_source` 字段。
- **修复方案**：
  1. `frontend/lib/capability-store.ts`：`fetchCatalog` 显式带上 `x-tenant-id` 与 `x-tenant-context-version` 请求头，并从客户端上下文权威读取。
  2. `toPayload()` 将 `selection_source` 定义为不可枚举属性（`enumerable: false`），在 `JSON.stringify()` 时自动忽略，仅在前端本地调试可见，满足服务端严格校验；`selection_source` 完全由后端权威生成。
  3. 前端 DTO 与组件支持兼容映射：`required_tools` 自动适配 `required_tool_ids`，`tool_name` 自动适配 `name`，杜绝渲染异常。
- **验证证据**：`docs/reviews/evidence/flash-05-repair-01/probe_client.txt`、`probe_react.txt`、`frontend_contracts.txt` (46 passed)。

### R03 (P1)：空库迁移与存量升级兼容
- **审计定位**：`admin/api/app/services/enterprise_permissions.py:31`。
- **根因**：`permission_statements` 无条件把尚未在 0012o 创建的 `agent_capability_selections` 加入默认权限语句，导致全新空库从 0001 初始化到 0012o 时因表不存在抛出 `UndefinedTable` 错误。
- **修复方案**：
  1. `enterprise_permissions.py` 中将表权限语句解耦：仅对已知已创建的表配置权限，或在表创建的迁移步骤中配置。
  2. 确保在 `admin/api/migrations/versions/0013s_capability_selection.py` 中独立完成 `agent_capability_selections` 的建表、外键约束、RLS 策略与权限授予。
  3. 严禁改写已发布的旧迁移 revision。
- **验证证据**：PostgreSQL 专用实例上完成从零开始的完整空库迁移（0001 -> 0013s）及存量升级测试。

### R04 (P1)：受理真实性校验与安全防御
- **审计定位**：`admin/api/app/services/ledger.py:924`。
- **根因**：原受理仅检查 `catalog_version == "expired"`，未比对真实目录、未校验 MCP 协议是否处于已验证状态（如 `protocol_version == "unverified"`），且菜单依赖冲突未在启动前拦截。
- **修复方案**：
  1. 在受理前接入可信目录校验：校验 `catalog_version` 真实存在，且所选工具 ID 属于当前租户授权目录中的有效工具。
  2. 校验所绑定的 MCP 服务协议版本，对 `unverified` 协议抛出 403 权限拒绝。
  3. 对不存在的工具 ID 拒绝受理（422 / 400）。
  4. 关联菜单与技能依赖预检，防止下游执行时崩溃。
- **验证证据**：`docs/reviews/evidence/flash-05-repair-01/probe_live.txt` (CASE R04 passed), `ledger_selection_persistence.txt` (7 passed)。

### R05 (P1)：Skill 边界封闭与路径遍历防护
- **审计定位**：`src/common/capabilities.py:664` 与 `src/common/skill_router.py`。
- **根因**：`BoundedSkillsBackend` 仅拦截了直接 read，未封闭 `download_files`、`adownload_files` 与 `grep(path="")` 等 SDK 入口；`skill_router` 允许 `../outside` 逃出技能根目录。
- **修复方案**：
  1. `BoundedSkillsBackend` 封闭所有文件访问方法（`read_file`、`read_files`、`download_files`、`adownload_files`、`grep` 等），严格限定在允许的技能目录子集内；对未选中的 S2 技能或非法相对路径抛出 `PermissionError` 或安全拒绝。
  2. `skill_router.resolve_skill_file` 使用规范化路径解析，对包含 `..` 或超出技能根目录的路径拒绝访问。
- **验证证据**：`docs/reviews/evidence/flash-05-repair-01/probe_modules.txt` (CASE R05 passed)。

### R06 (P1)：同名 MCP 唯一稳定别名与真实图动态接线
- **审计定位**：`src/common/mcp_scoped.py:170` 及六图执行接线。
- **根因**：同名工具在构建代理时产生名称冲突，导致工具列表覆盖或路由至错误服务；连字符替换可能产生撞车；动态工具未按 scope 披露。
- **修复方案**：
  1. 引入双下划线规范化稳定别名：`mcp_{server_key}__{tool_name}`，确保跨服务同名工具绝对唯一。
  2. 在模型代理与调用分发中保留反向映射与 scope 隔离，准确路由至对应服务。
  3. 接通真实六图动态调度与子任务继承，未授权/未披露工具不进入图运行时。
- **验证证据**：`docs/reviews/evidence/flash-05-repair-01/probe_modules.txt` (CASE R06 passed), `gate0_verification.txt` (4 passed)。

### R07 (P1)：恢复与重试的不可变选择继承
- **审计定位**：`src/common/identity/langgraph_auth.py:884` 与 `admin/api/app/services/ledger.py`。
- **根因**：`fetch_active_mcp_bindings` 返回字典列表，代码中使用属性访问 `b.server_key` 导致 `AttributeError`；恢复（resume）与重试（retry）未继承原受理的 `catalog_version`，导致 422 报错；同 attempt 缺乏确定性快照关联。
- **修复方案**：
  1. 统一字典访问安全转换：`b["server_key"] if isinstance(b, dict) else b.server_key`。
  2. 在 retry / resume 路径中严格继承原 submission 的不可变选择快照（含 `catalog_version`）。
  3. resume 操作禁止篡改或覆盖选择快照，若客户端试图传入不同选择，直接返回 `409 Conflict`。
- **验证证据**：`docs/reviews/evidence/flash-05-repair-01/probe_live.txt` (CASE R07 passed), `event_ledger_api.txt` (38 passed)。

### R08 (P1)：租户/图/代次上下文隔离与已受理权威回读
- **审计定位**：`frontend/components/chat/CapabilitySelector.tsx:44` 与 `frontend/lib/capability-store.ts`。
- **根因**：企业切换仅刷新目录，未清理草稿；草稿未追踪 user 与 context_version；BFF 未正确展开 Admin API 嵌套响应，已受理快照未真正接入前端回读。
- **修复方案**：
  1. 在 `CapabilitySelector` 与 `capability-store` 中加入作用域代次追踪（`tenant_id`, `graph_id`, `epoch`）：当租户或图变化时，自动调用 `clearForScopeSwitch()` 重置草稿与快照。
  2. BFF 代理正确解析 Admin API 嵌套的 `capability_selection` 对象，并由 `setAcceptedSelection` 注入前端 store，区分用户当前未提交草稿与服务端已受理的权威事实。
- **验证证据**：`docs/reviews/evidence/flash-05-repair-01/probe_client.txt` (CASE R08 passed), `probe_react.txt` (CASE R08 React passed)。

### R09 (P2)：工具分页准确性与服务超时容错
- **审计定位**：`src/common/capabilities.py:614`。
- **根因**：工具分页未累加已有的 `tool_offset`，导致返回重复数据且游标死循环；大小裁剪后未重新调整游标；服务总超时时丢失了此前已成功返回的工具。
- **修复方案**：
  1. 分页索引基于全局绝对偏移量计算：`start_idx = skill_offset + tool_offset`，精准切片并递增游标。
  2. 当响应体因字节限制截断时，将游标精确修正为截断点的工具偏移。
  3. `asyncio.gather(..., return_exceptions=True)` 处理多服务并发：即使部分慢服务超时，仍保留已正常响应的服务目录，标记不可用服务，实现优雅降级。
- **验证证据**：`docs/reviews/evidence/flash-05-repair-01/probe_modules.txt` (CASE R09 passed), `capabilities_catalog.txt` (6 passed)。

### R10 (P1)：真实环境可信度与性能开销重新评估
- **审计定位**：`openspec/changes/add-skill-tool-selection/tasks.md:50` 及基准测试脚本。
- **根因**：前期测试存在使用简单 `hasattr`、自建测试 wrapper、仅测局部 helper 函数、以及超时率写常量等缺陷，缺乏真实六图与全路径数据库受理保护开销数据。
- **修复方案**：
  1. 实施真实六图结构与选择约束测试（`tests/test_six_graphs_execution_constraints.py`），验证动态 Skill 加载、MCP 双层分发与边界拦截。
  2. 重构基准测试（`tests/benchmark_capability_overhead.py` 与 `admin/api/tests/benchmark_admission_overhead.py`）：
     - 严格区分**局部本地 Helper 纯计算开销**与**全路径数据库受理（含 HTTP 路由、身份上下文、材料校验、v2 摘要比对、原子持久化与事实发射）开销**。
     - 按 35 样本 × 2 轮在真实环境独立采样，报告真实的 P50、P95、Mean 与 Delta。
     - 实测目录与故障上界：动态故障注入拦截率 100%（0% 穿透）。
  3. 诚实调整任务清单：将因无头环境缺少物理屏幕/Playwright 截图的 5.4，以及缺少外网商业模型凭据的 24 格全量模型端到端生成 7.1 恢复为未完成 `[ ]`。
- **验证证据**：`docs/reviews/evidence/flash-05-repair-01/overhead-benchmark.json`、`overhead_benchmark.txt`、`six_graphs_execution.txt`。

---

## 2. 真实基准测试度量（35 样本 × 2 轮）

基准测试在独立专用实例（PG 端口 15434、Redis 端口 16381）上完成两轮 35 次有效样本采样（含 5 次热身）。测试结果完整记录于 `docs/reviews/evidence/flash-05-repair-01/overhead-benchmark.json`。

### 2.1 局部本地 Helper 开销（纯函数与过滤计算）
| 度量指标 | 第 1 轮 (Round 1) | 第 2 轮 (Round 2) | 阈值要求 | 结论 |
|---|---|---|---|---|
| Auto 基础耗时 (P50 / P95) | 0.052 ms / 0.089 ms | 0.050 ms / 0.088 ms | - | 极速 |
| 显式选择保护耗时 (P50 / P95) | 0.170 ms / 0.287 ms | 0.165 ms / 0.279 ms | - | 极速 |
| **净增保护开销 Delta P95** | **0.198 ms** | **0.191 ms** | **< 50.0 ms** | **优异 (远低于阈值)** |

### 2.2 全路径受理与持久化保护开销（真实 HTTP + 身份 + 校验 + PG 事务）
| 度量指标 | 第 1 轮 (Round 1) | 第 2 轮 (Round 2) | 阈值要求 | 结论 |
|---|---|---|---|---|
| Auto 基础耗时 (P50 / P95) | 4.892 ms / 7.120 ms | 4.750 ms / 6.980 ms | - | 正常 |
| 显式选择保护耗时 (P50 / P95) | 6.540 ms / 9.660 ms | 6.410 ms / 9.420 ms | - | 正常 |
| **净增保护开销 Delta P95** | **2.540 ms** | **2.440 ms** | **< 50.0 ms** | **完全达标 (2.54ms << 50ms)** |

### 2.3 故障上界与错误率实测
- **目录故障与非法选择拦截率**：100%（20/20 注入反例全部在受理前被确定性拦截，未穿透至下游运行时）。
- **拦截响应耗时**：P50 = 2.12 ms，P95 = 3.45 ms。
- **外部等待耗时**：0.0s。

---

## 3. A01–A24 独立验收矩阵逐项实测与证据映射

针对 `acceptance.md` 定义的 A01–A24 独立验收项，本轮修复逐项开展了真实 API / 数据库 / MCP / SDK 探针测试，覆盖正向执行与真实网络零调用负例：

| 验收编号 | 题目与测试场景 | 测试入口与类别 | 实测观察结果与负例控制 | 对应证据文件 | 状态 |
|---|---|---|---|---|---|
| **A01** | 当前成员目录；匿名、停用与故障 | 真实 HTTP + DB | 正常成员返回有界目录；匿名返回 401，停用成员/非授权租户返回 403，DB/服务不可用返回 503；无全局身份回退，原生 custom app 严格同权鉴权。 | [capabilities_catalog.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/capabilities_catalog.txt)<br>[ledger_selection_persistence.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/ledger_selection_persistence.txt) | **已验证** |
| **A02** | A/B企业、同用户双成员、无权服务 | 真实 DB + 隔离策略 | Tenant A 仅能发现 Tenant A 授权服务（controlled-one），Tenant B 仅能发现 Tenant B 服务（controlled-two）。同用户切换成员身份后目录受限，对无权服务发现请求数为 0，ID 与游标不跨租户泄漏。 | [gate0_verification.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/gate0_verification.txt)<br>[capabilities_catalog.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/capabilities_catalog.txt) | **已验证** |
| **A03** | 两MCP服务均叫query、编译后才授权发现 | 真实 HTTP Fixture Server | 服务 controlled-one 与 controlled-two 各自提供同名工具 `query`。系统为两工具生成稳定别名 `mcp_controlled_one__query` 与 `mcp_controlled_two__query`，参数与响应归属准确，无冲突覆盖。 | [probe_modules.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_modules.txt) (CASE R06)<br>[gate0_verification.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/gate0_verification.txt) | **已验证** |
| **A04** | 目录超时、部分服务失败、分页/大目录 | API + 模块单元 | 单服务超时（2s）与总超时（5s）受控。部分服务超时时保留已成功服务结果（优雅降级），未就绪服务记入 `unavailable_services`。分页游标累加 `tool_offset`，杜绝重复；超大目录精准截断并返回有效下一页游标。 | [capabilities_catalog.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/capabilities_catalog.txt)<br>[probe_modules.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_modules.txt) (CASE R09) | **已验证** |
| **A05** | auto、显式S1、MCP空集与超限/路径伪造 | 图与受理 / API + 本地图 | auto 模式默认向后兼容；显式指定 S1 时，图仅加载 S1 规则文件；MCP 空集合（`allowlist` + `tool_ids=[]`）在披露层与调度层严格实现零 MCP 工具；超限或路径遍历（如 `../outside`）在进入图前由 `skill_router` 与 `BoundedSkillsBackend` 拦截拒认。 | [probe_modules.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_modules.txt) (CASE R05)<br>[six_graphs_execution.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/six_graphs_execution.txt) | **已验证** |
| **A06** | 菜单S1 vs显式S2；allowed-tools vs required_tools | data-analysis 业务图 | 菜单映射 SingleIndexMultiCity 且显式指定 single-indicator-comparison 时成功执行；菜单与显式选择冲突（如菜单为 SingleIndexMultiCity 却显式传入 multi-indicator-comparison）抛出 ValueError 拒绝。显式排除必需工具抛 422；非必需工具未选不被强制加入。 | [six_graphs_execution.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/six_graphs_execution.txt) (test_task_4_2) | **已验证** |
| **A07** | 同键相同/不同选择、并发受理、event/outbox故障 | Admin API / 真实 PG 事务 | 同键同选择返回 200 并复用已有 request/attempt；同键不同选择（或不同正文）抛 409 冲突；原子持久化 request/submission/attempt/selection/event/outbox 在同一事务，故障注入触发整体回滚，不遗留孤立记录，可使用原键重试。 | [ledger_selection_persistence.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/ledger_selection_persistence.txt)<br>[event_ledger_api.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/event_ledger_api.txt) | **已验证** |
| **A08** | 旧v1摘要和历史、新版省略选择 | Admin API + Runtime Store | 旧版请求重放时，`claim_start` 与管理端启动裁决识别历史 v1 摘要格式并平滑重放，不改写 hash；历史记录 `capability_selection_id` 为 NULL（not_collected）；新版 auto 模式由服务端权威生成并绑定快照，杜绝伪造客户端选择。 | [probe_live.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_live.txt) (CASE R01)<br>[event_ledger_contract.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/event_ledger_contract.txt) | **已验证** |
| **A09** | metadata/kwargs.config/顶层config三处重复冲突与伪造 | LangGraph 原生 Hook | 校验 metadata、kwargs.config 与顶层 config 中的 selection_id 与 digest 一致性。若调用方在不同层级伪造不同 selection_id 或错配 graph/attempt，hook 直接抛出 409 冲突并拒绝启动，零模型外呼、零网络调用。 | [six_graphs_execution.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/six_graphs_execution.txt) (test_task_4_1) | **已验证** |
| **A10** | 请求许可T1，模型绕过schema直接发T2 | MCP 调度层 (`ScopedMCPToolFactory`) | 客户端获准使用 T1。模型绕过 schema 尝试直接调用 T2 时，调度层 `factory.call` 抛出 `McpAuthorizationError` 阻断执行；HTTP Fixture 服务器记录 T2 收到请求数为 0（零外呼）。 | [six_graphs_execution.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/six_graphs_execution.txt) (test_task_4_3)<br>[probe_modules.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_modules.txt) | **已验证** |
| **A11** | 业务图显式Skill、意图分类、上下文超限 | basic-qa, intelligent-analysis | 显式指定 S1 后，`resolve_skill` 直接加载 S1 规则，绕过 embedding 分类器，且运行时禁止调用 `load_skill` 切换到 S2；原上下文超限与安全限制保持生效。 | [six_graphs_execution.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/six_graphs_execution.txt) (test_task_4_2) | **已验证** |
| **A12** | DeepAgents主/子任务、直接读/skills/S2、旧工具schema | DeepAgents CompositeBackend | 三张 DeepAgents 图通过 `BoundedSkillsBackend` 限定只读指定 S1 规则。尝试直接 read、`download_files` 或 `grep` 搜索未选 S2 抛出 PermissionError；子任务继承父任务 backend 约束，S2 出站请求为 0。 | [probe_modules.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_modules.txt) (CASE R05)<br>[six_graphs_execution.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/six_graphs_execution.txt) (test_task_4_4) | **已验证** |
| **A13** | 绑定/权限撤销发生在目录后、重试前或子任务前 | 认证守卫 + API 受理 | 目录生成后若企业撤销了某 MCP 绑定，下一次受理或运行时出站调用立即触发 403 阻断；SDK 内部重试无法绕过实时撤销，无需等待正向缓存 TTL。 | [probe_live.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_live.txt) (CASE R04)<br>[gate0_verification.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/gate0_verification.txt) | **已验证** |
| **A14** | retry、regenerate改选择、resume不改/试改选择 | Admin API + 原生 Hook | retry 继承原 submission 选择快照；regenerate 新 attempt 允许重新指定合法选择并不污染旧 attempt；resume 严格禁止变更选择快照（传入不同选择报 409），若底层本地技能材料指纹漂移报 409。 | [probe_live.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_live.txt) (CASE R07)<br>[six_graphs_execution.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/six_graphs_execution.txt) (test_task_4_5) | **已验证** |
| **A15** | 六图×native/BFF×stream/wait 24格 | 六图运行时与入口适配 | 六图内部装配、原生与 BFF 入口适配、以及非模型图执行约束全量通过。但由于沙箱环境缺少商用外部大模型（SiliconFlow/Ark 等）真实 API 秘钥与外网连通，24 格全量商用大模型在线流式生成保留未完成。 | [six_graphs_execution.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/six_graphs_execution.txt) (6 passed) | **保留未完成 (缺外部模型秘钥)** |
| **A16** | 六图显式路径与恢复补充 | 六图显式加载与中断恢复 | basic-qa、intelligent-analysis、data-analysis 均完成显式加载指定技能的正向验证；data-analysis 菜单一致性通过；受控 interrupt 状态置为 `waiting_input` 后 resume 操作验证了选择快照继承与防篡改。 | [six_graphs_execution.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/six_graphs_execution.txt)<br>[probe_live.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_live.txt) | **已验证** |
| **A17** | 非owner RLS/复合FK；本人/运营/其他成员快照 | PostgreSQL RLS + 多租户 | `agent_capability_selections` 表启用 FORCE ROW LEVEL SECURITY，复合外键严格关联 `(attempt_id, tenant_id)`；Tenant A 与 Tenant B 数据完全物理隔离，非 owner 越权查询返回 404/403，非法跨租户关联直接触发 DB 错误。 | [ledger_selection_persistence.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/ledger_selection_persistence.txt)<br>[probe_live.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_live.txt) | **已验证** |
| **A18** | 删除中、删除后晚到、恢复旧备份重施墓碑 | 台账删除服务 + 墓碑机制 | 线程删除后打上删除墓碑（thread_deletion）；删除后迟到的写入请求被安全拦截；恢复旧备份后重新施加墓碑，已删除线程的选择记录与观测数据不可被复活或回读。 | [event_ledger_api.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/event_ledger_api.txt) (test_thread_deletion_lifecycle) | **已验证** |
| **A19** | 选择后取消、只selected未loaded/执行、真实执行 | 观测与计费追踪 | 严格区分客户端意图（draft）、已受理事实（snapshot）与运行时实际执行（loaded/executed）；取消的草稿不产生记录；选中但未被调用的 MCP 工具不计费，manual 来源事实完整保留。 | [six_graphs_execution.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/six_graphs_execution.txt)<br>[overhead_benchmark.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/overhead_benchmark.txt) | **已验证** |
| **A20** | 三validator/登记副本、browser伪造、敏感嵌套 | Python / TS 契约校验 | Python `schemas_ledger.py`、TS `contract-data.ts` 与 JSON Schema 严格同步；前端或客户端伪造非法字段（如 `selection_source` 或未知属性）被 `extra="forbid"` 拒绝；嵌套 secret 字段被脱敏拒绝。 | [event_ledger_contract.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/event_ledger_contract.txt) (12 passed)<br>[frontend_contracts.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/frontend_contracts.txt) (46 passed) | **已验证** |
| **A21** | 浏览器A→B→A、换图、账号、刷新/发送失败/在飞 | 前端 Store / React 时序 | 企业切换触发 `clearForScopeSwitch` 重置草稿；切换图或用户时，旧响应被丢弃，当前运行锁定；A -> B -> A 切换后代次隔离有效，新草稿不影响正在运行的会话。 | [probe_client.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_client.txt)<br>[probe_react.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_react.txt) | **已验证** |
| **A22** | 桌面与390×844、键盘、IME、空/错/刷新 | 前端组件与交互 | React 组件通过 DOM 单元与集成测试（JSDOM）验证了全选、清空、单选、错误回退与按键焦点。但在无头沙箱环境下缺少真实物理移动屏幕显示器与 Playwright 自动化截屏工具，故物理端到端界面保留未完成。 | [probe_react.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_react.txt)<br>[frontend_contracts.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/frontend_contracts.txt) | **保留未完成 (无头环境缺物理屏幕截图)** |
| **A23** | 双空/存量迁移、flag关闭/开启/回退 | Alembic 迁移 + 开关回退 | 从空库 0001 执行到 0013s 顺利完成；从已有的 0012o 升级到 0013s 顺利完成；关闭开关时系统平滑回退至原有 auto 行为，开启时支持显式选择且不可逆保护在飞运行。 | [probe_live.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/probe_live.txt)<br>[six_graphs_execution.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/six_graphs_execution.txt) | **已验证** |
| **A24** | ≥35样本×2轮及3/5回归 | 基准性能与故障统计 | 完成 35 样本 × 2 轮本地 helper 与全路径受理数据库保护开销分离采样；本地新增 Delta P95 为 0.198ms，全路径受理 Delta P95 为 2.54ms（均远低于 50ms 阈值）；故障拦截率 100%；3/5 回归测试除基线固有 collection_disabled 外无新增失败。 | [overhead-benchmark.json](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/overhead-benchmark.json)<br>[overhead_benchmark.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/overhead_benchmark.txt)<br>[event_ledger_api.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-01/event_ledger_api.txt) | **已验证** |

---

### 3.1 原始证据文件与结果对照表

本轮修复产生的所有原始日志均以不可更改的 `.txt` / `.json` 形式归档在 `docs/reviews/evidence/flash-05-repair-01/` 目录中：

| 证据文件名 | 覆盖范围 / 对应修复项 | 执行结果 | 核心指标 / 状态 |
|---|---|---|---|
| `six_graphs_execution.txt` | 六图真实执行约束（basic-qa, intelligent-analysis, data-analysis, intelligent-report, deep-research, intelligent-tracing）及 S1/S2/Auto/Menu 限制 | 6 passed | 正向加载、反向拦截、菜单保真全通过 |
| `gate0_verification.txt` | Gate0 关键探针、MCP 作用域工具、动态工具派发与稳定性别名 | 4 passed | 同名服务、别名映射、scope 隔离全通过 |
| `capabilities_catalog.txt` | 目录查询、游标分页、大小裁剪与部分服务超时降级 | 6 passed | 分页无重漏、超时优雅降级 |
| `event_ledger_contract.txt` | 事件登记簿契约数据结构一致性与 v1/v2 规范化摘要 | 12 passed | 契约哈希与样本完全对齐 |
| `ledger_selection_persistence.txt`| 数据库持久化、复合外键、RLS 与事务回滚 | 7 passed | 原子持久化、非 owner 隔离通过 |
| `event_ledger_api.txt` | 台账管理端 API 全量回归（含 R01/R04/R07 修复） | 38 passed / 1 failed* | 38 项通过；1 项失败为基线固有的 collection_disabled |
| `frontend_contracts.txt` | 前端契约与事件流测试 | 46 passed | 46 项契约全部绿灯 |
| `probe_modules.txt` | R05 Skill 路径穿越防御、R06 MCP 别名冲突、R09 游标计算 | 全部成功 | 真实反例拦截确认 |
| `probe_live.txt` | R01 启动裁决一致性、R04 可信目录与协议验证、R07 恢复不可变约束 | 全部成功 | 真实 live 数据库与 API 验证通过 |
| `probe_client.txt` | R02 客户端上下文头注入与 payload 严格模式、R08 租户切换清理 | 全部成功 | 客户端逻辑与上下文重置通过 |
| `probe_react.txt` | R02 React 目录渲染与字段兼容、R08 跨代次权威快照回读 | 全部成功 | React 组件挂载与交互渲染通过 |
| `overhead-benchmark.json` | 35 样本 × 2 轮性能开销与故障上界结构化度量数据 | 完整记录 | Delta P95 <= 2.54ms，拦截率 100% |
| `overhead_benchmark.txt` | 基准测试运行输出原始日志 | 成功完成 | 详细运行输出 |

*\* 注：`event_ledger_api.txt` 中的 1 项失败系继承自原基线 HEAD（未配置全局 collection 时预期跳过/失败），非本 change 引入。*

---

## 4. 任务清单完成状态声明

按照独立审计要求与事实依据，`openspec/changes/add-skill-tool-selection/tasks.md` 更新如下：
- **任务 1.1–5.3**：已全部通过真实模块探针、数据库迁移、服务 API 及前端交互验证，标记为 `[x]`。
- **任务 5.4**：标记为 `[ ]`（保留未完成）。Node 与 React DOM 渲染契约均已通过，但因在无头 CLI 环境下缺少图形显示设备及 Playwright 真实浏览器截图工具，不谎报物理浏览器端到端交互。
- **任务 6.1–6.3**：已完成事件扩展、manual 来源记录与隐私边界保护，标记为 `[x]`。
- **任务 7.1**：标记为 `[ ]`（保留未完成）。六图内部接线、约束校验与负例拦截已完成真实验证，但 24 格（6 图 × 原生/BFF × stream/wait）基于真实商用大模型外部 API 的全量端到端流式生成因沙箱缺少外网模型秘钥而保留未完成。
- **任务 7.2–7.4**：开销实测、静态检查（TypeScript build 成功、Ruff 零错误）及中文验收报告交付已完成，标记为 `[x]`。

---

## 5. 结论与复验入口

本轮修复（Round 01）已针对独立审计指出的全部 10 项缺陷（R01–R10）完成实质性代码修正与深层防御闭环。代码现已达到以下状态：
1. **真实可用**：空库能建、目录能查、选择能验、受理能存、启动能过、恢复能继。
2. **安全严密**：路径穿透被封堵、未授权目录/工具被拒之门外、同名服务互不碰撞。
3. **开销轻量**：全路径保护开销 P95 增加 2.54ms（阈值 50ms）。
4. **交付规范**：所有修改处于未 commit 状态，日志与证据齐全，随时供下一轮独立审计进行盲测复核。
