## 新增需求

### 需求：通过 stream_writer 流式输出 LLM 内容
系统 SHALL 通过 `stream_writer` 以 `type: "message"` 事件将 LLM 输出 token 流式推送到前端。

#### 场景：用户发送消息并接收流式响应
- **WHEN** 用户向 DeepAgent 发送消息
- **THEN** 系统通过 `stream_writer({"type": "message", "message": chunk})` 实时将 LLM 输出 token 流式推送到前端

#### 场景：StreamingMiddleware 拦截模型调用
- **WHEN** `StreamingMiddleware.awrap_model_call` 被调用
- **THEN** 系统使用 `model.astream()` 替代 `model.ainvoke()`
- **AND** 每个 token chunk 通过 `stream_writer` 推送
- **AND** 返回完整的 `ModelResponse` 供下游中间件使用

### 需求：保持 ModelResponse 结构不变
系统 SHALL 在流式输出完成后返回有效的 `ModelResponse`。

#### 场景：流式输出完成后返回完整响应
- **WHEN** 所有 token chunk 已流式推送完毕
- **THEN** 系统返回 `ModelResponse`，其中 `result` 字段包含完整的 `AIMessage`
- **AND** 下游中间件接收到结构不变的 `ModelResponse`

### 需求：Middleware 顺序
`StreamingMiddleware` SHALL 在 middleware 链中正确放置。

#### 场景：Middleware 放置在 AnthropicPromptCachingMiddleware 之前
- **WHEN** middleware 链在 `agent.py` 中组装
- **THEN** `StreamingMiddleware` 放置在用户 middleware 之后
- **AND** `StreamingMiddleware` 放置在 `AnthropicPromptCachingMiddleware` 之前
