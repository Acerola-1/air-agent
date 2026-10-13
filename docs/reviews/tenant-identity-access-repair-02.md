# 轮次02定向修复：F01–F05 关闭报告

日期：2026-10-12。实施标识：**本轮实现**（第二轮定向修复）。交付状态：**待独立复验**。
本文件不改动首次评审、首轮修复报告、轮次02 复验报告与任何原始证据；
`docs/reviews/evidence/flash-01-repair-01/**` 原样保留，本轮复跑输出全部写入独立目录
`/tmp/air-agent-repair02-pre`（修复前）与 `/tmp/air-agent-repair02-post`（修复后）。
所有改动保留在工作区 diff，未提交、未推送、未合并、未同步主规格、未归档；
仅处理 `add-tenant-identity-access`，未开始 `add-agent-event-ledger`。

输入：`docs/reviews/flash-blind-01-repair-01-review.md`（当前版本 60/100，未通过）中的
F01–F05 与 §复验状态。修复纪律与首轮一致：验收标准不改、评审观察不覆写、
测试不删不跳、凭据只经环境注入、专用库隔离、不重置业务卷、不做不明归属迁移。

## 0. 结果总览

| 检查 | 结果 | 口径 |
|---|---|---|
| F01–F05 | 5 项全部实现修复，每项有修复前复现失败/修复后通过证据 | 逐项见下 |
| admin 套件（清洁双库 air_agent_repair_api + air_agent_repair_rls，0001–0006 + 项目 conftest 前置） | **113 passed / 0 failed**（107 → +6 新回归） | 真实 PG + 实际 ASGI 路由 |
| root 单元 | **272 passed / 26 skipped / 5 failed**；5 项失败与既有基线同名同因（checkpoint 1 + knowledge_route 4），本轮未修不掩盖 | 干净环境 |
| root 集成 | **11 passed / 15 skipped / 1 failed**（500k 旧 ModelRegistry 引用为既有基线）；配 `TENANT_TEST_*` 归档 RLS 4 测启用 | 真实 PG |
| 真实联调（live，官方镜像 0.15.1 + PG + :8100/:3000/:2024） | **8 passed**：F01 未知历史/他人归属经 BFF 不可吸收、pending 不可读、**同会话才确认可**（他会话票据确认 false）、mt-v1 真实镜像窄更新（提交后 200/越界 403/读取拒绝）、**六图运行受理 + 跨企业创建拒绝 + 取消**、聊天代理旧标签页 409/403 与铸造 ID | 真实 HTTP/PG；basic-qa 完整真实模型问答成功（run=success，回答"2"） |
| 聊天前端契约 | **17 passed / 0 failed**（停止确认 7 例新语义 + 代理铸造/409 不确认断言 + 原 8 项） | node:test + 真实 store/route 源码 |
| 两前端 | tsc 通过；聊天 webpack 生产构建 exit 0；**admin/web 生产构建本轮首次通过** | 生产构建 |
| 评审探针修复后复跑 | live-claim：越权标记暴露=false、目标 ID 读 404；admin：`suspended_role_create=403`、成员 revision 1→2、`runtime_allows_after_role_permission_revoke=false(RuntimeIdentityRevoked)`；migration：首调即可见归属、pending=0、复跑 already_applied、回调 1 次；stop 三例全部 false；runtime 适配复跑 `missing_revision_snapshot_allowed=false`、重试不再发送、摘要 0 外呼 | 独立目录留存 |
| OpenSpec strict / 定向 Ruff | 通过；剩余 lint 仅既有基线（checkpointing F811、langfuse UP047、tests T201×5） | — |

## 1. F01：409 补偿重新打开认领越权（P1）——已关闭

**根因（评审实证）**：`route.ts` 把任意平台 409 当作"上次创建成功、确认丢失"，
内部确认端点又只查"存在本人 pending 行 + 身份匹配"，于是未知历史线程经 BFF
严格创建即被吸收（live-claim.json：`read_exposes_bob_marker=true`）。

**修复（三层，缺一不可）**：

1. **BFF 铸造创建标识**（`frontend/app/api/[..._path]/route.ts`）：
   `POST /api/threads` 的 `thread_id` 由代理生成（`crypto.randomUUID()`），
   客户端自选 ID 一律不采信、`if_exists` 不转发。攻击者无法把 BFF 创建指向任何
   既有对象——"当前可信创建操作产生"的事实由**入口铸造**保证。
   `frontend/lib/langgraph-api.ts::createThread` 改为消费响应 `thread_id`。
