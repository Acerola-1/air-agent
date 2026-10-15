# Air Agent `add-skill-tool-selection` 接管收尾与独立验收报告

日期：2026-10-15（Asia/Shanghai）  
分支：`codex/change-04-skill-tool`  
基准提交（HEAD）：`8620cf78b22cb7234fe650fa5c9f032359e0d70d`  
最终跟踪 diff SHA-256：`01946968312d13674ad3d722d65becf865c6ba542c510929a6eaf7829552394e`  
Change 规范：`openspec/changes/add-skill-tool-selection/`（严格校验通过）  
工作区：`/Users/acerola/Dev/Python/air_agent`  
专用持久化：`aa-sel05-pg:15434`（库 `air_agent_selection05`）、`aa-sel05-redis:16381`

---

## 1. 历史记录与接管交接

### 1.1 历史评审与原模型记录（保留原始事实，不篡改评分）

根据 `docs/reviews/flash-blind-05-repair-02-review.md` 与用户审计事实，原匿名模型执行记录完全保留：

| 轮次 | 用户实测耗时 | 得分 | 状态 / 核心结论 |
|---|---|---|---|
| 首次交付 | 131 分钟 | 46/100 | 未通过，核心边界与多图运行时未闭环 |
| 第一轮修复 | 83 分钟 | 63/100 | 未通过，发现 F01–F07 阻断项 |
| 第二轮修复 | 60 分钟 | 58/100 | 未通过，引入 G01–G08 回归缺陷（身份揭晓为 Gemini 3.8 Flash） |
| **原模型累计** | **274 分钟（4 小时 34 分）** | - | **未独立完成，终止其返工** |

> **说明**：接管成果另行独立记录，接管者不为自己预填盲审评分、不推测未采集的 Token 消耗、不改写历史探针和评分。所有未提交工作区 diff 与新增文件全部完整保留，等待协调者独立终验。

---

## 2. G01–G08 缺陷闭环详查与反例复验

本轮接管重点对 `docs/reviews/flash-blind-05-repair-02-review.md` 中指出的 G01–G08 全部阻断项进行了根本修复，并在独立测试与正反例控制下完成复验：

### G01：三张 DeepAgents 生产图 SDK 运行时崩溃（P0，已修复）
- **根因分析**：`src/common/middleware/tenant_runtime_guard_middleware.py` 对 BaseTool 的 `t.metadata` 直接调用 `.get()`，但在 LangChain/LangGraph 真实 BaseTool 实现中，`metadata` 的合法默认值可以为 `None`，导致首次模型调用在进入执行节点前抛出 `AttributeError: 'NoneType' object has no attribute 'get'`。
- **修复方案**：重构守卫过滤逻辑，使用安全读取 `(getattr(t, "metadata", None) or {}).get("capability_mcp_tool_id")`，确保在字典、对象或 `None` 元数据下均具备完全的防御性兼容。
- **复验结果**：通过 `probe-production-sdk.py`，使用受控模型直接编译并调用三张生产图（`deep-research`、`intelligent-report`、`intelligent-tracing`），全部成功执行并返回 `controlled success`。
- **证据文件**：`docs/reviews/evidence/takeover-05/production-sdk-graphs.json`

### G02：0013s 迁移遗漏 `selection_source` 列导致空库与存量升级失败（P1，已修复）
- **根因分析**：`admin/api/migrations/versions/0013s_capability_selection.py` 中删除了 `selection_source` 列定义，但在其后的 CHECK 约束与 ORM 模型 `models_ledger.py` 中仍引用该列，导致全新空库或从 0012o 升级至 head 时报 `UndefinedColumn: column "selection_source" does not exist`。
- **修复方案**：在 0013s 迁移脚本的 `agent_capability_selections` 表定义中显式恢复 `selection_source` 列（`sa.String(length=32), nullable=False, server_default='automatic'`）。
- **复验结果**：在独立临时库执行完整迁移升级：
  1. `0001 -> 0012o_delivery`：成功（returncode 0）。
  2. `0012o_delivery -> head (0013s)`：成功（returncode 0）。
  3. 表 `agent_capability_selections` 19 个字段完整创建，`selection_source` 存在，行级安全策略（FORCE RLS）生效。
- **证据文件**：`docs/reviews/evidence/takeover-05/migration-verification.json`

