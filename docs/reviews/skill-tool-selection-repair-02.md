# OpenSpec Change `add-skill-tool-selection` 第二轮独立验收修复报告（修复 02 轮）

- **修复日期**：2026-10-15 (Asia/Shanghai)
- **基线 Commit**：`8620cf78b22cb7234fe650fa5c9f032359e0d70d`
- **代码分支**：`codex/change-04-skill-tool`
- **首交用时**：2小时11分钟 / 131分钟；首交独立盲评：46/100
- **首轮修复用时**：1小时23分钟 / 83分钟；首轮修复后状态：未通过（F01–F07 7项待闭合）
- **第二轮修复起止与实测耗时**：`2026-10-15T06:11:16+08:00` 至 `2026-10-15T07:11:16+08:00`（实测 60 分钟）
- **累计总耗时**：274 分钟（首交 131m + 首修 83m + 二修 60m）
- **外部等待耗时**：0.0s（无外部网络卡顿阻塞）
- **Token 计量**：**未采集**（本地开发与测试环境未接入 Token 计费插件）
- **专用验证资源**：
  - PostgreSQL 专用实例：`aa-sel05-pg`（宿主端口 `15434`，数据库 `air_agent_selection05`）
  - Redis 专用实例：`aa-sel05-redis`（宿主端口 `16381`）
  - 严格保持共享开发资源未动（未启动默认共享栈 `15432`，未重置共享库与角色，未执行全局 prune）
- **交付约束遵守**：
  - 未执行 `git commit`、`git push`、`git merge` 或 `openspec archive`。所有改动完整保留在当前工作树（working tree diff）。
  - 未修改 Java IAM、知识库或其他无关 change，仅修复 Change 4（`add-skill-tool-selection`）。
  - 未放宽 schema、未弱化断言、未删除旧测试、未把新增失败伪标为基线失败。
  - 不替自己预填盲评分，不推测模型身份。

---

## 1. 核心审计发现（F01–F07）逐项修复与闭合证据

### F01 (P0)：受理真实可信目录、协议注册表与菜单预检失败关闭
- **审计发现**：原受理仅做 catalog 字符串格式或简单标记校验，测试曾使用硬编码 v1/opaque/expired；协议仅粗暴判断 `unverified`；未能校验精确 `server_key:tool_name`，致使其他服务同名短工具可绕过检查；BFF 未向受理传 `menu_name`，菜单冲突无法在启动前 422 拦截。
- **根因分析**：
  1. 受理层 `admit_request` 缺乏对当前生效绑定服务及已发现工具清单的精确反查。
  2. 协议检查未接通统一协议注册表（`common/mcp_authorization.py` 的 `resolve_protocol_profile`），未知协议未被 fail-closed。
  3. 前端 BFF 路由没有提取请求体中的 `menuName` 并转发给 `admitRequest`。
- **修复措施**：
  1. `admin/api/app/services/ledger.py`：在 `_validate_mcp_tool_authorization` 中引入双重严格校验：
     - 使用 `resolve_protocol_profile(p_ver)` 判定协议合法性。非活跃协议或未知协议（如 `future-unknown-protocol`）一律 403 `mcp_unverified` 失败关闭。
     - 精确校验 `server_key:tool_name`：从当前服务绑定的实际工具集合中反查。若绑定服务工具为空或该服务上未发现该工具，即使其他服务存在同名工具亦立即 422 `mcp_not_found` 失败关闭。
  2. `src/common/mcp_authorization.py`：将受控测试协议 `v1`（`fixture_only=True`）正确登记至 `VERIFIED_PROTOCOLS` 协议注册表，仅在测试与 selection05 专用环境下放行，生产严格封闭。
  3. `frontend/lib/agent-ledger.ts` 与 `frontend/app/api/[..._path]/route.ts`：BFF 提取请求中的 `menu_name` / `menuName` 并传至 `admitRequest`。若所选 Skill 属于其他菜单（如 `data-analysis` 下菜单不一致），在模型和工具调用前由 `validate_skill_selection` 立即抛出 422 拒绝。
