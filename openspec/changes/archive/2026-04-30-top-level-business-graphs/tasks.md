## 1. 架构提取

- [x] 1.1 审计当前 Main Agent、SubAgent、中间件、提示词、工具和技能接线，识别可复用的业务模块配置。
- [x] 1.2 为 `basic-qa`、`intelligent-analysis`、`data-analysis`、`intelligent-report`、`deep-research` 和 `intelligent-tracing` 创建业务 graph 配置模型或注册表。
- [x] 1.3 将模块特定的提示词从 SubAgent 定义移入 graph 级别提示词常量或配置条目。
- [x] 1.4 将模块特定的技能目录绑定移入 graph 级别配置条目。
- [x] 1.5 集中管理共享静态工具和 MCP 工具初始化，使六个 graph 构建器复用相同的工具设置路径。

## 2. 业务 Graph 构建

- [x] 2.1 实现一个 graph 工厂，为每个业务 graph 构建一个独立的 `create_deep_agent(...)` 实例。
- [x] 2.2 为每个 graph 配置自己的系统提示词、技能、工具、中间件、模型、权限和输出期望。
- [x] 2.3 将现有 PostgreSQL checkpointer 从 `get_checkpointer()` 附加到所有六个业务 graph。
- [x] 2.4 保持 graph 本地的 `mode` 处理用于 fast/expert 行为，不依赖 `module` 或 `subagent_type`。
- [x] 2.5 确保业务 graph 不将六个业务模块注册为 SubAgent。

## 3. LangGraph 入口点

- [x] 3.1 从稳定的 Python 入口点模块导出六个编译后的 graph 变量或工厂构建的 graph 对象。
- [x] 3.2 更新 `langgraph.json` 注册 `basic-qa`、`intelligent-analysis`、`data-analysis`、`intelligent-report`、`deep-research` 和 `intelligent-tracing`。
- [x] 3.3 从 `langgraph.json` 移除旧的公开 `agent` graph 条目。
- [x] 3.4 仅当无关 graph 条目仍被有意支持且不与六个业务 graph 名称重复时才保留。

## 4. 路由清理

- [x] 4.1 移除 Main Agent 提示词中指示 Agent 选择 SubAgent 或基于 `subagent_type` 调用 `task` 的逻辑。
- [x] 4.2 从运行时上下文辅助函数和中间件中移除对 `module` 的直接业务路由依赖。
- [x] 4.3 移除或停止导出仅用于委派到六个业务 SubAgent 的 Main Agent 路由代码。
- [x] 4.4 仅将 SubAgent 支持保留为未来 graph 本地委派的可选内部机制，而非页面-模块边界。

## 5. 流式输出和输出兼容性

- [x] 5.1 验证最终 LLM token 通过现有调用方消费的 token 事件路径从每个业务 graph 直接流式输出。
- [x] 5.2 验证富输出和图表数据推送保持现有自定义事件负载结构。
- [x] 5.3 验证业务 graph 响应不是由父 Agent 从 SubAgent 工具结果中解包 `rendered_text` 产生的。
- [x] 5.4 确认调用方渲染只需更改选择的 graph 名称，不需要新的消息或富输出解析逻辑。

## 6. 测试和验证

- [x] 6.1 添加单元测试或冒烟测试，证明所有六个业务 graph 入口点成功装配。
- [x] 6.2 添加测试，证明 `langgraph.json` 暴露六个必需的 graph 名称且不再暴露旧 `agent` graph。
- [x] 6.3 添加测试，验证六个业务 graph 构建器间的共享 PostgreSQL checkpointer 接线。
- [x] 6.4 添加回归测试，覆盖 graph 提示词或技能依赖 mode 时的 `mode=fast` 和 `mode=expert` 行为。
- [x] 6.5 添加流式输出回归覆盖，验证最终 token 输出和自定义富输出/图表数据推送。
- [x] 6.6 运行 `make lint` 和 `make test`。

`make test` 通过。`make lint` 已运行，但在本变更范围之外的预存仓库级 Ruff/mypy 问题上失败，主要在 `docs/deepagents` 下以及现有的类型化依赖缺口。

## 7. 文档和迁移说明

- [x] 7.1 记录新的调用方契约：直接选择六个 graph 名称之一，不传递 `module`。
- [x] 7.2 记录旧 `agent` graph 的破坏性移除。
- [x] 7.3 更新仍将六个业务模块描述为 Main Agent SubAgent 的内部架构说明。
