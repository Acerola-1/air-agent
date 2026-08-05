## Context

当前 6 个业务入口都是独立的 DeepAgent graph。业务回答正文主要由各模块 Skill 控制，已经包含固定结构、口径和数据真实性约束。旧方案尝试从 DeepAgents 基础提示词层面减少过程信息，但提示词约束无法保证所有模型和所有执行路径都不输出过程性内容，Java 调用方直接监听 messages 时仍可能看到工具计划、内部字段、执行阶段、失败重试或框架细节。

现有代码已有 `ExpandQuestionMiddleware` 这样的 `aafter_agent` 后置中间件，它能在主 Agent 完成后读取 `state.messages`，并通过 `runtime.stream_writer` 推送 custom 事件。这说明最终输出清理可以作为横切中间件实现，而不需要恢复旧的父 Agent + `task` SubAgent 架构。

## Goals / Non-Goals

**Goals:**

- 在业务 DeepAgent 完成后，新增一个最终输出清理中间件，统一作用于 6 个业务 graph。
- 中间件开始时立即推送 `type: "progress"` 的 custom 事件，`message` 固定为“正在整理最终答案”。
- 从 `state.messages` 中提取最后一条可见业务答案，跳过带 `tool_calls` 的中间 `AIMessage` 和 `ToolMessage`。
- 使用 finalizer 模型流式输出清理后的最终答案，供 Java API 优先监听。
- finalizer 的职责只限于删除过程信息和内部实现信息，必须保留原文业务结构、Markdown 层级、事实、数值、单位、结论顺序和 Skill 输出风格。
- finalizer 失败时不影响主回答完成，并允许调用方退化到原始 Messages。

**Non-Goals:**

- 不重构业务 Skill 内容、工具调用策略、MCP 工具、图入口或 LangGraph state schema。
- 不把最终整理实现为普通 DeepAgents `task` subagent；默认 `task` 只接收 description，不能稳定读取完整父 Agent state。
- 不要求实时逐 token 清洗主 Agent 原始 token；清理阶段在主 Agent 完成后开始，清理后的最终答案可以流式输出。
- 不改变现有 `progress`、`rich_output`、`expanded_questions` 的事件语义。
- 不用 finalizer 重新分析业务数据、补充结论、修正口径或改写为新的报告结构。

## Decisions

### Decision 1: 使用 `aafter_agent` 后置中间件，而不是 DeepAgents `task` SubAgent

实现新增 `FinalOutputCleanupMiddleware`，在 `aafter_agent` 中读取完整 `state.messages` 并推送 finalizer custom 事件。

理由：

- 后置中间件能看到完整 Agent state，适合选择最后可见业务答案并降级处理。
- 普通 `task` SubAgent 默认只通过 `description` 获取任务，容易丢失父 Agent 的完整输出上下文。
- 当前项目已经用 `ExpandQuestionMiddleware` 证明了 `aafter_agent` 中推送流式 custom 事件的可行性。

替代方案：使用父 Agent 调用专门整理 SubAgent。该方案会恢复旧链路的一次额外工具调用和父子消息包装，增加延迟和不确定性，不适合作为第一版。

### Decision 2: Java 侧优先监听 finalizer custom 事件，Messages 作为降级

中间件推送三个事件类型：

- `progress`：开始整理时立即推送，`message` 为“正在整理最终答案”。
- `final_output_delta`：finalizer 模型每个流式片段推送增量文本。
- `final_output_done`：finalizer 完成时推送完整清理后文本。

理由：

- Java 调用方可以忽略原始 messages 流中的过程文本，只消费 finalizer 事件。
- `final_output_done` 便于断线、重试或非流式调用时直接取完整正文。
- 保留原始 Messages 作为 fallback，不让 finalizer 成为主回答的单点故障。

替代方案：直接覆盖最后一条 `AIMessage` 并只让 Java 继续读 messages。该方案对流式消费不够清楚，也容易与 LangGraph 原始消息流混淆。

### Decision 3: finalizer 提示词采用“保真清理”而不是“总结重写”

finalizer prompt 必须明确：

- 只删除工具调用计划、进度播报、失败重试、内部字段、函数名、JSON、路径、Skill、middleware、LangGraph、MCP 等过程或实现信息。
- 不得总结、扩写、压缩、重排、翻译或美化原答案。
- 保留原 Markdown 结构、标题、列表、表格、数值、单位、日期、结论顺序、风险提示和建议。
- 如果原答案已经干净，原样输出。

理由：

- 业务正文通常已经由 Skill 精准控制，重新组织会破坏业务口径。
- 本变更目标是隐藏过程信息，不是提升文案质量。

替代方案：让 finalizer 根据全量 `state.messages` 重新生成最终答案。该方案可能引入事实漂移、口径变化和重复分析，不符合本次范围。

### Decision 4: 中间件顺序放在业务回答完成后、推荐追问之前

在各业务 graph 的 `_build_middleware()` 中接入 `FinalOutputCleanupMiddleware()`；如果该 graph 已有 `ExpandQuestionMiddleware()`，则将最终输出清理放在推荐追问之前。

理由：

- 推荐追问应基于用户最终看到的干净答案生成，而不是基于可能包含过程信息的原始答案。
- `ToolProgressMiddleware` 和 `RichOutputMiddleware` 仍在工具阶段正常推送可控事件。

替代方案：放在 `ExpandQuestionMiddleware()` 之后。该方案可能让推荐追问读取未清理答案，生成质量和安全边界较差。

### Decision 5: 失败降级必须静默且不阻断主链路

finalizer 出现模型异常、超时、空输出或 writer 不可用时，中间件只记录日志并返回 `None` 或保留原状态。Java 侧可继续读取原有 Messages。

理由：

- 主 Agent 已经完成业务回答，finalizer 是展示层清理增强，不应导致用户无答案。
- 失败时暴露“整理失败”本身也是过程信息，不应推送给用户。

## Risks / Trade-offs

- finalizer 会增加一次模型调用和最终正文首 token 延迟 → 使用轻量模型、设置短超时，并保持失败 fallback。
- finalizer 可能误删业务正文中的有效内容 → prompt 和测试都以“只删过程信息、其余原样保留”为核心约束，覆盖表格、标题、数值和单位场景。
- Java 同时监听原始 messages 和 finalizer 事件会重复显示 → 文档和测试明确调用方优先消费 `final_output_delta/final_output_done`，原 Messages 仅作为 fallback。
- 中间件无法清理已经由调用方提前展示的原始 token → 本方案要求 Java 侧改变监听策略，主 Agent 执行阶段只展示可控 custom 事件，不展示原始正文 token。
- finalizer 事件和推荐追问事件顺序可能影响前端体验 → 对已启用推荐追问的 graph，固定最终清理先于推荐追问。

## Migration Plan

1. 新增 `FinalOutputCleanupMiddleware` 和 finalizer prompt/helper。
2. 为提取最后可见 AI 答案、progress 事件、delta/done 事件、失败 fallback 和保真清理 prompt 添加单元测试。
3. 将中间件接入 6 个业务 graph，并在已存在 `ExpandQuestionMiddleware()` 的 graph 中保证最终清理顺序靠前。
4. 更新或新增图配置测试，确认 6 个 graph 都装配中间件。
5. 用模拟流式模型验证 Java 可消费的事件形状。
6. 回滚时从 6 个 graph 移除该中间件，调用方继续使用原 Messages fallback。

## Open Questions

- finalizer 默认使用哪个模型以及超时时间是否需要环境变量控制。
- Java API 是否需要同时支持非流式模式直接读取 `final_output_done`。
