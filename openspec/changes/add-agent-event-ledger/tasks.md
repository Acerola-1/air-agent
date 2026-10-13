# Tasks

> 勾选状态=**当前独立验证范围内完成**；作者就地测试说明保留为历史，当前独立结论见 `docs/reviews/flash-blind-02-repair-01-review.md`。
> 未勾选项含明确缺口说明；部分完成的项目在条目后标注实际完成范围。
> 交付状态：**匿名模型最终未独立完成；协调者补修后本地验收通过，待实际提交冻结**。CLI实际任务总数38，作者原“31/31全部闭合”计数不准确。下方原证据说明保留为作者自述历史；未勾选项由协调者根据独立反例重新打开，并非已完成。

## 1. 依赖与真实入口核验

- [x] 1.1 核对第一项的独立接管报告、HEAD+冻结工作树指纹和身份/RLS/运行授权实际接口，交付字段及权限映射；仅在协调者已放行的本地边界实施，生产门禁仍保留，关键接口未通过则停止依赖该接口的任务。
      — 393 个非秘密文件 sha256 逐字节匹配 `change2-source-baseline.json`，无偏差；生产门禁（真实MCP/完整矩阵）保留未动。
- [x] 1.2 对当前镜像验证 run_id 指定/重复创建、metadata 查询、流式/非流式入口和人工恢复，记录可复跑请求及结果，选定 design 第 3 节的受理适配路径。
      — `docs/reviews/evidence/flash-02/task-1.2-image-capabilities.md` + 两个探针脚本及原始输出；结论：走受控创建 + 可信关联 metadata + 受理校准。
- [x] 1.3 固定 v1 对象、事件登记、状态表及指标字典，交付 `docs/contracts/agent-events-v1/` 与 schema/跨语言合法非法样本；TS/Python 同一组契约测试通过。
  — 契约包未变（21 样本、双语言契约测试通过）；ownership.md 已改为**真实 18 条路由**（含 start-claim/content/deletions/due/replay），与 OpenAPI 一致。


- [x] 1.4 建立不依赖真实 MCP 凭据的六图生命周期测试适配与独立 PG 环境，文档列出与真实外部业务运行的区别，验证不连接或重置共享业务卷。
      — 专用库 `air_agent_ledger_test` + 受限调度角色；六图在 MCP 降级为空列表下真实运行并落账；未重置共享业务卷。

## 2. 持久结构与隔离

- [x] 2.1 为 request/submission/attempt/resume/answer/trace 及事件/outbox/receipt/投影建立显式 Alembic 迁移和复合唯一/外键，真实 PG 验证升级与跨租户关联拒绝。
  — 0007 + **0008 纠正迁移**：事件 request/attempt/answer 同租户复合外键 + `(attempt_id,request_id)` 配套约束 + submission 复合外键；空库迁移、0007 旧库升级与**冲突预检**（违规即中止）均实测（`upgrade_0007_to_0008.sh`）。


- [x] 2.2 接入第一项的租户事务和非 owner RLS，区分运行/查询/调度/迁移角色，A/B 交错、异常及取消后连接复用测试通过。
      — 运行/调度/迁移角色分离；真实 PG 五组 GUC 断言（有/无/跨租户/他人/运营）；admin 套件 A/B 用例通过。
- [x] 2.3 建立原问题受控快照或不可漂移版本引用、删除标记和保留配置，验证普通事件/运行列表无正文、票据或密码哈希。
  — F06：正常 BFF 提交采集按 request 的受控快照（保留期 LEDGER_CONTENT_RETENTION_DAYS=90），真实 API 用例先红后绿；事件载荷键集合实测无正文（`agent_events.payload` 无 content/票据/哈希），快照仅在 agent_content_refs 且受删除/保留期约束。
  — 原文按 **request** 精确引用（每请求一份未删除快照的部分唯一索引），保留期到期拒绝；删除标记立即禁读/禁新写；普通事件/运行列表仍无正文。


