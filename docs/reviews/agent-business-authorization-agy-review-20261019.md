# Agent 业务授权联合变更 AGY 独立验收审查报告

- **审查者**：AGY（独立第二意见评审者，非实现作者）
- **复核者**：Codex（最终决策与归档复核）
- **审查日期**：2026-10-19
- **审查对象**：
  - Agent 仓库：`/Users/acerola/Dev/Python/air_agent`（dev 分支，HEAD `6ff95dd`，change `simplify-agent-business-authorization`）
  - IAM 仓库：`/Users/acerola/Dev/Java/air_agent_iam`（dev 分支，HEAD `630b865`，change `add-agent-business-entitlements`）
- **共同契约**：两仓 `business-authorization-contract.md`（SHA-256：`d0801cd88f13e6159f7206f9f8cf4aafaa1900e1ee82f251ab7a20db18076bca` 一致）
- **原始证据目录**：`docs/reviews/evidence/run/agy-business-acceptance-20261019/`

---

## Verdict

**COMMENT（建议由 Codex 本地核实 5 项关键事实后准予交付）**

两仓产品核心设计与安全准入架构实现完备、边界严密：
1. 业务准入模型严格实现了“企业有效开通 ∩ 本企业有效角色授权并集”，无通配符或 root 旁路；
2. 运行期逐发送复查完整绑定了服务端冻结的业务许可快照与执行引用；
3. MCP 工具采用双白名单硬约束，取消成员级 RBAC 后无匿名降级；
4. 数据库升级与物理清理（0022 迁移移除旧表、IAM 清除 94 条旧授权边）按共同契约完成且未生成备份残留。

之所以给出 **COMMENT** 而非无条件 APPROVE，主要基于以下需要 Codex 在本地正式运行环境中最终核实的 5 项事实与跨仓管理差异（均非代码阻断性缺陷）：
- **[核查点 1] 跨仓任务勾选漂移**：Agent 29 项任务全勾，而 IAM `tasks.md` 中 6.1/6.2/6.3 仍保持未勾选 `[ ]`。这是由于两阶段实施交付与联合发布的交接时差导致，并非功能缺陷；
- **[核查点 2] root 登录与口令策略一致性**：IAM 交付报告早期称 4 字符口令被 8..256 策略阻塞，而 Agent 协调发布在 19:01 通过口令文件驱动 `verify-root-entitlements.py` 成功读回 4 企业 200 OK。19100 进程实际为 10-18 编译的上一版 jar，其登录代码自初次提交即包含 8..256 检查。需 Codex 在本地脱敏核对正式 root 口令实际长度及哈希未改动事实；
- **[核查点 3] 长运行中断与重试记录**：Agent 真实长运行中断（resume）未在正式三图中构造实例，任务 2.6 勾选依靠管理端持久化测试与原生预留校验覆盖；且 12 矩阵中有 3 项 data-analysis 因模型整理失败执行了重试（已如实记录于报告限制中）；
- **[核查点 4] 测试替换实质覆盖**：7 个旧测试文件的删除主要是由于旧 `mcp_identity_bindings` 物理退役，新测试套件与契约测试已实质接替安全与隔离覆盖，但存在 4 个文件的 `ruff format` 格式漂移；
- **[核查点 5] 清理核对**：根据发布状态与检查点，旧表与旧授权边已清理完毕。由于 AGY 受限不直连正式库，报告中已提供 Codex 本地专用只读 SQL 进行最终核对。

---

## Findings

按严重级别排序如下：

### [MEDIUM] F-01: IAM 任务清单 tasks.md 存在跨仓交接未回填的未勾选状态

- **文件与行号**：
  - `/Users/acerola/Dev/Java/air_agent_iam/openspec/changes/add-agent-business-entitlements/tasks.md#L40-L42`
  - `/Users/acerola/Dev/Python/air_agent/openspec/changes/simplify-agent-business-authorization/tasks.md#L45-L48`
- **触发序列**：
  1. 17:31（Asia/Shanghai），IAM 作者完成阶段一交付并提交 `agent-business-entitlements-delivery-20261019.md`，因等待 Agent 侧就绪和联合清理，将任务 6.1（读回部分）、6.2、6.3 标记为未完成（`[ ]`）；
  2. 18:46–19:46，Agent 发布协调者执行 `deployment/run_business_release.py`，成功完成了 `root-verify`（19:01）、12 项运行矩阵验证（19:10–19:40）及 `iam-cleanup`（19:45）；
  3. Agent 侧 `tasks.md` 6.1–6.6 全部勾选为 `[x]`，但 IAM 侧 `tasks.md` 在 17:24 之后未被修改回填，保持未勾选。
