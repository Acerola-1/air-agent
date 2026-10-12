# 前后端消息流与上下文治理修复

## Why

2026-10-11 评估发现工具错误与内部摘要进入临时正文、摘要覆盖边界失效，以及超大数据局部读取和累计预算失守。需要将已确认的完整数据处理、受限 QuickJS 计算、可靠归档与前端展示契约落实为可验证的行为。

## What Changes

- 仅修改 `basic-qa`、`intelligent-analysis`、`data-analysis` 与当前聊天前端；保持显式 LangGraph 节点结构，排除其余 DeepAgents 图。
- 区分终答、工具结果、内部模型输出与状态事件，消除错误 JSON 和摘要闪现；修复重复终答、SSE 错误、取消及过期回调。
- 分开管理完整聊天历史与模型工作上下文；所有意图使用增量摘要覆盖水位，摘要失败不移除未覆盖消息。
- 主模型和摘要模型分别配置名称、窗口、输出预算及安全余量；所有模型调用前校验完整请求预算。
- 超大结构化输出完整保存并给出字段、数据范围、记录数和引用；由模型生成受限 JavaScript，在 QuickJS 中按问题范围完整计算。固定污染边界等规则使用测试过的函数。非结构化长文本按动态 token 预算完整分块摘要，不再仅取固定前缀。
- 整理阶段默认总时限 90 秒，一次生成、至多一次代码修正；单次 JS 默认 5 秒、64 MiB heap，全部可配置。计算失败不把局部摘要冒充完整统计。
- 归档保存线程／用户／run 归属、原始与派生数据、处理状态，默认 30 天保留；聊天正文、摘要与调用记录保留到删除会话，删除会话清理关联归档。
- 前端使用后端容量与压缩状态，只显示整理开始／完成／失败、用量变化和保留轮次，不展示或编辑内部历史摘要；错误详情主动展开才显示原始诊断。
- 实施完成须有真实浏览器、LangGraph API、PG 联调证据。测试登录使用用户在本轮会话提供的 admin 测试账号；密码从会话读取，不落入版本控制。

## Capabilities

### New Capabilities

- `langgraph-chat-streaming`: 三个显式图的消息分类、运行状态、取消与会话隔离。
- `model-context-governance`: 增量会话摘要、主／摘要模型动态完整请求预算和容量事件。
- `tool-artifact-lifecycle`: 原始／派生数据归档、归属检查、回读、过期与会话删除清理。
- `bounded-data-computation`: 当前会话归档数据上的有界 QuickJS 计算及结果依据校验。
- `data-output-reduction`: 完整覆盖结构化数据与非结构化分块摘要、业务错误和重试治理。

### Modified Capabilities

- `final-output-cleanup`: 三个显式图明确唯一可见终答，保留其他 DeepAgents 图既有契约。

## Impact

涉及 `src/common/business_graph/`、`src/common/context_guard.py`、`conversation_summarizer.py`、`storage/artifact_db.py`、配置与模型注册、`src/data_analysis/`、当前聊天 store／SDK 适配／组件和 API 代理。新增受限计算工具、归档与运行状态接口、兼容的归档表迁移；复用已锁定的 quickjs-rs/langchain-quickjs，不默认升级依赖。环境配置写入 `.env.example`，保留现有真实值。保留完整聊天 state 的读取兼容性；旧的无归属归档不自动授权回读。已有旧节点测试导入失效纳入修复。
