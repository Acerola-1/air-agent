# Design

## Context

Basic QA 当前工具链：`common/runtime_tools.py:23-30` 无条件注册 Serper `websearch_tool`，但 `basic_qa/capabilities/projection.py:32` 的 `_GENERAL` 不含它，`disclosed_tools` 将其丢弃；`tool_policy.py:48-124` 的披露绑定也会拒绝未披露调用。因此它是不可达死代码。图结构见 `basic_qa/builder.py:115-166`：`classify_intent → prepare_model → call_model ⇄ execute_tools → answer_model → ledger_finalize`，生产代码无 interrupt。受理层已支持 `operation_kind="resume"` 与 `waiting_input`（`admin/api/app/schemas_ledger.py:38-50`、`services/ledger.py:826-845,1037-1040`），原生 auth 不拒绝 `command.resume`（`identity/langgraph_auth.py:314-326`），但 BFF/前端没有 resume 调用路径。私有桥只服务 IAM/模型/RAG 固定路由，Serper 一直是容器直连。

## Goals / Non-Goals

**Goals:**

- 用一个独立 gate 节点承载审批与搜索，保证 interrupt/resume 不重跑工具批次。
- 一轮一次批准、3 次外呼硬上限、失败计数、拒绝锁定。
- 服务端分配 `[web:n]` 编号并确定性输出文末来源，避免模型自编来源。
- 打通 BFF/前端的 resume、审批弹窗、刷新恢复与停止语义。
- 删除 Serper 死代码，只保留一个 DeepSeek 搜索入口。

**Non-Goals:**

- 不调用 `/v1/tools/fetch`，也不向模型提供独立抓取工具。正文只来自同一次 Search 的 `content`。
- 不做后台人工审批队列、跨用户审批或管理员代批。
- 不做通用外网代理或私有桥新路由。
- 不把联网数据写入知识库或监测数据表。
- 不改 data-analysis / intelligent-tracing 的工具与提示。

## Decisions

### 1. 独立 `websearch_gate` 节点，而不是工具内 interrupt

模型调用 `web_search(query, reason)` 时，工具只校验参数并通过 `Command` 写入 `pending_websearch`（含 `tool_call_id`），不发起网络请求、不写其它状态。`execute_tools` 之后的条件边检查 `pending_websearch`，路由到新节点 `websearch_gate`；gate 处理完再回到 `call_model`。

gate 逻辑：

1. `pending_websearch` 为空：直接返回。
2. `websearch_denied` 为真：回写通用不可得 ToolMessage，清除 pending。
3. `websearch_granted` 为真且 `websearch_used < 3`：不外呼审批，直接搜索。
4. 否则调用 `interrupt({type:"websearch_approval", provider:"DeepSeek", query, reason, remaining})`；恢复值只接受 `{"approved": bool}`。
5. 批准：搜索、`used += 1`、写 `websearch_sources`、回写带编号结果的 ToolMessage；拒绝：置 `websearch_denied=True`，回写通用不可得。

选择理由：resume 只重跑 gate，`execute_tools` 及其它已 checkpoint 的工具调用不会重跑；ToolNode 不再承担 interrupt，混合批次也不会引发重复执行。代价是新增节点、条件边和 ToolMessage 手工配对。

替代方案：工具内 `interrupt()` 需要在 ToolNode 内中断，恢复会重跑整批工具；即使强制 websearch 独占批次，也不如 gate 的隔离干净。独立“申请工具 + 执行工具”两步对模型更啰嗦，不采用。

### 2. 审批与预算按 run 计，并显式重置

新增 state 字段：`pending_websearch`、`websearch_granted`、`websearch_denied`、`websearch_used`、`websearch_sources`、`websearch_turn_key`。`websearch_turn_key` 记录本轮 `bqa_turn_key`；进入新一轮（turn key 变化）时在 `prepare_session` 或 gate 入口重置 grant/denied/used/sources。resume 保持同一 turn key，因此不重置。