- **证据**：
  - IAM `tasks.md`：
    ```markdown
    - [ ] 6.1 完成独立测试后，对19132/iam_identity核对实际身份并执行现有root正式授权...
    - [ ] 6.2 与Agent协调者验证全部当前业务真实运行及root Agent数据查看...
    - [ ] 6.3 Agent切换新模型且删除其旧表后，实际执行IAM残留清理并重启复验...
    ```
  - Agent `checkpoint.json` 与发布日志证明上述步骤实际已由脚本成功执行。
- **影响**：
  两仓任务追踪状态漂移。若直接归档，可能在 IAM 侧触发 pre-commit 或 OpenSpec archived tasks 检查失败。
- **建议处理**：由 Codex 审查本报告后，在 IAM 侧统一回填 6.1/6.2/6.3 的最终完成状态及证据索引。

---

### [LOW] F-02: Agent 任务 2.6 勾选全覆盖，但正式长运行中断（resume）未在实际图上构造

- **文件与行号**：
  - `/Users/acerola/Dev/Python/air_agent/openspec/changes/simplify-agent-business-authorization/tasks.md#L18`
  - `/Users/acerola/Dev/Python/air_agent/docs/reviews/agent-business-authorization-acceptance-20261019.md#L102-L103`
- **触发序列**：
  - 任务 2.6 声明：`覆盖resume/重新生成/旧运行缺快照处理，验证不可换业务...` 并勾选 `[x]`；
  - 报告第 6 节如实承认：`resume 真实长运行未构造：当前三张公开图没有可用的真实中断样例；resume/重新生成/旧快照缺失由管理端选择持久化测试与 native create_run 预留校验覆盖，未伪造中断运行。`
- **证据**：
  - 单元与集成测试 `test_ledger_selection_persistence.py` 覆盖了 `_assert_source_business` 拒绝更换业务（`business_snapshot_missing` / `business_snapshot_changed`）；
  - `langgraph_auth.py` 覆盖了 `claim_start` 与预留校验；
  - 但生产真实 LangGraph 运行中未实际中断并 resume 一个真实的长时间节点。
- **影响**：
  对真实业务中断恢复的验证仅达到集成测试/受控模拟级别，未达到端到端长周期中断的物理验证。不过作者并未隐瞒，在验收报告限制项中已如实说明。
- **建议处理**：保持报告记录，后续待有真实人机交互中断业务时再做物理长运行验证。

---

### [LOW] F-03: 数据分析（data-analysis）真实运行存在首答整理失败与重试

- **文件与行号**：
  - `/Users/acerola/Dev/Python/air_agent/docs/reviews/agent-business-authorization-acceptance-20261019.md#L45`
  - `/Users/acerola/Dev/Python/air_agent/docs/reviews/agent-business-authorization-acceptance-20261019.md#L97-L98`
- **触发序列**：
  - 在正式 root 12 项运行矩阵测试中，`data-analysis` 在 BFF stream、native stream、native wait 3 项请求的首轮回答为“分析结果已生成，但最终答案整理未完成”；
  - 随后采用相同输入重试，耗时 15.9s–18.3s 成功返回完整答案。
- **证据**：
  - 报告明确指出首轮失败是 LLM 输出格式整理失败，并非授权系统（403/401/409）拒绝；
  - 原始失败记录保留在 `root-matrix.jsonl`，重试成功记录在 `root-matrix-retry.jsonl`。
- **影响**：
  展示了数据分析业务在大模型最终总结步骤存在偶发不稳定性，但不属于业务授权与访问控制机制的问题。
- **建议处理**：保持原始失败记录，不掩盖重试事实，符合验收准则。

---

### [NIT] F-04: Agent 仓库 4 个文件存在 Ruff 代码格式漂移

- **文件与行号**：
  - `admin/api/migrations/versions/0021_business_authorization.py`
  - `admin/api/migrations/versions/0022_retire_mcp_bindings.py`
  - `tests/unit_tests/test_mcp_connection_execution.py`
  - `tests/unit_tests/test_mcp_connections.py`
- **触发序列**：
  - 执行 `.venv/bin/ruff format --check <受影响文件>`；
  - 提示 `4 files would be reformatted, 11 files already formatted`。
