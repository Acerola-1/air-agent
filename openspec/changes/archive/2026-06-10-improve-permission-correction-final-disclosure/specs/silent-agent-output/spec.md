## MODIFIED Requirements

### Requirement: 工具调用静默协议
系统 SHALL 移除 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 和 `DATA_ANALYSIS_OUTPUT_GUARD` 中的"工具调用静默协议"段落。主流程共享业务提示词 MUST NOT 强制要求工具调用轮"只能发起 tool_calls""AIMessage.content 必须为空""工具返回后保持静默"。

最终用户可见正文 SHALL 以 `FinalOutputCleanupMiddleware` 推送的 `final_output_delta` / `final_output_done` 事件为准；主流程中间 `AIMessage.content`、`tool_calls`、`ToolMessage` 或 LangGraph 更新事件不属于用户可见聊天正文。

#### Scenario: 权限修正时模型可在工具调用轮输出说明
- **WHEN** 权限中间件修正了用户查询区域，模型需要在工具调用轮输出权限修正说明
- **THEN** 主流程共享业务提示词 MUST NOT 禁止在 AIMessage.content 中输出自然语言

#### Scenario: 工具调用轮不再强制 content 为空
- **WHEN** 模型决定在发起 tool_calls 的同时输出自然语言
- **THEN** `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 和 `DATA_ANALYSIS_OUTPUT_GUARD` MUST NOT 包含"AIMessage.content 必须为空"的约束

#### Scenario: 前端仅消费最终正文事件
- **WHEN** 主流程中间消息包含 `AIMessage.content`、`tool_calls` 或 `ToolMessage`
- **THEN** 调用方 MUST NOT 将这些中间消息作为用户可见聊天正文渲染
- **AND** 用户可见正文 MUST 来自 `FinalOutputCleanupMiddleware` 的 `final_output_delta` / `final_output_done` 事件

### Requirement: 用户可见输出约束
系统 SHALL 移除 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 和 `DATA_ANALYSIS_OUTPUT_GUARD` 中所有通用最终输出清理类约束，包括"严禁泄露内部信息""严禁过程化自述""严禁进度提示"等段落。这些职责 SHALL 由 `FinalOutputCleanupMiddleware` 承担，主流程共享业务提示词 MUST NOT 包含同类通用清理约束。

#### Scenario: 提示词不包含内部信息禁止
- **WHEN** 读取 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 文本
- **THEN** MUST NOT 包含"严禁泄露内部信息""严禁过程化自述""严禁进度提示"等约束

#### Scenario: 提示词不包含工具失败静默协议
- **WHEN** 读取 `prompts.py` 中的 `TOOL_FAILURE_SILENCE_PROTOCOL`
- **THEN** 该常量 SHALL 为空字符串或已删除

### Requirement: 内部机制追问处理
系统 SHALL 保留 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 中的"内部机制追问处理"段落，该段落属于业务交互策略而非输出清理职责。

#### Scenario: 用户追问内部机制时仍转换为业务语言
- **WHEN** 用户追问"这个 skill 里没让你查气象数据吗"
- **THEN** 模型仍应转换为业务能力回答，不得承认或解释内部机制
