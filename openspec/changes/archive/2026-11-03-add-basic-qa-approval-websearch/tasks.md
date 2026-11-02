# Tasks

## 1. 提示词文本与文档

- [x] 1.1 在 `src/common/prompts.py` 新增独立常量 `WEBSEARCH_HUMAN_APPROVAL_GUIDE`，写明允许范围（政策公告、新闻背景、MCP/知识库未取得的数据，包括数字）、禁止范围（MCP/知识库已能取得的数据、法规限值引用）、一次批准 3 次、`[web:n]` 引用、拒绝/失败话术、网页内容不可信；在 `src/basic_qa/prompts.py` 拼接进系统提示；验证：单测断言关键句存在，且 9 个 Skill 的 `rules.md` 未改动。
- [x] 1.2 更新 `UX-CONTRACT.md` 与 `AGENTS.md`：新增联网审批弹窗、`[web:n]` 上标与文末来源、拒绝/失败话术、resume 交互；验证：`rg "websearch|联网|\[web:"` 命中预期段落，无“可未经批准联网”的表述。

## 2. DeepSeek 搜索客户端

- [x] 2.1 新增 `src/common/websearch/deepseek.py`：`POST https://api.deepseek.com/v1/tools/search`，body `{query, count:5, need_content:false}`，Bearer `DEEPSEEK_API_KEY`；10s 超时、`follow_redirects=False`、`trust_env=False`，只记录条数/耗时/错误类别；验证：mock HTTP 单测覆盖成功、空结果、超时、非 200、缺 key，且日志不含 key/query 全文。
- [x] 2.2 在 `src/common/config/settings.py` 增加 `DEEPSEEK_API_KEY`（可选或必需按现有模式），`.env.langgraph` 增加该键；缺 key 时搜索工具失败关闭且不触发审批；验证：单测缺 key 路径，`rg "DEEPSEEK_API_KEY"` 命中配置与客户端。
- [x] 2.3 客户端返回结构化结果 `{results:[{title,url,site,date,snippet}]}`，对结果数量与字段做边界校验；验证：畸形响应、超长 snippet、缺 url 的用例按不可得处理。
- [x] 2.4 按 Search 字段用途使用结果：`need_content: true`、超时 20s、query 限 128 字并在登记前拒绝；解析 summary/content/rerank_score/authority。模型回执保留全部 summary，仅 rerank_score≥1.5 的前两条附最多 8000 字正文摘录。来源状态只留编号、标题、站点、URL 和日期。费用仅在成功且结果非空时记 0.005，失败与空结果记 0 仍占预算。不调用 fetch。

## 3. 图、状态与 gate

- [x] 3.1 在 `src/basic_qa/state.py` 增加 `pending_websearch`、`websearch_granted`、`websearch_denied`、`websearch_used`、`websearch_sources`、`websearch_turn_key`；在 `prepare_session` 或 gate 入口按 `bqa_turn_key` 变化重置；验证：单测同一 turn 复用、新 turn 重置、resume 不重置。
- [x] 3.2 新增模型可见的 `web_search(query, reason)` 工具：只校验参数、通过 `Command` 写入 `pending_websearch`（含 `tool_call_id`），零外呼、零其它副作用；同一批多个 `web_search` 返回错误；验证：单测工具返回的 Command 字段，批次冲突用例。
- [x] 3.3 新增 `websearch_gate` 节点：按 `denied → granted/used → interrupt` 顺序处理；interrupt payload `{type, provider, query, reason, remaining}`，恢复值只接受 `{"approved": bool}`；批准后搜索、`used += 1`、失败也计数、不自动重试、3 次后第 4 次直接不可得；拒绝置 `denied`；回写配对的 ToolMessage；验证：节点级单测覆盖批准、拒绝、失败、额度、重复请求。
- [x] 3.4 在 `src/basic_qa/builder.py` 增加 `execute_tools → websearch_gate → call_model` 条件边，gate 处理完清除 `pending_websearch`；验证：图编译与节点路由单测，确认其它工具在 resume 时不重跑（用调用计数断言）。
- [x] 3.5 服务端编号与回答后处理：gate 按唯一 URL 分配 `[web:1..n]` 并写 `websearch_sources`；回答发布前只保留已知编号、追加“互联网来源”列表；验证：单测重复 URL、未知编号、知识引用共存、无来源不追加。

## 4. 前端引用渲染