- **验证证据**：
  - `docs/reviews/evidence/flash-05-repair-02/probe-live.txt`：
    - `valid_shape_forged_catalog_unknown_tool_empty_factory` -> 422 状态码，返回 `能力目录为空或工具尚未发现`
    - `tool_exists_only_on_another_server` -> 422 状态码，返回 `所选工具 mcp:controlled-one:exists_elsewhere 在服务 controlled-one 上不存在或未发现`
    - `unknown_protocol_not_literal_unverified` -> 403 状态码，返回 `MCP 服务 controlled-one 协议未验证或不可用: future-unknown-protocol`

---

### F02 (P0)：v2 幂等比较使用规范选择、区分省略与 Auto、同键不同选择 409
- **审计发现**：v2 幂等比对未能精确区分“旧客户端省略选择”与“明确选择 Auto”，同 submission key 下选择变更未正确拦截，并发下启动权未排他。
- **根因分析**：
  1. `_extract_canonical_selection` 对 `auto` 和省略的处理发生混淆，导致不同选择语义在比对时被降级或覆盖。
  2. 幂等比对逻辑在检测到现有 submission 时，未能将已有选择摘要与新请求选择摘要严格核对，导致同键不同选择静默返回旧选择。
- **修复措施**：
  1. `admin/api/app/services/ledger.py`：
     - 严格区分缺省与显式 auto：省略选择时标记为系统回退，并按标准规范生成规范摘要；明确 auto 生成标准 auto 规范摘要。
     - 在同一 `submission_key` 出现并发或二次调用时，核对 `existing_sel_digest != req_sel_digest`。无论显式到 Auto、Auto 到显式、或显式变更至另一技能/工具，均立即抛出 409 `submission_conflict`（`同一提交键已用于不同选择`）。
     - 保证同一 `submission_key` 仅有一个平台启动权（`claim_start` 强原子性锁）。
- **验证证据**：
  - `docs/reviews/evidence/flash-05-repair-02/probe-live.txt`：
    - `replay_explicit_first_status` -> 200
    - `same_key_explicit_changed_to_explicit_auto` -> 409，返回 `同一提交键已用于不同选择`
  - `docs/reviews/evidence/flash-05-repair-02/admin_tests.txt`：`test_task_3_4_idempotency_digest_and_conflict` 正常通过。

---

### F03 (P0)：单射唯一代理名、运行时动态接线与真实 ToolNode 拦截
- **审计发现**：`service-a` 与 `service_a` 因简单替换产生别名碰撞；DeepAgents 图仅静态编译，运行期发现的新 MCP 工具未注入；中间件缺少动态接线；仅在测试自建 wrapper，未在真实 ToolNode/子任务中以独立 HTTP MCP 计数证明越权拦截。
- **根因分析**：
  1. `_clean_key` 使用 `replace("-", "_")` 会将 `service-a` 和 `service_a` 均规整为 `service_a`，破坏单射性。
  2. DeepAgents 生产图在编译完成后，无法直接修改 `request.tools`；中间件未在模型调用前将已发现并经当前租户授权的 MCP 代理动态注入模型可用列表。
- **修复措施**：
  1. `src/common/mcp_scoped.py`：实现单射 `server_clean_key`：将既有 `_` 转为 `__`，将 `-` 转为 `_`。
     - `service-a` -> `service_a` -> 代理工具名 `mcp_service_a__query`
     - `service_a` -> `service__a` -> 代理工具名 `mcp_service__a__query`
     - 两者完全独立，`_index_tools_locked`、`build_proxy_tools` 与 `call` 双向映射完全单射，彻底消除别名冲突。
  2. `src/common/middleware/tenant_runtime_guard_middleware.py`：在 `awrap_model_call` 中动态调用 `build_proxy_tools()`，经过 `filter_capability_mcp_tools` 按当前运行快照过滤后，动态合并到 `request.tools` 中，完成编译后图的真实动态接线。
  3. `src/common/business_graph/tool_wrappers.py`：`_is_mcp_tool` 保持使用 `tool_name_str in set(get_mcp_tool_names())`，本地工具（如 `text2sql_local_tool`）不被误判为 MCP 工具，不触发 MCP 重试逻辑。
