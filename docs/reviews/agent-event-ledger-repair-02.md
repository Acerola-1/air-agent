# 匿名02 第二轮定向修复报告（F01–F06）

日期：2026-10-13；change：`add-agent-event-ledger`；状态：**待独立复验**。

本轮回应首轮修复的独立复验结论（`docs/reviews/flash-blind-02-repair-01-review.md`，诊断 68/100，未通过）
与 `docs/reviews/evidence/flash-02-repair-01-independent/` 的反例。首交 1 小时 52 分、首修 1 小时 49 分
（累计 3 小时 41 分）**原样保留**；本轮用时另计（见 §9）。未 commit / push / merge / archive，
未改协调者评分，未动首交与首修证据与独立探针断言，未启动第 3/5 项。

## 0. 起点、方法与环境

| 项 | 实际 |
|---|---|
| 先失败后修复 | 每条反例先落成**会失败的回归**并确认红（F01 三条 + F03 两条 + F05 两条 + F06 一条真实三端；F02 三条管理 API；F04 三条；共 16 条新增，全部先红后绿） |
| 第一步（按依赖） | 先证明**真实三端合法首笔提交可成功启动**（F01），再依次 F02 → F03 → F04 → F05 → F06；避免只验证各层孤立 200 |
| 迁移 | 新增 **0009**（`0008` 已执行，`0007/0008` 均未改写）；空库 `0001→0009` 与业务库 `0008→0009` 均验证 |
| 环境 | 本轮新建 3 套随机专用 PG（ledger/api/rls）+ 随机非 owner worker 角色（`setup_repair02_env.py`）；**不重置共享卷、不改真实账号口令、不清理他人线程**；独立探针只操作本轮合成线程 |
| 证据分层 | 真实 API / 真实平台库与台账库 / 真实主模型（basic-qa 图）分别标注；**未使用模型替身**；真实企业 MCP 仍安全禁用 |

## 1. F01 启动权交接：BFF 与原生重复消费启动权

**根因**：BFF 先调 `claimStart`（管理端裁决），随后原生 hook 再次 `claim_start` → 第二次必然
`already_started`，合法首笔被 409 拒绝；同键重试 202。管理端生成的 `start_claim_token` 没有任何
可信交接机制，等于把启动权消费了两次。契约测试只断言"重复时不 fetch"，无法暴露"第一次过不去"。

**实现（单点权威裁决 + 可信交接）**
- **裁决点唯一化**：启动权只由**原生入口**（平台 auth hook）裁决——那是唯一不可绕过的判定位。
  BFF 不再预 claim，职责回到"受理 → 注入可信关联 → 转发"。
- **可信交接**：原生把拒绝原因以**结构化 `detail.code`** 返回
  （`run_already_confirmed` / `run_already_started` / `run_request_mismatch` / `run_attempt_terminal`），
  BFF 只按 code 分流：已有运行→按台账（或 detail 内 run_id）**复用**；已启动无映射→202 待确认，
  **不盲重发**；文本型 409 一律原样透传（不把任意 409 当成功）。客户端自报 source/布尔值/token 均不参与放行。
- **失败与丢失分离**：转发中网络中断/超时 → 记 `admission_unknown(reason=upstream_unreachable)` 并回 202，
  由有界补偿按**平台可信 metadata**（`metadata.ledger_attempt_id`）定位既有运行后收敛；确认无运行且
  超过宽限期才**释放**未确认启动权（`release_unstarted_claim`，已有映射一律不动），因此不会永久制造
  "不存在的运行"的待确认记录。

**先失败后通过**（`tests/integration_tests/test_live_repair02_boundaries.py`，真实三端）

| 用例 | 修复前 | 修复后 |
|---|---|---|
| `test_f01_bff_first_submission_starts_run` | 409「启动权已被消费」 | 200 + run_id，且平台运行数 = 1 |
| `test_f01_bff_stream_first_submission_starts_run` | 409 | 流式首笔成功，运行数 = 1 |
| `test_f01_same_submission_replay_never_creates_second_run` | 重试 202、语义不明 | 重放复用（200 同 run_id）或 202，运行数恒为 1 |
| `test_f01_release_unstarted_claim_is_bounded_and_guarded`（台账） | 无释放路径 | 释放后可重试；有映射时 `released=false/mapping_present` |

