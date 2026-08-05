## 动机

当前项目将 DeepAgent 作为子图嵌套在 LangGraph StateGraph 中，导致两层图框架争夺异步事件循环，触发 `asyncio.run()` 冲突（模块级 MCP 工具初始化无法在异步环境中执行）。这种混用架构引入了不必要的复杂性：两层状态管理、双重编译、导入时序依赖等问题。DeepAgent 本身就是基于 LangGraph 构建的完整框架，自带 Middleware 洋葱模型、SubAgent 调度、Skills 加载、stream 输出等能力，完全可以直接作为底座运行，无需外层再套一层 LangGraph 图。

## 变更内容

- **BREAKING**: 拆除 `graph.py` 的 LangGraph StateGraph 定义，不再使用独立的图编译和节点编排
- **BREAKING**: 拆除 `nodes.py` 作为独立节点模块，权限校验、模式初始化、问题扩展等功能迁移至 DeepAgent Middleware 体系
- 将权限校验（含规则引擎 + MCP 工具调用 + interrupt）实现为自定义 `PermissionMiddleware`，作为 `before_agent` 前置中间件
- 将问题扩展实现为自定义 `ExpandQuestionMiddleware`，作为 `after_agent` 后置中间件
- 将 `chat_mode_init` 的 mode/module 参数提取合并到 DeepAgent 的 Configuration 或 `@dynamic_prompt` 中间件
- MCP 工具初始化改为 DeepAgent 的启动钩子（`on_startup` 或 Middleware `abefore_agent`），彻底消除 `asyncio.run()` 冲突
- 保留所有现有消息推送格式（progress / message / rich_output / expanded_questions），通过 Middleware 的 `runtime.stream_writer` 实现
- 保留 PostgreSQL checkpointer 配置，通过 `create_deep_agent(checkpointer=...)` 传入
- 4 个 SubAgent 配置（basic/analysis/interactive/knowledge）直接传入 `create_deep_agent(subagents=...)`
- Skill 三层文件体系保持不变，由 DeepAgent SkillsMiddleware 管理

## 能力

### 新增能力
- `permission-middleware`: 权限校验中间件，含规则引擎预分类、LLM 参数提取、MCP 工具调用、interrupt 中断恢复
- `expand-question-middleware`: 问题扩展后置中间件，流式生成推荐追问并推送前端
- `mode-routing-middleware`: mode/module 参数路由中间件，从 configurable params 提取并格式化为 SubAgent task 描述

### 修改的能力
- `chart-data-push`: 从 deepagent_integration.py 的 `_extract_rich_outputs_from_message` 迁移至 Middleware `wrap_tool_call` 钩子中拦截 ToolMessage 提取富输出
- `subagent-architecture`: SubAgent 配置从独立 config 包改为直接在 `create_deep_agent` 中声明，工具列表延迟构建

## 影响范围

- **核心文件变更**: 删除 `graph.py`、大幅重构 `nodes.py`（权限逻辑迁移）、重构 `deepagent_integration.py`（改为 Middleware 声明）
- **服务入口变更**: `langgraph.json` 的 graph 入口改为直接暴露 `create_deep_agent` 返回的 CompiledStateGraph
- **前端协议不变**: 所有 stream writer 推送格式保持兼容，前端无需修改
- **依赖变更**: 不再需要手动构建 StateGraph/ToolNode，但 LangGraph 核心依赖保留（DeepAgent 内部使用）