## 背景

当前项目使用 LangGraph StateGraph 编排 6 个节点（permission_agent → permission_eval → permission_interrupt → chat_mode_init → deepagent_executor → expand_question），其中 `deepagent_executor` 调用 DeepAgent `create_deep_agent` 作为子图。这种两层嵌套架构导致：

1. `nodes.py` 模块级 `asyncio.run()` 初始化 MCP 工具，在 LangGraph 异步事件循环中触发 RuntimeError
2. 两层状态管理（LangGraph AgentState + DeepAgent 内部 state），信息传递复杂
3. 图编译和节点编排是额外维护成本，DeepAgent 本身已具备这些能力

DeepAgent 框架提供完整的洋葱模型 Middleware 体系，包括 `before_agent`/`after_agent`/`wrap_tool_call` 等钩子，可以替代所有 LangGraph 外层节点功能。

## 目标 / 非目标

**目标：**
- 拆除 LangGraph StateGraph 外层图，直接以 `create_deep_agent` 作为服务入口
- 将权限校验迁移为 `PermissionMiddleware`（before_agent 前置）
- 将问题扩展迁移为 `ExpandQuestionMiddleware`（after_agent 后置）
- 将 mode/module 参数路由迁移为 `@dynamic_prompt` 中间件
- MCP 工具初始化在 Middleware `abefore_agent` 中异步完成，消除 `asyncio.run()`
- 所有前端消息推送格式保持不变（progress / message / rich_output / expanded_questions）
- 保留 PostgreSQL checkpointer 和 SubAgent 架构

**非目标：**
- 不改变 Skill 三层文件体系（由 DeepAgent SkillsMiddleware 管理）
- 不改变前端协议格式（前端零改动）
- 不引入新的外部依赖
- 不重写 MCP 工具接口

## 决策

### D1: 权限校验 -> PermissionMiddleware (before_agent)

**选择**: 自定义 `PermissionMiddleware`，在 `abefore_agent` 钩子中执行权限校验。

**替代方案**:
- A) 作为 Tool 注入 SubAgent，由 Skill 流程指引 LLM 调用 -> 需要改所有 SKILL.md，LLM 可能跳过
- B) `HumanInTheLoopMiddleware` -> 只支持工具调用前的中断，不支持"无权限时整体中断对话"

**理由**: `before_agent` 在 DeepAgent 主循环开始前执行，可以完全阻止请求进入模型调用。权限通过时返回 None（继续执行），不通过时通过 `runtime.stream_writer` 推送错误消息并返回 `{"messages": [AIMessage(content=错误消息)]}` 结束对话。interrupt 机制通过 `langgraph.types.interrupt()` 实现，与现有前端 resume 流程兼容。

### D2: 问题扩展 -> ExpandQuestionMiddleware (after_agent)

**选择**: 自定义 `ExpandQuestionMiddleware`，在 `aafter_agent` 钩子中流式生成推荐追问。

**理由**: `after_agent` 在 DeepAgent 完成回答后执行，自然位置适合后处理。通过 `runtime.stream_writer` 推送 `expanded_questions` 格式与现有一致。

### D3: mode/module 参数 -> @dynamic_prompt 中间件

**选择**: 使用 `@dynamic_prompt` 装饰器创建中间件，从 `runtime.config["configurable"]` 提取 mode/module，动态注入到 system prompt 中（格式化为 `[module:XXX][mode:XXX]` 路由指令）。

**替代方案**:
- A) 在 `before_agent` 中修改 state -> DeepAgent state schema 不同，改 state 麻烦
- B) 在 SubAgent description 中硬编码 -> 不灵活，无法动态切换

**理由**: `@dynamic_prompt` 是 DeepAgent 专为动态注入上下文设计的钩子，最自然的位置。Main Agent 的 system prompt 中已有路由规则表，只需在用户消息前追加 module/mode 标记即可。

### D4: MCP 工具初始化 -> Middleware abefore_agent 延迟加载

**选择**: 在 `PermissionMiddleware.abefore_agent` 中调用 `await ensure_mcp_tools()`，首次请求时异步初始化 MCP 工具列表。

**理由**: 这是权限校验的前置步骤，权限校验本身就需要 MCP 工具（`permission_vaild`）。在同一个中间件中完成初始化和校验，逻辑内聚。初始化完成后 MCP 工具列表填充到模块级变量，后续 SubAgent 构建时可以直接引用。

### D5: 富输出提取 -> wrap_tool_call 拦截

**选择**: 自定义 `RichOutputMiddleware`，在 `awrap_tool_call` 钩子中拦截 ToolMessage，提取 `rich_outputs[]` 或 `chart_data`，通过 `runtime.stream_writer` 推送。

**替代方案**:
- A) 在 `after_agent` 中遍历 state.messages -> 失去了实时性，工具调用完成后才能提取
- B) 保持 deepagent_integration.py 的流式提取 -> 需要保留外层流式处理逻辑

**理由**: `wrap_tool_call` 在每个工具执行后立即触发，能实时提取富输出并推送，与现有流式推送时机完全一致。

### D6: 服务入口 -> 直接暴露 create_deep_agent

**选择**: `langgraph.json` 的 graph 入口改为暴露 `create_deep_agent()` 返回的 CompiledStateGraph，不再经过 `graph.py` 的 StateGraph 编译。

**理由**: DeepAgent 返回的 `CompiledStateGraph` 100% LangGraph 兼容，可以直接被 LangGraph Cloud 加载。只需要在模块级创建 agent 并赋值给 `graph` 变量即可。

### D7: SubAgent 工具构建 -> 延迟函数 + 模块属性引用

**选择**: SubAgent 的 tools 参数使用延迟构建函数 `get_all_subagents()`，内部通过 `nodes.xxx` 访问 MCP 工具（而非 `from nodes import`），确保获取初始化后的最新值。

**理由**: `from X import Y` 在导入时绑定值，后续模块级赋值不会更新引用。`nodes.ipp_mcp_tools` 访问的是模块属性的当前值，初始化完成后能拿到正确数据。

## 风险 / 权衡

- **[interrupt 机制兼容性]** -> DeepAgent 的 `HumanInTheLoopMiddleware` 是 after_model 钩子，只拦截工具调用。权限校验的 interrupt 需要在 before_agent 中使用 `langgraph.types.interrupt()`，与现有前端 resume(Command) 流程需验证兼容 -> 提前测试 interrupt/resume 全流程
- **[MCP 工具首次请求延迟]** -> 首次请求需要等待 MCP 工具异步初始化完成（约 2-5 秒）-> 首次请求推送 "初始化中..." 进度消息，后续请求无延迟
- **[权限校验 LLM 调用]** -> before_agent 中调用 LLM 做参数提取，与 DeepAgent 主循环的 LLM 调用是两个独立调用 -> 权限 LLM 使用轻量模型（DeepSeek-V3 fast），开销可控
- **[模块级 agent 创建]** -> 需要在模块级创建 DeepAgent 实例暴露给 langgraph.json -> 使用延迟初始化模式（首次调用时构建）