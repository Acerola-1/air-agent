## 1. 中间件基础实现

- [x] 1.1 新增 `FinalOutputCleanupMiddleware` 模块，并实现 `aafter_agent` 后置入口。
- [x] 1.2 实现从 `state.messages` 逆序提取最后一条不含 `tool_calls` 且内容非空的 `AIMessage`。
- [x] 1.3 在开始清理前通过 `runtime.stream_writer` 推送 `type: "progress"`、`message: "正在整理最终答案"` 的 custom 事件。
- [x] 1.4 实现 finalizer prompt/helper，明确只清理过程和内部信息，不重构、不扩写、不压缩、不重排业务正文。
- [x] 1.5 实现 finalizer 模型 `astream` 调用，将非空 chunk 推送为 `type: "final_output_delta"` 事件。
- [x] 1.6 在 finalizer 完成时推送 `type: "final_output_done"` 事件，`message` 为完整清理后正文。
- [x] 1.7 实现异常、超时、空输出和 writer 不可用时的静默降级，不阻断主 Agent 回答。

## 2. Graph 集成

- [x] 2.1 在 `src/common/middleware/__init__.py` 导出 `FinalOutputCleanupMiddleware`。
- [x] 2.2 将 `FinalOutputCleanupMiddleware()` 接入 `src/basic_qa/graph.py`，并放在 `ExpandQuestionMiddleware()` 之前。
- [x] 2.3 将 `FinalOutputCleanupMiddleware()` 接入 `src/intelligent_analysis/graph.py`，并放在 `ExpandQuestionMiddleware()` 之前。
- [x] 2.4 将 `FinalOutputCleanupMiddleware()` 接入 `src/data_analysis/graph.py`；该 graph 当前未装配 `ExpandQuestionMiddleware()`，不新增无关行为。
- [x] 2.5 将 `FinalOutputCleanupMiddleware()` 接入 `src/intelligent_report/graph.py`，并放在 `ExpandQuestionMiddleware()` 之前。
- [x] 2.6 将 `FinalOutputCleanupMiddleware()` 接入 `src/deep_research/graph.py`，并放在 `ExpandQuestionMiddleware()` 之前。
- [x] 2.7 将 `FinalOutputCleanupMiddleware()` 接入 `src/intelligent_tracing/graph.py`，并放在 `ExpandQuestionMiddleware()` 之前。

## 3. 单元测试

- [x] 3.1 测试中间件能跳过 `ToolMessage`、空 `AIMessage` 和带 `tool_calls` 的 `AIMessage`，提取最后可见业务答案。
- [x] 3.2 测试无可见业务答案时不调用 finalizer 模型、不推送 finalizer 事件。
- [x] 3.3 测试清理开始时先推送固定 `progress` 事件，文案为“正在整理最终答案”。
- [x] 3.4 使用模拟流式模型测试 `final_output_delta` 和 `final_output_done` 事件内容。
- [x] 3.5 测试 finalizer 异常、超时或空输出时静默降级，不抛出异常、不推送失败说明。
- [x] 3.6 测试 finalizer prompt 包含保真约束：不得重构、扩写、压缩、重排或改变 Skill 输出正文。
- [x] 3.7 测试 Markdown 标题、列表、表格、数值、单位和日期在保真清理要求中被明确保留。

## 4. 配置与回归验证

- [x] 4.1 更新业务 graph 配置测试，确认 6 个 graph 均包含 `FinalOutputCleanupMiddleware`。
- [x] 4.2 更新业务 graph 配置测试，确认 `FinalOutputCleanupMiddleware()` 位于 `ExpandQuestionMiddleware()` 之前。
- [x] 4.3 运行新增中间件单元测试。
- [x] 4.4 运行业务 graph 配置相关单元测试。
- [x] 4.5 运行 `ruff check` 和 `ruff format --check` 覆盖新增或修改的 Python 文件。
- [x] 4.6 手动或用模拟运行确认 Java 可优先监听 `final_output_delta` / `final_output_done`，原 Messages 仍可作为 fallback。