**真实平台运行数量**（首笔用例）：BFF 首笔 1 个、流式首笔 1 个、重放后仍 1 个；F03 负例 0 个。

## 2. F02 恢复：摘要比对对象与作用域

**根因**：恢复复用旧 attempt 后，`start-claim` 取 `a.content_digest`（初次摘要，含
`operation_kind=initial`），而恢复提交有自己的 resume 摘要 → 必然 `mismatch/content`，
HTTP 200 里带 mismatch 被当成"恢复受理成功"。来源校验只按用户过滤（不校验线程/等待态）；
`confirm_admission` 按 attempt 批量改写全部已取权的提交。

**实现**
- 摘要比对对象改为**本次提交**：`COALESCE(s.content_digest, a.content_digest)`
  （管理端 `claim_start` 与 Agent 端 `attempt_reservation` 同步改），恢复不再被初次摘要误判。
- 来源校验：`operation_kind=resume` 时来源必须是**本用户本企业本线程**、且处于
  **可恢复等待状态**（`waiting_input`）；两条来源解析路径（显式 `source_attempt_id` /
  服务端按线程就近解析）统一适用。非等待态、跨线程一律拒绝。
- `confirm_admission` 改为**按 submission 作用域**（新增 `submission_id` 参数，缺省回退到
  "该尝试下最近一次已取权提交"），恢复确认不再牵连共享同一 attempt 的初次提交；
  恢复确认同时落**恢复映射**（`agent_run_resumes`：`original_run_id` + 新 `platform_run_id`，幂等）。

**先失败后通过**（`admin/api/tests/test_event_ledger_api.py`）

| 用例 | 修复前 | 修复后 |
|---|---|---|
| `test_f02_resume_start_claim_uses_its_own_digest` | 200 + `mismatch/content` | 200 + `claimed` |
| `test_f02_resume_source_must_be_waiting_and_same_thread` | 非等待/跨线程均受理 | 403（"当前没有可恢复的等待状态"）/ 跨线程拒绝 |
| `test_f02_confirm_is_submission_scoped` | 按 attempt 批量改写 | 初次提交保持 `admitting`；恢复提交 `confirmed`；`agent_run_resumes` 落 1 条映射 |
| 既有 `test_r03_resume_keeps_request_and_attempt` | — | 继续通过（补 `waiting_input` 前置，语义更严） |

### 2.1 真实三端 waiting → resume → 完成（复验补充）

复验指出本轮 F02 只有"管理 API 夹具把 attempt 置为 `waiting_input`"的用例，缺真实平台恢复的端到端。
现补 `tests/integration_tests/test_live_resume_roundtrip.py`（真实平台 resume，非替身）：

| 步骤 | 真实证据 |
|---|---|
| 1. 初次运行（真实 basic-qa 长文任务）→ **取消** | 平台 run 状态 `interrupted`（可恢复），不是 tinkering 的假中断 |
| 2. 受理结果按产品路径回填（真实 run_id）+ **服务级只读校准**（rs-v1，按 run_id 读真实状态） | 台账尝试收敛到 `waiting_input`（真实平台状态的结论，未重执行模型/MCP） |
| 3. 受理 resume（`source_attempt_id`） | 返回**同一 request / 同一 attempt**，`operation_kind=resume` |
| 4. 原生 `command.resume` 创建真实恢复运行 | 新平台 run，执行到 **success**（真实主模型回答） |
| 5. 计数与关系 | **请求 1 / 尝试 1 / 恢复事实 1 / 回答 1**；`attempt.platform_run_id` = 恢复运行；`agent_run_resumes.original_run_id` = 初次运行（保留原 run 关系）；平台共 2 个运行对应**同一次逻辑提问** |
| 6. 同键重放 | 复用**同一 submission**，平台运行数仍为 2（没有第三个运行） |

同一能力也在浏览器里走通：见 §8 的"恢复（F02）"步骤——页面内用真实 cookie 会话调用真实 BFF 发起
`x-operation-kind=resume` 的 `command.resume`，刷新后从侧栏打开该会话，可见恢复后的真实回答。

**本轮为该校准补的能力**：`--thread` / `Reconciler.reconcile_scope(thread_id=...)`——按线程收窄有界校准，
与 rs-v1 票据的线程清单一致，避免历史积压占满批次而漏掉目标会话（这也是把"仅按授权租户/线程定位"
落到可执行路径上）。