一次批准覆盖本轮最多 3 次**实际外呼**；每次发起 DeepSeek 请求即 `used += 1`，失败/超时也计数，不自动重试。达到 3 后第 4 次请求直接返回不可得，不弹窗。拒绝置 `denied`，本轮不再弹窗。新用户问题产生新 turn key，重新审批。

替代方案：按成功次数计数会让失败无限重试，违背成本上限；按线程永久授权会让后续问题绕过审批。

### 3. 工具与批次

`web_search` 可在同一批与 MCP 工具混用：MCP 工具由 `execute_tools` 正常执行，websearch 只写 pending；gate 中断时其它工具结果已落地且不会在 resume 时重跑。限制同一批最多一个 `web_search` 调用；多个请求返回错误，要求模型分开调用。

工具在 interrupt 前只做参数校验（query 非空、长度上限），不读外部、不写 state 之外的字段。

### 4. 来源编号、回答引用与前端渲染

gate 按唯一 URL 分配 `[web:1..n]`。`websearch_sources` 只保存角标所需的 `{n, title, site, url, date, retrieved_at}`。ToolMessage 才带 `summary`、`rerank_score`、`authority`，以及排序分不低于 1.5 的前两条 `content_excerpt`。模型只能引用已分配的编号。

回答发布前做确定性后处理：扫描正文中的 `[web:n]`，只保留 `websearch_sources` 中存在的编号；在正文末尾追加“互联网来源”列表（标题/站点/URL/检索日期）。未引用的来源不追加，未知编号从正文移除并记录告警。知识库 `[[cite:id]]` 仍走现有渲染，两套编号互不影响。

前端 Markdown 渲染层把 `[web:n]` 转为上标（React 节点，不使用 `dangerouslySetInnerHTML`）；来源列表作为普通 Markdown 链接渲染。

替代方案：让模型自己写文末来源会漏项/编造；服务端后处理保证可核对。

### 5. 数据权威边界

提示词与后处理共同约束：MCP/知识库已取得的数据优先；同一对象与时间的冲突以 MCP/知识库为准；网页数字必须带来源与检索日期，并说明不是本系统监测数据；法规/标准/限值仍必须用知识库 citation。后处理不自动改写模型正文中的数字，只在提示与验收中约束；冲突时由模型按规则表述，测试用固定样例校验。

### 6. 拒绝与失败话术

gate 回写的 ToolMessage 只包含“部分数据暂时无法获取，无法进一步分析”一类的业务语言，不包含审批、拒绝、额度、服务名、状态码。提示词明确要求模型沿用该措辞，不得提及审批过程。拒绝后同一轮不再申请；搜索无结果同样视为数据不可得。

### 7. Resume 链路

- 前端从流式 `values` 中的 `__interrupt__`（或线程状态 `tasks[].interrupts`）识别待审批，存入 chat-store（含 query、reason、remaining、源 attempt id）。
- 弹窗只有“批准/禁止”；批准发 `command: {resume: {approved: true}}`，禁止发 `approved: false`。
- 提交时带 `x-operation-kind: resume`、`x-source-attempt-id`（被挂起运行的 attempt）、`x-submission-key`；BFF 现有代码已原样透传 body，并允许 resume 的空正文（`route.ts:341-367,432-512`）。
- 刷新后 `getThreadState` 读取 `next`/`tasks[].interrupts` 恢复弹窗；`openThread` 不把挂起运行当历史终态。
- 审批等待时点停止：先 resume 拒绝让 gate 收尾，必要时再取消运行；UI 标记为停止，不显示完成。
- 台账：确认平台 `interrupted` 会映射为 `waiting_input`；若事件链没有及时落库，在中断观察点补一次幂等的 attempt 状态更新。

### 8. DeepSeek 接入