- **证据**：
  - `docs/reviews/evidence/run/agy-business-acceptance-20261019/ruff-format-check.txt`。
- **影响**：
  代码风格与项目 Ruff 配置存在轻微空白/换行差异，不影响逻辑运行（`ruff check` 代码检查为 100% 通过）。
- **建议处理**：交付前由 Codex 或作者统一运行 `ruff format` 格式化。

---

## 预设 5 项核查点逐项分析

### 1. 任务清单差异（Agent 29 项全勾 vs IAM 6.1/6.2/6.3 未勾选）

- **核实结论**：**非产品缺陷，为两阶段实施交接证据未回填**。
- **事实链路**：
  - IAM 独立交付报告于 17:31 产出，此时 Agent 尚未进行联合模型运行与清理，IAM 作者严格按照边界将联合验收标记为未完成（`[ ]`），属于诚实验收行为；
  - Agent 协调脚本于 18:46–19:46 统一启动执行，调用了 IAM 提供的 `scripts/verify-root-entitlements.py`、`scripts/iam-final-cleanup.sh --apply` 和 `scripts/iam-ops.sh verify`，并完成了 root 数据查看及 12 图矩阵；
  - 执行事实在 Agent `checkpoint.json` 及两仓证据日志中有完整记录；IAM `tasks.md` 仅缺少最终对勾的回填。

### 2. root 口令策略阻塞与 formal-root-readback.json 证据核验

- **核实结论**：**证据真实生成，运行进程与代码逻辑完全吻合；建议 Codex 最终比对口令长度**。
- **事实链路**：
  1. **运行进程版本**：通过 `ps -o pid,lstart,command -p 98956` 查明，当前监听 19100 的 Java 进程启动于 `Sun Oct 18 22:28:41 2026`，执行的正是上一版 jar（`/Users/acerola/Dev/Java/air_agent_iam/server/target/iam-server.jar`）。这与 IAM 报告第 4 节声明“19100 当前仍运行上一版 jar，新代码在数据库生效”完全一致；
  2. **口令校验逻辑**：`SessionServiceImpl.java:718` 中的 `password.length() < 8` 校验自初次提交 `a4955ac` 起就已存在，所有打到 `/v1/auth/login` 的短于 8 位的口令必然在进入数据库前返回 400 `password 不合法`；
  3. **证据产生过程**：
     - 17:30 产出的 `formal-root-readback-blocked.json`（357 字节）：IAM 作者测试时可能使用了简短测试口令（如 account 名 `"root"`，4 字符），触发 400 校验被拦截；
     - 19:01 产出的 `formal-root-readback.json`（9287 字节）：Agent 发布脚本 `run_business_release.py` 在 `root_verify` 步骤中，从 `.local/i4-live/root-password` 读取了实际的 root 口令，调用 `verify-root-entitlements.py` 发送至 19100。该口令成功通过了 19100 的 8..256 检查，并完成了 4 个 active 企业的登录、切企业与 `/v1/auth/me` 读回，返回 200 OK；
  4. **密码未被篡改**：IAM 报告第 3.2 节确认 root UUID、`auth_revision=7`、口令 BCrypt 摘要在配置前后完全一致。
  5. **核查指引**：AGY 受权限约束禁止读取真实口令或查看密钥文件。已在 `docs/reviews/evidence/run/agy-business-acceptance-20261019/read-only-queries-for-codex.sql` 准备好查询 2.8，由 Codex 在本地比对数据库内密码哈希长度与真实口令长度是否满足 >= 8。

### 3. 12 项运行矩阵与 resume 长运行覆盖

- **核实结论**：**业务授权有效放行，重试事实与限制项记录诚实完整**。
- **事实链路**：
  - 12 项请求全部获得 HTTP 200，并产生真实模型调用与日志台账；
  - 3 项 `data-analysis` 首次因最终答案排版失败返回提示文本，重试后返回有效业务数据。作者在报告第 3 节与第 6 节如实标明原始失败，未伪造成一次通过；
  - 针对长运行 `resume`，由于当前 3 张公开图未引入人工审批暂停节点，作者通过管理端状态持久化测试（`test_task_3_4_delivery_switch_fallback`、`test_ledger_selection_persistence.py`）及原生 `create_run` 预留快照校验进行逻辑覆盖，未在生产图上伪造中断。

### 4. 旧测试删除与新测试替代覆盖