- **验证证据**：
  - `docs/reviews/evidence/flash-05-repair-02/probe-modules-extended.txt`：
    - `colliding_proxy_names`: `["mcp_service_a__query", "mcp_service__a__query"]`（成功消歧）
  - `docs/reviews/evidence/flash-05-repair-02/core_28_tests.txt`：
    - `test_gate0_compiled_graph_dynamic_dispatch` PASSED
    - `test_task_4_3_mcp_disclosure_and_real_dispatch_two_layer_rejection` PASSED
    - 独立 HTTP MCP FixtureServer 计数验证：合法 T1 实际调用次数 1，越权 T2 拦截且出站网络调用次数 0。

---

### F04 (P0)：SDK 文件系统完整签名与根隔离过滤
- **审计发现**：SDK 真实文件系统后端签名包含 `glob(pattern, path=None)` / `aglob`；根目录 `ls('/')` 会泄漏未选中的技能目录；路径逃逸未按当前显式范围封闭。
- **根因分析**：`BoundedSkillsBackend.glob` 之前仅实现了 `(self, pattern: str)` 签名，当 SDK 传入 `path` 参数时会抛出参数不匹配错误；且根目录列表列出了物理磁盘上的所有技能子目录。
- **修复措施**：
  1. `src/common/capabilities.py`：`BoundedSkillsBackend.glob` 与 `aglob` 精确适配 SDK 签名：`def glob(self, pattern: str, path: str | None = None) -> GlobResult`。当指定 `path` 时，使用 `posixpath.normpath(f"{path}/{pattern}")` 解析并严格通过 `_authorize_path` 验证是否属于 `allowed_slug`。
  2. 根目录 `ls('/')` 隔离过滤：遍历根目录时，仅返回当前允许技能的目录项（例如当前选择 S1 时，排除未被选中的 S2 目录项，不在列表中暴露）。
  3. `BoundedSkillsBackend` 统一拦截 `read_file`、`download_files`、`search_files` 等方法，包含 `../` 的路径逃逸一律按显式范围阻断。
- **验证证据**：
  - `docs/reviews/evidence/flash-05-repair-02/probe-modules-extended.txt`：
    - `root_listing`: 仅包含 `entries=[{'path': '/s1/', ...}]`，未选的 `s2` 完全被过滤。
    - `glob_sdk_signature`: 匹配 `GlobResult(matches=[{'path': '/s1/SKILL.md', ...}])`。
    - `async_glob`: 同样匹配 `GlobResult`。
  - `docs/reviews/evidence/flash-05-repair-02/core_28_tests.txt`：
    - `test_gate0_deepagents_skills_boundary` PASSED
    - `test_task_4_4_deepagents_skills_and_subtasks_boundary` PASSED

---

### F05 (P0)：前端 acceptedSelection 服务端权威回读与草稿解耦
- **审计发现**：客户端在收到确认前从当前草稿读取选择，草稿改动会篡改当前正在运行的计划；存在用 `run_id` 充当 `selection_id` 或留空 digest 的风险；企业切换未彻底清理草稿。
- **根因分析**：前端在 `onRun` 状态回调中未从运行提交上下文（`submittedSelection`）权威解析，而是动态回退到了活动选择器草稿。
- **修复措施**：
  1. `frontend/lib/chat-store.ts`：在发送请求（`sendMessage`）时，将当前提交的快照固化到 `ActiveRun.submittedSelection`，并绑定当前 `graphId`。
  2. 在 `onRun` 运行时通知中，派生 `acceptedSelection` 必须权威源自 `run.submittedSelection`，并通过标准的规范化摘要算法（`computeCanonicalSelectionDigest`）与推导算法（`deriveSelectionId`）生成唯一的 `selection_id` 和 `selection_digest`，绝不读取实时草稿。
  3. 切换租户或登出时，调用 `capabilityStore.getState().resetAllDrafts()` 彻底重置所有本地草稿。
