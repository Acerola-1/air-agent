# Proposal

## Why

Basic QA 目前只能依赖企业 MCP 与知识库；当政策公告、新闻背景或自有数据源缺失时，模型没有受控的补充渠道。现有 `websearch_tool`（Serper）虽然注册进 ToolNode，但从未进入模型披露（`projection.py` 的 `_GENERAL` 不含它），即使被调用也会被披露绑定拒绝，实际是不可用的死代码。直接开放联网又会带来外发隐私、成本失控和来源不可追溯的问题，因此需要“用户逐轮批准 + 硬预算 + 明确来源”的人工在环联网能力。

## What Changes

- **BREAKING** 删除 Basic QA 的 Serper `websearch_tool` 及其 `SERPER_API_KEY` 配置；Basic QA 只保留一个联网入口。
- 新增 DeepSeek 搜索工具 `web_search(query, reason)`：只做参数校验并写入待审批状态，不直接外呼。
- 图新增 `websearch_gate` 节点：用 LangGraph `interrupt()` 弹审批；批准后本轮最多 3 次实际外呼（失败也计数），拒绝或额度用尽后返回通用“数据暂不可得”，不提及审批。
- 一次批准覆盖本轮 3 次额度；后续两次由模型自行决定查询，不再弹窗；新的用户问题重置额度。
- 回答中的联网内容必须用服务端分配的 `[web:n]` 角标，并在文末输出“互联网来源”列表（标题/站点/URL/日期）；与知识库 `[[cite:id]]` 分开渲染。
- 联网数据只做补充：MCP/知识库已取得的数据优先；重复且冲突时以 MCP 为准；网页数字必须带来源与时间，不能伪装成监测数据。
- 完整恢复链路：BFF 以 `command.resume` + `operation_kind=resume` + 源 attempt 提交；前端审批弹窗、刷新恢复、等待中停止按拒绝收尾。
- 系统提示新增一段可拼接的统一规则；批准/拒绝、外呼次数、剩余额度与外部工具费用进入台账/观测。
- 新增 capability `basic-qa-approval-websearch`，不改其他图。

## Capabilities

### New Capabilities

- `basic-qa-approval-websearch`: Basic QA 的受控联网补充能力，覆盖单一搜索入口、人工审批与预算、interrupt/resume、来源角标与引用、数据权威边界、隐私审计与失败话术。

### Modified Capabilities

无。费用与工具观测沿用现有 `agent-cost-accounting`、`agent-capability-observability` 的既有要求，仅新增外部工具费用事实，不改变其规格文本。

## Impact

- 后端：`src/common/tools.py`（删 Serper）、`src/common/runtime_tools.py`、`src/common/config/settings.py`、`src/basic_qa/state.py`、`builder.py`、`nodes.py`、`capabilities/runtime.py`、`capabilities/projection.py`、`prompts.py`、`src/common/prompts.py`。
- 搜索客户端：新增 DeepSeek search 客户端（直连 `api.deepseek.com/v1/tools/search`，`DEEPSEEK_API_KEY` 放 `.env.langgraph`），超时、禁跟随重定向、`trust_env=False`、不记录密钥。
- 前端：`lib/langgraph-api.ts`（`command.resume`）、`lib/chat-store.ts`（审批/中断/恢复状态）、新增审批弹窗组件、`ChatPanel.tsx`、`[web:n]` 渲染与文末来源区。
- 受理/BFF：`frontend/app/api/[..._path]/route.ts` 的 resume 提交头与正文提取；沿用 `admin/api` 既有 `operation_kind=resume` 与 `waiting_input` 规则，必要时补齐中断后 attempt 状态。
- 部署：`.env.langgraph` 新增 `DEEPSEEK_API_KEY`；删除 `SERPER_API_KEY` 的必需配置；不修改私有桥。
- 测试：gate/预算/拒绝/额度、引用编号与文末来源、interrupt/resume 端到端、BFF 契约、真实 DeepSeek 调用与浏览器弹窗验收。
