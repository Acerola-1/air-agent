## 背景

当前 `StreamingMiddleware` 通过拦截 `model.astream()` 实现流式输出，存在以下问题：

1. **重复造轮子**：LangChain/LangGraph 已内置 `astream_events()` API，提供完整的事件流（LLM tokens、工具调用、Chain 状态）
2. **tool_calls 处理复杂**：需要手动累积，容易出错
3. **与 LangSmith 冲突**：自定义 Middleware 破坏了 callback 机制
4. **事件类型单一**：只能推送 message，无法区分思考过程、工具调用、最终答案

LangChain 官方提供了 `astream_events()` API，支持完整的事件类型：
- `on_chat_model_stream`：LLM 流式输出
- `on_tool_start`/`on_tool_end`：工具调用生命周期
- `on_chain_end`：最终答案

## 变更内容

- **移除** `StreamingMiddleware`（不再需要自定义流式处理）
- **新增** Java 端调用 `astream_events()` 的支持
- **修改** 前端事件处理逻辑，支持多种事件类型
- **保留** `custom` stream_mode 用于业务自定义事件（rich_output、expanded_questions）

**破坏性变更**：前端事件格式变化，需要同步更新

## 能力

### 新增能力

- `event-stream-api`: 使用 LangChain 官方 `astream_events()` API 实现事件流推送

### 修改能力

无

## 影响

- **Python 端**：移除 `src/agent/middleware/streaming_middleware.py`
- **Java 端**：修改 `LangGraphService.java`，调用 `astream_events()`
- **前端**：修改 `chat_test.html`，处理新的事件格式
- **事件格式**：从单一 `message` 类型变为多种事件类型