### 2.2 恢复的**并发**与**回包丢失**（复验补充）

`tests/integration_tests/test_live_resume_roundtrip.py::test_f02_resume_concurrent_and_lost_response`：

| 场景 | 真实结果 |
|---|---|
| 并发：两个同提交的恢复创建同时发出 | 恰好一个 200、另一个被启动权裁决拒绝（文本含"启动权"/"已有关联运行"），平台运行数 = 2（初次被取消 + 一次恢复） |
| **回包丢失**：两处均不调用任何确认端点 | 由图内**可信 run_id 回调**自行补齐映射：`platform_run_id` = 恢复运行、`original_run_id` = 被取消的初次运行；无需外部补偿 |
| 计数 | 请求 1 / 尝试 1 / 恢复事实 1 / 回答 1；同键重放复用同一 submission，不产生第三个运行 |

## 3. F03 真实图准入：换图在**执行前**被拒

**根因**：准入比对的是**客户端自报**的 `ledger_graph_id`，与预留一致即放行；
真实执行的图（由 `assistant_id` 决定）只在图结束时比对——那时模型/工具副作用已经发生。

**实现**
- 新增平台可信图解析：`resolve_executing_graph(assistant_id)`——图名直接命中平台图注册表
  （运行期 `langgraph_api.graph.GRAPHS`，回退平台声明的图清单环境变量）；助手 UUID 用
  `uuid5(NAMESPACE_GRAPH, 图名)` 反查（实测镜像 0.15.1 的映射，与平台
  `get_assistant_id` 一致，已用真实 run 的 `assistant_id=0ed7587b-…`=basic-qa 交叉验证）。
  两者都来自平台/部署，客户端不可写。
- 准入判定：`actual = resolve(assistant_id)`；**预留图必须等于 actual**；客户端所有自报图
  （`metadata` 与 `configurable` **分别取全部声明值**）与 actual 冲突即 409；无法解析 actual 即 403。
- `_pin_ledger_ids` 把 `ledger_graph_id` 定为**解析出的实际图**（覆盖客户端自报），
  图内收尾节点的换图比对因此以事实为基准。执行前的原生拒绝使业务节点执行计数为 **0**。

**先失败后通过**

| 用例 | 修复前 | 修复后 |
|---|---|---|
| `test_f03_graph_swap_rejected_before_execution`（合法 basic-qa 预留 + 自报 basic-qa + `assistant_id=intelligent-analysis`） | 200 受理并创建运行 | 409 拒绝，平台运行数 **0** |
| `test_f03_metadata_config_graph_conflict_rejected`（metadata 说 basic-qa、config 说 intelligent-tracing） | 200 | 409 拒绝，平台运行数 **0** |
| 六图合法入口（`test_live_six_graphs_run_acceptance`） | — | 六图带预留仍全部 200；无预留 403、伪造 409 保持 |

## 4. F04 永久删除：清理完成后仍禁读禁写

**根因**：`is_thread_deletion_pending` 只匹配 `state <> 'completed'`，清理一完成该会话就重新可读、
可新写；`worker.py` 重放与 `store.py` 孤立补投递的排除条件同样带 `state <> 'completed'`；
原生 `DELETE /threads/{id}` 只删归属登记，直连删除路径未接台账。

**实现**
- 删除判定改为**存在即已删除**（永久墓碑，`state` 仅作运维可见性）：管理端回读/原文/新受理，
  Agent 侧重放/孤立补投递/版本重建全部按"存在删除记录"排除。
- **原生删除接入**：`on_threads.delete` 先落持久删除标记（`record_thread_deletion`，幂等 + 排入
  受限清理），标记失败则拒绝删除；直连原生删完不再可读/可写。
- **创建运行封口**：`on_threads.create_run` 增加删除校验（封住"旧预留 + 直连原生"旁路）。

**先失败后通过**

| 用例 | 修复前 | 修复后 |
|---|---|---|
| `test_f04_deleted_thread_stays_blocked_after_cleanup`（管理 API，模拟清理完成） | 明细 200、新写 200 | 明细 404、原文 404、新受理 403 |
| `test_f04_deletion_record_is_permanent_after_cleanup` | — | 记录保留（state=completed 且 user_id 在位） |
| `test_f04_orphan_repair_skips_deleted_thread_after_completion`（台账） | 清理完成后可补投递 | 0 条被复活 |
| `test_f04_replay_skips_deleted_thread_after_completion`（台账） | 重建计划含已删会话 | 重建计划为空 |
| 真实浏览器删除（§8） | — | 明细 404 / 新受理 403 / 校准读取 404 |