2. **确认端点绑定可信创建事实与作用域**（`src/common/artifact_http.py` +
   `src/common/identity/db.py` + 迁移 0006）：请求体只允许 `thread_id`；
   租户/用户/会话三元组一律取自**已验签运行票据 claims**（内部凭据 + pending
   存在 + 身份匹配不再单独构成依据）；新增列 `thread_ownership.claim_session_id`
   （0006）由创建钩子写入，确认只升级同一票据会话建立的 pending 行；已确认同归属
   重复确认幂等 true。**409 路径彻底删除**：非 2xx 不确认，失败重试 3 次后 503
   （线程保持 pending：不可读、不可运行）。
3. **恢复语义**：响应丢失时，第一次代理调用已在服务端完成确认（确认先于响应），
   浏览器重试只会得到新的铸造线程；确认失败（身份库抖动）保留 pending，用户重试
   新建会话，不存在"未确认却可用"或"可用却未确认被吸收"的中间态。

**修复前断言**（评审探针复跑，修复前）：`alice_strict_proxy_create=200`、
`proxy_exposes_bob_marker=true`、`read_exposes_bob_marker=true`。
**修复后断言**（同一探针适配复跑）：200 但 `bff_minted_new_id=true`、两个 marker
布尔均 false、目标旧 ID 经代理与原生读取均 404。

**回归**：`tests/integration_tests/test_live_tenant_claims.py`
（`test_live_bff_never_confirms_unowned_ids`、`test_live_claim_confirm_requires_same_session`、
`test_live_unknown_history_claim_denied` 更新；`_confirm_claim` 契约改为仅 thread_id +
`test_live_pending_until_confirmed_and_confirmed_flow` 断言旧形态自报身份请求体=400）；
聊天契约（`frontend/tests/chat-contract.test.cjs::proxy: stale-tab claim rejected…`
新增断言：上游 body thread_id 为服务端铸造 uuid、`if_exists` 不透传、确认体仅
`{thread_id}`、**409 响应不触发确认端点**）；原生端点 401/400 负例。

## 2. F02：撤权未贯通权限变更与全部外呼（P1）——已关闭

**(1) 权限变化 → 修订同事务**（`admin/api/app/routers/roles.py`）：
`set_role_permissions` 与 `delete_role` 在同一 `session.commit()` 前
`_bump_role_holder_revisions`（对持有该租户角色的全部 `tenant_memberships`
`revision+1`；平台角色不涉成员、跳过）。删除在级联清理 membership_roles 之前捕获持有者。
成员/企业/改密/切换链路首轮已通（本轮复核 `test_role_revocation_effective_next_request`、
`test_member_disable_blocks_next_request` 保持通过）。平台授权变化：管理面每请求重算权限
即时生效；运行面票据不携带平台权限，不构成撤权缺口（如实说明，未扩表）。

**(2) 每次实际外呼前守卫**：
- `business_graph/nodes.py::call_model`：入口 + **每个重试 attempt** + **空回复 nudge**
  发送前各自 `guard_runtime_call`；`RuntimeIdentityError` 单列 except——不进入重试
  循环、不落入兜底，返回可读"授权状态已变化"可见消息；
- `data_governance.invoke_with_data_deadline`：claim 循环（可耗时）后、草稿生成
  `model.ainvoke` 前再复查一次；
- `business_graph/tool_wrappers._invoke_mcp_with_retry`：**每次重发前**
  `identity_from_request + revalidate`，拒绝即终止重试并返回可读 ToolMessage 错误；
   composed_tool_wrapper 的守卫改用框架无关的 `guard_request_call`；
- `text_reduction.summarize_complete_text(+run_config)`、
  `conversation_summarizer.summarize_conversation_history(→run_config)`、
  `subagent_summarizer.summarize_large_tool_output(→run_config)`：每次摘要模型外呼
  前经 `guard_runtime_call`——长会话/大输出整理不再绕过；tool_wrappers 调用点已
  透传 run_config；
- 运行期技能路由 `resolve_skill`：embedding `amatch` 前复查；`prepare_model`：
  请求期 `ensure_mcp_tools()`（工具目录刷新外呼）前复查。**启动服务公共索引
  （skill_router.prewarm / catalog 预热）在用户请求之外**，不携带身份，按部署
  引导处理，与请求期行为分道（报告口径，未改其行为）。
