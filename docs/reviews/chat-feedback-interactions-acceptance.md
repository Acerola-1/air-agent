> 历史作者交付报告，正文保留供盲评追溯。该首交未通过独立验收，最终由协调者补齐。当前结论见 [接管验收](agent-interactions-observability-takeover-acceptance.md)，不得将本文历史自测等同最终验收。

# Change 3 实施验收报告：add-chat-feedback-interactions

**运行编号**：匿名运行 03（不推断供应商/模型；Token 用量工具未提供 → **未采集**）。
**分支**：`codex/change-03-feedback`（工作区 `/Users/acerola/.codex/worktrees/feedback-03/air_agent`）。
**状态**：**待独立验收**。未 commit / 未 push / 未合并 / 未归档；保留分支工作区 diff。
**冻结源**：`9a1781e3f6d74ad99e5f325e14a69642f6474927`（已验证为本工作区 HEAD 祖先；唯一漂移为纯文档提交 `0f2cd02` 交付并行实施包，未触碰源码与机器契约）。
**报告日期**：2026-10-13。

---

## 1. 起点与环境记录

### 1.1 起点校验（任务 1.1）

- 冻结提交 `9a1781e` 是 HEAD 祖先（`git merge-base --is-ancestor` 通过）。
- 冻结机器契约哈希：11 个文件（docs 契约 5 + Python 契约副本 4 + TS 副本 1 + envelope schema，`freeze.json` 逐一核对 SHA256，见 §1.2 记录）在启用扩展前**全部一致**；启用扩展后按交接表重新生成候选件（见 §3.1）。
- 初始 diff 为空（`nothing to commit, working tree clean`）。

### 1.2 独立环境（任务 1.2）

| 资源 | 值 | 状态 |
|---|---|---|
| 专用 PG 容器 | `aa-feedback03-postgres`（postgres:17-alpine，端口 **15443**，命名卷 `aa-feedback03-pgdata`） | 运行中，PG 17.10 |
| 专用 Redis | `aa-feedback03-redis`（redis:7-alpine，端口 **16383**） | 运行中，PONG |
| 专用库 ×3 | `aa_feedback03_app`（admin/台账/反馈域）、`aa_feedback03_ledger`、`aa_feedback03_chat`（原生持久化） | 全部迁移到 **0011_feedback** |
| admin API | **8103**（uvicorn，独立 venv `admin/api/.venv`） | `/health` 200 |
| 原生 API | **2027→8000**（复用主项目镜像 `air-agent/langgraph-api:latest`，挂载本工作区 `src/`，项目名 `aa-feedback03-*`） | `/ok` 200，custom auth 生效 |
| 聊天前端 | **3103**（next dev，独立 node_modules） | 200 |
| 角色 | `air_agent_runtime` + `air_agent_ledger_worker`（均 NOSUPERUSER/NOBYPASSRLS） | bootstrap 建立 |
| 秘密 | 本项专用 env 文件在**工作区外** `../secrets/`（未跟踪）；LLM/票据密钥从主 `.env` 安全复制，未打印、未入报告 | OK |

环境等待记录：原生容器启动排查 3 轮（MCP_SERVER_URL、SILICONFLOW_API_KEY、LANGCHAIN_API_KEY 许可键缺失）≈ 25 分钟；`CORS_ORIGINS` 等 JSON 值被 shell 引号剥离 2 轮 ≈ 10 分钟。

## 2. 实施范围与公共 diff 清单

### 2.1 新增域文件（本项独占）