### 4.1 恢复旧备份 → 重施删除 的永久语义演练（复验补充）

复验指出：删除改为永久语义后，首轮那套演练（旧语义）没有重跑。现补
`docs/reviews/evidence/flash-02-repair-02/restore_drill_f04.py`（**随机命名**专用库，只用完即删自己建的库）：

| 阶段 | 结果 |
|---|---|
| 备份前 | 存活原文 2、删除记录 0 |
| **备份之后**才删除（并把清理标记为 `completed`，即新语义下的"已清理完成"） | 已删原文 1、删除状态 `completed` |
| 灾难 + 恢复旧备份 | head `0009`；**删除记录 0**、该线程原文又是"未删除" —— 正是要防的泄露风险 |
| 重施外部删除记录 + 当前权限 | 删除记录在位 1；被删线程原文仍受挡 1；其它原文保留 1；**备份后的新写保留 1** |
| 恢复后 RLS | 无 GUC 0 / 本人 3 / 跨企业 0；被删线程可见未删除行 **0** |
| 收尾 | 只 `DROP` 本脚本自建的随机库 |

（脚本内 `permission_regrant` 行里的 `open_deletions=0` 是该脚本沿用首轮的计数口径——只数
`state <> 'completed'`；在本轮永久语义下删除记录本就带 `completed`，真实断言以
`deletion_present_any_state=1` 与上面的表为准。）

### 4.2 删除后的**晚到回调**：一律拒绝且不复活事实（复验补充）

复验指出"晚到回调"未验证。本轮先补回归再修：

| 层 | 用例 | 修复前 | 修复后 |
|---|---|---|---|
| Agent 侧（事实写入） | `test_f04_late_callback_after_deletion_is_refused` | 图内收尾/状态校准等路径不带删除检查，可把回答与事件写回已删会话 | `_write_event_in` 增加**永久删除闸门**（线程可由事件字段或 attempt/request 反查），抛 `ThreadDeleted`；事件/回答/投递任务计数均为 0 |
| Agent 侧（登记自身） | `test_f04_deletion_registration_itself_still_writes` | — | 删除登记（含重复登记）仍能写入且幂等——闸门不挡登记 |
| 管理侧（内部回调） | `test_f04_late_internal_callbacks_after_deletion_are_refused` | 受理确认/状态/回答/恢复/Trace 五个内部写端点删除后仍会写新事件 | 全部 409 `thread_deleted`，事件数与回答/恢复映射**零增长** |

**顺带查实的真实缺陷（重要）**：原生删除登记一直在写一个**被 v1 契约拒绝的事件**
（payload 多带了 `deletion_entry`，注册表只允许 resource_type/resource_id/deletion_state/deleted_at），
而 `_write_event_in` 只以 `written:false` 静默返回 → **清理任务从未入队**（删了但没人清）。
现按冻结契约去掉该字段，并让"事件被契约拒绝"**显式失败**（抛 `LedgerBackendUnavailable`，
原生删除入口因此拒绝删除而非"删了没登记"）；测试夹具也改为走真实登记路径。

## 5. F05 平台映射 + 服务级只读校准

**根因**：图内结束只写答案与终态，不补平台运行映射 → "平台 success、台账 succeeded、
答案 1，但 `platform_run_id` 空"；`attempts_needing_reconcile` 只筛 active，终态缺映射记录被补偿**永久跳过**；
`LEDGER_RECONCILE_TICKET` 只是环境变量名，身份体系并不签发服务级凭据，"原成员撤销后仍可校准"不成立。

**实现（映射）**
- 图内结束前用**平台注入的可信 run_id**（`configurable.run_id`，实测镜像 0.15.1 在图内可见；
  另有 `metadata.run_id` 兜底）补映射：无映射 → `attach_platform_run` 收敛；已有映射但不同
  （人工 resume 的新 run）→ `record_resume` 追加恢复映射，保留原 run 关系。
- 补偿候选纳入"**终态但缺 platform_run_id**"（`missing_mapping`），不再只筛活跃。

