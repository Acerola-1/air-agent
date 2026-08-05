## 新增需求

### 需求：六个业务模块为顶层 graph

系统 SHALL 将六个页面选择的业务模块暴露为顶层 LangGraph graph，命名为 `basic-qa`、`intelligent-analysis`、`data-analysis`、`intelligent-report`、`deep-research` 和 `intelligent-tracing`。

#### 场景：业务 graph 名称已注册

- **WHEN** LangGraph 加载应用配置
- **THEN** `graphs` 配置包含 `basic-qa`、`intelligent-analysis`、`data-analysis`、`intelligent-report`、`deep-research` 和 `intelligent-tracing`

#### 场景：旧的主路由 graph 不被暴露

- **WHEN** LangGraph 加载应用配置
- **THEN** 旧的公开 `agent` graph 不作为面向调用方的 graph 注册

### 需求：调用方直接选择业务 graph

系统 SHALL 使用调用方选择的 graph 名称作为业务模块选择机制，且 MUST NOT 要求 `module` 运行时参数用于业务路由。

#### 场景：前端调用 basic-qa graph

- **WHEN** 调用方向 `basic-qa` graph 发送请求
- **THEN** 请求由 basic-qa 业务 DeepAgent 直接处理，不调用父路由 Agent

#### 场景：不需要 module 参数

- **WHEN** 调用方向任何已注册的业务 graph 发送请求且不包含 `module` configurable 参数
- **THEN** graph 仍根据自身业务配置处理请求

### 需求：每个业务 graph 拥有自己的 Agent 配置

每个业务 graph SHALL 是独立的 DeepAgent 实例，拥有自己的 `system_prompt`、技能、工具、中间件、权限、模型选择、内部规划规则和最终输出契约。

#### 场景：Basic QA graph 加载 basic QA 技能

- **WHEN** `basic-qa` graph 被构建
- **THEN** 它加载 basic-qa 业务技能目录，不依赖父 Agent 选择的 SubAgent 技能目录

#### 场景：Deep research graph 使用自己的配置

- **WHEN** `deep-research` graph 被构建
- **THEN** 它使用为该 graph 配置的 deep research 提示词、技能目录、工具策略和输出期望

### 需求：业务 graph 共享当前 PostgreSQL checkpointer

所有六个业务 graph SHALL 使用现有 PostgreSQL checkpointer 路径，以便 checkpoint 和恢复行为与当前运行时保持兼容。

#### 场景：Graph 使用共享 checkpointer

- **WHEN** 六个业务 graph 中的任何一个被装配
- **THEN** 它接收现有 checkpointer 工厂返回的 checkpointer

#### 场景：恢复对选定业务 graph 有效

- **WHEN** 调用方针对选定的业务 graph 恢复线程
- **THEN** graph 使用共享 checkpoint 存储继续该 graph 执行的对话状态

### 需求：业务 graph 直接流式输出最终输出

业务 graph SHALL 直接向调用方流式输出最终 LLM token，且 MUST NOT 要求父 Agent 解包、总结或重新发送 SubAgent 工具消息作为用户可见的答案。

#### 场景：直接 token 流式输出

- **WHEN** 业务 graph 生成最终答案
- **THEN** 最终答案 token 通过现有 token 流路径从该 graph 发出

#### 场景：无父 Agent 渲染文本解包

- **WHEN** 业务 graph 完成请求
- **THEN** 最终用户可见的响应不是由父 Agent 从 SubAgent 工具结果中复制 `rendered_text` 产生的

### 需求：保持现有推送内容和格式

系统 SHALL 保持当前面向调用方的推送内容和事件格式，包括最终消息 token 事件和自定义富输出或图表数据推送。

#### 场景：富输出推送兼容性

- **WHEN** 业务 graph 发出图表数据或其他富输出
- **THEN** 发出的自定义负载保持调用方消费的现有结构

#### 场景：调用方仅更改 graph 名称

- **WHEN** 调用方从旧的基于 module 的调用切换为选择六个业务 graph 名称之一
- **THEN** 调用方可以保持现有的消息渲染和富输出处理逻辑

### 需求：mode 保持为 graph 本地运行时控制

系统 SHALL 继续通过 graph 本地的 `mode` 运行时参数支持 fast/expert 行为，当业务 graph 需要该行为时。

#### 场景：Expert 模式影响单个 graph 执行

- **WHEN** 调用方以 `mode=expert` 调用业务 graph
- **THEN** 仅该 graph 执行应用 expert 模式规划规则

#### 场景：mode 不选择业务模块

- **WHEN** 调用方以任何有效 `mode` 调用业务 graph
- **THEN** graph 名称（而非 `mode` 或 `module`）决定哪个业务模块处理请求
