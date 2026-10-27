# Change6/7 联合验收 A 阶段报告

时间：2026-10-21。Agent 仓库 `dev`，起点 HEAD `336ad9b`；IAM 仓库 `dev`，起点 `d9d4923`。本轮没有 commit、push、合并、归档或正式部署，只保留可审查 diff。B/C 已完成：B（合成集质量）通过，C（R + 固定规模 SQL）未通过；详见 [B 报告](B-report-20261021.md) 和 [C 报告](C-report-20261021.md)。

## 结论先行

- 统一工具的三个已发现缺陷、采集器占位、IAM 权限阻塞和三个 Change7 产品问题已修复并有定向测试。
- P/S 40 题冒烟在修复后的发送器上重跑通过：`20261021-161640-5a659c`，40/40 `stream_completed`，281 项 PASS；同环境 v2 的限流失败保留为失败证据。
- Change7 全链、多意图、重复扫描、晚确认、暂停/续期、预算、归类租约接管、撤权/删除竞态和含洞察数据的恢复演练均完成。
- **A 阶段场景已完成**：溯源 4 个真实子执行、内容补齐、15+10 个洞察 DB 测试、浏览器删除→刷新、迟到响应 A→B→A、IME 输入、窄屏/键盘、权限切换均有本次实测证据。Token 未采集；Playwright 物理指针 actionability 在该 Next dev 页面超时属于工具限制，实际操作使用原生 DOM click/键盘事件完成。
- **B（合成集质量）通过**：160 条合成标签集、真实 SiliconFlow `Qwen/Qwen3-Embedding-8B` 4096 维；留出 100 条清晰意图与 62 条 hard negative 全正确，false merge 0；隔离回放 80/80 + 10/10 首意图匹配，10 条敏感样本按预期跳过。该结论只适用于合成集。
- **C（性能）未通过**：聊天 R 并发 1 为 120/120，但并发 5/10 仅 52/120、29/120，159 次 503 受理失败；6/7 固定规模 SQL 当前 schema 为 3 FAIL/2 PASS，候选索引后仍有 aggregate/leaderboard 2 项 FAIL。未执行 F/开放到达/组合负载，不能宣称容量或生产就绪。
- 没有把 40 题链路跑通当成联合验收通过；OpenSpec 任务未勾选。

## 环境与资源

| 组件 | 本轮值 |
|---|---|
| IAM Postgres / Redis / HTTP | `127.0.0.1:25441` / `25442` / `25445`，Flyway V28 |
| 业务 Postgres | `127.0.0.1:25443/campaign_business`，Alembic `0029` |
| 恢复副本 | 同实例 `campaign_restore`（演练后回收） |
| 图 Redis / LangGraph | `25444` / `25447`，官方镜像 `air-agent/langgraph-api:tracing-merge-20261020` |
| 管理 API / BFF / 管理页 | `25446` / `25448` / `3100` |
| C 固定规模 PG | 专用 `25460` 容器，4 CPU / 1024 MB；100k request / 300k attempt / 1M observation；200k intent / 1000 cluster |
| 受控模型 + MCP / 宿主机桥 / MCP 代理 | `25449` / `192.168.64.1:25450` / `192.168.64.1:25451` |

正式 `15432`/`19132`、共享 Langfuse/知识库未连接、未修改。

## 分项状态

| 任务 | 状态 | 摘要 |
|---|---|---|
| 1.1 工具/计划/冒烟 | 通过（工具与 P/S） | 发送器/核对/受控依赖/采集器修复；v3 40/40 |
| 1.2 IAM 目录与授权 | 通过 | V28；正常 API 授予/撤权；旧会话失效；恢复成功 |
| 1.3 worker 合成全链 | 通过 | 归类/榜单/来源/纠错/删除/刷新；多意图/重扫/晚确认/预算/续期/接管 |
| 1.4 撤权与删除竞态 | 通过（Change7） | 外呼前/运行中撤权、发出后删除/取消、租约接管均有正反对照 |
| 1.5 三图与浏览器 | **通过** | basic-qa/data-analysis/溯源 4 专员；浏览器榜单/来源/纠错/删除刷新/权限切换/迟到响应/窄屏/键盘/IME |
| 1.6 备份恢复 | 通过 | 含洞察数据；墓碑+权限+有效期；原文/向量/原型/失效作业 |
| 1.7 B 质量 | **通过（仅合成集）** | 真实 Embedding 4096 维；100/100 + 62/62；回放 80/80 + 10/10；敏感 10 条跳过 |
| 1.8 C 性能 | **未通过** | R 并发 1 通过、5/10 失败；6/7 SQL 3 FAIL/2 PASS；候选索引后仍有 2 FAIL |
| Change6 计数/删除回归 | 通过 | 冒烟增量 92；删除后提问数不变、洞察 6 请求消失 |

