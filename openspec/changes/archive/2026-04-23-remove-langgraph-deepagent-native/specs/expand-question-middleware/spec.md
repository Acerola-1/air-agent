## 新增需求

### 需求：ExpandQuestionMiddleware 在 Agent 响应后生成追问
ExpandQuestionMiddleware SHALL 在 DeepAgent 的 `aafter_agent` 钩子中执行，使用轻量级 LLM 根据用户原始问题和 Agent 回答生成 3-5 个追问。

#### 场景：成功的追问扩展
- **WHEN** DeepAgent 完成响应且回答非空
- **THEN** 中间件调用 Qwen2.5:14b 生成追问，通过 stream_writer 推送为 `{"node": "expand_question", "type": "expanded_questions", "message": [q1, q2, ...]}`

#### 场景：无需扩展
- **WHEN** LLM 响应为"无"（表示无相关追问）
- **THEN** 中间件推送空列表并返回 None

### 需求：ExpandQuestionMiddleware 使用 stream_writer 推送格式
ExpandQuestionMiddleware MUST 使用 `runtime.stream_writer` 推送扩展问题，保持精确格式 `{"node": "expand_question", "type": "expanded_questions", "message": list}`，与现有前端协议兼容。

#### 场景：前端接收扩展问题
- **WHEN** 中间件生成 3 个追问
- **THEN** 前端收到 `{"node": "expand_question", "type": "expanded_questions", "message": ["问题1", "问题2", "问题3"]}`