- [x] 2.4 实现事实/事件/outbox 同事务及消费 receipt/投影同事务，事务回滚与确认丢失重投测试通过。
  — Agent 侧全部事实（状态/回答/恢复/停止/Trace）与事件、outbox **同一事务**；receipt 与投影同事务且在副作用之前；故障注入证明整笔回滚（`test_r02_*`、`test_r06_*`）。


- [x] 2.5 文档说明表所有权、角色、索引、连接初始化和配置；验证 graph 导入无外部 I/O、Alembic head 唯一且不改平台内部表。
      — 迁移文档串 + `.env.example`；六图导入对照（仅注册节点/中间件，无台账 I/O）；head 唯一 `0008`；未触碰平台内部表。

## 3. 提问受理和版本关联

- [x] 3.1 实现可信身份受理、submission_key 内容摘要与并发幂等，测试同键同内容复用、不同内容 409、伪造归属和旧上下文拒绝。
  — 受理/幂等/409 语义未变且回归通过；新增启动权原子裁决（同提交只能启动一次）。


- [x] 3.2 落地经 1.2 验证的受控创建与 run_id 关联，测试 PG 受理失败不启动、重复请求不新建运行、直连缺台账上下文拒绝。
  — F01/F05：启动权单点裁决（原生）；重复请求复用同一运行（真实三端重放用例）；无预留 403、伪造 409、直连缺台账拒绝；图内补可信 platform_run_id。
  — 受控创建前先取启动权（`POST /agent/runs/start-claim`），重复请求复用既有运行或 202 待确认；原生入口同样裁决，缺预留拒绝。


- [x] 3.3 实现 admission_unknown 校准，注入\"上游已受理但响应丢失\"和\"已提交但确认未写入\"故障，验证未知时不盲重发、重启恢复不双执行。
  — F01：上游不可达 → 202 待确认且不重发；有界补偿按可信 metadata 定位既有运行收敛；确认无运行且超宽限期才释放启动权（`release_unstarted_claim`，有映射不动），因此重启/重放不双执行。
  — 受理歧义不盲重发；新增按**可信 metadata** 定位既有 run 并收敛映射（`admission_recovered`），定位不到仍保持待确认。


- [x] 3.4 实现新提问/失败重试/重新生成/人工恢复的对象关系，确定 fixture 验证请求、尝试及恢复计数和内部 retry 不增加提问。
  — F02：恢复保留原 request/attempt 且按 submission 摘要裁决；来源必须同用户企业线程的可恢复等待态；确认按 submission 作用域并落恢复映射（原 run 关系 + 新 run）；既有 R03 用例继续通过。
  — submission 稳定绑定自己的 attempt；重放回到原 attempt；resume 复用原 request/attempt 不新建提问；来源须同用户/企业/线程。


- [x] 3.5 建立用户可见 answer 版本与 trace 多重映射写入/回读，测试草稿排除、部分/确认区分、重放幂等及辅助 Trace 晚到。
  — 回答版本与 Trace 多重映射写入/回读未变；写入改为与事实同事务，事件带 attempt/answer/run 关联。


- [x] 3.6 前端 API/store 接入关联 ID、稳定提交键和回读，保留当前渲染与 SSE 契约；前端契约测试与重放/刷新实际操作通过，说明新增错误状态。
  — F01/F06：前端契约 31 通过（R01 代理用例按新架构重写并新增\"首笔必须成功\"正向锁）；真实浏览器提交/刷新回读通过；新增状态 already_confirmed/admission_unknown 在契约中断言。
  — 前端接入 submission/attempt 关联、稳定提交键、启动权与删除标记；SSE 与渲染未变；新增 R01 代理回归（复用/待确认时零平台调用）。


## 4. 六图状态与停止事实

- [x] 4.1 在六图流式及获准非流式路径接入可信 lifecycle，验证每个图都有开始/完成或异常关联且无未登记创建入口。
  — F03：准入在执行前由平台图注册表解析实际图并比对预留（换图/冲突 409 且运行数 0）；六图带预留全部 200，无预留 403；BFF 流式首笔成功。
  — 六图 lifecycle 未变；**未登记创建入口已封**：无预留 403、伪造 409、六图带预留全 200；图内收尾节点以**实际执行的图名**与声明比对，不一致只落冲突证据、不记回答。


