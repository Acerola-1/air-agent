## 1. MCP 工具延迟初始化

- [x] 1.1 将 nodes.py 中 `asyncio.run()` 模块级 MCP 初始化改为延迟模式：`ipp_mcp_tools`/`datacenter_mcp_tools`/`permission_mcp_tools` 初始为空列表，新增 `async ensure_mcp_tools()` 函数在首次请求时异步填充
- [x] 1.2 将 `global_websearch_tool` 的 `func` 路径改为同步兜底（移除 `asyncio.run`），`coroutine` 保持异步调用
- [x] 1.3 验证 MCP 工具延迟初始化：确保 `ensure_mcp_tools()` 填充后，所有通过 `nodes.xxx` 访问的工具列表包含正确工具

## 2. PermissionMiddleware 实现

- [x] 2.1 创建 `middleware/permission_middleware.py`，定义 `PermissionMiddleware(AgentMiddleware)` 子类，实现 `abefore_agent` 钩子
- [x] 2.2 在 `abefore_agent` 中调用 `await ensure_mcp_tools()` 确保 MCP 工具已初始化
- [x] 2.3 将 nodes.py 中的 `_classify_permission_need` 规则引擎逻辑迁移到 PermissionMiddleware
- [x] 2.4 实现 `no_check` 路径：规则引擎判断无需校验时直接返回 None
- [x] 2.5 实现 `need_check` 路径：简化 LLM 调用仅提取参数 + MCP `permission_vaild` 工具调用
- [x] 2.6 实现 `uncertain` 路径：完整 LLM 判断含上下文补全 + 路由决策
- [x] 2.7 实现 interrupt 机制：权限失败时调用 `interrupt({"type": need_login/need_permission, "message": ...})`，resume 后提取 user_id 重新校验
- [x] 2.8 实现 stream_writer 推送：权限校验进度 `{"type": "progress"}` + 错误消息 `{"node": "permission_eval", "type": "permission_error"}`
- [x] 2.9 权限通过时返回 None（继续 DeepAgent 主流程），失败时返回状态更新结束对话

## 3. ExpandQuestionMiddleware 实现

- [x] 3.1 创建 `middleware/expand_question_middleware.py`，定义 `ExpandQuestionMiddleware(AgentMiddleware)` 子类，实现 `aafter_agent` 钜子
- [x] 3.2 从 state.messages 中提取最后一条 HumanMessage（问题）和最后一条纯文本 AIMessage（答案）
- [x] 3.3 调用 Qwen2.5:14b 流式生成推荐追问，按换行分割为列表
- [x] 3.4 通过 `runtime.stream_writer` 推送 `{"node": "expand_question", "type": "expanded_questions", "message": list}`
- [x] 3.5 LLM 返回 "无" 时推送空列表

## 4. ModeRoutingMiddleware 实现

- [x] 4.1 创建 `middleware/mode_routing_middleware.py`，使用 `@dynamic_prompt` 装饰器定义中间件
- [x] 4.2 从 `runtime.config["configurable"]` 提取 `mode` 和 `module` 参数
- [x] 4.3 默认值：`mode="fast"`，`module="auto"`（未指定时由 Main Agent 智能判断）
- [x] 4.4 格式化为 `[module:XXX][mode:XXX]` 标记追加到 system prompt

## 5. RichOutputMiddleware 实现

- [x] 5.1 创建 `middleware/rich_output_middleware.py`，定义 `RichOutputMiddleware(AgentMiddleware)` 子类，实现 `awrap_tool_call` 钜子
- [x] 5.2 在工具执行完成后拦截 ToolMessage，解析 JSON 提取 `rich_outputs[]` 或 `chart_data`
- [x] 5.3 支持 Skill output-hints 的 `display_mode: none` 跳过规则
- [x] 5.4 通过 `runtime.stream_writer` 推送 `{"node": "deepagent_executor", "type": "rich_output", ...}` 格式与现有一致
- [x] 5.5 非 JSON / 非成功的 ToolMessage 静默跳过

## 6. SubAgent 配置重构

- [x] 6.1 将 SubAgent 配置从独立 `deepagent_config/` 包迁移到 `config/subagents.py`（或保持独立但改为延迟构建）
- [x] 6.2 实现 `get_all_subagents()` 延迟构建函数，通过 `nodes.xxx` 模块属性访问 MCP 工具
- [x] 6.3 SubAgent 工具列表按模块分配：basic(7类工具)、analysis(6类)、interactive(8类含websearch)、knowledge(6类)
- [x] 6.4 SubAgent system prompt 保持现有内容（从 config/subagent_prompts.py 导入）

## 7. 服务入口重构

- [x] 7.1 创建 `agent.py`（新入口文件），在模块级调用 `create_deep_agent()` 组装所有 Middleware + SubAgent + tools + checkpointer
- [x] 7.2 组装顺序：`middleware=[PermissionMiddleware, ExpandQuestionMiddleware, ModeRoutingMiddleware, RichOutputMiddleware]`
- [x] 7.3 传入 `checkpointer=PostgresSaver(...)`、`backend=FilesystemBackend(...)`、`subagents=get_all_subagents()`
- [x] 7.4 更新 `langgraph.json` 入口改为 `"agent": "./src/agent/agent.py:graph"`
- [x] 7.5 删除 `graph.py`（不再需要 StateGraph 编译）

## 8. 清理与验证

- [x] 8.1 从 nodes.py 中移除已迁移到 Middleware 的节点函数：`permission_agent`、`permission_eval`、`permission_interrupt_node`、`chat_mode_init`、`expand_question`、`error_response_node`
- [x] 8.2 从 nodes.py 中移除已迁移的路由函数：`route_after_*` 系列
- [x] 8.3 保留 nodes.py 中仍需要的模块级变量：模型配置、MCP 客户端、Redis、Vanna、text2sql_tool、retriever_tool 等
- [x] 8.4 从 deepagent_integration.py 中移除 `DeepAgentWrapper` 类和 `deepagent_executor` 函数（已迁移至 Middleware）
- [x] 8.5 保留 deepagent_integration.py 中仍需要的 `_extract_rich_outputs_from_message` 逻辑（已迁移至 RichOutputMiddleware）
- [x] 8.6 运行 `ruff check` + `pyright` 确认代码质量通过
- [ ] 8.7 运行 `langgraph dev` 验证服务启动和基本请求流程（需要完整服务基础设施）