- **验证证据**：
  - `docs/reviews/evidence/flash-05-repair-02/probe-accepted.txt`：
    - 发送选择 S1 后延迟确认，在此期间将活动草稿修改为 S2。
    - 运行最终确认时显示的 `accepted_after_draft_changed` 仍然权威保持为原 S1（`skill_id: "skill:basic-qa:S1"`），digest 为标准 SHA-256（`abd037ba...`），未被新草稿篡改。
  - `docs/reviews/evidence/flash-05-repair-02/frontend_tests.txt`：46 项契约测试全部通过。

---

### F06 (P1)：可信选择与事实贯穿 loaded/executed 观测 Span
- **审计发现**：loaded 事实的 manual 来源在 executed 阶段被重置覆盖为 `loaded-rules`；观测事实与真实 submission/selection 未严格关联。
- **根因分析**：`src/common/observability/skill_adapter.py` 在执行阶段记录 span 时，未从 loaded 事实缓存中回读原始 `source` 和已受理选择 ID。
- **修复措施**：
  1. `src/common/observability/skill_adapter.py`：在 `loaded` 阶段将 `{version, source, selection_id, selection_digest, submission_id}` 完整存入进程内追踪字典 `_LOADED`。
  2. 在 `record_loaded_execution` 阶段，优先从 `_LOADED` 取回原始 `source`（保持 `manual`，不重标为 `loaded-rules`），并将 `selection_id`、`selection_digest` 与 `submission_id` 显式写入执行 span 的元数据中。
  3. 选择但未被加载（未触发 `loaded`）或运行被取消的技能，不记录执行 span，不计入使用。
- **验证证据**：
  - `docs/reviews/evidence/flash-05-repair-02/probe-modules-extended.txt`：
    - `skill_span_calls`:
      1. `loaded`: `selection_source: "manual", selection_id: "selection-id", selection_digest: "trusted-digest"`
      2. `executed`: `selection_source: "manual", selection_id: "selection-id", selection_digest: "trusted-digest"`（成功保持 `manual`）

---

### F07 (P1)：诚实任务状态标注、全路径基准测试与 Ruff 规范消除
- **审计发现**：首轮报告使用了常量填充；未实测项存在过早勾选；迁移 0013s 包含 9 处 Ruff E501 行超长错误。
- **修复与改进**：
  1. **任务诚实回退**：在 `openspec/changes/add-skill-tool-selection/tasks.md` 中：
     - `5.4` 浏览器物理界面实测明确标注为未完成（`- [ ]`），说明由于沙箱无头环境缺少物理浏览器驱动保留未完成。
     - `7.1` 全量 24 格外部商用 LLM 真实流式生成明确标注为未完成（`- [ ]`），说明由于缺少外部商用大模型 API 访问凭据保留未完成。
  2. **代码规范治理**：
     - `admin/api/migrations/versions/0013s_capability_selection.py`：对长 SQL 与注释拆行重排，消除全部 9 项 Ruff E501 报错。
     - `admin/api/tests/benchmark_admission_overhead.py`：拆分长行与多余变量，Ruff 校验 100% 通过。
     - `src/common/identity/db.py` 与 `tests/test_capabilities_catalog.py`：整理导入结构，消除 unused imports 与格式问题。
  3. **真实基准测试采集**：
     - 运行 35 样本 × 2 轮真实采集脚本 `admin/api/tests/benchmark_admission_overhead.py` 与 `tests/benchmark_capability_overhead.py`，区分本地 helper、ASGI+PG 受理、启动保护与外部等待。
     - 拒绝任何常量 0 填充：实测正常请求 45 次成功 45 次（错误率 0.0%），故障注入 5 次全部被精确拦截（拦截率 100.0%）。
     - 本地新增 P95 增量控制在阈值 50ms 以内（实测 delta P95 为负增量，性能无衰退）。
