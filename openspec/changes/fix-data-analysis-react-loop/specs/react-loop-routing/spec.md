## ADDED Requirements

### Requirement: ReAct 循环由 route_after_model 统一路由
系统 SHALL 在 `call_model` 后由 `route_after_model` 统一决定后续路由：当最后一条 AIMessage 包含 `tool_calls` 且 `iteration_count < MAX_ITERATIONS` 时路由到 `execute_tools`；否则路由到 `finalize_output`。

#### Scenario: 模型返回 tool_calls 且未超迭代
- **WHEN** `call_model` 返回的 AIMessage 包含 `tool_calls` 且 `iteration_count < MAX_ITERATIONS`
- **THEN** 系统 SHALL 路由到 `execute_tools` 节点执行工具

#### Scenario: 模型返回纯文本无 tool_calls
- **WHEN** `call_model` 返回的 AIMessage 不包含 `tool_calls`
- **THEN** 系统 SHALL 路由到 `finalize_output` 节点

#### Scenario: 模型返回 tool_calls 但已超迭代上限
- **WHEN** `call_model` 返回的 AIMessage 包含 `tool_calls` 且 `iteration_count >= MAX_ITERATIONS`
- **THEN** 系统 SHALL 路由到 `finalize_output` 节点

### Requirement: 工具执行后直接回到 call_model
系统 SHALL 在 `execute_tools` 执行完成后路由到 `call_model`，而非 `prepare_model`。`prepare_model` 仅在 `resolve_skill` 后执行一次。

#### Scenario: 首次调用前执行 prepare_model
- **WHEN** 图从 `resolve_skill` 进入
- **THEN** 系统 SHALL 经过 `prepare_model` 组装 system_prompt 和刷新 MCP 后再进入 `call_model`

#### Scenario: 工具执行后不重新执行 prepare_model
- **WHEN** `execute_tools` 完成
- **THEN** 系统 SHALL 直接进入 `call_model`，不经过 `prepare_model`

### Requirement: 迭代计数无 off-by-one
系统 SHALL 使用 `iteration_count` 表示已完成的工具调用轮数。每次 `execute_tools` 后 `iteration_count` 递增 1。`route_after_model` 直接比较 `iteration_count` 与 `MAX_ITERATIONS`，不再额外 +1。

#### Scenario: MAX_ITERATIONS=3 允许 3 轮工具调用
- **WHEN** `MAX_ITERATIONS = 3` 且模型持续返回 `tool_calls`
- **THEN** 系统 SHALL 允许 3 轮完整的 `call_model → execute_tools` 循环，第 3 轮结束后若模型仍返回 `tool_calls` 则强制进入 `finalize_output`

### Requirement: 答案提取兜底
系统 SHALL 在 `_extract_last_visible_answer` 中增加兜底逻辑：当所有 AIMessage 都包含 `tool_calls` 时，提取最后一条 AIMessage 的 `content` 文本作为兜底答案，而非返回空字符串。

#### Scenario: 所有 AIMessage 都带 tool_calls
- **WHEN** 消息列表中所有 AIMessage 都包含非空 `tool_calls`
- **THEN** 系统 SHALL 提取最后一条 AIMessage 的 `content` 文本作为兜底答案

#### Scenario: 存在无 tool_calls 的 AIMessage
- **WHEN** 消息列表中存在不含 `tool_calls` 的 AIMessage
- **THEN** 系统 SHALL 提取最后一条不含 `tool_calls` 且内容非空的 AIMessage 作为答案（保持原有行为）