**实现（服务级只读校准，rs-v1）**
- 新密钥集与受众：`LEDGER_RECONCILE_KEYS` / `LEDGER_RECONCILE_AUDIENCE`，与用户运行票据**完全分离**，
  独立轮换；未配置时校准如实输出 `blocked(missing_read_only_calibration_credential, rc=2)`。
- 签发：管理端内部端点 `POST /agent/reconcile/ticket`（内部令牌门控，**不需要任何用户会话**），
  绑定租户、可再以 `thread_ids` 收窄、TTL ≤ 1h。
- 认证：平台 `@auth.authenticate` 在用户票据/迁移票据之后尝试 rs-v1，返回**服务主体**
  （无 user/membership/permissions）；`threads.read` 仅按票据租户（+线程清单）只读放行，
  且归属必须落在该租户（`fetch_thread_in_tenant`，租户级 RLS 策略）。写操作/其它资源仍被各自钩子拒绝。
- **有界 + 可续跑**：`Reconciler.reconcile_scope(cursor, max_batches)` 按批次推进游标，
  每次运行落一条 `agent_ledger_reconcile_runs`（`0009` 新表：作用域、游标、批次、计数、状态）；
  CLI 暴露 `--max-batches/--cursor/--ticket-file/--actor`。

**先失败后通过**

| 用例/证据 | 修复前 | 修复后 |
|---|---|---|
| `test_f05_native_success_persists_platform_run_id`（真实主模型最小回答） | `platform_run_id` 空 | 与平台 run_id 一致 |
| `test_f05_reconcile_includes_terminal_without_mapping`（台账） | 终态缺映射被跳过 | 进入候选且 `missing_mapping=true` |
| `test_f05_service_calibration_survives_member_revocation`（真实三端） | 无服务级凭据（只能靠用户会话） | 原成员会话撤销后（用户票据 401）服务票据仍 200；**服务票据创建运行 403**（不恢复业务权限） |
| `test_f05_cross_tenant_calibration_denied` | — | 票据线程清单外的线程 403/404 |
| CLI 真实运行（`service-calibration.json`） | 只报 blocked | `status=ok`，2 批次、10 条候选全部类别化（kept_unknown/no_platform_run_id），并落校准运行记录 |

### 5.1 任务 6.1 的"冻结 OpenAPI"（本轮交付）

上一轮把 6.1 记为未交付（缺冻结 OpenAPI）。本轮补齐并**用漂移检测锁住**：

- `docs/contracts/agent-events-v1/openapi-agent-ledger.json`：台账 API 面（`/agent/*`）**19 条路由**的冻结签名；
- `admin/api/scripts/freeze_ledger_openapi.py`：生成/重新生成（人工复核后执行）；
- `admin/api/tests/test_ledger_openapi_freeze.py`：与运行期 `app.openapi()` 逐条比对，路由缺失/新增/签名漂移都会失败；
- 生成过程中发现并修正了 `start-claim` 路由文档里"原生入口会再裁决一次，双重保护"的**过期描述**（F01 起裁决点已单点化）。

另有 6.1 的列表侧收口：`test_61_runs_listing_cursor_scope_and_max_page` 覆盖游标作用域与最大分页，
并修掉一个真实缺陷——**游标含 `+00:00`，经查询串解码成空格后无法翻页**（服务端生成 → 客户端回传必然 400）；
现生成侧 `quote`、解析侧 `unquote`（兼容历史未编码形态）。

## 6. F06 正常原问题来源：真实用户入口采集

**根因**：BFF 默认 `store_content=false`，真实普通提问只留摘要；表里字段与手工
`store_content=true` 测试通过不能代替"真实用户入口采集"。

**实现**：BFF 在正常提交时按**本次用户原问题文本**采集受控快照（`storeContent` + 保留期
`LEDGER_CONTENT_RETENTION_DAYS`，默认 90 天，缺省非法值回退 90），空内容（如 resume）不落快照；
正文只进 `agent_content_refs`，**不进入普通事件与指标**（事件载荷仍只有摘要前缀）；
按 `request_id` 精确回读、受删除与保留期约束。

**先失败后通过**

| 用例 | 修复前 | 修复后 |
|---|---|---|
| `test_f06_bff_submit_stores_request_content_source`（真实 BFF 提交） | 无快照 | 恰一条未删除快照，`request_id` 与请求一致，正文与原问题一致 |

## 7. 迁移与配置影响

