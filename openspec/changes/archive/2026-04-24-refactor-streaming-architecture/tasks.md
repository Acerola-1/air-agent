## 1. Python 端改造

- [x] 1.1 从 `src/agent/agent.py` 移除 `StreamingMiddleware` 导入和实例化
- [x] 1.2 更新 `src/agent/middleware/__init__.py`，移除 `StreamingMiddleware` 导出
- [x] 1.3 删除或注释 `src/agent/middleware/streaming_middleware.py` 文件
- [ ] 1.4 验证 `RichOutputMiddleware` 仍正常工作（chart_data 推送）

## 2. Java 端事件处理改造

- [x] 2.1 修改 `LangGraphService.streamChat()` 中 `stream_mode` 为 `["messages", "updates"]`
- [x] 2.2 修改 `LangGraphService.streamDataAnalysis()` 中 `stream_mode` 为 `["messages", "updates"]`
- [x] 2.3 修改 `LangGraphService.regenerateFromCheckpoint()` 中 `stream_mode` 为 `["messages", "updates"]`
- [x] 2.4 修改 `LangGraphService.resumeAfterLogin()` 中 `stream_mode` 为 `["messages", "updates"]`

## 3. Java 端 messages 事件处理

- [x] 3.1 在 `LangGraphService` 中新增 `handleMessagesEvent()` 方法
- [x] 3.2 实现 `AIMessageChunk` 格式解析逻辑
- [x] 3.3 更新 `dispatchV2Event()` 增加 `messages` 分支
- [x] 3.4 添加调试日志记录原始事件格式

## 4. 测试验证

- [ ] 4.1 重启 LangGraph 服务，验证 Python 端无报错
- [ ] 4.2 测试简单问答场景，验证 LLM 流式输出正常
- [ ] 4.3 测试 SubAgent 调用场景，验证工具调用正常执行
- [ ] 4.4 测试 chart_data 推送场景，验证富输出正常
- [ ] 4.5 测试中断/恢复场景，验证 updates 事件正常处理

## 5. 文档更新

- [x] 5.1 更新 CLAUDE.md 中关于流式输出的说明
- [x] 5.2 记录 `messages` 事件格式和解析逻辑
