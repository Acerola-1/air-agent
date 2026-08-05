## 新增需求

### 需求：DeepAgents 基础提示词保留执行能力同时抑制过程叙述
系统 SHALL 通过 `HarnessProfile.base_system_prompt` 替换默认 DeepAgents `BASE_AGENT_PROMPT`，使用项目维护的提示词，该提示词保留任务理解、工具使用、迭代执行、验证、错误分析、澄清行为和完成持久性能力，同时抑制用户可见的过程叙述。

#### 场景：工具工作在无自然语言过程内容的情况下进行
- **WHEN** 智能体确定在回答之前需要一个或多个工具调用
- **THEN** 面向模型的基础提示词要求智能体以空的自然语言内容发出工具调用，并静默继续执行直到最终答案准备就绪

#### 场景：DeepAgents 任务执行行为保持可用
- **WHEN** 智能体接收到需要读取上下文、调用工具、重试或验证结果的复杂任务
- **THEN** 自定义基础提示词保留等同于先理解、行动、验证、持续工作直到完成并仅报告真正阻塞的指令

#### 场景：最终答案保持面向用户
- **WHEN** 所有必需的工具工作完成
- **THEN** 智能体的自然语言答案仅包含最终结论、必要证据、关键数据和可操作指导，不提及内部步骤、工具、重试、文件、Skill、中间件、LangGraph 节点或执行进度

### 需求：Profile 注册支持 OpenAI 兼容模型切换
系统 SHALL 以适用于使用 OpenAI 兼容 `ChatOpenAI` 模型客户端的业务智能体的方式注册静默 DeepAgents 基础提示词，即使具体第三方模型名称变更也适用。

#### 场景：使用 provider 级 OpenAI 兼容 profile
- **WHEN** 业务图使用预配置的 `ChatOpenAI` 模型创建 DeepAgent，该模型的 LangSmith provider 解析为 `openai`
- **THEN** 已注册的 `"openai"` HarnessProfile 应用静默基础提示词，无需为每个模型名称进行模型特定注册

#### 场景：Profile 注册在图创建之前完成
- **WHEN** 任何业务图模块使用 `create_deep_agent(...)` 构建其 `graph`
- **THEN** 静默 HarnessProfile 已完成注册，使 DeepAgents 使用自定义基础提示词而非默认 `BASE_AGENT_PROMPT` 组装最终系统提示词

#### 场景：未来精确模型覆盖仍可行
- **WHEN** 未来模型需要不同的 DeepAgents 基础提示词行为
- **THEN** 实现允许添加精确 `provider:model` HarnessProfile，无需更改共享静默基础提示词注册模式

### 需求：静默行为仅适用于模型文本内容
系统 SHALL 将静默输出定义为抑制 `AIMessage.content` 中面向过程的自然语言内容，同时保留内部工具调用结构、`ToolMessage` 状态和 LangGraph 执行语义。

#### 场景：工具调用结构在内部保持可用
- **WHEN** 智能体发出 `AIMessage.tool_calls` 或接收 `ToolMessage` 结果
- **THEN** 这些消息对 LangGraph 和模型保持可用以继续执行，即使基础提示词禁止将它们暴露为用户可见的过程叙述

#### 场景：结构化消息渲染仍为调用方责任
- **WHEN** 客户端或 SSE 消费者直接将 `AIMessage.tool_calls`、`ToolMessage` 或 LangGraph 更新事件渲染为聊天文本
- **THEN** 此能力不保证这些结构被隐藏，因为隐藏结构化消息属于响应渲染层而非 DeepAgents 基础提示词