| 项 | 内容 |
|---|---|
| 迁移 0009（新） | ① `agent_submissions.admission_status` 取值域增加 `released`（释放启动权需要，原 CHECK 只允许三项）；② 新表 `agent_ledger_reconcile_runs`（有界/可续跑校准的审计落点）+ 索引 + 运行角色租户 RLS + 受限角色最小列只读；③ 授权语句以 `ledger_grant_statements(role_checker)` 暴露（与 0007/0008 同形），隔离环境可用随机受限角色复现 |
| 未改写 | `0007`、`0008` 一字未动；`0009.downgrade` 抛 `NotImplementedError`（回退等同重新引入已复验缺陷） |
| 升级验证 | 业务库 `0008→0009` 已执行；`air_agent_tenant_test` 升级到 0009；3 套全新空库 `0001→0009` 通过 |
| 新配置 | `LEDGER_RECONCILE_KEYS`（服务级只读校准密钥，与运行票据分离；未配置=不签发，reconcile 报 blocked）、`LEDGER_RECONCILE_AUDIENCE`、`LEDGER_CONTENT_RETENTION_DAYS`（默认 90）；均写入 `.env.example`（真实值在本地 `.env`，不入库） |
| 契约变化 | `POST /agent/runs/admission-confirmed` 增加可选 `submission_id`（确认按提交作用域）；新增 `POST /agent/reconcile/ticket`（内部令牌） |
| 前端 | BFF 去掉预 claim；上游 409 按结构化 code 分流；正常提交采集受控快照；`getRequestDetail` 辅助（普通模块，非 Route 导出） |

## 8. 真实浏览器验证（区分证据层级）

`docs/reviews/evidence/flash-02-repair-02/browser-verification.json`（10 步，真实浏览器 + 真实 API + 真实主模型）

| 步骤 | 观察到 |
|---|---|
| 登录 → 选择 E2E企业A | 正常进入对话页 |
| **普通提交**（F01 关键正向） | 用户气泡出现、助手"正在调度模型与工具…"→ 渲染真实主模型回答（basic-qa，21061 tokens）；**修复前此路径首笔 409** |
| **刷新回读** | reload 后从侧栏重开该会话，问题与完整回答可回读 |
| **停止** | 前端进入保守态（"停止尚未确认…确认前不能切换企业或发送新问题"）；服务端事实：平台 run=`interrupted`、台账 `cancel_requested=true/origin=user_stop`；未伪造终态 |
| **导航/刷新** | 页面正常，未产生伪装停止事实（导航停止事实由契约用例断言） |
| **企业切换** | reload 重置前端内存态后切换 E2E企业A→B 成功，侧栏切换为 B 企业视图（不再显示 A 企业会话）；首次切换尝试被既有 stopPending 保守态拒绝（提示原文如实记录；本轮**未改** `chat-store.ts`/`ChatPanel.tsx`/`langgraph-api.ts`） |
| **删除（用户路径）** | 侧栏「删除会话」→ 台账落 `state=requested` 且记录归属人；随后请求明细 404、新受理 403、原文 403、服务级校准读取 404 |
| **恢复（F02）** | 合成线程先经真实运行+取消得到平台 `interrupted`、再由服务级只读校准收敛为等待态；页面内用真实 cookie 会话调用真实 BFF 发起 `command.resume` → 返回新 run id；刷新后从侧栏打开该会话，**显示恢复后的真实模型回答**；服务端计数：请求 1 / 尝试 1 / 恢复事实 1 / 回答 1，`original_run_id` 保留初次运行（`browser-resume-counts.json`） |

**证据层级说明**：本表为真实浏览器 + 真实 API + 真实主模型；平台库/台账库直接复核服务端事实；
未使用模型替身；真实企业 MCP 仍安全禁用（未为本轮恢复全局凭据）。

## 9. 验证、用时与修复次数