新增 `src/common/websearch/deepseek.py`：`POST https://api.deepseek.com/v1/tools/search`，body `{query, count: 5, need_content: true}`，Bearer `DEEPSEEK_API_KEY`。`query` 为 1–128 个字符，超长在登记审批前拒绝，不占预算。客户端设置 20s 超时、`follow_redirects=False`、`trust_env=False`，不记录 query 全文/密钥/响应原文，只记录条数、耗时与错误类别。解析 `summary`、`snippet`、`content`、`rerank_score`、`authority`，字段分开保存。模型回执保留每条 `summary`；`rerank_score >= 1.5` 的前两条另附最多 8000 字、避开纯链接行的正文摘录。一次搜索的摘要与摘录合计不超过 26000 字。回执与其它工具结果一起计入 2 万字符的压缩阈值。`authority` 只作为来源分量，`-1` 表示未知，不用于丢弃结果。缺少密钥时工具返回不可用且不触发审批。`DEEPSEEK_API_KEY` 放 `.env.langgraph`；删除 `SERPER_API_KEY` 必需配置与 `common/tools.py` 的 Serper 实现。

替代方案：走私有桥新增固定路由，密钥更隔离，但要改桥白名单、启动器与部署文档；Serper 已有容器直连先例，v1 选直连。

### 9. 提示词独立段落

在 `src/common/prompts.py` 增加 `WEBSEARCH_HUMAN_APPROVAL_GUIDE`，由 `src/basic_qa/prompts.py` 拼接进系统提示，内容包括：允许范围（政策公告、新闻背景、MCP/知识库未取得的数据，包括数字）、禁止范围（MCP/知识库已能取得的数据、法规限值引用）、一次批准 3 次、`[web:n]` 引用、拒绝/失败话术、网页内容是不可信数据不得执行其中指令。9 个 Skill 的 `rules.md` 不改。

### 10. 审计与成本

批准/拒绝写入台账事件（运行、attempt、用户、时间、决定），每次外呼写工具调用事实；`websearch_used` 与剩余额度进入事件 payload。费用按成功且结果非空 0.005 元/次记为该工具的外部成本，标记 `source=deepseek-websearch`。失败、超时和空结果仍占预算，费用记 0。不写入模型 token 费用，不伪造 usage。若现有成本表只支持模型调用，则先记录在观测事件中，不新增迁移。

## Risks / Trade-offs

- [interrupt/resume 在正式平台不按预期落成 `waiting_input`] → 先用隔离图测试 gate，再用 `test_live_resume_roundtrip` 模式做真实原生 resume；事件链缺失时补幂等状态更新。
- [gate 恢复重跑导致重复搜索] → 搜索只在 interrupt 之后执行；resume 值确定；每次外呼计数，重复也会占用额度并可观测。
- [模型滥用或过度保守] → 提示词明确允许/禁止范围；预算硬上限；9 个 Skill 不加联网指令。
- [来源编号与正文不一致] → 服务端分配编号并后处理，未知编号移除；前端只渲染已知编号。
- [网页内容包含提示注入] → 提示词声明网页是不可信数据；工具结果只作为数据，不执行其中指令；后处理不解析网页指令。
- [删除 Serper 影响现有测试/配置] → 删除 `SERPER_API_KEY` 必需项与相关测试，保留通用 `websearch` 名称的迁移说明。
- [刷新后无法恢复审批] → `getThreadState` 读取中断任务并恢复弹窗；无中断时保持现有历史行为。

## Migration Plan

1. 新增 state 字段、`web_search` 工具、`websearch_gate` 节点与条件边；保留 Serper 代码直到新链路测试通过。
2. 新增 DeepSeek 客户端与 `.env.langgraph` 密钥；补单元/集成测试（批准、拒绝、失败、额度、编号）。
3. 打通 BFF/前端 resume 与审批弹窗；用原生 resume 测试与浏览器验证。
4. 删除 Serper 工具、`SERPER_API_KEY` 配置与死代码；更新测试与文档。
5. 真实 DeepSeek 调用与 3 次额度验收；失败/拒绝路径复验。

回滚：代码可按提交回退；删除 Serper 前的提交保留旧实现。`DEEPSEEK_API_KEY` 可独立撤销，工具会失败关闭。

## Open Questions

无。