## 1.1 工具修复与 P/S 冒烟

修复：

1. `sender.py`：改为增量 SSE 解析；首字只认当前 run 的首段可见 AI 正文，排除复用线程历史助手消息、工具参数、推理块和 `<think>`；`values` 仅作回退并先记录初始消息 ID；终态区分流结束/业务成功/超时/失败/关联不匹配。
2. `reconcile.py`：按 request→intent→assignment 核对；不同意图可跨簇，同一意图不能多簇；同一 `(request, split_index)` 不能多意图；缺口只计请求覆盖。
3. `controlled.py`：`already_tasked` 只认真正的 `task` 调用；城市名用内置行政区目录提取；补充 MCP 协议、合成站点/序列/网格数据和确定性 Embedding。
4. 采集器：`calls.jsonl` 从管理 API 观测导出真实逐调用记录；`resources.jsonl` 采集客户端与 5 个隔离容器的前后增量；`checks.jsonl` 生成逐请求与聚合业务检查；采不到时写 `not_collected`。

定向测试：`tests/unit_tests/test_unified_campaign.py` 32 项通过；`src/intelligent_tracing` 相关 109 项通过；在专用空库 `campaign_insights_test` 上 `test_review_regressions.py` 15 项、`test_db_integration.py` 10 项全部通过（此前 5 个 setup error 是未配置测试 DSN，已用专用空库补齐）。

冒烟：

| run | 结果 | 说明 |
|---|---|---|
| `20261021-161459-5a659c` (v2) | 30/40，62 条 503 | 隔离 IAM 内部端点 600/分钟限流；保留为失败证据 |
| `20261021-161640-5a659c` (v3) | 40/40 `stream_completed` | 提高隔离限额并加 0.3s 间隔后重跑 |

v3 指标（仅冒烟，不是性能/容量结论）：完成 P50/P95 = 952.5/1092.45 ms；首字 P50/P95 = 757.5/849.35 ms；94 条真实模型观测；6 条资源采样；usage 由受控模型返回空，记 `not_provided`；Token 未采集。

证据：`旧性能证据已清理`、`docs/reviews/evidence/run/20261021-ps-smoke-v3-fixed-sender/`，失败 run 的同名 v2 目录保留。

## 1.2 IAM 权限

详见 `iam-insights-permission.md`。要点：V28 只登记权限码；正常 IAM Web 管理 API 授予/撤权；修复平台管理员写企业角色权限时的 `42501` 上下文错误；未授拒绝 → 授予成功 → 撤权旧会话失效 → 恢复后成功均已实测。

## 1.3 Change7 全链与边界

首轮作业 `ca93ca12`：处理 107 来源、100 已归类、54 未归类。补跑 `30e583d3`：123 来源、113 已归类、59 未归类。重复扫描 `1debdce5`：处理 0，意图/归属/来源数不变。API 侧核对 `reconcile-v3.json`：92 请求全覆盖、326 意图、213 已归类、113 未归类、76 个多意图请求，无重复拆分、无同一意图多簇；声明的多意图请求复核 PASS。

