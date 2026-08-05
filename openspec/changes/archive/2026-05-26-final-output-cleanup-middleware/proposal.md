## Why

DeepAgents 在业务图中执行工具、读取 Skill、重试或整理结果时，仍可能把过程性文本混入最终 `AIMessage.content`，Java 调用方如果直接监听消息流会看到工具计划、执行阶段或内部实现细节。业务 Skill 已经定义了精准的最终正文结构，因此需要在业务 Agent 完成后增加一个后置整理层，只清理过程信息，完整保留业务正文。

## What Changes

- 新增最终输出清理中间件，在 DeepAgent 完成回答后从 `state.messages` 提取最后一条可见业务答案。
- 中间件开始执行时先通过 `runtime.stream_writer` 推送 `type: "progress"` 的 custom 事件，内容固定为“正在整理最终答案”。
- 使用轻量 finalizer 模型对最终答案做流式清理，逐步推送清理后的最终正文，供 Java API 优先监听。
- 清理范围严格限定为过程信息、工具信息、内部字段、执行计划、失败重试和与用户无关的系统实现细节；不得重构、扩写、压缩或改变 Skill 输出的业务结构和事实内容。
- finalizer 失败、超时或无有效输出时，保留原始最后可见 `AIMessage` 作为降级路径，不影响主回答完成。
- 将该中间件接入现有 6 个业务 DeepAgent 图，并保持现有 progress、rich_output、推荐追问、工具和 Skill 执行链路不变。

## Capabilities

### New Capabilities
- `final-output-cleanup`: 定义最终输出清理中间件、流式 finalizer 输出协议、只清理过程信息的保真约束和失败降级行为。

### Modified Capabilities

## Impact

- 影响 `src/common/middleware/`：新增最终输出清理中间件及其单元测试。
- 影响 6 个业务图的 middleware 装配：`basic-qa`、`intelligent-analysis`、`data-analysis`、`intelligent-report`、`deep-research`、`intelligent-tracing`。
- 影响 Java API 消费约定：调用方可优先监听新的最终整理流式 custom 事件，失败时继续退化使用原有 Messages。
- 不改变 DeepAgents 源码、业务 Skill 文件、MCP 工具、rich_output 协议、LangGraph state schema 或现有工具执行逻辑。
