# silent-agent-output 规范

## 目的
待定 - 由归档变更 silent-deepagents-base-prompt 创建。归档后更新目的。
## 需求
### 需求: DeepAgents 基础提示词在保留执行能力的同时抑制过程叙述
系统 SHALL 通过 `HarnessProfile.base_system_prompt` 替换默认的 DeepAgents `BASE_AGENT_PROMPT`，使用项目维护的提示词，该提示词在保留任务理解、工具使用、迭代执行、验证、错误分析、澄清行为和完成持久性的同时，抑制用户可见的过程叙述。

系统 SHALL 移除 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 和 `DATA_ANALYSIS_OUTPUT_GUARD` 中的"工具调用静默协议"段落。主流程共享业务提示词 MUST NOT 强制要求工具调用轮"只能发起 tool_calls""AIMessage.content 必须为空""工具返回后保持静默"。

最终用户可见正文 SHALL 以 `FinalOutputCleanupMiddleware` 推送的 `final_output_delta` / `final_output_done` 事件为准；主流程中间 `AIMessage.content`、`tool_calls`、`ToolMessage` 或 LangGraph 更新事件不属于用户可见聊天正文。

#### 场景: 权限修正时模型可在工具调用轮输出说明
- **WHEN** 权限中间件修正了用户查询区域，模型需要在工具调用轮输出权限修正说明
- **THEN** 主流程共享业务提示词 MUST NOT 禁止在 AIMessage.content 中输出自然语言

#### 场景: 工具调用轮不再强制 content 为空
- **WHEN** 模型决定在发起 tool_calls 的同时输出自然语言
- **THEN** `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 和 `DATA_ANALYSIS_OUTPUT_GUARD` MUST NOT 包含"AIMessage.content 必须为空"的约束

#### 场景: 前端仅消费最终正文事件
- **WHEN** 主流程中间消息包含 `AIMessage.content`、`tool_calls` 或 `ToolMessage`
- **THEN** 调用方 MUST NOT 将这些中间消息作为用户可见聊天正文渲染
- **AND** 用户可见正文 MUST 来自 `FinalOutputCleanupMiddleware` 的 `final_output_delta` / `final_output_done` 事件

#### 场景: DeepAgents 任务执行行为保持可用
- **WHEN** 智能体接收到需要读取上下文、调用工具、重试或验证结果的复杂任务
- **THEN** 定制后的基础提示词保留与以下行为等价的指令：先理解、再行动、验证、持续工作直到完成，以及仅报告真正的阻塞问题

#### 场景: 最终答案保持面向用户
- **WHEN** 所有必需的工具工作已完成
- **THEN** 智能体的自然语言答案仅包含最终结论、必要证据、关键数据和可操作指导，不提及内部步骤、工具、重试、文件、技能、中间件、LangGraph 节点或执行进度

### 需求: 配置文件注册支持 OpenAI 兼容模型切换
系统 SHALL 以适用于使用 OpenAI 兼容 `ChatOpenAI` 模型客户端的业务智能体的方式注册静默 DeepAgents 基础提示词，即使具体的第三方模型名称发生变化也能生效。

#### 场景: 使用提供者级别的 OpenAI 兼容配置文件
- **WHEN** 业务图使用预配置的 `ChatOpenAI` 模型创建 DeepAgent，该模型的 LangSmith 提供者解析为 `openai`
- **THEN** 已注册的 `"openai"` HarnessProfile 应用静默基础提示词，无需为每个模型名称进行特定模型注册

#### 场景: 配置文件注册在图创建之前完成
- **WHEN** 任何业务图模块使用 `create_deep_agent(...)` 构建其 `graph`
- **THEN** 静默 HarnessProfile 已完成注册，因此 DeepAgents 使用定制的基础提示词而非默认的 `BASE_AGENT_PROMPT` 来组装最终系统提示词

#### 场景: 未来精确模型覆盖仍然可行
- **WHEN** 未来的模型需要不同的 DeepAgents 基础提示词行为
- **THEN** 实现允许添加精确的 `provider:model` HarnessProfile，而无需更改共享的静默基础提示词注册模式

### 需求: 静默行为仅适用于模型文本内容
系统 SHALL 将静默输出定义为抑制 `AIMessage.content` 中面向过程的自然语言内容，同时保留内部工具调用结构、`ToolMessage` 状态和 LangGraph 执行语义。

#### 场景: 工具调用结构在内部保持可用
- **WHEN** 智能体发出 `AIMessage.tool_calls` 或接收 `ToolMessage` 结果
- **THEN** 这些消息对 LangGraph 和模型保持可用以继续执行，即使基础提示词禁止将它们作为用户可见的过程叙述暴露

#### 场景: 结构化消息渲染仍为调用方责任
- **WHEN** 客户端或 SSE 消费者直接将 `AIMessage.tool_calls`、`ToolMessage` 或 LangGraph 更新事件渲染为聊天文本
- **THEN** 此能力不保证这些结构被隐藏，因为隐藏结构化消息属于响应渲染层而非 DeepAgents 基础提示词的职责

### 需求: 用户可见输出约束
系统 SHALL 移除 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 和 `DATA_ANALYSIS_OUTPUT_GUARD` 中所有通用最终输出清理类约束，包括"严禁泄露内部信息""严禁过程化自述""严禁进度提示"等段落。这些职责 SHALL 由 `FinalOutputCleanupMiddleware` 承担，主流程共享业务提示词 MUST NOT 包含同类通用清理约束。

#### 场景: 提示词不包含内部信息禁止
- **WHEN** 读取 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 文本
- **THEN** MUST NOT 包含"严禁泄露内部信息""严禁过程化自述""严禁进度提示"等约束

#### 场景: 提示词不包含工具失败静默协议
- **WHEN** 读取 `prompts.py` 中的 `TOOL_FAILURE_SILENCE_PROTOCOL`
- **THEN** 该常量 SHALL 为空字符串或已删除

### 需求: 内部机制追问处理
系统 SHALL 保留 `MAIN_AGENT_TOOL_USE_OUTPUT_GUARD` 中的"内部机制追问处理"段落，该段落属于业务交互策略而非输出清理职责。

#### 场景: 用户追问内部机制时仍转换为业务语言
- **WHEN** 用户追问"这个 skill 里没让你查气象数据吗"
- **THEN** 模型仍应转换为业务能力回答，不得承认或解释内部机制