- DeepAgents 两图机制共用 `TenantRuntimeGuardMiddleware`（每节点执行/每次重试节点
  都会重入守卫），首轮真实框架测试保持通过。

**(3) 缺快照 fail-closed**（`runtime.revalidate`）：强制模式下四项修订快照任一为
None/非法 → `RuntimeIdentityRevoked`（旧运行安全停止，新请求重建可信上下文），
不降级为仅查 active；非强制（本地测试模式）保持原状态检查——评审 runtime_probe
在 `TENANT_ENFORCE_RUNTIME_IDENTITY=true` 下 `missing_revision_snapshot_allowed=false`。
测试 fixture 已补全快照（`test_tenant_runtime_guard._IDENTITY` 等）。

**证据**：
- 真实 PG + 实际路由：admin 探针复跑（`/tmp/air-agent-repair02-post/admin-proofs.json`）
  `remove_role_permissions=200 → member revision 1→2 →
  runtime_allows_after_role_permission_revoke=false (RuntimeIdentityRevoked)`；
  项目回归 `test_role_permission_revoke_bumps_member_revision`
  （含 tenant_admin 模板无 role:delete 的既有设计确认、平台恢复性删除路径）；
- 实际调用函数 + 替身模型：`tests/unit_tests/test_per_call_outbound_guards.py` 5 例
  （重试循环撤权后第二次不发送、nudge 前撤权不发送、无身份/撤权摘要 0 外呼、
  MCP 重试每次重发前核验且撤权即停、缺快照拒绝）；runtime 适配复跑 JSON：
  `model_attempts=1, runtime_checks_during_model_retry=3, model_retry_sent…=false,
  visible_denial=true, summary_external_calls_without_identity=0`；
- 真实服务：live 六图运行受理 + 跨企业拒绝 + basic-qa 完整真实模型问答成功。

**未做（如实）**：五图（除 basic-qa 外）的完整真实模型长运行、运行中撤权对
checkpoint 秘密性的长时间观测——保留在 10.3/§13。授权拒绝的"不被重试吞"已由
单元/契约级证明，但未在真实网关抖动场景演练。

## 3. F03：迁移先于提交调用 API + 失败被台账短路（P1）——已关闭

**修复**（`admin/api/scripts/migrate_identity.py`，`docs` 语义不变）：

- 阶段 A：线程归属 + 用户/成员/角色/关注 + 归档回填在同一事务**提交**；每个线程
  写实体台账行（`entity_type='thread'`，status=`pending`，即"归属已提交、平台更新
  待执行"）。
- 阶段 B：提交后逐线程 `PATCH /threads/{id}`（mt-v1 票据，真实 httpx）；2xx 才把
  实体行推进 `applied`（每实体独立提交，进程中断安全）；非 2xx/超时/连接失败保留
  `pending`（不中断其余实体）。归属行已存在但非 `migrated` 同归属 → `SystemExit`
  停止（冲突不改写、不猜测）。
- manifest 完成判定：所有 `thread` 实体均 `applied` 才登记 `entity_type='manifest'`
  `status='applied'`；否则返回 `completed:false` + `applied.incomplete_threads`，
  **绝不登记 applied、复跑绝不短路**。幂等短路只在指纹 applied 时发生。
- 平台不可达（原 `LANGGRAPH_MIGRATION_BASE_URL` 错误端口/未起服务）→ 0 不 2xx →
  pending 计数，绝不回退私有表直写（原路径已删）。
- 历史归档/计算一致性：apply 内 `metadata_pending>0` 即 `completed:false`；
  artifacts 回填计数在报告与 manifest 台账 detail 中登记（threads/artifacts 计数
  核对保留 `report` 子命令）。

**修复前断言**（评审探针原样复跑）：`first_metadata_pending=1`、
`ownership_visible_at_first_api_call=false`、`second_already_applied=true`、
`platform_callback_count_after_rerun=1`（失败项不再续传）。
**修复后断言**（同探针复跑，`/tmp/air-agent-repair02-post/migration-proofs.json`）：
`first_metadata_pending=0`、首调即可见归属（真实运行角色独立连接校验）、
复跑 `already_applied=true` 且外呼回调仍 1 次（真完成才短路）。

