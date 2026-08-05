## 原因

当前 6 个业务模块被建模为同一个 Main Agent 下的 SubAgent，导致 Main Agent 实际承担路由、计划和结果搬运职责。由于 SubAgent 结果会以工具消息形式返回给 Main Agent，最终输出无法由业务 Agent 直接控制，并且容易出现父 Agent 二次规划、包装或改写的问题。

业务模块已经由页面参数明确决定，且每个模块拥有独立流程、技能目录和输出格式，因此应将 6 个业务模块提升为 6 个顶层 LangGraph graph，由调用方直接选择 graph。

## 变更内容

- **BREAKING** 废弃旧 `agent` graph 作为 Main Agent 路由入口，不再支持通过父 Agent 委派到业务 SubAgent。
- 新增 6 个顶层业务 graph：`basic-qa`、`intelligent-analysis`、`data-analysis`、`intelligent-report`、`deep-research`、`intelligent-tracing`。
- 每个业务 graph 都是独立 DeepAgent 实例，拥有自己的 `system_prompt`、`skills`、`tools`、`middleware`、`model`、权限策略、内部规划流程和最终输出格式。
- 调用方只需要指定 graph 名称即可无缝使用对应业务能力，不再传递或依赖 `module` 参数。
- 6 个业务 graph 共用当前 PostgreSQL checkpointer，保持现有线程恢复和 checkpoint 能力。
- 业务 graph 直接流式输出最终 token，同时保持当前推送内容和格式兼容，包含既有富输出和图表数据推送行为。
- SubAgent 仅保留为未来单个业务 graph 内部真正需要上下文隔离或并行委派时的可选机制，本次迁移不再使用 SubAgent 承载 6 个业务模块。

## 能力

### 新增能力

- `business-graph-architecture`: 定义 6 个业务模块作为顶层 graph 的路由、配置隔离、流式输出和兼容性要求。

### 修改的能力

- `subagent-architecture`: 废弃 Main Agent 路由调度和 6 个业务 SubAgent 的需求，避免旧父子 Agent 架构继续作为目标实现。

## 影响范围

**Python / LangGraph**：
- `langgraph.json`：移除旧 `agent` graph，注册 6 个业务 graph，并保留既有 `data_analysis` graph。
- `src/basic_qa/`、`src/intelligent_analysis/`、`src/data_analysis/`、`src/intelligent_report/`、`src/deep_research/`、`src/intelligent_tracing/`：分别承载 6 个业务 graph 的入口、配置和各自的 `skills/` 目录。
- `src/data_analysis_assistant/`：保留既有数据分析辅助 graph。
- `src/common/`：承载共享配置、模型、工具、中间件、MCP 客户端、checkpointer 和通用 graph 装配能力。
- `src/common/context.py`、`src/common/middleware/mode_routing_middleware.py`：移除或收敛 `module` / `subagent_type` 路由语义，保留 `mode` 等仍需要的运行时参数。

**调用方 / 流式推送**：
- 调用方通过 graph 名称选择业务模块，不再依赖 `module` 参数。
- SSE / LangGraph stream 输出格式保持兼容，业务 graph 直接输出最终 token，并继续支持现有 `custom` 富输出推送。

**测试**：
- 需要覆盖 6 个 graph 的装配、graph 名称注册、共享 checkpoint、流式 token 输出、富输出推送和旧 `agent` graph 不再暴露。
