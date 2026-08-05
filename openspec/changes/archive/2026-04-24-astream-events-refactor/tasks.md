## 1. Python 端修改

- [x] 1.1 从 agent.py 移除 StreamingMiddleware
- [x] 1.2 从 middleware/__init__.py 移除 StreamingMiddleware 导出
- [x] 1.3 删除 streaming_middleware.py 文件（或标记为废弃）

## 2. Java 端修改

- [x] 2.1 在 LangGraphService 中实现 astream_events 调用
- [x] 2.2 实现事件类型转换（on_chat_model_stream → thinking 等）
- [x] 2.3 处理 custom stream_mode 的业务事件
- [x] 2.4 更新 SSE 事件格式

## 3. 前端修改

- [x] 3.1 更新 handleSSEEvent 处理新事件类型
- [x] 3.2 实现 thinking 事件的可折叠显示
- [x] 3.3 实现 tool_call/tool_result 事件的显示（暂不实现，后续可扩展）
- [x] 3.4 保持 message 事件的正常显示

## 4. 测试验证

- [ ] 4.1 重启 LangGraph 服务
- [ ] 4.2 测试"湖州市昨天的空气质量如何"，验证事件流
- [ ] 4.3 验证 thinking 事件可折叠显示
- [ ] 4.4 验证 tool_call/tool_result 事件正常显示
- [ ] 4.5 验证最终答案正常显示
- [ ] 4.6 验证 rich_output/expanded_questions 正常推送
