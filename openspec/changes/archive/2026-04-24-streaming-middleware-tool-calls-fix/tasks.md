## 1. 修复 StreamingMiddleware tool_calls 累积

- [x] 1.1 在 `awrap_model_call` 中添加 `tool_calls` 累积变量
- [x] 1.2 在 `astream()` 循环中累积 `chunk.tool_calls`
- [x] 1.3 将累积的 `tool_calls` 设置到最终返回的 `AIMessage` 中

## 2. 实现分类推送逻辑

- [x] 2.1 添加判断逻辑：检测是否有 tool_calls
- [x] 2.2 有 tool_calls 时使用 `type: "progress"` 推送
- [x] 2.3 无 tool_calls 时使用 `type: "message"` 推送

## 3. 测试验证

- [ ] 3.1 重启 LangGraph 服务
- [ ] 3.2 测试有工具调用的场景（如"湖州市昨天的空气质量如何"）
- [ ] 3.3 验证前端显示：思考过程为 progress，最终结果为 message
- [ ] 3.4 测试无工具调用的场景（如"你好"）
- [ ] 3.5 验证流式输出正常