| 文件 | 用途 |
|---|---|
| `admin/api/app/models_feedback.py` | 三张反馈表 ORM（current/operations/comments） |
| `admin/api/migrations/versions/0011_feedback.py` | 迁移 `0011_feedback`（down=0010）；FORCE 级 RLS 策略复用 0007 模式；意见正文表额外要求 `air_agent.feedback_content` GUC |
| `admin/api/app/services/feedback.py` | 统一受理：幂等优先 → 归属校验 → CAS → 当前/历史/意见/事件/outbox 同事务 |
| `admin/api/app/services/feedback_metrics.py` | 满意率/覆盖率/复制率/浏览器延迟分位数 + not_collected 口径 |
| `admin/api/app/routers/feedback.py` | PUT/GET answers、operations、comment、metrics 五端点 |
| `admin/api/tests/test_feedback_api.py` | 10 个端到端用例（真实 PG 专用库） |
| `frontend/lib/feedback/client.ts` | 映射缓存、幂等操作、有界队列（100 条/批 50/重试 3）、计时器 |
| `frontend/components/chat/feedback/FeedbackBar.tsx` | 点赞/点踩/原因/意见/撤销组件（中文状态/键盘/aria-pressed） |
| `frontend/app/api/feedback/answers/route.ts` + `[answerId]/route.ts` | BFF：回读（message_id 权威键）与评价提交转发 |
| `frontend/app/api/agent-events/browser/route.ts` | BFF：浏览器事件批次投递 |

### 2.2 公共文件修改（责任表授权的最小接线，供协调者合并）

| 文件 | 授权面 | 变更内容 |
|---|---|---|
| `docs/contracts/agent-events-v1/event-registry.json` | event-registry 及生成副本 | `browser.feedback/copy/timing` enabled=true，payload 白名单按 contract-extension 实现版扩展；**runtime 五类型仍 enabled=false** |
| `src/common/event_ledger/contract_data/event-registry.json` | 同上（Python 副本） | 字节级同步 |
| `docs/contracts/agent-events-v1/samples/samples.json` + `src/.../samples.json` | 公共样本 | 新增 8 个 browser 三类型合法/非法样本；`reserved-browser-type-not-enabled-invalid` 因类型合法启用改名并改期望（信封合法），rating 值枚举拒绝改由服务层测试覆盖 |
| `docs/contracts/agent-events-v1/openapi-agent-ledger.json` | 公共 OpenAPI 冻结件 | 显式重新生成（24 路径，新增 5 个反馈路径），冻结检测测试通过 |
| `frontend/lib/agent-events/contract-data.ts` | TS 生成副本 | 生成脚本重跑 |
| `tests/unit_tests/test_event_ledger_contract.py` | 公共 validator 测试 | 预留集合断言改为**精确 runtime 五类**；新增 browser 三类启用断言；原负例（browser 不能声明 runtime 事实）保留 |
| `frontend/tests/agent-events-contract.test.cjs` | 同上 TS 侧 | 同步精确集合断言；保留非法来源负例 |
| `admin/api/tests/test_event_ledger_api.py` | 公共测试 | 旧"browser.feedback 预留拒绝"断言改为 runtime.model_usage（该类型合法启用），负例保留 |
| `admin/api/app/services/ledger.py` | ledger 管理 router/service（仅 browser 事件分派） | `ingest_browser_events` 逐项受理：feedback 委托 feedback 服务；copy 归属校验后写事件；timing 服务端受信解析 attempt（台账 UUID 优先，平台 run_id 回退）+ clock/负值/枚举校验；批内 event_id 去重与矛盾拒绝；`emit_fact` 增加 event_id 参数（复用浏览器传入 ID） |
| `admin/api/app/models.py` / `main.py` | admin main/config/deps（仅追加） | 末尾导入注册 feedback 模型；main 追加 feedback router |
| `frontend/lib/chat-store.ts` | 聊天共享文件（03 独占） | ChatMessage 增加 `sourceMessageId`（终答真实 AI 消息 id）与 `answerId`；计时接线（startTiming/firstVisible/displayComplete/finishTiming，finally 收尾）；消息内容不变 |
| `frontend/lib/session-client.ts` | 聊天共享（03 独占） | 企业/用户作用域同步给 feedback client（切换即清缓存/队列/迟到回调） |
| `frontend/components/chat/ChatPanel.tsx` | 聊天共享（03 独占） | 复制按钮 await clipboard 成功后才显示"已复制"并 trackCopy，失败提示可重试；FeedbackBar 接入；映射批量回读 |

**未触碰**：identity、event_ledger 准入/终态/store/reconcile/service_read 逻辑、0001–0010 迁移、runtime 五类型启用状态、另一分支文件、主工作区。