- [x] 4.1 前端 Markdown 渲染层把 `[web:n]` 转为上标 React 节点（不使用 `dangerouslySetInnerHTML`），文末来源列表渲染为标题/站点/URL/日期链接；未知编号不渲染；验证：前端契约测试覆盖已知/未知编号与知识引用混排。
- [x] 4.2 流式回答中角标随正文出现，来源列表在回答完成后显示；验证：`npm --prefix frontend run test:contracts` 与浏览器检查。

## 5. 审批 UI、resume 与台账

- [x] 5.1 `frontend/lib/langgraph-api.ts` 的 `streamRun` 支持 `command.resume`；`getThreadState` 返回 `next`/`tasks[].interrupts`；验证：契约测试 mock 流事件，确认 resume body 与 interrupt 解析。
- [x] 5.2 `frontend/lib/chat-store.ts` 增加待审批状态（query、reason、remaining、源 attempt id、run id），实现批准/禁止/停止时 resume、刷新恢复、A→B→A 隔离；验证：chat 契约测试覆盖批准、拒绝、刷新、停止。
- [x] 5.3 新增审批弹窗组件：上方展示提供方、查询词、检索理由、剩余额度，下方只有“批准/禁止”；运行锁定与无障碍焦点；验证：组件契约测试与浏览器实测。
- [x] 5.4 BFF `/api/[..._path]` 支持 resume：`x-operation-kind: resume`、`x-source-attempt-id`、空正文受理；验证：新增 BFF 契约用例，确认 body 透传、content 为空、来源 attempt 正确。
- [x] 5.5 台账联调：确认平台 `interrupted` 映射为 `waiting_input` 且 resume 复用原 attempt；若事件链未及时落库，补一次幂等状态更新；验证：隔离/真实 resume 集成测试断言 request/attempt 不新增。

## 6. 删除 Serper

- [x] 6.1 删除 `src/common/tools.py` 的 Serper 实现、`runtime_tools.py` 的注册、`settings.py` 的 `SERPER_API_KEY` 必需项与 `.env`/`.env.langgraph` 对应键；验证：`rg "serper|SERPER|websearch_tool" src tests .env*` 无生产命中，相关测试更新。
- [x] 6.2 确认 Basic QA 工具列表只剩 `web_search` 一个联网入口，其他图不受影响；验证：工具披露单测与 `rg` 检查。

## 7. 审计与成本

- [x] 7.1 批准/拒绝、每次外呼、已用/剩余额度写入台账事件或工具调用事实；验证：单测断言事件字段与运行归属。
- [x] 7.2 外部工具费用按 0.005 元/次记录，`source=deepseek-websearch`，不写模型 token；验证：成本/观测单测，未知价格不记零。
- [x] 7.3 日志与事件脱敏：不出现 API key、完整请求头、查询全文（按现有正文策略）；验证：日志捕获测试。

## 8. 集成验收

- [x] 8.1 图级集成测试（假 DeepSeek）：批准 → 3 次外呼 → 第 4 次不可得；拒绝 → 不再弹窗；失败 → 计数且话术通用；验证：新增 pytest 集成用例通过。
- [x] 8.2 原生 interrupt/resume 集成测试：参考 `tests/integration_tests/test_live_resume_roundtrip.py`，断言只重跑 gate、不重复执行 MCP 工具、台账复用 attempt；验证：隔离环境用例通过。
- [x] 8.3 真实 DeepSeek 调用：一次搜索返回结构、`[web:n]` 与文末来源正确；失败路径计数；验证：记录问题、查询、结果条数、耗时、额度。
- [ ] 8.4 浏览器全链路：弹窗批准/禁止、刷新恢复、等待中停止、正文角标与来源列表；验证：实际页面操作记录与截图。（弹窗/拒绝/批准/刷新已实测；停止图标已实现并有契约测试，浏览器点击受自动化稳定性限制未完成）
- [x] 8.5 回归：Basic QA 现有单测/集成、`npm --prefix frontend run test:contracts`、`npm --prefix frontend run build`；验证：全部通过，原有失败单独记录。

## Workflow follow-up

- 归档在 8.x 全部通过后进行；本 change 的 specs 是新增 capability，archive 会写入 `openspec/specs/basic-qa-approval-websearch/spec.md`。

## 归档豁免（2026-11-03）

用户要求强制归档。任务 8.4 不改为通过：弹窗批准、禁止和刷新已实测，停止图标有契约测试；等待中在浏览器里点击停止受自动化稳定性限制，尚未完成。
