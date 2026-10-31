## ADDED Requirements

### Requirement: System SHALL gracefully intercept oversized user inputs at graph entry

系统 SHALL 在业务图入口节点（如 `classify_intent`）检查当前轮次用户的 `HumanMessage` 文本长度。当输入字符数超过 `MAX_INPUT_CHARS`（默认 8,000 字符）时，系统 MUST 直接生成友好的分段提示回复，并直接路由至 `END` 节点结束执行，不触发后续模型推理或工具调用。

#### Scenario: 用户正常长度输入顺利放行

- **WHEN** 用户输入的提问文本长度为 150 字符（≤ 8,000 字符）
- **THEN** 图入口节点正常执行意图分类或路由逻辑，进入后续正常工作流

#### Scenario: 用户超长提问输入优雅拦截

- **WHEN** 用户输入的提问文本长度达到 12,000 字符（> 8,000 字符）
- **THEN** 入口节点 MUST 拦截本次请求，生成提示文本：“您的输入内容过长（共 12000 字符，单次提问上限为 8000 字符），请精简或分段提问”
- **AND** 系统 MUST 直接路由至 `END`，不调用大模型推理，不消耗 Token

### Requirement: System SHALL prune historical tool outputs read-only before model invocation

系统 SHALL 在 `call_model` 节点将上下文消息发送给 LLM 之前，提供只读修剪能力。当全量消息的估算 Token 数超过 `MODEL_CONTEXT_BUDGET_TOKENS`（默认 120,000 Tokens）时，系统 MUST 将历史已完成轮次的旧 `ToolMessage.content` 置换为占位符，清空对应历史 `AIMessage.tool_calls` 参数，同时保证当期轮次所有数据 100% 完整保留。

#### Scenario: 消息总 Token 未超预算时全量透传

- **WHEN** 发送给模型的上下文总估算 Token 数量为 25,000 Tokens（≤ 120,000 预算）
- **THEN** 系统 MUST 不执行任何修剪，保留全量历史消息直接送入模型

#### Scenario: 当期轮次工具结果享有绝对豁免权

- **WHEN** 上下文总 Token 超过 120,000 预算，且当前轮次包含刚调用的 3 个工具结果（每个 10,000 字符）
- **THEN** 系统 MUST 100% 完整保留这 3 个当前轮次的工具结果
- **AND** 仅对前几轮已由模型总结过的历史老旧工具结果进行占位替换

#### Scenario: 保持 tool_call_id 与消息协议严格闭合

- **WHEN** 历史某条 `ToolMessage` 的内容被修剪为占位符 `"[历史工具数据已省略]"`
- **THEN** 该消息的 `tool_call_id`、`name` 与 `type="tool"` 结构 MUST 保持不变
- **AND** 对应的历史 `AIMessage` 中对应 `id` 的 `tool_call` 项 MUST 保持存在，仅其 `args` 字段被清空为 `{}`

#### Scenario: 数据库持久化状态不受只读修剪影响

- **WHEN** `call_model` 完成了只读上下文修剪并调用模型
- **THEN** LangGraph State 中的 `messages` 列表及 Checkpointer 保存的状态 MUST 保持原始未修剪的历史记录
- **AND** 前端通过 State 获取历史记录时能完整查看历史交互