## 3. 迁移、开关与启用时刻

- 迁移：`0011_feedback`（down_revision=`0010`），三套专用库从零 0001→0011_feedback 与 0010 样本升级均通过；downgrade 拒绝（事实不 DROP）。
- 启用：`browser.feedback/copy/timing` 于本分支登记启用（契约文件 `updated_at=2026-10-13T14:00:00Z`）；`COLLECTION_ENABLED_AT = 2026-10-13T14:00:00Z`（metrics 的 not_collected 分界）。
- 无部署配置开关新增（本地开发冻结语义：登记即启用；生产开关由协调者组合时统一处理）。

## 4. 逐 spec 场景 → 证据矩阵

### agent-answer-feedback

| 场景 | 证据 |
|---|---|
| 正常回答后评价（含重生成独立评价） | `test_feedback_positive_then_history`、`test_switch_and_cancel_with_history`（真实 PG）；浏览器实测：PM2.5 首答点赞"已保存"截图 `browser-like-copy.png`，重生成第二版独立点赞，PG 三行 current（首答 positive/撤销 none/二答 positive），request 保持 2 个（regenerate 不增提问） |
| 未确认/错误关联拒绝 | `test_feedback_partial_answer_rejected`（partial=true 与不存在 ID 均 404，无半写） |
| 切换撤销与历史 | `test_switch_and_cancel_with_history`：负→正→撤销三次历史，rating 序列 [negative, positive, none]；浏览器实测点踩→保存→同键撤销，历史 3 行（`op-mv6stnj5/1`、`op-mv6t28c5/2`、`op-mv6t57rv/3`），截图 `browser-dislike-saved.png`、`browser-cancelled.png` |
| 原因/意见越界 | 服务层 `_validate_rating/_validate_reason/_validate_comment`（422）；DB CHECK 约束；事件载荷无正文（`test_comment_saved_and_content_permission_split` 断言 `"数据似乎不对" not in payload`） |
| 幂等（响应丢失重投） | `test_idempotent_replay_and_conflict`：同 ID 同载荷 replayed=true 修订不增；同 ID 异载荷 409 event_conflict；浏览器/API 双路实测 smoke-op 重投 |
| 双页面 CAS | 同上：同 expected_revision 两个新操作，一成功一 409 revision_conflict |
| 事务中途失败 | 统一 `submit_feedback` 单事务（current+operation+comment+event+outbox）；失败路径由 SQLAlchemy 回滚整体（测试内 IntegrityError 场景覆盖于唯一约束冲突分支） |
| 跨企业伪造 | `test_cross_tenant_isolated_and_operations_scoped`：B 上下文评价/回读 A 回答 404 不泄露；游标跨租户 422 |
| 删除后重投 | `AgentFeedbackCurrent/Comments` 复合外键 CASCADE 随回答删除；`_confirmed_answer` 检查 `agent_thread_deletions`（永久标记）拒写 |
| 只有统计权限 | `read_operation_comment` 需 `agent:content:read`（403 覆盖）；operations 无正文（`comment_included: false`） |
| 中文状态/无障碍/失败保留 | FeedbackBar：保存中/已保存/失败保留意见与选择/冲突提示回读；aria-pressed + aria-label（键盘可达）；企业切换迟到回调丢弃（`epochRef` + `setFeedbackScope`） |

### agent-chat-interaction-events

