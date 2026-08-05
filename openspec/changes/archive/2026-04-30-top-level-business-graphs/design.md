## 背景

当前应用在 `langgraph.json` 中暴露 `agent` 作为 Main Agent graph，并暴露 `data_analysis` 作为独立 graph。`agent` graph 是一个 DeepAgent 实例，注册了六个业务 SubAgent，并使用运行时上下文决定通过 `task` 工具调用哪个 SubAgent。

这种架构与产品流程不匹配。页面已经知道业务模块，且每个模块拥有自己的技能、工具、权限、规划风格和最终响应格式。将这些模块保留为 SubAgent 会强制结果以工具消息形式返回给 Main Agent，这导致两个问题：业务 Agent 无法直接拥有最终输出，并且 Main Agent 仍可以对委派结果进行规划、总结或改写。

## 目标 / 非目标

**目标：**

- 将六个业务模块暴露为六个顶层 LangGraph graph：`basic-qa`、`intelligent-analysis`、`data-analysis`、`intelligent-report`、`deep-research` 和 `intelligent-tracing`。
- 使每个业务 graph 成为独立的 DeepAgent 实例，拥有自己的提示词、技能、工具、中间件、权限、模型选择、记忆行为、规划规则和输出契约。
- 从公开的 LangGraph 配置中移除旧的 Main Agent 路由 graph。
- 所有业务 graph 复用现有 PostgreSQL checkpointer。
- 保持当前面向调用方的流式输出内容和事件格式，包括最终 token 流式输出和自定义富输出/图表数据推送。
- 移除 `module` 和 `subagent_type` 作为必要的运行时路由概念。在业务 graph 仍需要 fast/expert 行为时保留 `mode`。

**非目标：**

- 不重新设计前端 UI 或发明新的页面-模块分类法。
- 不实现业务模块之间的自动 LLM 路由。
- 不要求调用方同时传递 graph 名称和 `module`。
- 不将较旧的进行中流式输出 OpenSpec 变更作为本提案的一部分解决。
- 不移除单个业务 graph 内部未来可能用于专门委派的 SubAgent 可能性；仅停止将 SubAgent 作为六个顶层业务模块边界使用。

## 决策

### 使用顶层 graph 作为业务边界

`langgraph.json` 将为每个业务模块注册一个 graph。前端或 API 层直接选择 graph 名称：

```text
调用方选择的 graph
        │
        ▼
┌──────────────────────────────┐
│ 独立 DeepAgent graph          │
│ - 提示词                      │
│ - 技能                        │
│ - 工具                        │
│ - 中间件                      │
│ - 权限                        │
│ - 模型                        │
│ - checkpointer                │
└──────────────────────────────┘
        │
        ▼
直接最终 token 流式输出
```

考虑过的替代方案：保留一个公开的 `agent` graph 并使用 `module` 进行内部路由。这保留了当前 API 形态，但保留了本变更旨在消除的父/SubAgent 工具消息问题。

### 用 graph 配置工厂替代 `get_all_subagents()`

现有 SubAgent 定义已包含有用的模块特定提示词、技能、工具和中间件。这些定义应迁移为可复用的业务 graph 配置工厂，而非复制六次。

工厂应明确各 graph 的差异：

- graph 名称
- 系统提示词
- 技能目录
- 工具列表
- 中间件列表
- 模型
- 权限行为
- 响应/输出期望

共享设置（如 MCP 工具初始化和通用静态工具）应保持集中管理。

### 从公开配置中移除旧 `agent` graph

公开的 `agent` graph 将从 `langgraph.json` 中移除。将不存在接收 `module` 并委派到业务 SubAgent 的兼容 graph。这是一个设计上的破坏性变更：调用方必须直接选择目标 graph。

如果在实现过程中需要临时的本地兼容代码，该代码不得作为公开 graph 导出。

### 共享当前 PostgreSQL checkpointer

所有六个业务 graph 将使用现有 `get_checkpointer()` 路径。这保持了 checkpoint 和恢复行为与当前运行时的一致性，避免为每个模块引入独立的存储契约。

graph 名称成为调用方选择的执行边界的一部分。当同一用户在不同业务 graph 之间切换时，线程标识符和 checkpoint 命名空间必须继续正确工作。

### 保持流式输出和富输出契约

业务 graph 将直接通过调用方当前使用的标准 LangGraph 消息流输出最终 LLM token。富输出、图表数据和其他非 token 推送必须保持现有自定义事件负载结构，以便调用方只需更改 graph 名称而无需更改渲染逻辑。

当前支持权限检查、时间上下文、旧版图表兼容性、问题扩展和富输出的中间件仍可逐个 graph 附加，但不得依赖于父 Agent 接收 SubAgent 工具消息。

### 保留 `mode`，移除 `module`

`mode` 仍然是每个 graph 内部 fast/expert 行为的有效运行时控制。`module` 及其派生的 `subagent_type` 不再需要，因为 graph 选择即模块选择。

任何指示 Agent 基于 `subagent_type` 调用 `task` 的提示词或中间件文本必须从业务 graph 路径中移除。

## 风险 / 权衡

- [风险] 部署后仍调用旧 `agent` graph 的调用方将失败。→ 缓解措施：在从部署的 LangGraph 配置中移除 `agent` 之前，更新调用方配置和测试。
- [风险] 跨 graph 共享 checkpoint 可能暴露旧线程总是在 `agent` 下恢复的假设。→ 缓解措施：添加至少两个 graph 名称的 checkpoint 恢复冒烟测试，并记录 graph 选择要求。
- [风险] 部分中间件可能依赖 SubAgent 工具消息格式，尤其是渲染文本或富输出处理。→ 缓解措施：审计中间件输入，为 token 流式输出和自定义图表数据推送添加回归测试。
- [风险] 六个 graph 间重复的工具初始化可能减慢启动速度。→ 缓解措施：集中管理 MCP/静态工具创建，在安全处复用不可变配置。
- [风险] 移除父 Agent 同时也移除了自动路由。→ 缓解措施：这是可接受的，因为产品页面已经提供 graph 名称；不需要 `module=auto` 行为。

## 迁移计划

1. 为每个 graph 引入一个顶层包：`src/basic_qa/`、`src/intelligent_analysis/`、`src/data_analysis/`、`src/intelligent_report/`、`src/deep_research/`、`src/intelligent_tracing/`，并保留 `src/data_analysis_assistant/`。
2. 将共享的模型、工具、中间件、权限、MCP、checkpointer 和 graph 装配代码移入 `src/common/`。
3. 将每个业务模块的技能移入该模块自己的 `skills/` 目录。
4. 将模块特定的提示词、技能、工具、中间件、权限和模型设置从 SubAgent 定义移入 graph 本地规格。
5. 更新 `langgraph.json`，仅暴露六个业务 graph 名称和任何仍被有意支持的无关现有 graph。
6. 移除旧的公开 `agent` graph 条目，删除或停止导出 Main Agent 路由代码。
7. 更新运行时上下文辅助函数和提示词，从直接业务 graph 路径中移除 `module` 和 `subagent_type`。
8. 对所有六个 graph 名称运行 graph 装配和流式输出回归测试。
9. 部署时更新调用方的 graph 选择。

回滚策略：如果调用方在生产环境中无法选择 graph 名称，则恢复之前的 `langgraph.json` 和 Main Agent graph 导出。回滚应视为临时措施，因为它会恢复 SubAgent 输出控制问题。

## 待解决问题

无。调用方契约已确认：页面可以直接传递六个 graph 名称之一，旧 `agent` graph 应被移除，所有业务 graph 应共享当前 PostgreSQL checkpointer。