- [x] 4.2 实现带证据/修订版本的状态转换，测试乱序、重复、来源冲突和等待人工/恢复，不由 onDone 或断流推断成功。
      — `test_observe_state_*`、`test_state_evidence_rank_and_no_terminal_regression`、`business_state_from_native` 未知不猜。
- [x] 4.3 将既有停止/导航/企业切换来源写成独立停止意图，测试先停止后 run_id、完成竞争、未知阶段与实际取消确认。
      — 六种 origin + 四阶段；先停止后 run_id（proxy 实测 recorded）；同秩终态冲突保留；`guessed` origin 422。
- [x] 4.4 接入最小授权的补偿查询与状态待确认，真实 API 验证进程退出、接口不可用、对象缺失、撤销后校准和可信超时，禁止重执行业务操作。
  — F05：rs-v1 服务级只读校准票据（独立密钥/受众、绑定租户、可收窄线程、TTL≤1h、内部端点签发不依赖用户会话）；真实三端验证\"原成员会话撤销后校准仍 200\"、跨租户/线程清单外拒绝、服务凭据创建运行 403；有界+可续跑并落校准运行记录。
  — 校准新增按可信 metadata 定位与映射收敛；只读凭据独立于成员会话（`LEDGER_RECONCILE_TICKET`），**未配置时如实报阻塞**（rc=2），不把跳过当闭合。


- [x] 4.5 运行消息治理、正文确认、完整资料归档/计算相关回归，文档列出六图适配点和原生状态到业务状态映射及未知处理。
      — 归档租户 RLS + 治理 ToolNode + 500k 守卫集成回归：11 passed / 1 failed（**既有** `test_mcp_500k_overflow_end_to_end` monkeypatch 失败，基线保留）/ 16 skipped；根单元 300 passed 无新增失败。
      — 六图适配点与原生→业务状态映射见 `states.json`、`lifecycle.business_state_from_native`（未知一律 `status_unknown`）。

## 5. 事件受理与工作进程

- [x] 5.1 实现事件 v1 来源/类型白名单、载荷限制、关联验证和同 ID 冲突，测试浏览器伪造终态/usage、未知类型/版本、重复与跨日迟到。
  — 白名单/载荷/关联校验未变；同 ID 冲突判定扩大到**信封身份字段**（类型/来源/关键关联），不只比 payload。


- [x] 5.2 提供独立 browser/runtime 扩展适配及预留类型启用机制，测试未启用事件明确拒绝，交付第 3/5 项的契约样本而不假装已采集。
      — `event-registry.json` 已登记 8 个预留类型（`enabled=false`）并逐个断言拒绝；责任表 `ownership.md`。
- [x] 5.3 实现租约/领取/退避/死信与消费幂等，真实 PG 双 worker 验证重领、租约过期/旧持有者确认拒绝、毒事件及每租户有界处理。
  — 租约加**代次台账**：领取记代、确认前校验租约与代次，陈旧持有者拒绝确认；receipt 幂等裁决在副作用之前；双 worker/死信回归通过。


- [x] 5.4 实现有界 lifespan/关停、健康指标和脱敏错误，验证在途退出、重启恢复、积压可见且无逐 token 同步写入。
      — 真实进程演练：worker 流式中途 `SIGKILL`（exit=137）→ 94 行停在 `leased`（持有者为被杀进程）；租约到期后新 worker 全部重领为 `done`，**重复 receipt = 0**。
      — `test_inflight_shutdown_returns_lease`（关停归还）、`test_live_lease_not_stolen_by_other_worker`（独占）、`outbox_health` 积压可见；无逐 token 同步写入。
- [x] 5.5 提供带 dry-run/租户/窗口/消费版本的重放和投影重建工具，测试重放计数一致、删除优先及不调用模型/MCP，并交付可运行说明。
  — F04：重建/补投递按\"存在删除记录\"排除已删会话（清理完成亦然）；新增回归断言重建计划不含已删线程；工具仍不调用模型/MCP。
  — `replay` 改为从保留事实重建（**含已 done**），真实使用 consumer_version/租户窗口/游标/删除规则，并写运维审计；不调用模型/MCP。