**回归**（真实事务可见性 stub——独立 runtime 连接 + 与 auth 相同的 GUC/票据作用域
校验，不再"预设所有 PATCH 成功"）：`admin/api/tests/test_tenant_migration.py`
`test_migration_patch_happens_after_commit_visible`、
`test_migration_resumes_after_platform_failure`（503 注入 → pending → 复跑仅重发
未完成实体 → completed → 第三次短路零新外呼）、
`test_migration_stops_on_foreign_ownership`（native 归属冲突停止、0 次平台调用）、
`test_metadata_migration_uses_supported_api_not_private_table` 保持；
真实镜像端：live `test_live_migration_ticket_native_api`（提交后 200、伪造键被票据
作用域覆盖、绑定他线程 403、迁移 ctx 读/搜 403）。

**未做（如实）**：真实业务库全量历史迁移与切换/恢复演练（8.4 已重开）——
`drill_recovery.sh` 未对 0006 schema 重跑。

## 4. F04：停用企业仍可创建角色（P1）——已关闭

**修复**（`admin/api/app/routers/roles.py` + `deps.py`）：新增
`_require_active_enterprise`，接入企业角色的**创建 / 修改 / 删除 / 权限分配**
（create_role 租户分支 + `_require_manageable` 统一 chokepoint）；非平台操作者所在
企业停用或成员停用一律 403。平台例外通道：`_require_manageable` 对平台管理员放行
租户角色目标（明确平台权限 + 目标范围），`PATCH /tenants/{id}` 恢复入口与
平台成员解除（remove）不受影响——恢复流程不被误阻断。

**修复前断言**：探针 `suspended_role_create=201`（部门 403）。
**修复后**：`suspended_role_create=403`（含正确上下文头的停用判定，非缺头 403——
探针在 post 运行中 `missing_context_department=403` 与 `suspended_department=403`
并列成立，角色 403 为**带正确头**的停用拒绝）；恢复后 create2=201。

**回归**：`admin/api/tests/test_tenant_api.py::test_suspended_tenant_blocks_role_writes_all_scopes`
（创建/权限分配/修改/删除四路全 403 + 平台恢复后放行）；探针 post 复跑留档。
成员写入口首轮已按 `link/update/roles/remove` 把关（本轮保持）。

## 5. F05：不明确的停止结果被当作确认（P2）——已关闭

**修复**（`frontend/lib/chat-store.ts` + `frontend/lib/langgraph-api.ts`）：

- `getRunStatus` 语义拆分：`{found:false}`、`status:null`、查询异常一律**未确认**
  （`reason:'unknown'/'backend'`）；只有服务端 `runs.get` 见证
  `cancelled/success/error/failed/timeout` 才 `confirmed:true`。
- `cancelRun` 409 不再视为"已终态"：本栈 409 的现实来源是代理 `context_changed`
  （实测原生 cancel 只有 202/404），因此 409 → `{confirmed:false, reason:
  'context_changed'}` 并触发上下文刷新。cancel 404（`no matching runs to cancel`）
  继续轮询状态、见终态才确认。
- run_id 竞争：停止早于 `onRun` 元数据时，在 3s 解析窗口内经
  `listRecentRuns`（新增，返回状态）找回活动 run；最近运行已处可信终态→确认；
  空列表/仍不明→**未确认 unknown**，绝不"空即已停"。
- TenantBadge 仅在 `confirmed:true` 时继续切换（保持首轮门槛）。

**修复前断言**（评审 stop_probe 复跑，pre 目录）：
`context_changed_409_confirmed/unknown_status_confirmed/run_not_found_confirmed`
全为 **true**。**修复后**（post 目录）：三项全部 **false**。

**回归**：聊天契约新增 4 例、改写 2 例、扩展 1 例（铸造/409 断言并入代理用例）（409→false+context_changed；found:false /
status:null→false+unknown；cancel 404+观察终态→true；空列表窗口→false+unknown；
迟到 run_id 找回→true；重试/终态轮询计数）。过程中自纠一例我新加的契约用例
（空列表用例缺 `stream.wait.resolve()` 导致挂起——已修，与实现无关）。
真实侧：live 代理票据链。

**票据签发窗口**（R02 关联项）：`RuntimeTicketIn` 新增
`expected_ctx_version`，admin `POST /auth/runtime-ticket` 与
`switch-tenant` 同款版本比对；代理在上下文校验后携带页面声明版本换票
（`frontend/lib/admin-api.ts`/`route.ts`）。回归：
`test_runtime_ticket_expected_version_window_a_to_b_to_a`（A→B→A 后旧声明 409、
新版本换票成功）。