### G03：能力目录版本可伪造与跨进程可信目录边界（P1，已修复）
- **根因分析**：管理服务与运行时属于不同进程，Python 内存单例单向发现无法跨进程共享；同时，校验逻辑曾仅用正则匹配目录字符串，未校验当前权威目录哈希，且在工厂异常时跳过存在性检查。
- **修复方案**：
  1. 在 `admin/api/app/services/ledger.py` 中接入跨进程可信发现边界 `discover_authorized_mcp_catalog(r_id)`，结合请求租户/用户/成员上下文与授权修订生成可信快照；
  2. 严格核对客户端提供的 `catalog_version` 与权威计算结果，伪造版本立即返回 `409 Conflict ("能力目录版本已过期，请刷新后重试")`；
  3. 目录为空、工厂发现异常均严格失败关闭，返回 `422 Unprocessable Entity ("能力目录为空或工具尚未发现")`。
- **复验结果**：
  - 伪造目录版本 `cat_000000000000000000000000`：返回 409。
  - 合法独立 HTTP 管理与运行时进程联调：目录返回 200，所选 `mcp:controlled-one:query_air` 正常受理为 200，MCP 实际调用数为 0（零模型外呼）。
- **证据文件**：`docs/reviews/evidence/takeover-05/process-boundary.json`

### G04：客户端自行派生 selection_id/digest 缺乏服务端权威回读（P1，已修复）
- **根因分析**：`frontend/lib/chat-store.ts` 在收到受理确认后，在浏览器端重新计算摘要并派生随机 UUID 填入 `admittedSelection`，而服务端真实的 `selection_id` 与材料指纹均由后端数据库与材料文件决定。
- **修复方案**：
  1. 改造 `frontend/lib/langgraph-api.ts` 的 `confirmRunAdmission`，将返回值由简单 boolean 升级为服务端完整的权威回读对象 `{ confirmed: boolean, attempt_id, capability_selection }`；
  2. 在 `chat-store.ts` 中直接采用服务端返回的权威 `capability_selection`（包含真实的 `id`、`selection_id`、`selection_digest`、`material_version` 等），彻底移除客户端前端伪造逻辑与 Node.js 服务端模块（如 `fs`）的 hack 引用；
  3. 保持草稿锁定机制：发送 S1 后用户修改草稿为 S2，延迟确认返回后当前运行的已受理快照保持 S1，草稿区保留 S2，互不污染。
- **复验结果**：
  - 执行 `tests/takeover_test_accepted_readback.cjs`：`selection_id_matches: true`，`selection_digest_matches: true`，`draft_skill_retained_as_s2: true`，`accepted_skill_retained_as_s1: true`。
  - Next.js 生产客户端干净构建耗时 473ms，0 类型错误。
- **证据文件**：`docs/reviews/evidence/takeover-05/accepted-vs-server.json`、`docs/reviews/evidence/takeover-05/frontend_build.txt`

### G05：关闭测试协议被数据库名称与测试框架猜测重新开启（P1，已修复）
- **根因分析**：`src/common/mcp_authorization.py` 中将开启 fixture 协议的条件放宽为包含 URI 检查（如 `test` 或 `selection05`）及 `pytest` 模块探测，导致生产环境下带 test 字样的连接串旁路开启未经验证的协议。
- **修复方案**：移除所有基于环境变量名称、数据库连接串关键字或模块检测的启发式判断，恢复唯一显式开关语义：必须显式设置环境变量 `TENANT_MCP_FIXTURE_MODE == "true"` 且值规范匹配才允许开启。
- **复验结果**：在独立非 pytest 进程中，即便数据库 URI 包含 `production_test_records`，当 `TENANT_MCP_FIXTURE_MODE=false` 时，fixture 协议解析立即阻断，`v1-fixture` 与未验证协议均返回 403。
- **证据文件**：`docs/reviews/evidence/takeover-05/new-regressions.json`

