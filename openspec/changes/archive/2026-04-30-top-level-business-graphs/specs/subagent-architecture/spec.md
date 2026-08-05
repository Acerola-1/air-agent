## 移除的需求

### 需求：Main Agent 路由调度

**原因**：页面已经能够直接选择业务 graph，继续使用 Main Agent 通过 `task` 委派到业务 SubAgent 会导致业务 Agent 无法直接控制最终输出，并引入父 Agent 二次规划或改写风险。

**迁移**：调用方 SHALL 直接调用 `basic-qa`、`intelligent-analysis`、`data-analysis`、`intelligent-report`、`deep-research` 或 `intelligent-tracing` graph。旧 `[module:XXX][mode:XXX]` 消息格式和 `subagent_type` 路由语义不再作为目标架构使用。

#### 场景：基础问题模块路由

- **WHEN** 用户请求基础问答业务
- **THEN** 调用方直接调用 `basic-qa` graph，而不是调用 Main Agent 再由 Main Agent 调用 `task(subagent_type="base-agent")`

#### 场景：智能分析模块路由

- **WHEN** 用户请求智能分析业务
- **THEN** 调用方直接调用 `intelligent-analysis` graph，而不是调用 Main Agent 再由 Main Agent 调用 `task(subagent_type="intelligent-analysis-agent")`

#### 场景：未指定 module 时智能判断

- **WHEN** 调用方未指定业务 graph
- **THEN** 系统不提供 Main Agent 自动判断 module 的兼容行为，调用方必须选择明确的业务 graph

#### 场景：未指定 mode 时默认 fast

- **WHEN** 调用方调用业务 graph 且未指定 `mode`
- **THEN** 业务 graph 可以按自己的默认模式处理，但不得依赖 Main Agent 注入 `[mode:fast]` 到 SubAgent `description`

### 需求：6 SubAgent 模块化定义

**原因**：这 6 个模块是页面级业务入口，而不是同一个父 Agent 内部的辅助角色。将它们建模为 SubAgent 会把最终答案包装为工具结果返回父 Agent，破坏业务 Agent 对输出格式和流式输出的直接控制。

**迁移**：将 6 个模块迁移为 6 个顶层业务 graph。每个 graph SHALL 直接配置自己的 DeepAgent 实例、skills、tools、middleware、permissions、model、memory 和输出契约。

#### 场景：base-agent 配置

- **WHEN** 基础问答能力被构建
- **THEN** 系统构建 `basic-qa` 顶层 graph，而不是注册 `base-agent` 作为 Main Agent 的 SubAgent

#### 场景：预留模块配置

- **WHEN** 智能分析、数据分析、智能报告、深入研究或智能溯源能力被构建
- **THEN** 系统构建对应顶层 graph，而不是将这些能力作为预留 SubAgent 挂载到 Main Agent

#### 场景：新模块英文命名

- **WHEN** 模块配置被读取
- **THEN** 使用 `basic-qa`、`intelligent-analysis`、`data-analysis`、`intelligent-report`、`deep-research`、`intelligent-tracing` 作为公开 graph 名称

### 需求：AgentState 路由字段

**原因**：业务模块不再通过 Main Agent 的 state 字段或 `subagent_type` 派生值路由，公开 graph 名称已经是业务选择边界。

**迁移**：移除业务路由对 `module` state 字段的依赖。仍需要的运行模式信息 SHALL 作为 graph-local runtime configurable，例如 `mode`。

#### 场景：state 路由参数传递

- **WHEN** 调用方请求某个业务模块
- **THEN** 调用方选择对应 graph，系统不再要求 Main Agent 将 `module` 和 `mode` 写入 AgentState 后再路由到 SubAgent
