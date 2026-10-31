# Proposal

## Why

当前基础问答和数据分析按未覆盖早期历史约 30,000 token 首次触发摘要，已有覆盖水位后又会逐轮更新，且模型输入另受固定 120,000 token 应用上限约束；这些规则既增加理解成本，也不能随未来百万上下文模型自然扩展。用户确认保留已有摘要生成和持久化机制，只统一按模型窗口比例触发，并让运行中的容量整理与一次超窗恢复由系统承担。

## What Changes

- 主配置使用模型上下文窗口和 `CONVERSATION_SUMMARY_TRIGGER_RATIO=0.8`，水位为二者乘积；主模型、独立摘要模型及不同模型子任务按实际绑定的容量分别处理。
- **BREAKING**：停用 `MODEL_CONTEXT_BUDGET_TOKENS` 和 `CONVERSATION_SUMMARY_THRESHOLD_TOKENS` 的行为作用，移除固定 120K 请求上限、30K 早期历史阈值及“已有摘要就持续逐轮整理”的条件；旧环境残留只产生无秘密的弃用提示，不暗中参与计算。
- 保留由真实模型容量、输出预留、安全余量及明确服务商输入限制推导的最终请求边界；它不是另一个人为固定上限。普通配置主要只需理解窗口与比例，不删除现有输出参数和独立摘要通道能力。
- 在每次业务模型调用之前检查完整待发请求，包含工具循环、最终回答与清洗；统一覆盖三个公开图及智能溯源实际模型子任务，保留现有摘要提示、存储及身份守卫。
- 基础问答和数据分析继续保存完整历史、摘要和成功覆盖位置；最近三轮由绝对保护改为默认目标，必要时可整理其中较早的已完成轮次，保护当前用户问题及调用配对。
- 服务商明确报上下文超限时，自动整理并仅重试失败的模型逻辑调用一次；不重放已完成工具、整图或已发布正文，摘要与恢复发送前重新核权。
- 保留默认 8,000 字符的独立用户输入限制，补齐前端提示、提交校验和三图后端一致校验；同步容量展示及兼容契约，避免把触发线、输入边界、上下文窗口和历史账单混淆。

## Capabilities

### New Capabilities

- `model-context-governance`：窗口比例触发、完整请求容量检查、覆盖一致性、主子模型接线、一次自动恢复及容量状态。
- `chat-input-limits`：服务端权威的用户输入字符上限、页面一致校验和受信配置读取。

### Modified Capabilities

无。当前主规格尚无上述两个能力；`model-context-governance` 已存在于完成但未归档的旧 change。本变更新增同名能力作为后续整合目标，明确取代旧触发和固定应用上限，保留覆盖及隐私边界；不改写旧验收或顺手归档旧 change。详见 design.md 的冲突处理。

## Impact

- 后端：`src/common/config/settings.py`、`models.py`、`model_budget.py`、`conversation_summarizer.py`、`context_guard.py`、业务节点和工具结果治理；`src/data_analysis/`；`src/intelligent_tracing/{guards,graph,subagents}.py` 及必要的模型/观测适配。
- 页面与接口：`frontend/components/chat/ChatPanel.tsx`、容量显示、`lib/{chat-store,langgraph-api}.ts`、认证 BFF 和自定义 HTTP 配置读取；如扩展事件/API，按仓库规则同步登记、校验器和样本。
- 配置及文档：`.env.example`、上下文治理说明、`DESIGN.md`、`UX-CONTRACT.md` 及部署配置的相关引用；本次计划不修改私有环境、真实模型选型或当前窗口值。
- 不要求依赖升级、数据库破坏性迁移、历史消息删除、IAM/RAG 外部仓库修改或业务能力扩展；不恢复旧服务和评测环境。
- 验收区分定向测试、真实 SDK 图执行、真实浏览器和真实配置模型；百万窗口的受控验证不等于真实百万模型已通过。实施及交接依据见 design.md、tasks.md 和 handoff.md。