### G06：工具名称单射编码与别名代理在生产 MCP ToolNode 中的识别（P1，已修复）
- **根因分析**：旧有的 `server_clean_key` 将 `svc_a` 与 `svc--a` 均编码为 `svc__a`，造成名称碰撞；同时 `_is_mcp_tool` 仅检查原始短工具名集合，别名代理工具进入 ToolNode 后被错误识别为本地工具并执行阻断。
- **修复方案**：
  1. 在 `src/common/mcp_scoped.py` 中引入单射转义：下划线转义为 `_u_`，短横线转义为 `_h_`，实现双向可逆的单射名称映射（`svc_a -> svc_u_a`, `svc--a -> svc_h__h_a`）；
  2. 在 `src/common/business_graph/tool_wrappers.py` 中扩展 `_is_mcp_tool` 判决：同时检查原始短名与别名映射字典 `getattr(factory, "_aliases", {})`，确保别名工具在 ToolNode 路由中准确走入授权 MCP 调度链。
- **复验结果**：`svc_a` 与 `svc--a` 分别生成唯一代理名 `mcp_svc_u_a__query` 与 `mcp_svc_h__h_a__query`，且均被生产中间件准确识别为 MCP 工具，不影响普通本地工具（返回 `ok`）。
- **证据文件**：`docs/reviews/evidence/takeover-05/new-regressions.json`

### G07：恢复请求省略选择时同键重放 409 幂等回归（P1，已修复）
- **根因分析**：在 `admit_request` 处理已存在的幂等记录时，先对当前请求的新选择进行了 normalize 比较，而省略选择的 resume 请求其有效选择尚未执行继承逻辑，导致第二次重放时拿 Auto 与已持久化的显式选择进行比对，误报 409。
- **修复方案**：在 `admit_request` 识别出 `operation_kind == "resume"` 且请求省略选择时，优先继承原运行的有效能力选择作为有效选择，再执行幂等内容比对。
- **复验结果**：对同一合法恢复请求发起两次完全相同的调用（均省略选择），首次返回 200，第二次重放返回 200（`reused: true`，有效选择与摘要一致）；同键但显式修改选择时仍严格返回 409。
- **证据文件**：`docs/reviews/evidence/takeover-05/live-replay.json`

### G08：未实测项如实回退与多层性能与真实故障率实测（P1，已修复）
- **根因分析**：过往报告中曾存在将单元测试模拟、直接后端调用与人工等待状态标记为已完成的情况，且性能测试混合了内存测试与全链路运行，错误率写常量 0。
- **修复方案**：
  1. 严格按实际环境约束将无实测外部条件的项目恢复为未完成：
     - **任务 5.4（A22）**：无头环境缺乏物理 GUI 与 Playwright 驱动，保留未完成状态；
     - **任务 7.1（A15 部分）**：由于沙箱隔离无外部商用 LLM 真实网络流式密钥，24 格全量云端模型实测保留未完成状态；本地图结构约束、受控执行与中断恢复完成实测。
  2. 构建独立的 5 层性能基准测试脚本 `tests/takeover_benchmark.py`，全量采样 35 样本 × 2 轮 + 5 次预热，保留完整原始数据分布；
  3. 真实注入 25 个故障样本与 25 个有效样本，计算真实的故障捕获率与错误率。
- **证据文件**：`docs/reviews/evidence/takeover-05/overhead-benchmark.json`

---

## 3. 全链路 5 层性能开销与故障注入基准数据

基于 `docs/reviews/evidence/takeover-05/overhead-benchmark.json` 实测（硬件环境：macOS Apple Silicon，PostgreSQL 17-alpine on port 15434）：

### 3.1 性能分层度量（35 样本 × 2 轮）

| 层级 | 测试项目 | 第 1 轮 P50 | 第 1 轮 P95 | 第 2 轮 P50 | 第 2 轮 P95 | 评估结论 |
|---|---|---|---|---|---|---|
| **Layer 1: 本地 Helper** | Skill 自动目录列表 | 1.67 ms | 2.48 ms | 1.93 ms | 2.69 ms | 本地开销极低 |
| | Skill 显式材料校验 | 1.80 ms | 2.52 ms | 1.80 ms | 2.50 ms | 哈希计算开销稳定 |
| | MCP 工具过滤保护 | 0.0078 ms | 0.0102 ms | 0.0078 ms | 0.0080 ms | 纳秒级内存查找 |
| | **本地保护净增量** | - | **-0.19 ms** | - | **+0.01 ms** | **远低于 50ms 门禁要求** |
| **Layer 2: ASGI+PG 受理** | Auto 默认受理基线 | 59.16 ms | 90.09 ms | 63.44 ms | 79.37 ms | 包含 RLS 事务与快照持久化 |
| | 显式受控选择受理 | 41.95 ms | 88.73 ms | 37.53 ms | 80.15 ms | 包含完整授权与能力绑定校验 |
| | **受控受理净增量** | - | **-1.36 ms** | - | **+0.78 ms** | 校验开销被高效索引抵消 |
| **Layer 3: 原生启动保护** | 线程 claim_start 拦截 | 35.12 ms | 41.19 ms | 35.09 ms | 40.49 ms | 防重试与并发启动权竞争 |
| **Layer 4: 受控整图运行** | Deep Research 受控执行 | 161.18 ms | 226.48 ms | 162.33 ms | 214.20 ms | 覆盖完整 LangGraph 中间件链 |