| 套件 | 本轮结果 | 与独立复验基线对比 |
|---|---|---|
| 台账专项（contract+lifecycle+store+反例回归） | **70 passed** | 独立复验 64 → 本轮新增 4 条 + 复验补充 2 条（F04 晚到回调、删除登记幂等） |
| 真实等待→恢复往返 | **2 passed**（真实平台 resume：往返 + 并发/回包丢失） | 本轮复验补充（§2.1/§2.2） |
| 管理端 `admin/api/tests/` | **156 passed / 0 failed** | 独立复验 148 → 本轮新增 5 条（F02×3、F04×2）+ 复验补充 3 条（F04 晚到回调、6.1 运行明细、OpenAPI 漂移） |
| 根单元（`LANGCHAIN_TRACING_V2=false`） | **303 passed / 70 skipped / 5 failed** | **与独立复验基线完全一致**（checkpoint 关闭 1、knowledge 路由 4）；失败断言未删除/未弱化/未新增 skip |
| 集成（`RUN_TENANT_LIVE_E2E=1`） | **31 passed / 7 skipped / 1 failed** | 独立复验 20/7/1 → 本轮新增 9 条真实三端边界（F01×3、F03×2、F05×3、F06×1）**+ 2 条真实恢复（waiting→resume→完成、并发+回包丢失）**；唯一失败仍为既有 500k monkeypatch |
| 前端契约 | **31 passed / 0 failed** | 独立复验 31 → R01 代理用例按新架构重写并**加强**（新增"首笔必须成功"正向锁） |
| 前端 tsc | 通过 | — |
| **隔离干净生产构建** | **通过**（`exit=0`，`Finished TypeScript`，无 TS2344） | 保持首修成果 |
| Ruff（本轮改动的 Python/迁移） | 全部通过 | — |

修复后逐个复跑命令与原始输出见 `docs/reviews/evidence/flash-02-repair-02/`
（`ledger.txt`、`admin.txt`、`unit.txt`、`integration.txt`、`contracts.txt`、`clean-build-after.txt`）。

| 计时与修复次数 | 值 |
|---|---|
| 实际开始 / 结束 | 2026-10-13 15:5x（本轮目标下发）→ 2026-10-13 17:4x（报告完成） |
| 逐项实测（工具计时） | 台账 68 项 **1.7 s**；管理端 153 项 **62 s**；根单元 **28 s**；集成 37 项 **37 s**；真实三端边界 9 项 **12 s**；前端契约 31 项 **12 s**；隔离干净构建 **~19 s** |
| 复验补充段用时 | 2026-10-13 18:0x–19:0x（F02 真实往返 + 浏览器恢复 + F04 演练 + 凭据清理 + 套件复跑） |
| 环境等待（可观测） | langgraph-api 容器重建 **4 次**（含 1 次容器名残留需 `rm` 后 up；1 次新增环境变量需重建才生效），每次 60–150 s；管理服务重启 3 次；业务库迁移 0009 **1 次** |
| 复验补充段新增能力 | 校准按线程收窄（`--thread` + `Reconciler.reconcile_scope(thread_id=…)`）：把「仅按授权租户/线程定位」落到可执行路径；另修自身缺陷 3 处（校准批次被历史积压占满、联调台账库被隔离测试变量覆盖、证据文件凭据清理） |
| 复验补充二段（本轮）新增能力 | 删除后**晚到回调**闸门（Agent 侧事实写入 + 管理侧 5 个内部回调端点）；冻结 OpenAPI（19 条路由）+ 漂移检测；另修两处真实缺陷（删除事件被契约静默拒绝导致清理不入队、运行明细游标经查询串被解码成空格而无法翻页） |
| 本轮修复次数 | **F01–F06 各 1 组实现改动**；实现过程中修掉的自身缺陷 **7 处**（认证分支顺序导致用户票据被 401、`_parse_key_pairs` 漏 label、路由缺少内部令牌依赖、rs-v1 分支位置、租户读取需平台 GUC、隔离库未建 0009 新表、R01 契约用例随架构调整重写而非放宽；复验补充段另修 3 处：删除事件载荷越界、游标未编码、契约脆弱断言按 URL 定位） |
| 首交 1 小时 52 分 + 首修 1 小时 49 分（累计 3 小时 41 分） | **原样保留**，不计入本轮 |
| Token | 工具不提供计量 → **未采集**；测试/构建耗时**不代表**应用运行性能 |

## 10. 仍未完成（如实保留，不声称通过）

1. change 1 **7.6 真实企业 MCP**：外部材料缺失，继续安全禁用（本轮未恢复全局凭据）。
2. change 1 **10.3 完整矩阵**：六图循环/长运行/两前端矩阵未做。
3. **停止确认体验**：平台 run 已 `interrupted`、台账已记 `user_stop`，但前端在宽限窗口内未观测到终态时
   会保持保守态；若此时删除该线程，界面无法再无刷新地清除该态（reload 后正常）。本轮**未改**停止逻辑，
   如实记录为既有交互限制，供后续 UI 轮次处理。
