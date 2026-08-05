## 新增需求

### 需求：Java 端处理 messages 事件
Java 服务 SHALL 正确解析 LangGraph `messages` 事件，提取 LLM 流式输出内容并推送到前端。

#### 场景：接收到 AI 消息 chunk
- **WHEN** LangGraph 返回 `event: messages` 类型的 SSE 事件
- **AND** data 包含 `AIMessageChunk` 类型的消息对象
- **THEN** Java 服务提取 `content` 字段
- **AND** 构造 `type: message` 的 SSE 事件推送到前端

#### 场景：处理流式消息累积
- **WHEN** 连续收到多个 `messages` 事件
- **THEN** Java 服务按顺序推送每个 chunk 到前端
- **AND** 累积完整内容用于持久化

### 需求：messages 事件格式解析
Java 服务 SHALL 正确解析 LangGraph `messages` 事件的数据结构。

#### 场景：解析标准 messages 事件
- **WHEN** 收到如下格式的事件：
  ```json
  {
    "event": "messages",
    "data": [
      {"type": "ai", "content": "你好", "id": "msg-xxx"},
      {"langgraph_node": "agent", "langgraph_step": 1}
    ]
  }
  ```
- **THEN** 提取 `data[0].content` 作为消息内容
- **AND** 提取 `data[0].type` 判断消息类型（ai/human/tool）

#### 场景：处理 tool_calls 信息
- **WHEN** `AIMessageChunk` 包含 `tool_calls` 或 `tool_call_chunks` 字段
- **THEN** Java 服务忽略该字段（工具执行在 Python 端完成）
- **AND** 继续正常处理 `content` 字段

### 需求：前端 SSE 格式兼容
Java 服务 SHALL 保持前端 SSE 事件格式不变。

#### 场景：推送消息到前端
- **WHEN** 处理完 `messages` 事件后
- **THEN** 构造如下格式的 SSE 事件：
  ```json
  {
    "type": "message",
    "runId": "xxx",
    "name": "assistant",
    "stage": "agent",
    "output": "消息内容"
  }
  ```
- **AND** 前端无需任何修改即可正常显示