- **核实结论**：**测试删除为物理清理必然结果，新测试实质接替关键安全契约**。
- **事实链路**：
  - 被删除的 7 个测试文件包括：
    - `admin/api/tests/test_observability_mcp_live.py`
    - `tests/test_capabilities_catalog.py`
    - `tests/test_gate0_verification.py`
    - `tests/test_public_graphs_execution_constraints.py`
    - `tests/unit_tests/test_mcp_client.py`
    - `tests/unit_tests/test_mcp_outbound_send_guard.py`
    - `tests/unit_tests/test_mcp_scoped_authorization.py`
  - 删除原因分析：上述测试中大量包含对旧表 `mcp_identity_bindings` 的直接 SQL 插入、更新（如 `status='disabled'`）以及依赖旧目录结构的代码。在迁移 0022 彻底删除该表后，这些测试已不具备物理执行条件；
  - 实质替代覆盖：
    - 新增 `test_business_access.py`（8 项测试）：覆盖业务精确求交、未授权拒绝、租户缺失、平台标志不绕过、运行快照冻结与篡改拦截；
    - 新增 `test_business_registry.py`（7 项测试）：覆盖 3 图业务目录自洽性、精确权限格式、双白名单工具作用域、无秘密摘要；
    - 新增 `test_mcp_connections.py`（8 项测试）：覆盖连接配置严格校验、共享只读与企业连接隔离、禁止匿名兜底、URL 防探针与歧义拒绝；
    - 新增 `test_mcp_connection_execution.py`（9 项测试）：覆盖真实 HTTP 屏障下未配置服务拦截（网络增量为 0）、缺凭据拦截、未登记工具拦截、A/B 租户缓存隔离、连接轮换与用户空工具集硬拦截；
    - 结合前端 63 项契约测试（`npm run test:contracts`），核心安全保证（如撤权后网络调用为 0、同名服务隔离、无凭据不兜底）得到了实质强化而非弱化。

### 5. 清理执行只读核对

- **核实结论**：**发布检查点与执行日志证明清理已生效，未发现备份残留**。
- **事实链路**：
  - 检查点 `checkpoint.json` 显示：
    - `alembic_version`: `0022_retire_mcp_bindings`；
    - `old_table_present`: `false`，`old_table_rows`: 0；
    - `backup_objects`: `[]`；
    - `forbidden_files`: `[]`；
    - `iam-cleanup`: 执行返回码 0，清理后复验通过，旧码与授权边为 0；
  - 提供了 `read-only-queries-for-codex.sql` 供 Codex 本地直连 15432 与 19132 进行只读核实。

---

## 规格覆盖矩阵

| OpenSpec 需求 / 场景 | 规格出处 | 验证方式与证据 | 结果 |
|---|---|---|---|
| **稳定业务目录与可信执行映射**（改名/新增图、借获准业务启动另一图拒绝） | Agent `agent-business-access` | 单元测试 `test_business_registry.py`、`test_business_access.py`、`native-negative.txt`（409/403） | **PASS** |
| **IAM 实时业务资格**（企业开通角色未授403、同账号跨企业隔离） | Agent `agent-business-access` / IAM `agent-business-entitlements` | IAM 单元测试 `entitlementAndTenantRoleIntersectionWithCrossTenantIsolation`、`AgentBusinessEntitlementIntegrationTest` | **PASS** |
| **全入口受理与恢复检查**（直连原生/BFF统一守卫、防伪造快照） | Agent `agent-business-access` | `test_ledger_selection_persistence.py`、`langgraph_auth.py` 预留比对 | **PASS** |
| **每次发送前复查及内部委派**（逐发送复查、长运行撤权阻断、增量为0） | Agent `agent-business-access` | `test_mcp_outbound.py`、`inflight-revocation-after-fix.txt`、`mcp_identity_scope` | **PASS** |
| **失败关闭与可解释状态**（401/403/409/503、前端中文提示） | Agent `agent-business-access` / IAM `agent-business-entitlements` | 前端契约测试（63项全过）、`Vue` 业务选择与状态展示测试（28项全过） | **PASS** |
| **root 实际测试权限与数据边界**（4企业开通、运营明细/指标/受控正文查看200） | Agent `agent-business-access` / IAM `agent-root-test-access` | `formal-root-readback.json`、`root-agent-permissions.txt`、`root-data-view.txt` | **PASS** |
| **服务连接独立于人员授权**（无旧绑定正常查询、缺连接明确报错） | Agent `agent-service-connections` | `test_mcp_connections.py`、`test_mcp_connection_execution.py`、109 项生产真实查询 | **PASS** |
| **真实认证与共享只读模式**（共享只读不伪造Bearer/租户、禁止写操作） | Agent `agent-service-connections` | `test_mcp_connections.py:74-85`、`IPP_APPROVED_QUERY_TOOLS` 限制 | **PASS** |
| **业务范围与用户选择共同约束**（直接MCP、Skill后MCP、空工具集网络为0） | Agent `agent-service-connections` | `test_mcp_connection_execution.py:305`、`test_deep_explicit_skill.py` | **PASS** |
| **连接缓存与秘密隔离**（A/B隔离、连接轮换重新发现、秘密不入日志） | Agent `agent-service-connections` | `test_mcp_connection_execution.py:218,239`、`secret_scan.py`（772路径0泄露） | **PASS** |
| **旧授权物理退役与无备份交付**（0022删表、IAM清94边、无dump/快照/影子库） | Agent `agent-service-connections` / IAM `agent-root-test-access` | 迁移 `0022`、`iam-final-cleanup.sh`、`release-status-dump.txt` | **PASS** |
| **外部 MCP 范围如实声明**（不宣称外部企业全数据隔离已完成） | 两仓共同契约 Section 3/6 | 两仓交付报告第 6 节明确标明边界与未完成项 | **PASS** |