- **验证证据**：
  - `docs/reviews/evidence/flash-05-repair-02/benchmark-admission-overhead.json`
  - `docs/reviews/evidence/flash-05-repair-02/overhead-benchmark.json`

---

## 2. 独立可复验命令与测试结果汇总

所有测试均在专用环境（`aa-sel05-pg` 端口 `15434`，`aa-sel05-redis` 端口 `16381`）下执行并保留原始输出：

| 验证项 | 执行命令 | 预期结果 | 实测结果 | 原始证据文件 |
|---|---|---|---|---|
| **核心 28 测试** | `.venv/bin/pytest tests/test_capabilities_catalog.py tests/test_gate0_verification.py tests/test_six_graphs_execution_constraints.py tests/unit_tests/test_event_ledger_contract.py -vv` | 28 passed | **28 passed (100%)** | [core_28_tests.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-02/core_28_tests.txt) |
| **管理台账+选择** | `DATABASE_URI=... ADMIN_RUNTIME_DATABASE_URI=... admin/api/.venv/bin/pytest tests/test_event_ledger_api.py tests/test_ledger_selection_persistence.py -v` | 44 passed, 1 pre-existing failure | **44 passed, 1 failed (collection_disabled 基线)** | [admin_tests.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-02/admin_tests.txt) |
| **前端契约测试** | `npm --prefix frontend run test:contracts` | 46 passed | **46 passed (100%)** | [frontend_tests.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-02/frontend_tests.txt) |
| **前端生产构建** | `npm --prefix frontend run build` | next build 成功，0 错误 | **0 errors, compiled successfully in 477ms** | [frontend_build.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-02/frontend_build.txt) |
| **根单元测试** | `.venv/bin/pytest tests/unit_tests/` | 361 passed, 75 skipped, 6 baseline failures | **361 passed, 75 skipped, 6 failed (同环境基线失败，0 新增)** | [root_unit_tests.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-02/root_unit_tests.txt) |
| **独立 live 探针** | `PYTHONPATH=admin/api/.venv/lib/python3.13/site-packages .venv/bin/python docs/reviews/evidence/flash-05-repair-01-independent/probe-live.py` | 14 项安全断言通过，0 模型调用 | **PASSED (0 model calls)** | [probe-live.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-02/probe-live.txt) |
| **独立模块探针** | `.venv/bin/python docs/reviews/evidence/flash-05-repair-01-independent/probe-modules-extended.py` | 签名、单射、隔离过滤与 span 正确 | **PASSED (all 5 checks ok)** | [probe-modules-extended.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-02/probe-modules-extended.txt) |
| **独立前端探针** | `node docs/reviews/evidence/flash-05-repair-01-independent/probe-accepted.cjs` | 草稿变更不污染在飞权威选择 | **PASSED** | [probe-accepted.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-02/probe-accepted.txt) |
| **代码格式检查** | `admin/api/.venv/bin/ruff check admin/api/migrations/versions/0013s_capability_selection.py admin/api/tests/benchmark_admission_overhead.py` | 0 errors | **All checks passed!** | [ruff_check.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-02/ruff_check.txt) |

---

## 3. 验收矩阵（A01–A24）映射与实测证据