**未做（如实）**：真实模型长运行下的取消延迟/超时场景（15s 上限策略未在真实
慢模型下测量）；停止确认与 `runs/stream` 断流事件的浏览器级组合矩阵部分完成
（见 §6）。

## 6. 浏览器矩阵（本轮增量）

- 新代理链路端到端正常：3000 登录态发消息 → 铸造线程创建、运行流式、回答完成
  （basic-qa 真实模型）；空闲态企业切换成功、列表随企业收敛。
- 运行中"停止生成"按钮：点击后干净停止（按钮消失、无错误态、输入恢复），
  证明可信终态等待不卡 UI。
- 运行中**下拉切换**触发的 window.confirm 与 Playwright selectOption 存在模态
  互阻塞（选择命令挂起 32s 后 accept 迟到），该组合以 store/契约 + live HTTP
  层证据替代；完整浏览器矩阵（两前端全场景）仍保留为 10.3 未完成。
- admin/web 双标签旧上下文回归保持第一轮结论（本轮服务端上下文校验代码未变，
  F04 探针证据为 API 级）。

## 7. 环境与配置影响

- 新迁移 **0006**（幂等）：`thread_ownership.claim_session_id uuid NULL`；
  已应用于 air_agent / air_agent_repair_api / air_agent_repair_rls / 评审双库。
  历史行 `claim_session_id=NULL` 永不匹配确认条件（保持 pending 不可见，符合预期）。
- 代理契约变化：`POST /api/threads` 不再接受客户端自选 `thread_id`/`if_exists`；
  确认内部端点请求体仅 `{thread_id}`，作用域/会话取自已验签票据。
- `POST /auth/runtime-ticket` 新增可选 `expected_ctx_version`（省略=旧行为兼容）。
- 归档表 `artifact_computations` 计算认领映射跨企业引用为可读 ComputationLimit
  错误（上一轮已入，本轮报告口径统一）。
- 迁移 CLI：`LANGGRAPH_MIGRATION_BASE_URL` 现为**必需**（真实原生地址），缺失时
  指向 localhost 默认值；未完成时输出 `completed:false` + pending 计数，
  不登记 manifest applied。
- 新可选环境：`RUN_TENANT_LIVE_E2E`、`TENANT_E2E_OWNER_URI`（live 套件门控）；
  admin 测试新增 `INTERNAL_API_TOKEN` 环境依赖说明（conftest seed 不变）。
- ledger 状态词汇未扩（复用 `pending/applied/…` CHECK，避免再动约束）。

## 8. 复跑命令（复验入口）

评审探针（同 README 命令组，输出建议指向独立目录）+ 项目内回归：

```sh
# 前提：容器栈 + :8100(admin, 当前代码) + :3000(next dev) + :2024(langgraph-api)
# 原生改动生效：container rm -f air-agent-langgraph-api &&
#   container-compose up -d --profile prod langgraph-api

# 1) root 单元 / 集成（干净环境；基线 5 失败 + 500k 单列）
LANGFUSE_ENABLED=false LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false PYTHONPATH=src \
  .venv/bin/python -m pytest tests/unit_tests -q
LANGFUSE_ENABLED=false LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false PYTHONPATH=src \
  .venv/bin/python -m pytest tests/integration_tests -q

# 2) 真实联调 8 测（票据/口令经环境提供）
RUN_TENANT_LIVE_E2E=1 REVIEW_FIXTURE_PASSWORD='<环境提供>' \
  TENANT_E2E_OWNER_URI='<owner>' LANGFUSE_ENABLED=false LANGSMITH_TRACING=false \
  LANGCHAIN_TRACING_V2=false .venv/bin/python -m pytest \
  tests/integration_tests/test_live_tenant_claims.py -q

# 3) admin 清洁双库全套（前置=声明迁移+conftest seed；INTERNAL_API_TOKEN 环境提供）
export DATABASE_URI=<repair_api owner> ADMIN_RUNTIME_DATABASE_URI=<repair_api runtime> \
       TENANT_TEST_OWNER_URI=<repair_rls owner> TENANT_TEST_RUNTIME_URI=<repair_rls runtime>
(cd admin/api && LANGFUSE_ENABLED=false .venv/bin/python -m pytest tests -q)

# 4) 两前端
npm --prefix frontend run test:contracts
(cd frontend && ./node_modules/.bin/tsc --noEmit) && npm --prefix frontend run build -- --webpack
(cd admin/web && ./node_modules/.bin/tsc --noEmit) && npm --prefix admin/web run build

# 5) 规格与静态
openspec validate add-tenant-identity-access --strict
.venv/bin/ruff check src/common tests admin/api/app admin/api/scripts admin/api/migrations admin/api/tests
# （基线例外：checkpointing F811、langfuse UP047、tests T201×5——与首轮相同，未触碰）

# 6) 评审探针修复前/后对照（README 命令组 + REVIEW_REPAIR_OUTPUT_DIR 指向新目录）
```

