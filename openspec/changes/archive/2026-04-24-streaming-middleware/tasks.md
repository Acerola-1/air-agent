## 1. 创建 StreamingMiddleware

- [x] 1.1 创建 `src/agent/middleware/streaming_middleware.py`，实现 `StreamingMiddleware` 类
- [x] 1.2 使用 `model.astream()` 实现 `awrap_model_call` 钩子
- [x] 1.3 累积 chunk 并通过 `stream_writer({"type": "message", "message": chunk})` 推送
- [x] 1.4 返回包含完整 `AIMessage` 的 `ModelResponse`

## 2. 集成到 DeepAgent

- [x] 2.1 在 `src/agent/middleware/__init__.py` 导出中添加 `StreamingMiddleware`
- [x] 2.2 在 `src/agent/agent.py` 的 middleware 列表中添加 `StreamingMiddleware()`
- [x] 2.3 验证 middleware 顺序（在 `AnthropicPromptCachingMiddleware` 之前）

## 3. 测试

- [x] 3.1 运行 `make lint` 验证代码风格
- [ ] 3.2 通过 chat_test.html 测试流式输出
- [ ] 3.3 验证前端控制台出现 `type: "message"` 事件