| 场景 | 实测 | 证据 |
|---|---|---|
| 多意图 | 326 意图中 76 个请求拆出 ≥2 意图；不同意图可分属不同簇 | `reconcile-v3.json`、`reconcile-v3-multi-intent.json` |
| 重复扫描 | 新作业处理 0，不重复写归属 | `rescan-job.json` |
| 晚确认 | 直接受理未确认 → `pending_confirmation` 缺口 → 用同一 submission key 启动真实 run 并确认 → 缺口消失、1 个意图 | 快照与 `race-late-confirmation.json` |
| 内容补齐 | 已确认但无正文 → `missing_content` 缺口 → 用 retry 受理补正文（正常 API）→ 缺口消失，content_ref/source/intent 各 1 | `content-completion.json` |
| 暂停/重试 | `paused_user` → `retry` → 完成 3 来源 | `race-pause-retry.json` |
| 预算 | `max_tokens=1000` 作业处理 10 后 `paused_budget`；新作业完成剩余 8 | `race-budget.json` |
| 归类租约接管 | A 在 Embedding 中停止；租约过期后 B 完成 3；A 恢复收到 `stale_lease`，迟到结果被拒 | `race-lease-takeover.json` |
| 纠错 | 改名（修订 1）、指派（归属修订 231）、合并（232，目标 25 意图/21 请求）；旧修订 409 | `correction-*.json` |
| 删除/刷新 | 删除 6 条共享线程请求；提问数 235 不变，洞察 230→224、意图 326→316、已归类 214→208 | `delete-refresh.json` |

说明：“归类租约接管”是 Change7 worker 的证据，和上一轮台账 worker 的 `lease-takeover.json` 不是同一对象，不能互相代替。

## 1.4 撤权与删除竞态

| 场景 | 结果 | 证据 |
|---|---|---|
| 归类外呼前撤权 | 有效但无 insights 码的会话：建立作业/重试均 403，Embedding 增量 0 | `race-revoke-before-outbound.json` |
| 模型已发后删除 | 删除目标线程后，作业处理 0；该请求 0 来源/0 意图；后续作业把其余 7 条正常归类 | `race-delete-after-dispatch.json`、`race-delete-followup.json` |
| 模型已发后取消 | 取消后处理 0；立即查库该批 0 意图；后续作业 3 处理/2 归类 | `race-cancel-after-dispatch-b2.json`、`race-cancel-followup.json` |
| 运行中撤权 | 作业 `paused_authorization`、处理 0；恢复+续期后完成 3 | `race-renew-paused-authorization.json`、`race-renew-completed.json` |
| 租约接管 | 原持有者迟到结果 `stale_lease` 被拒，接管者完成 3 | `race-lease-takeover.json` |

过程中发现并修复：per-source 的 IAM 不可用被误记为 `paused_failure`，与“IAM 故障暂停授权”约定不符；现在统一映射为 `paused_authorization`（新增 `pause_state_for` 及单测）。另修复取消/租约丢失后来源永久 `processing` 的问题：重试耗尽转为 `failed/attempts_exhausted`，并让等待态缺口不消耗重试预算。

## 1.5 三张公开图与浏览器

- `basic-qa`：v3 冒烟 40/40，历史 50、代表 2 全部 `stream_completed`。
- `data-analysis`：代表场景在冒烟中 `stream_completed`、关联成功；P 层只证明链路，不证明分析正确性。
- `intelligent-tracing`：受控 MCP（`ipp`，合成站点/序列/网格）+ `approved_query_tools` 连接；正常 `freeze_tracing_request` 返回 `frozen`；模型按“站点/网格→气象→证据审查”分阶段派发 4 个 `task`；`child_executions` 记录 **4 个真实子执行**（station-data、grid-data、meteo-analyst、evidence-reviewer）且全部 `succeeded`，`meteo_findings` 存在；最终发布 `validated_report=true`、正文 637 字符；本 run 新增 7 次模型、27 次 MCP 请求（其中 6 次工具调用）。
- 根因与修复：受控模型原先一次性并行派发 4 个 task，气象专员在站点结果写入父状态前启动，`MeteoDeterministicMiddleware` 拿不到 `station_findings`，所以不记录子执行；同时把声明式气象专员改为编译子图以接收父状态。改为分阶段派发后 4/4 通过。旧的 3 条记录证据 `tracing-representative.json` 保留，新的 4 条证据见 `tracing-representative-4-specialists.json`。

