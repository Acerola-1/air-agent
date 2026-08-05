## 背景

当前 `StreamingMiddleware` 在 middleware 层接管 LLM 流式输出，绕过了 DeepAgents 框架的 `handler(request)` 调用链，导致：
1. **工具调用丢失**：手动构造 `AIMessage` 时未正确传递 `tool_calls`，SubAgent 无法被调用
2. **输出中断**：破坏了框架的工具执行链路，模型输出一段后立即终止
3. **架构违背**：官方推荐 middleware 只做请求控制，流式输出由 API 层统一处理

官方 DeepAgents CLI 使用 `stream_mode: ["messages", "updates"]` 获取 LLM 流式输出，而非在 middleware 层自定义流式处理。

## 变更内容

- **破坏性变更** 移除 `StreamingMiddleware`，middleware 不再负责流式输出
- 修改 Java 端 `stream_mode` 从 `["custom", "updates"]` 改为 `["messages", "updates"]`
- 新增 Java 端 `messages` 事件处理逻辑，解析 LLM 流式 chunk 并推送前端
- 保留 `RichOutputMiddleware` 用于 `chart_data` 推送（仍使用 `custom` 事件）
- 统一 Python 端 `stream_writer` 推送格式，确保 `custom` 事件仅用于非 LLM 输出

## 能力

### 新增能力

- `messages-streaming`: Java 端处理 LangGraph `messages` 事件，实现 LLM 流式输出推送前端
- `stream-event-router`: 统一的事件路由器，区分 `messages`/`custom`/`updates` 事件类型

### 修改能力

- `streaming-middleware`: 移除流式输出职责，仅保留请求控制职责（权限、路由、富输出）

## 影响

**Python 端**：
- `src/agent/middleware/streaming_middleware.py` - 删除或重构为空壳
- `src/agent/agent.py` - 从 middleware 列表中移除 `StreamingMiddleware`
- `src/agent/middleware/__init__.py` - 更新导出

**Java 端**：
- `LangGraphService.java` - 修改 `stream_mode`，新增 `handleMessagesEvent()` 方法
- `LangGraphService.java` - 重构 `dispatchV2Event()` 增加 `messages` 分支

**前端**：
- 无需修改，SSE 事件格式保持兼容（`type: message`）
