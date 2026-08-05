## 新增需求

### 需求：使用 astream_events API 获取事件流

系统 SHALL 使用 LangChain 官方 `astream_events()` API 获取完整的事件流，而非自定义 Middleware。

#### 场景：调用 astream_events 获取事件流

- **WHEN** Java 端调用 LangGraph 服务
- **THEN** 系统 SHALL 使用 `graph.astream_events(input, version="v2")`
- **AND** SHALL 返回包含完整事件类型的流

#### 场景：事件包含完整元数据

- **WHEN** 事件流返回事件
- **THEN** 每个事件 SHALL 包含以下字段：
  - `event`：事件类型
  - `name`：Runnable 名称
  - `run_id`：运行 ID
  - `data`：事件数据
  - `parent_ids`：父运行 ID 列表

### 需求：事件类型映射

系统 SHALL 将 LangChain 事件映射为前端可识别的事件类型。

#### 场景：LLM 流式输出映射为 thinking

- **WHEN** 收到 `on_chat_model_stream` 事件
- **THEN** 系统 SHALL 映射为 `type: "thinking"`
- **AND** 前端 SHALL 显示为可折叠的思考过程

#### 场景：工具调用开始映射为 tool_call

- **WHEN** 收到 `on_tool_start` 事件
- **THEN** 系统 SHALL 映射为 `type: "tool_call"`
- **AND** SHALL 包含工具名称和参数

#### 场景：工具调用结束映射为 tool_result

- **WHEN** 收到 `on_tool_end` 事件
- **THEN** 系统 SHALL 映射为 `type: "tool_result"`
- **AND** SHALL 包含工具名称和结果

#### 场景：Chain 结束映射为 message

- **WHEN** 收到 `on_chain_end` 事件且为根节点
- **THEN** 系统 SHALL 映射为 `type: "message"`
- **AND** SHALL 包含最终答案

### 需求：保留业务自定义事件

系统 SHALL 保留 `custom` stream_mode 用于业务自定义事件。

#### 场景：rich_output 事件正常推送

- **WHEN** SubAgent 产生图表数据
- **THEN** 系统 SHALL 通过 `stream_writer` 推送 `type: "rich_output"`
- **AND** 前端 SHALL 正常渲染图表

#### 场景：expanded_questions 事件正常推送

- **WHEN** ExpandQuestionMiddleware 生成推荐追问
- **THEN** 系统 SHALL 推送 `type: "expanded_questions"`
- **AND** 前端 SHALL 显示推荐追问列表

### 需求：移除 StreamingMiddleware

系统 SHALL 移除自定义 `StreamingMiddleware`，使用官方事件流机制。

#### 场景：不再使用 StreamingMiddleware

- **WHEN** 系统启动
- **THEN** `StreamingMiddleware` SHALL NOT 在 middleware 链中
- **AND** 流式输出 SHALL 由 `astream_events` 处理

#### 场景：tool_calls 自动保留

- **WHEN** LLM 返回包含 `tool_calls` 的响应
- **THEN** `tool_calls` SHALL 自动保留在事件流中
- **AND** Agent 循环 SHALL 正常执行工具调用