## 9. 保持未完成的项（不虚报）

- 7.6 真实统一平台 MCP（A/B 授权身份、生产协议、无秘密 Trace 核对）：材料仍缺，
  登记表无生产条目，企业 MCP 生产安全禁用。
- 10.3 完整 design 验收矩阵：六图完整真实模型长运行（本轮仅 basic-qa 完整问答 +
  六图受理/取消/跨租户拒绝）、两前端完整浏览器矩阵、归档重对话浏览器验收、
  性能/负载基准——均未做，保留 [ ]。
- 8.4 切换/恢复演练对 0006 schema 未重跑，保留 [ ]。
- 撤权后"确认不再发送外部请求"在真实 LLM 网关与真实 MCP 平台的端到端证明
  （替身计数与真实 PG/真实路由已覆盖对应层）。
- 业务库 e2e 账号会话清理**过度**：本轮浏览器清理把 acc_alice/acc_bob 两家 e2e
  企业的历史本地合成会话一并删除（均为历轮测试产物，无真实用户对话；业务卷与
  平台线程表未重置，thread_ownership/线程由平台正常删除路径处理）。如实申报。

## 10. 耗时与效率（单列，不覆盖既有口径）

本轮（第二轮定向修复）：约 **2 小时 20 分～2 小时 40 分** 墙钟
（起始 ≈19:0x，成稿 ≈21:5x；含套件/构建等待：root×2、admin×3、live×4、
webpack/admin 构建各 1、契约×3，合计等待约 15–20 分钟；探针复跑与两次容器
重启另计 ≈8 分钟）。Token/工具次数未采集。
口径链保留：首交 **4 小时（用户报告）**；首轮修复 **2 小时 15 分（用户报告）**，
其实现方自记 **3 小时 40 分**另列不覆盖；累计（首交+首修，用户口径）
**6 小时 15 分**时点仍未达验收；本轮修复用时以本节为准。达验收总用时待独立
复验通过后回填。

## 11. 变更文件清单（本轮 diff 增量）

实现：`src/common/identity/db.py`、`src/common/identity/runtime.py`、
`src/common/identity/langgraph_auth.py`、`src/common/identity/ticket.py`（无本轮新增改动，
mt-v1 为首轮）、`src/common/artifact_http.py`、`src/common/business_graph/nodes.py`、
`src/common/business_graph/tool_wrappers.py`、`src/common/business_graph/subagent_summarizer.py`、
`src/common/conversation_summarizer.py`、`src/common/data_governance.py`、
`src/common/text_reduction.py`、`admin/api/app/routers/roles.py`、
`admin/api/app/routers/auth.py`、`admin/api/app/schemas.py`、
`admin/api/scripts/migrate_identity.py`、`admin/api/migrations/versions/0006_claim_session.py`、
`frontend/app/api/[..._path]/route.ts`、`frontend/lib/langgraph-api.ts`、
`frontend/lib/chat-store.ts`、`frontend/lib/admin-api.ts`、
`openspec/changes/add-tenant-identity-access/tasks.md`（8.4/10.3 重开 + 轮次注记）。

测试：`tests/unit_tests/test_per_call_outbound_guards.py`（新）、
`tests/unit_tests/test_tenant_runtime_guard.py`（快照 fixture + 缺快照拒绝）、
`tests/unit_tests/test_thread_claim_protocol.py`（签名适配）、
`tests/integration_tests/test_live_tenant_claims.py`（+4 改写 2）、
`admin/api/tests/test_tenant_api.py`（+3 改写 1）、`admin/api/tests/test_tenant_migration.py`
（stub 真实可见性 + 故障脚本化，+3）、`frontend/tests/chat-contract.test.cjs`
（停止语义 6 例 + 代理铸造/409 不确认）。

证据（独立目录，非仓库文件）：`/tmp/air-agent-repair02-pre/*`、
`/tmp/air-agent-repair02-post/{live-claim,admin-proofs,migration-proofs,stop-proofs,runtime-proofs}.json`。