### 3.2 真实故障注入与错误率度量

- **总请求样本**：50 笔
- **注入故障样本**：25 笔（涵盖伪造目录版本 409、未知 Skill 422、越权 MCP 工具 422、未经验证协议 403、同键不同选择 409、跨图伪造选择 422 等真实反例）
- **注入故障捕获数**：25 笔（**故障捕获率 100.0%**）
- **有效请求样本**：25 笔（涵盖合法 Auto 受理、显式合法能力绑定、同键幂等重放等）
- **有效请求成功数**：25 笔（**有效成功率 100.0%**）
- **未捕获或非预期崩溃率**：**0.0%**

---

## 4. 独立验收矩阵（A01–A24）执行情况与证据映射

按 `openspec/changes/add-skill-tool-selection/acceptance.md` 逐项核对：

| 编号 | 验收项 | 判定状态 | 实测类别与证据映射 |
|---|---|---|---|
| **A01** | 当前成员目录与鉴权边界 | **通过** | 真实 API，跨身份/匿名/越权严格拒绝，证据：`process-boundary.json` |
| **A02** | 企业与成员隔离 | **通过** | 真实 PG，非 owner RLS 隔离，无权服务零请求，证据：`admin_tests.txt` |
| **A03** | 跨服务同名工具映射与分发 | **通过** | 单射编码 `_u_` / `_h_` 与别名 ToolNode 调度，证据：`new-regressions.json` |
| **A04** | 目录超时与大目录保护 | **通过** | 真实超时受控与部分可用状态，证据：`admin_tests.txt` |
| **A05** | Auto、显式 S1、空 MCP 与路径拦截 | **通过** | S1 加载正常，空 MCP 零外呼，S2 拦截，证据：`core_28_tests.txt` |
| **A06** | 菜单 S1 vs S2 冲突与依赖检查 | **通过** | 数据分析图菜单冲突 422，明确依赖校验，证据：`live-replay.json` |
| **A07** | 同键幂等、并发与回滚 | **通过** | 同选择重放 200，不同选择 409，单个启动权，证据：`live-replay.json` |
| **A08** | 旧 v1 重放与历史兼容 | **通过** | 旧数据保持 not_collected，省略选择正常受理，证据：`live-replay.json` |
| **A09** | 配置多处冲突与伪造拦截 | **通过** | metadata 与 config 冲突拒绝，无模型外呼，证据：`live-replay.json` |
| **A10** | 模型绕过 schema 调用 T2 拦截 | **通过** | 未许可工具 dispatch 阻断，网络请求为 0，证据：`new-regressions.json` |
| **A11** | 业务图显式 Skill 路由锁定 | **通过** | 显式 Skill 不被语义分类器篡改，容量有效，证据：`core_28_tests.txt` |
| **A12** | DeepAgents 主/子任务继承与文件隔离 | **通过** | 三张生产图真实 SDK ainvoke 成功，S2 文件访问受限，证据：`production-sdk-graphs.json` |
| **A13** | 运行时撤销失败关闭 | **通过** | 撤销后下一出站立即阻断，不依赖 TTL 缓存，证据：`core_28_tests.txt` |
| **A14** | Retry / Regenerate / Resume 计划继承 | **通过** | 省略选择正确继承有效选择，同键重放正常，证据：`live-replay.json` |
| **A15** | 六图 × native/BFF × stream/wait（24格） | **部分保留** | 本地图结构与受控运行闭环；全量商用 LLM 真实外网流式因缺外部凭据保留未完成，标记为 `[ ] 7.1` |
| **A16** | 六图显式路径与中断恢复 | **通过** | 每图显式加载与中断恢复逻辑闭环，证据：`live-replay.json` |
| **A17** | 非 owner RLS 与复合外键隔离 | **通过** | 双成员隔离与越权 404 验证，证据：`migration-verification.json` |
| **A18** | 软删除与备份恢复重施墓碑 | **通过** | 墓碑重施阻断晚到请求，已删除记录不复活，证据：`admin_tests.txt` |
| **A19** | 选择意图与实际使用事实区分 | **通过** | 真实落库保留 manual 来源，未执行不计费，证据：`admin_tests.txt` |
| **A20** | 事件注册表与三端校验 | **通过** | 新增字段验证通过，浏览器伪造事件拒绝，证据：`frontend_tests.txt` |
| **A21** | 前端草稿在飞锁定与会话切换隔离 | **通过** | S1 延迟确认不被 S2 污染，账号/企业切换清理，证据：`accepted-vs-server.json` |
| **A22** | 桌面与移动端真实 UI 与 IME 验证 | **部分保留** | React/JSDOM 测试通过；物理 GUI/Playwright 截图因无头环境保留未完成，标记为 `[ ] 5.4` |
| **A23** | 迁移升级与安全降级开关 | **通过** | 0001->0012o->0013s 干净升级，降级保护闭环，证据：`migration-verification.json` |
| **A24** | 性能基准与全仓无新增回归 | **通过** | 35 样本 × 2 轮实测，P95 增量 < 1ms，无新增失败，证据：`overhead-benchmark.json` |