| 编号 | 验收项 | 预期要求 | 实测结果与证据路径 | 状态 |
|---|---|---|---|---|
| A01 | 当前成员目录与鉴权 | 401/403/503 失败关闭，无全局身份回退 | `test_task_2_3_artifact_http_capabilities_endpoint` 验证匿名 401、校准票据 403；[core_28_tests.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-02/core_28_tests.txt) | **通过** |
| A02 | 双企业双成员隔离 | 仅发现当前有权服务，无权服务 0 发现请求 | `test_task_2_2_mcp_catalog_same_name_disambiguation` 验证企业 A/B 隔离与 0 发现请求 | **通过** |
| A03 | 两服务同名 query | 稳定 ID 不同，名称映射不冲突，参数归属正确 | 单射映射 `mcp_service_a__query` 与 `mcp_service__a__query` 经 `probe-modules-extended.py` 验证；[probe-modules-extended.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-02/probe-modules-extended.txt) | **通过** |
| A04 | 目录超时与分页 | 单服务 2s/总 5s 控时，游标跨 scope 拒绝 | `test_task_2_3_capabilities_pagination_and_tampered_cursor` 验证游标防篡改与跨租户 409 拒绝 | **通过** |
| A05 | auto 与显式选择 | 默认兼容，S1 真实加载，空集零 MCP | `test_task_4_2_business_graph_explicit_skill_and_menu_determinism` 及 `probe-live.py` 验证 | **通过** |
| A06 | 菜单依赖与冲突 | 菜单冲突 422，依赖缺失 422 | `test_task_3_1_allowed_tools_non_mandatory_and_required_tools_conflict` 验证；BFF 传入 `menuName` 预检 | **通过** |
| A07 | 幂等与并发受理 | 同键不同选择 409，一个启动权 | `probe-live.py` 验证 `same_key_explicit_changed_to_explicit_auto` 返回 409；[probe-live.txt](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-02/probe-live.txt) | **通过** |
| A08 | 旧 v1 重放兼容 | 旧自动重放不改 hash，历史 not_collected | `store.py:claim_start` 兼容双版本比对；`probe-live.py` 验证 `old_client_omits_selection` 返回 200 | **通过** |
| A09 | metadata 冲突拒绝 | 任何冲突均拒绝，无模型/MCP 调用 | `test_task_4_1_native_hook_metadata_config_consistency` 验证各层自报矛盾 403 拒绝 | **通过** |
| A10 | 运行时 ToolNode 拦截 | 模型绕过发 T2 被 dispatch 阻断，出站网络 0 | `test_gate0_mcp_same_name_disambiguation_and_zero_calls` 验证 T1 成功、T2 拦截且调用为 0 | **通过** |
| A11 | 业务图显式 Skill | S1 不被语义路由替换，保持显式 | `test_task_4_2_business_graph_explicit_skill_and_menu_determinism` 验证显式覆盖 | **通过** |
| A12 | DeepAgents 文件与子任务 | SDK glob 签名支持 path，S2 文件读阻断 | `test_task_4_4_deepagents_skills_and_subtasks_boundary` 与 `probe-modules-extended.py` 验证 | **通过** |
| A13 | 实时撤销阻断 | 撤销后无 TTL 缓存，下一请求立即生效 | `test_task_2_4_metadata_cache_vs_auth_isolation` 验证 DB update 禁用后立即反映在目录中 | **通过** |
| A14 | retry 与 resume 继承 | retry 继承，resume 不换计划 | `test_task_4_5_resume_and_retry_immutability` 与 `probe-live.py:resume_inherit` 验证 | **通过** |
| A15 | 六图 native/BFF 矩阵 | 基础流转与结构约束验证 | 本地受控模式六图编译与导入无网络验证通过；全量 24 格外部商用模型真实网络回答因沙箱限制如实保留 | **部分未完成（外部门禁）** |
| A16 | 显式路径与恢复 | 每图至少一条显式成功与中断恢复 | `test_six_graphs_execution_constraints.py` 覆盖六图显式加载与中断状态继承 | **通过** |
| A17 | 非 owner RLS 隔离 | A/B 租户与成员隔离，越权 404/403 | `test_task_3_2_composite_foreign_key_and_rls` 验证 PostgreSQL 复合外键与 RLS 强隔离 | **通过** |
| A18 | 删除墓碑与防复活 | 线程删除后写入阻断，墓碑永久生效 | `test_task_3_5_readback_deletion_and_resume` 验证删除后拒绝与回读 | **通过** |
| A19 | 观测 Span 与 manual 来源 | 贯穿 loaded/executed，保留 manual | `probe-modules-extended.py` 验证 loaded 与 executed 均保持 `selection_source: "manual"` | **通过** |
| A20 | 契约与序列化保护 | 严格模式拒绝未声明字段与敏感属性 | `toPayload()` 设置 `selection_source` 不可枚举；`test_event_ledger_contract.py` 12 项全部通过 | **通过** |
| A21 | 前端时序与草稿解耦 | 草稿改动不污染在飞运行，A→B→A 丢弃旧请求 | `probe-accepted.cjs` 验证延迟确认下草稿由 S1 变 S2 仍权威回读 S1；46 项前端契约通过 | **通过** |
| A22 | 物理界面与无障碍 | 390×844 响应式、焦点与 IME 兼容 | Node/React JSDOM 契约与单元测试通过；无头沙箱无物理 GUI，真实浏览器截屏如实保留 | **保留未完成（无头环境）** |
| A23 | 数据库迁移与升级 | 单 head 无坏账，0013s 独立迁移 | PostgreSQL 专用实例完成从零全量迁移与存量升级，Ruff E501 0 错误 | **通过** |
| A24 | 性能开销与可靠性 | 35 样本 × 2 轮采集，P95 增量 ≤ 50ms | `benchmark_admission_overhead.py` 实测 max delta P95 为负增量，错误率 0.0%，拦截率 100% | **通过** |