---

## What I could not verify（必须明确项）

受限于本轮独立评审的安全与授权边界，以下项目 AGY 未在本次执行中直接读取或操作，需由 Codex 本地复核：

1. **正式数据库内部数据的直接只读查询**：
   - AGY 未直接使用凭据连接正式业务库 `127.0.0.1:15432/air_agent_business` 或正式身份库 `127.0.0.1:19132/iam_identity`；
   - 依赖证据为发布协调器产生的脱敏 `checkpoint.json`、控制台日志及作者脱敏报告。Codex 需执行 `read-only-queries-for-codex.sql` 完成本地直读核对。
2. **私有配置、真实令牌与密码明文**：
   - AGY 未读取 `.env`、`.local/i4-live/credentials.md`、`root-password` 或真实用户的会话密钥；
   - root 登录 8..256 策略的最终吻合性依赖于 19100 代码审查与 `formal-root-readback.json` 的执行结果，未直接提取 root 口令字符串。
3. **外部真实 MCP 服务端的多租户数据隔离机制**：
   - 如两仓契约和报告所述，外部 MCP 接口（https://www.mapairs.com/product/datacenter/api2/mcp）仅验证了 109 项工具的只读查询能力，无法验证外部服务端内部的数据隔离实现。此项已作为已知边界被正确接受，不属于本系统缺陷。
4. **真实长时间中断会话的人工触发与恢复（Resume）**：
   - 公开三图当前未配置长时间人机交互挂起节点，未能触发真实生产运行的暂停与恢复，仅通过模拟测试验证了业务快照防篡改与匹配规则。

---

## 建议的最小补修与交接方向

1. **[Codex 执行] 回填 IAM `tasks.md` 勾选状态**：
   - 将 IAM `openspec/changes/add-agent-business-entitlements/tasks.md` 中的 6.1、6.2、6.3 回填为 `[x]`，并引用联合发布的检查点与证据文件，消除两仓任务状态漂移。
2. **[Codex 执行] 执行本地只读 SQL 复核**：
   - 运行 `docs/reviews/evidence/run/agy-business-acceptance-20261019/read-only-queries-for-codex.sql`，确认 15432 库中 `alembic_version=0022_retire_mcp_bindings`、`mcp_identity_bindings` 为空，以及 19132 库中旧 MCP 权限码与关联边为 0。
3. **[Codex 执行] 运行 Ruff Format**：
   - 针对 Agent 4 个文件执行 `ruff format`，消除代码格式漂移。
4. **[后续规划] 推进外部 MCP 企业数据隔离协议与真实长运行中断验证**：
   - 纳入后续专项，本阶段保持当前稳定共享只读接入边界。

---

## 结论汇总

本轮联合独立验收确认：两仓在业务授权简化、统一准入守卫、MCP 部署连接化、root 显式授权及无备份物理清理方面的实现与共同契约高度一致。在 Codex 本地核实上述 SQL 输出并回填 IAM 任务清单后，**建议批准本阶段交付并进入规格同步与归档流程**。