浏览器：管理页 `localhost:3100` 经隔离 IAM SSO 登录，榜单/详情/改名、390×844 窄屏（无横向溢出）、键盘 Enter 导航、撤权重定向 `/login`、恢复后页面可用均已实测并截图。刷新按钮经键盘 Enter 触发；“截止”取最新来源时间，无新来源时不会变化，因此没有把时间不变当作刷新成功或失败。删除→刷新：在聊天页侧栏真实删除内容补齐线程（最终 DELETE 204，侧栏 2→1），回洞察页刷新后合格/已处理请求 2→1、被删请求进入 `deleted` 缺口；首次 503 是隔离管理 API 的 CSRF 允许源未含聊天 BFF 端口，补齐配置后成功。迟到响应：受控首包延迟 3 秒，发送后停止并切到 B，B 无迟到问题/回答；切回 A 见迟到问题但无迟到回答（`late-response-browser.json`、两张截图）。IME：compositionstart/input 输入「洛阳市」、发送按钮启用；组合期间 Enter 不发送；compositionend 后 Enter 发送并得到回答（`ime-browser.json`、两张截图）。聊天输入/发送控件可交互；此前的“不可交互”是测试定位问题（仅图标发送按钮的 `textContent` 为空，Playwright click 在 IAB + Next dev 下 actionability 超时），改用原生 `.click()`/键盘事件后正常。物理指针 Playwright actionability 仍为工具限制；企业选择和改名保存曾用页面内 React 处理器。详见 `change7-live/browser-flow.md`。

## 1.6 备份恢复（含洞察数据）

流程：新目标来源归类 → 导出删除前快照 → 删除线程 → 恢复到 `campaign_restore` → 重施墓碑 → 运行产品 `restore_guards`。

| 检查 | 结果 |
|---|---|
| 恢复旧快照、未重施墓碑 | 内容 1、来源 1、意图 1、归属 1、原型向量 1、墓碑 0（旧备份确实含已删数据） |
| 重施墓碑 + restore_guards | 来源/意图/归属/向量均为 0；簇 retired |
| 运行角色读取原文（带墓碑过滤） | 0；原始行仍为 1（旧备份保留，应用读取被墓碑挡住） |
| 过期委托/在飞作业 | 委托 1 条过期、作业 1 条转 `paused_authorization`，错误 `delegation_restored_expired`，不可恢复 |

证据：`change7-live/restore-drill.json`。

## Change6

见 `change6-regression.md`：冒烟前后提问增量 92 PASS；删除 6 条请求后提问数 235→235，洞察 230→224。

## B 质量（合成集）

详见 [B-report-20261021.md](B-report-20261021.md)。按用户选择使用现有 160 条合成标签集（60 校准 / 100 冻结留出），真实 SiliconFlow `Qwen/Qwen3-Embedding-8B`，4096 维，profile `siliconflow-qwen3-8b-v1`。阈值 0.72/0.1/0.62；留出 100/100 正确、62/62 hard negative 正确、false merge 0；隔离回放 80/80 非上下文首意图匹配、10/10 上下文追问匹配、10 条敏感样本按预期 `sensitive_content` 跳过。质量门槛 PASS，但仅适用合成集；辅助 LLM 未参与质量门槛，真实生产分布未验证。

## C 性能（R + 固定规模 SQL）

详见 [C-report-20261021.md](C-report-20261021.md)。聊天 R：并发 1 三轮 120/120；并发 5 三轮 52/120（68 次 503）；并发 10 三轮 29/120（91 次 503）；5/10 每轮均未达 P 层 100% 成功率，**FAIL**。失败集中在受理阶段：管理 API `runtime-ticket`/`auth/me`/`agent/requests` 与 BFF `threads`/`stream` 的 503，叠加隔离 IAM Hikari/outbox 警告；不是模型耗时。后台 6/7 固定规模 SQL：`change6_list` P95 1222.1ms（目标 500）、`change6_aggregate` 2185.2ms（1500）、`change7_leaderboard` 5778.4ms（1000）FAIL；content 157.3ms、sources 103.9ms PASS。候选索引仅在隔离规模库试加：list/content/sources 转为 PASS，aggregate 4186.0ms、leaderboard 4117.3ms 仍 FAIL，故未改产品迁移。仅 SQL，不含 API/IAM/BFF/浏览器；未执行 F/开放到达/组合负载，不能据此宣称容量。

## 未执行 / 受阻 / 已知局限