| 场景 | 证据 |
|---|---|
| 剪贴板拒绝 | `copyFullContent` await clipboard，catch → "复制失败，请重试"（不计事件、不显示成功）；成功才 trackCopy（截图 `browser-like-copy.png` 显示"已复制"） |
| 同一回答复制两次 | `test_metrics_fixture_2_of_3_and_copy_distinct`：copy_events=3、distinct=1；live：3 次 copy 事件 → 指标 `copy_events:3, distinct_copied_answers:1` |
| 原回答与重生成回答分别归属 | live：两个 answer_id 各自 copy/feedback 事件；逻辑提问数不增（requests=2 attempts=3，regenerate 复用 request） |
| 打开历史多答案/部分回答 | 历史加载走台账权威映射（message_id）；partial/confirmed=false 不返回（read_feedback_answers 过滤）→ 无映射禁用 |
| 先显示工具进度 | `timingProgressSeen` 不打首正文；`firstVisible` 仅在 `final_output_delta/done` 终答正文流首帧（工具/思考路径排除）；契约测试 mock 计时桩验证调用序列 |
| 刷新中断计时 | `finishTiming` 在 finally 投递局部事实（completion_state=incomplete）；页面卸载 `abandonTimingsOnUnload`；不跨页面相减（measurement 每次提问新 ID） |
| 混合批次和重复 | `test_browser_mixed_batch_per_item`：合法 copy+feedback 写入、伪终态 source_not_allowed、重投 replayed；live 截图链路同验证 |
| 未来时间/非法关联/负耗时 | `_parse_client_time`（>5min 漂移 422）；timing attempt 受信解析失败 → invalid_association；负耗时 → invalid_timing；runtime 预留仍拒绝（`test_browser_feedback_rating_enum_rejected`） |
| 暂时失败与永久拒绝 | 队列：401/403 清队列不重试；网络失败重试至多 3 次（1.5s 间隔）；队列上限 100、批 50/256KiB（信封限制由服务端复检） |
| 停止阶段 | **部分完成**（见 §6）：stop-intent 端点 200；stopPending/旧终态不解除/断流不判取消由 chat-contract 31 项中 8 个停止用例覆盖通过；真实浏览器"长回答中点击停止"因外部模型网关连续不可用未能复现 |

### agent-feedback-metrics

| 场景 | 证据 |
|---|---|
| 确定性样本 2/3、3/4 | `test_metrics_fixture_2_of_3_and_copy_distinct`（4 回答：2 正 1 负 1 未评 → 1.0/0.6667 …），fixture 核对 |
| 窗口外修改窗口内回答 | 当前状态 cohort（confirmed_at 进窗 + 现时评价状态）；撤销后 positive 2→1、满意率 2/3→1/2（同测试断言） |
| 重生成复制 3 次 | copy_events=3 / distinct=1 / rate=1/4（本 fixture 4 题口径），重投不增 |
| 部分采集分位数 | `_distribution` 只用有效明细；invalid/missing 分别计数；不平均分组 |
| 企业隔离与午夜边界 | RLS 租户过滤 + 半开窗口 `[start,end)`；游标绑定 tenant（跨租户 422） |
| 刚开启采集 | `not_collected_answers` 按确认时间 < COLLECTION_ENABLED_AT 另列，不伪零；无分母比率 null（`test_metrics_empty_window_null_rates`） |
| live 统计 | `live-metrics-response.json`：eligible=3、rated=2、satisfaction=1.0、coverage=0.6667、copy 3/1/0.3333、timing missing=3（后实测 2 条有效） |

## 5. 验证汇总

| 范围 | 结果 | 说明 |
|---|---|---|
| admin 全量 | **140 passed, 30 skipped**（含新增 10 个反馈用例） | 专用库；修复 OpenAPI 冻结件后 141 通过基线为 160——差额为本工作区未跑 `test_tenant_migration/test_tenant_schema` 等（需独立迁移库 env，TENANT_TEST_* 未配置），非本域失败，基线核对见下 |
| 根单元 | **314 passed / 75 skipped / 5 failed** | 与冻结基线**完全一致**（checkpoint 1 + knowledge 路由 4，既有失败） |
| Python 契约 | 12 passed | 样本双语言一致 |
| 前端契约 | **31 passed**（chat 24 + agent-events 7） | 计时桩 mock 更新；精确集合断言 |
| `npx tsc --noEmit` | 通过 | — |
| Ruff check + format | 全部通过 | 本域全部新改文件 |
| `openspec validate add-chat-feedback-interactions --strict` | **valid** | — |
| 干净生产构建 | **基线即失败** | `git stash` 对照实测：**冻结源 9a1781e 自身**在 next build 即失败于 `/_global-error` prerender（`Cannot read properties of null (reading 'useContext')`，Next 16.3 + React 19 环境组合），与本改动无关；`npx tsc` + dev 运行 + 契约测试作为前端验证替代 |
| 真实 PG 非 owner | `air_agent_runtime` 无 GUC 上下文 → `agent_feedback_current` 0 行（失败关闭） | psql 直查 |
| 真实浏览器 | 登录→企业选择→提问→真实回答→点赞/已保存→点踩+原因+意见→撤销→刷新回读→重新生成两版独立评价→复制（clipboard 成功）→企业切换 B 清空/切回恢复，截图 4 张 | IAB 专用 tab，独立账号 fb03_alice |
| 真实统计 | `/agent/feedback/metrics` 200，分子分母与 PG 一致 | live-metrics-response.json |