## 6. 查询授权与首期指标

- [x] 6.1 实现本人 request/attempt/answer 回读及企业运行明细分页，测试本人/他人/跨企业/撤销、cursor 作用域和最大分页，冻结 OpenAPI。
  — 回读/权限用例通过；本轮补 `test_61_runs_listing_cursor_scope_and_max_page`（游标作用域跨企业不泄露、limit>200 显式 422、列表不含正文）并修掉游标含 `+00:00` 经查询串被解码成空格导致**无法翻页**的真实缺陷；**冻结 OpenAPI 已交付**：`docs/contracts/agent-events-v1/openapi-agent-ledger.json`（19 条 /agent 路由）+ 漂移检测 `admin/api/tests/test_ledger_openapi_freeze.py` + 生成器 `admin/api/scripts/freeze_ledger_openapi.py`。
  — 功能与权限用例（本人/他人/跨企业/撤销、分页）已通过；**\"冻结 OpenAPI\"未交付**（契约目录无冻结的 OpenAPI 产物），故本项保持未完成。
  — 回读/分页未变；新增删除中统一 404 与原文按请求精确读取。


- [x] 6.2 实现统计、运营元数据、原文及运维独立权限，测试平台账号无隐式原文权、字段白名单和错误不泄露其他企业存在性。
      — 四个独立权限码；`tenant_admin` 不含 `agent:content:read`/`agent:replay:manage`；仅元数据权限读原文 403；跨企业 404。
- [x] 6.3 实现基础 summary 的提问/尝试/恢复/活跃/状态/完成率/停止意图，fixture 核对预期分子分母且与明细一致。
      — `test_metrics_denominator_and_not_collected`（分子分母 + `not_ended` 单列 + 明细一致）。
- [x] 6.4 实现可信服务延迟、样本/缺失、明细分位数、UTC 与业务日窗口，边界/七日/迟到修订测试通过，浏览器和模型时间不混算。
      — `server_latency_ms` = 服务端时钟 `admitted_at → ended_at` 的 `percentile_cont` P50/P95/P99，附 `sample_count`/`missing_count`/`quantile_method`；不与浏览器墙钟相减、不平均每日分位数。
      — 证据：`test_metrics_latency_percentiles_from_detail`（确定性 100/200/300/400/1000ms → P50 200–300、P95≥700、P99≥P95）。
- [x] 6.5 实现口径版本/截止/投影时间/延迟和 not_collected/unknown/estimated 等质量标签，测试尚未接入的反馈/复制/usage/成本不返回伪零，并交付响应样本。
      — `metric_version=v1` + 质量标签；未接入项返回 `null` + `not_collected` + `reserved_for`；输出样本见 `metrics.json` 与 e2e 输出。

## 7. 删除、迁移与恢复

- [x] 7.1 接入会话删除与租户保留配置，测试删除后立即禁读、异步清理失败重试、outbox/死信/重放不恢复正文。
  — F04：删除为永久语义（存在即已删除）；原生删除同样落持久标记；清理完成后回读/新写/补投递/重建全部拒绝；真实浏览器删除后 API 复核 404/403。
  — 删除入口校验归属或独立权限（`agent:thread:delete`）；BFF 真实删除先落持久标记；读路径/新写统一限制；最小权限 worker 按归属人受限清理，0 行不假 done。


- [x] 7.2 提供历史 dry-run/显式归属/缺失报告与幂等导入，隔离 PG 验证计数一致、不补造反馈/Token/时间、未知归属不进入企业报表。
      — `history-report`（只读归属/缺失计数，`native_pending` 计为不可读）+ `scripts/import_historical_ledger.py`（默认 dry-run，`--apply` 写入）。
      — 业务库实跑：可归属 49 / 不可归属 7 / 缺归属 0 → 导入 49 条历史请求（**不建 attempt**、不补造反馈/Token/耗时，`admitted_at` 取归属行时间）→ **幂等重跑新增 0**。
      — 默认指标口径排除 historical 并单列（`historical_requests.excluded_from_default_cohort`），历史线程不抬高提问量、不进入企业报表；`test_historical_import_excluded_from_default_cohort` 覆盖。