- Playwright 物理指针点击在该 Next dev 页面持续 actionability 超时（IAB 工具限制）；聊天发送/删除使用原生 DOM click，导航/IME 使用真实键盘/DOM composition 事件完成。删除→刷新见 `delete-refresh-browser.json`，迟到响应/IME 见 `late-response-browser.json`/`ime-browser.json`。
- 受控模型不返回 usage，Token 记未采集；Token 预算未评估。
- 洞察 DB 集成测试已在专用空库 `campaign_insights_test` 上补齐：`test_review_regressions.py` 15 项、`test_db_integration.py` 10 项全部通过。
- B 已按用户选择使用现有合成集完成校准/回放（真实 Embedding）；真实案例/生产分布、辅助 LLM 拆分与复核未验证。C 已执行聊天 R 与 6/7 固定规模 SQL；F/开放到达/60 分钟稳定性/组合负载、真实浏览器并发、API 全路径未执行；C 的 FAIL 结论不能由 A/冒烟替代，也不能据此宣称生产就绪。
- 正式 IAM Java 测试套件未跑（其测试库默认 19132，本轮禁止连接）；用隔离真实 API 序列验证。

## 两仓改动

Agent 仓库（本轮新增/修改，未提交）：

- `scripts/unified_campaign/{sender,reconcile,controlled,cli,records,iam_catalog}.py`
- `tests/unit_tests/test_unified_campaign.py`
- `admin/api/app/demand_insights/{worker,source_reader}.py`
- `admin/api/app/schemas_demand.py`
- `admin/api/tests/demand_insights/{test_review_regressions,test_db_integration}.py`
- `src/intelligent_tracing/subagents.py`
- `docs/performance/pressure-testing-workflow.md`
- 新证据：`旧性能证据已清理`、`.../20261021-ps-smoke-v3-fixed-sender/`、`.../20261021-chat-r-20261021/`、`docs/reviews/evidence/run/20261021-ps-smoke-v{2,3}-fixed-sender/`、`docs/reviews/console-insights-unified-20261021/change7-live/`、`B-calibration-20261021.json`、`B-replay-summary.json`、`B-report-20261021.md`、`C-scale-6-7-20261021.json`、`C-scale-6-7-after-indexes-20261021.json`、`C-report-20261021.md`、本报告与 IAM/Change6 报告。

工作区里还有上一轮遗留的未提交改动（`admin/api/app/routers/feedback.py`、`admin/api/app/services/ledger.py`、`deployment/local_host_bridge.py`、`frontend/app/api/[..._path]/route.ts`、`src/common/artifact_http.py`、`tests/unit_tests/test_local_host_bridge.py`、OpenSpec 文档等），未回退、未覆盖。

IAM 仓库（本轮新增/修改，未提交）：

- `server/src/main/resources/db/migration/V28__agent_insights_manage.sql`
- `server/src/main/java/com/airagent/iam/admin/service/impl/RoleAdminServiceImpl.java`

## 资源与清理

自建资源：6 个 `aa-campaign-20261021-*` 容器（含 C 的 `scale-pg`）、隔离 PG 上的 `campaign_restore`、`campaign_business`/`campaign_scale`、25445/25446/25447/25448/25449/25450/25451/25460/3100 进程。结束前已确认无他人使用；回收本任务容器与进程、随容器删除业务/规模库，保留正式栈和共享资源。私有凭据、会话、dump 只在 gitignore 的 `.local/campaign-20261021/`，未写入报告或提交。清理结果：`change7-live/cleanup.json`；最终 `container ls` 为空（无 `aa-campaign-*`），25440–25460/3100 无监听、无本任务进程。

## 耗时与 Token

实施/修复约 70 分钟；验证、浏览器、恢复演练和三次隔离栈重建约 135 分钟；外部等待（租约过期 126 秒、冒烟/Next 编译/浏览器等待）约 12 分钟；B 校准/回放约 20 分钟；C（R + 规模 SQL + 候选索引复测）约 50 分钟，均按证据时间戳近似。C 在用户 ≤120 分钟预算内。Token：未采集。

## 最终判断

- 代码保存：工具、Change6/7 修复和 IAM 最小迁移已保存为可审查 diff。
- 系统联通：隔离 IAM/PG/图/BFF/worker/MCP 全链已实测。
- 案例质量：B 合成集通过（真实 Embedding，100/100 留出、62/62 hard negative、回放 80/80+10/10）；真实案例/生产分布未验证。
- 性能/容量：C 未通过；聊天 R 并发 5/10 成功率 43.3%/24.2%，6/7 规模 SQL 当前 schema 3 FAIL/2 PASS，候选索引后仍 2 FAIL；未执行 F/开放到达/组合负载，不能宣称容量。
- 生产启用：未启用；A 阶段场景已完成，B 仅合成集，C 未通过，不能据此宣称生产就绪。