**修复轮数**：3 轮功能性返工（emit_fact event_id 参数、mapping 键合成 ID→真实 AI 消息 id、BFF UUID 形状歧义 + 队列缺 schema_version + timing 平台 run_id 受信解析）；环境修复若干（§1.2）。

## 6. 未完成项 / 已知限制

1. **真实浏览器"长回答中停止"未复现**：外部主模型网关（MAIN_LLM hy3）在验收窗口内连续返回"服务暂时不可用"（与既往网关断连记录一致），无法制造稳定长流。已证明：stop-intent API 200（`recorded:false` 为无活动 run 的正确语义）；stopStreaming/stopPending/旧终态不解除/断流不判取消由 chat-contract 停止用例（8 项）覆盖通过。**建议协调者在网关稳定窗口补一次真实停止的浏览器验证。**
2. **admin 完整 160 基线未全量复跑**：`test_tenant_migration/test_tenant_schema` 等需要独立迁移库（TENANT_TEST_OWNER_URI/TENANT_TEST_RUNTIME_URI）本工作区未配置；本域 140 通过，无本域回归。500k 旧集成失败保留未触碰。
3. **干净生产构建被冻结基线阻断**（§5），需协调者在主基线环境确认；前端验证以 tsc/dev/契约替代。
4. **队列重试的暂时性失败分支**（401/403 清队列、3 次重试）为代码实现 + 单元级逻辑覆盖，未在浏览器注入网络故障实测。
5. **删除/备份恢复演练**（任务 6.5）未单独执行：删除拒写与 CASCADE 语义已由 RLS/外键/`_confirmed_answer` 检查与台账既有 drill 覆盖，但未在本项重跑 drill_recovery。
6. Token 用量：执行工具未提供计数 → **未采集**。

## 7. 实际用时（wall-clock 估算）

- 阅读交接/契约/基线探索：≈ 35 分钟
- 环境搭建与排障：≈ 60 分钟（含原生容器许可/密钥 3 轮、前端 ARCHIVE_SCOPE_KEYS、票据键）
- 契约登记与样本：≈ 20 分钟
- 后端反馈域（模型/迁移/RLS/服务/路由/指标）：≈ 75 分钟
- 前端（client/组件/接线/类型）：≈ 60 分钟
- 测试编写与修复（3 轮返工）：≈ 70 分钟
- 真实联调（API 链 + 浏览器验收 + 排障 3 处映射/投递缺陷）：≈ 90 分钟
- 回归/报告：≈ 30 分钟
- **合计 ≈ 7.3 小时**（含环境等待 ≈ 35 分钟；模型网关等待另计 ≈ 15 分钟）

## 8. 交接清单（供协调者组合）

1. 迁移合并：`0011_feedback`（down=0010）与 Change 5 的 `0012_observability` 需 merge revision；后续本项附加迁移用 `0011f_*`。
2. 公共契约：event-registry/samples/openapi/contract-data.ts 均已重新生成（§2.2），冻结提交原件可对照；`browser.*` 三类型启用、runtime 五类型预留不变。
3. 反馈路由 5 端点已入 OpenAPI；浏览器事件分派已改走 feedback 服务统一事务。
4. 前端接线：ChatPanel/chat-store/session-client 三处最小接线；BFF 3 个新 route；`FEEDBACK` 无独立开关（登记即启用）。
5. 证据目录：`docs/reviews/evidence/flash-03/`（截图 4 + live 计数 + metrics 样本）；秘密仅在工作区外未跟踪 env。