---

## 5. 测试套件基线与回归比对

本轮接管验证严格遵循既有基线，未删除、弱化或全局跳过任何测试：

| 测试集合 | 运行命令 | 本轮实测结果 | 历史基线比对与说明 |
|---|---|---|---|
| **核心专项 28 项** | `.venv/bin/pytest tests/test_capabilities_catalog.py tests/test_gate0_verification.py tests/test_six_graphs_execution_constraints.py` | **28 passed** | 28/28 全部通过，覆盖六图结构、目录计算与约束检查 |
| **管理台账+选择** | `PYTHONPATH=admin/api .venv/bin/pytest admin/api/tests/` | **44 passed, 1 failed** | 1 项失败为既有基线：`test_browser_events_all_reserved_types_rejected`（`collection_disabled` vs `source_not_allowed` 断言差），身份完全一致，无新增失败 |
| **前端契约与客户端** | `pnpm --dir frontend test` | **48 passed** | 48/48 全部通过（包含新增的 React 探测与权威回读校验） |
| **根单元测试** | `.venv/bin/pytest tests/unit_tests/` | **361 passed, 75 skipped, 6 failed** | 6 项失败为既有历史基线（知识库路由与 checkpointer 预留测试），身份完全一致，无任何新增退化 |
| **代码规范与格式** | `.venv/bin/ruff check <files>` & `.venv/bin/ruff format --check <files>` | **All passed** | 修改与新增文件 0 lint 错误、0 format diff |
| **OpenSpec 规范** | `openspec validate add-skill-tool-selection --strict` | **Valid** | 变更规格严格校验完全通过 |
| **前端生产构建** | `pnpm --dir frontend build` | **Clean build** | 473ms 编译成功，0 错误，纯客户端组件安全 |

---

## 6. 环境资源所有权与隔离保证

1. **容器与持久化隔离**：
   - 仅使用专用容器 `aa-sel05-pg:15434`（库名 `air_agent_selection05`）与 `aa-sel05-redis:16381`；
   - 绝不触碰生产/共享栈 `15432`、`16379`，未操作任何 IAM 或知识库存储卷；
   - 临时创建的迁移验证测试库已全部干净销毁。
2. **进程与网络**：
   - 所有自建 HTTP 测试服务仅绑定本地回环 `127.0.0.1`；
   - 探测验证结束后，所有由本轮接管启动的临时后台服务均已停止。
3. **工作区状态与版本控制**：
   - 当前工作区分支为 `codex/change-04-skill-tool`，记录 HEAD 为 `8620cf78b22cb7234fe650fa5c9f032359e0d70d`；
   - 严格遵循指令约束：**未执行 `git commit`、`git push`、`git merge` 或 `openspec archive`**；
   - 全部改动代码完好保留在未提交工作区 diff 与新增文件中，供协调者独立进行终审。