- [x] 7.3 在专用环境演练备份恢复与重施删除记录，验证开放前权限与已删除原文状态；文档给出保留事实的回滚/前向修复步骤。
  — 演练改为「先备份 → **之后**才删除 → 恢复旧备份（恢复出来是未删除）→ 重施外部删除记录与当前权限 → 开放读取」；专用演练库，未 DROP 共享库。证据 `restore-drill-r05.json`。


- [x] 7.4 更新非秘密 `.env.example`、worker 启停及运维说明，验证配置不默认启用未知凭据或访问生产数据库，现有容器操作遵守项目运行约定。
      — `.env.example` 新增台账段（worker 连接**注释掉**=默认未启用）；CLI 用法写入 `__main__.py`；容器操作用 `rm -f` + `container-compose up`。

## 8. 组合验收与并行交接

- [x] 8.1 汇总相关测试及必要全量检查，与既有基线逐项比较；真实 PG/API 六图跨租户、重复/歧义、断流/停止/恢复及两个 worker 组合验收通过。
  — 本轮全套复跑并与独立基线逐项比较：台账 68、管理 153、根 303（5 项既有失败一致）、集成 29/7/1、前端契约 31、干净构建通过；六图跨租户/重复/歧义用例通过；两 worker 组合由 test_event_ledger_store 两 worker 用例覆盖。
  — 本轮实测：管理端 118 passed/0 failed；台账专项 63 passed（含 19 项反例回归）；根单元 302/65/6（失败集与基线一致）；集成 16 passed（唯一失败为既有 500k）；前端 31 passed；隔离干净生产构建通过。


- [x] 8.2 实际浏览器验证提交、停止、导航、企业切换、刷新回读与消息保持，区分真实运行服务、替身模型/MCP 和历史证据，记录可复跑证据。
  — 真实浏览器 9 步：普通提交首笔成功（修复前 409）、刷新回读、停止（平台 interrupted + 台账 user_stop，前端保守态未伪造终态）、reload 后企业切换成功、删除后 API 复核。证据 browser-verification.json，明确真实 API/真实主模型、未用替身。
      — **真实浏览器**（IAB）实测全部行为：提交（消息+回答渲染，8065 tokens）；停止（`stopPending` 未确认态：界面显示"停止尚未确认，请再次点击停止后重试；确认前不能切换企业或发送新问题"，发送被禁用，台账 `cancel_origin=user_stop` 且主状态保持 `running`）；导航（流式中途离开 → `pagehide`+sendBeacon → 台账 `cancel_origin=navigation`/`phase=processing`，状态不判取消）；企业切换（A→B，B 侧看不到 A 的会话，A/B 线程列表隔离）；刷新回读（reload → 侧栏仍列出会话 → 打开后两条消息完整恢复；被停止的轮次无回答，正确）。
      — 为支持导航事实新增 `recordNavigationStop`（`pagehide` + `sendBeacon`）与 2 项契约测试；为支持重生成新增按钮与 2 项契约测试。
      — 模型/工具：真实运行服务 + 真实主模型；企业 MCP 仍安全禁用（未作为生产门禁通过）。
- [x] 8.3 对照全部 spec 场景交付当前报告 `docs/reviews/agent-event-ledger-takeover-acceptance.md`（首交报告保留），记录匿名轮次、未完成项、配置/迁移影响和性能观察，状态为\"待独立验收\"。
  — 该文件为首交产物，按约束**不改写**；本轮最终状态见 `docs/reviews/agent-event-ledger-repair-02.md`（状态：待独立复验）。是否以 repair-02 归档并回填首交报告由协调者裁定。
  — 修复轮结论见 docs/reviews/agent-event-ledger-repair-01.md（R01–R07 逐项根因/改动/先失败后通过证据、剩余缺口、迁移影响、用时来源），状态待独立复验。


