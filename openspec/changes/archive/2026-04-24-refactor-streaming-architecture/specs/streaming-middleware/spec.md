## 移除需求

### 需求：Middleware 拦截模型调用进行流式输出
**原因**：该实现绕过了 DeepAgents 框架的 `handler(request)` 调用链，导致工具调用丢失、输出中断等问题。官方推荐在 API 层处理流式输出，middleware 只负责请求控制。

**迁移方案**：使用 LangGraph 原生 `messages` 流式模式获取 LLM 输出。Java 端通过 `stream_mode: ["messages", "updates"]` 接收 `AIMessageChunk` 事件。

### 需求：手动构造 AIMessage
**原因**：流式 chunk 中的 `tool_calls` 需要增量聚合，手动处理容易丢失或格式错误。

**迁移方案**：由 DeepAgents 框架自动处理 `tool_calls` 聚合，Java 端只需解析 `messages` 事件中的 `content` 字段。

## 新增需求

### 需求：Middleware 仅负责请求控制
StreamingMiddleware（如保留）SHALL 不再负责 LLM 流式输出，仅保留请求控制职责。

#### 场景：权限校验前置
- **WHEN** 请求进入 middleware 链
- **THEN** `PermissionMiddleware` 执行权限校验
- **AND** 不拦截模型调用

#### 场景：富输出拦截
- **WHEN** 工具返回包含 `chart_data` 的结果
- **THEN** `RichOutputMiddleware` 通过 `stream_writer` 推送 `custom` 事件
- **AND** 不影响 LLM 流式输出