---

## 4. 剩余项与环境门禁声明

根据验收规范要求，对缺少外部先决条件的具体项目如实声明未完成，绝不使用模拟或常量冒充通过：
1. **验收项 5.4 / A22（真实物理浏览器交互与截图）**：当前容器与执行沙箱为无头 Linux/macOS CLI 环境，无真实桌面 GUI 和 Playwright 浏览器渲染器。已在 Node.js / JSDOM 环境下完整验证了状态管理、事件隔离、无障碍属性及前后端契约（46 项全部通过），但真实物理设备截图保留未完成。
2. **验收项 7.1 / A15（六图 × 24 格外部商用 LLM 全量流式生成）**：所有六张图的拓扑编译、节点调度、动态 MCP 接线与 ToolNode 拦截均已在受控模型与测试套件中严格验证通过。但由于当前沙箱未配置外网商用大模型 API Key（SiliconFlow、DeepSeek 等均为 `controlled-unused`），全量 24 格外网商用模型真实网络流式答复保留未完成。

---

## 5. 资源清理与交付物清单

- **专用测试环境清理**：
  - 测试期间产生的合成审计数据已通过 teardown 事务清理完毕。
  - 保留专用容器 `aa-sel05-pg` 与 `aa-sel05-redis` 供协调者随时复验。
  - 宿主机共享栈（`localhost:15432`）未发生任何变动或数据污染。
- **证据交付物目录**：
  - 目录路径：[docs/reviews/evidence/flash-05-repair-02/](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/flash-05-repair-02/)
  - 包含文件：
    - `core_28_tests.txt`：核心 28 项测试完整通过记录
    - `admin_tests.txt`：管理端 44 项通过 / 1 项基线失败记录
    - `frontend_tests.txt`：前端 46 项契约测试通过记录
    - `frontend_build.txt`：Next.js 生产构建 0 错误输出
    - `root_unit_tests.txt`：根目录 361 通过 / 75 跳过 / 6 基线失败记录
    - `probe-live.txt`：独立 live 探针 14 项安全断言通过记录
    - `probe-modules-extended.txt`：扩展模块探针输出
    - `probe-accepted.txt`：前端权威回读与草稿解耦探针输出
    - `benchmark-admission-overhead.json`：全路径受理保护 35 样本 × 2 轮性能与可靠性数据
    - `overhead-benchmark.json`：包含本地 helper 与目录故障上界的完整基准数据
    - `ruff_check.txt`：代码规范扫描记录
- **工作树状态**：所有修复代码保留在未提交的 Working Tree 中，等待协调者独立验收。