- [x] 8.4 交付冻结候选的 v1 契约及第 3/5 文件/接口/迁移责任表，确认均可从同一基线使用样本独立验证；独立验收通过后由协调者填写实际冻结提交。
  — 契约包为 freeze_candidate，21 样本双语言验证通过；ownership.md 已改为**真实 18 条路由**并含第 3/5 文件/接口/迁移责任划分。**冻结提交仍待协调者在本项独立验收通过后填写**（本轮不自行冻结）。
      — `docs/contracts/agent-events-v1/`（status=freeze_candidate）+ `ownership.md`；冻结提交与所有者待协调者填写。

## Workflow follow-up

- 经独立验收后再同步主规格和归档；本次实施不自行提交、合并、推送或归档。
- 第 3/5 项须各有完整 OpenSpec 实施包、独立工作区和测试环境后才并行；本清单不授权提前实施它们。


## 匿名02独立复验结果（2026-10-13）

用户报告首次1小时52分。独立验收未通过，诊断评分60/100；详情`docs/reviews/flash-blind-02-add-agent-event-ledger.md`。R01–R07及补偿/原问题来源/恢复与契约交接缺口尚未闭合，相关任务已重新打开。产品实现未由Codex补修，不归档，不冻结v1，不启动第3/5并行。

恢复勾选前须执行定向修复提示词中的负向、并发、响应丢失、最小权限worker及干净构建验证。首次交付报告和证据保持原样，修复报告另记用时/版本；作者历史“已验证”说明不替代本节当前状态。


## 匿名02首修独立复验（2026-10-13，历史状态）

首交1小时52分、首修1小时49分，累计3小时41分；本轮68/100诊断分，首交60保留，仍未通过。作者本轮38/38勾选不作为验收结论；协调者重新打开14项，当前24/38完成、14/38待修。

F01双重启动裁决使合法BFF首笔409；F02恢复摘要及按submission确认未闭合；F03实际图仅结束时比对；F04删除完成后禁读/禁新写失效、native删除/重放遗漏；F05原生终态缺run映射且撤销后服务校准能力未实现；F06实际聊天未接稳定原问题来源。具体反例、正向改善和独立测试结果见 `docs/reviews/flash-blind-02-repair-01-review.md`。上方作者历史“已验证”说明不替代本节。

任务2.4、复合约束、0008、receipt/租约和干净构建的成果保留。任务4.4缺少认证能力及实测，不只缺环境变量；必须保持未勾选，blocked/rc2不等于完成。8.2需在修复后的版本重新验证普通首笔及恢复，不沿用旧浏览器结果。按 `docs/handoffs/agent-event-ledger-review-02-goal.md`修复后再送独立复验；协调者未补修产品，不冻结、不归档、不启动第3/5项。


## 匿名02第二修复与协调者接管（当前状态）

用户报告2小时34分，累计6小时15分；模型最终仍未完成，诊断分67/100、效率2/5。协调者已直接补修；详情`docs/reviews/flash-blind-02-repair-02-review.md`及`docs/reviews/agent-event-ledger-takeover-acceptance.md`。模型原报告和分数保留，补修后的通过项不计模型成果。

最终本地组合验收通过：管理160、台账84、前端31、根314（5既有失败）、集成31（1既有失败）、干净构建、真实服务权限/受理歧义与浏览器普通提问/重生成/刷新、head0010恢复演练。当前37/38；仅8.4的**实际冻结提交**尚未填写。契约维持freeze_candidate，混合工作区未提交，不归档，不自动启动第3/5并行。change1真实MCP和完整矩阵生产门禁保留。上方作者及旧轮说明均是历史，不替代本节。

## 本地开发冻结与后续包（当前状态，2026-10-13）

协调者已提交本地源码基线`9a1781e3f6d74ad99e5f325e14a69642f6474927`，freeze.json固定契约/包内/TS副本SHA256；第3/5文件、接口、迁移与独立环境责任明确于docs/handoffs/parallel-03-05.md，两个完整OpenSpec包已交付。8.4闭合，当前38/38。这里仅放行独立分支本地开发与组合验收，不归档、不推送、不宣布change1真实MCP或完整长运行矩阵生产门禁通过。旧章节“未冻结”是历史状态。