4. **旧 HMAC 治理套件**（`test_governance_live.py`）：仍以旧式会话认证，属既有失效；本轮未删除/未 skip/
   未改成假成功，需身份 change 负责人迁移。
5. **服务校准的部署前置**：rs-v1 能力已实现并实测（含撤销后可用、跨租户拒绝、有界续跑），
   但**生产密钥与运行主体仍需部署侧配置**（`LEDGER_RECONCILE_KEYS`），本轮只在本地验证。
7. **凭据卫生（复验指出后已修）**：`service-calibration.json` 已删除签发响应体（rs-v1 票据前缀），
   全目录复查无票据/口令/cookie/正文（含浏览器轨迹）；报告与证据均不再出现明文凭据。
8. **服务凭据的能力边界（如实记录）**：平台把**运行列表**按主体作用域过滤，因此 rs-v1 服务主体
   可以按 run_id 读取状态、但**列不出线程的运行**（实测：同一线程用户票据列出 1 条，服务票据 0 条；
   metadata/status 过滤同样为空）。影响：`locate_run`（按可信 metadata 找回"响应丢失"的运行）
   在服务凭据下不可用，需用仍有效的用户票据；服务凭据用于"已知 run_id/尝试"的状态校准。
   已据此把 F02 的真实往返改为"按产品路径回填 run_id → 服务校准读状态"，并把可按线程收窄的
   有界校准（`--thread`）作为服务路径的正式能力。
9. **本轮顺带查实的真实缺陷（已修，记录在案）**：① 原生删除登记一直在写**被 v1 契约拒绝的事件**
   （多带 `deletion_entry`），且拒绝被静默吞掉 → 删除的清理任务从未入队；② 运行明细**游标含 `+00:00`**，
   经查询串解码成空格后必然 400，翻页实际不可用。两者都由"先补回归"暴露，已修并加断言。
10. **前端契约套件的一处既有脆弱断言**（`calls.at(-1)` 假定"最后一条即被转发的创建请求"，后台归属确认调用
   可能后到）在本轮一次并发负载下抖红一次；已改为**按 URL 精确定位**目标请求（断言强度不变），
   连续 3 次单文件 + 3 次合并运行均 31/31。如实记录，不作为"已通过"以外的解释。
11. Token/成本未采集；本节不计入产品缺陷。

## 11. 与独立复验 14 项待修的对应

本轮据实际证据重新勾选任务（`openspec/changes/add-agent-event-ledger/tasks.md`）：现为
**36 项完成 / 2 项未完成**。由本轮 F01–F06 的实现与红→绿证据支撑并勾选的是
**2.3 / 3.2 / 3.3 / 3.4 / 3.6 / 4.1 / 4.4 / 5.5 / 6.1 / 7.1 / 8.1 / 8.2**（共 12 项；6.1 在本轮补交付冻结 OpenAPI 与列表侧收口后勾选）。

**保持未完成（不勾）**：

| 项 | 原因 |
|---|---|
| 8.3 | 该项指向的首交验收报告按约束**不改写**；本轮最终状态记录在本报告。是否以 repair-02 作为归档口径、并回填首交报告，属协调者裁定 |
| 8.4 | 冻结候选的实际冻结提交，按约定由协调者在独立验收通过后填写 |

## 10.1 与首修复验基线的差异说明

独立复验基线为 admin 148 / 台账 64 / 根 303·5 / 集成 20·7·1 / 前端契约 31 / 干净构建通过；
本轮为 admin **156**、台账 **70**、根 303·5（一致）、集成 **31**·7·1（新增 9 条真实三端边界 + 2 条真实恢复）、
前端契约 31（R01 用例按新架构重写并新增正向锁；另把一处脆弱断言改为按 URL 定位）、干净构建通过。**既有失败断言未删除、
未弱化、未新增 skip**：根单元仍是同样 5 项既有失败，集成仍是同一项既有 500k 失败。

## 12. 交付状态

**待独立复验**。diff 保留、未 commit / push / merge / archive / 冻结；未改协调者评分；
未改写首交（`agent-event-ledger-acceptance.md`）、首修（`agent-event-ledger-repair-01.md`）
与协调者报告及独立探针断言；未启动第 3/5 项。
