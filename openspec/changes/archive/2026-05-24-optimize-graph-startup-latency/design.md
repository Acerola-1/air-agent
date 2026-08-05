## 背景

项目通过 `langgraph.json` 暴露 6 个 LangGraph graph 入口。当前部分 graph 模块会在导入时执行 MCP 工具发现，所有 graph 又会在模块加载时构造 PostgreSQL checkpointer。这会让服务在处理任何请求之前，就依赖外部网络和数据库健康状态。

请求路径中也存在可避免的 LLM 调用：Text2SQL 成功执行 SQL 后使用 LLM 判断图表类型，知识检索每次都使用 LLM 判断 knowledge/policy 路由，推荐追问在 `aafter_agent` 返回前执行本地模型流式调用。data-analysis Skill 发现还会在每次菜单命中时重复读取同一个 `SKILL.md` frontmatter。

## 目标 / 非目标

**目标：**

- 让 graph 模块导入保持轻量，并在 MCP 或 PostgreSQL 变慢时具备韧性。
- 让 MCP 工具初始化支持并行加载、并发安全、局部可用和失败退避。
- 在进程内复用 checkpointer 初始化结果。
- 在不改变公开工具名称和响应格式的前提下，减少高频工具路径中的默认 LLM 调用。
- 避免推荐追问这类可选用户体验增强功能拖慢主答案完成路径。
- 为初始化行为、路由规则和缓存行为增加聚焦测试。

**非目标：**

- 不替换 LangGraph、DeepAgents、PostgreSQL checkpointing、MCP、Vanna 或当前模型注册体系。
- 不改变 graph 名称、Skill 目录结构、MCP 工具名称或前端 configurable 字段名。
- 不承诺所有外部依赖全部不可用时仍完整可用；目标是优雅降级和更快隔离故障。
- 不重做 Text2SQL 的 SQL 生成能力或向量检索质量。

## 决策

### 懒加载 graph 业务工具

graph 模块停止在导入阶段同步解析 MCP 工具。业务工具注册移动到共享懒加载提供者后面，由中间件在运行时使用；也可以提供显式预热函数，在启动完成后异步预热。

备选方案是保留导入期同步解析，只增加更长超时。这个方案改动小，但仍然把服务加载和外部 MCP 健康状态绑定在一起，不能解决启动阻塞问题。

### 进程内 checkpointer 单例

`get_checkpointer()` 缓存当前进程内已经初始化成功的 checkpointer。第一次成功调用创建 PostgreSQL 连接并执行 `setup()`，后续 graph 导入复用同一个保存对象。

备选方案是每个 graph 一个 checkpointer，但延迟初始化。完全懒加载时这能减少导入工作量，但多个 graph 被使用后仍会重复创建连接并执行初始化。

### MCP 初始化状态机

MCP 加载按 server 维护状态，而不是只使用一个全局全有或全无标志。初始化过程使用进程级 `threading.Lock` 和每个 server 的加载完成事件保护状态写入，避免跨线程、跨 event loop 或同步初始化路径重复发现；实际 server 发现使用 `asyncio.gather()` 并行加载独立 server，并为每个失败 server 维护重试截止时间，避免失败后立即重复重试。

备选方案是在现有 `_mcp_initialized` 之外只加一个 event-loop 本地 `asyncio.Lock`。它能避免同一 loop 内重复初始化，但同步路径会新建线程和 event loop，首次请求跨线程并发时仍可能重复发现并竞态写入模块级工具列表。

### Text2SQL 图表类型默认使用确定性判断

图表类型默认使用 DataFrame 形态规则判断：空结果或单值结果返回 `none`，大结果返回 `table`，时间列加数值列返回 `line`，分类列加数值列返回 `bar`，模糊场景返回 `table` 或按配置启用 LLM 兜底。

备选方案是保留 LLM 分类并缓存结果。缓存能改善重复相同结果，但每个新查询仍要为通常可由模式和行数推断出的判断支付一次模型调用。

### 知识检索快速路由

知识检索在调用路由 LLM 前先应用确定性关键词和元数据规则。完全相同的 query/top_k 路由决策使用短 TTL 缓存。LLM 仍保留给低置信度或模糊 query。

备选方案是完全移除 LLM router。这样延迟最低，但中文环保领域问题中的 policy/knowledge 边界有时模糊，因此保留 LLM 兜底更稳妥。

### 推荐追问可选且生命周期内推送

推荐追问生成由运行时配置控制。启用时在 `aafter_agent` 生命周期内用短超时生成并推送，避免分离任务在 graph run 结束后丢失 stream writer；禁用、超时或模型失败时直接跳过，不影响主答案内容成功返回。

备选方案是用 `asyncio.create_task()` 脱离 graph 生命周期后台生成。这能降低 `aafter_agent` 等待时间，但真实 stream 可能在 graph run 完成后关闭，导致 `expanded_questions` 事件丢失。

### 缓存 data-analysis Skill 元数据

data-analysis 菜单 Skill 元数据在工具创建时加载，或首次使用时缓存。缓存按 skill name 保存 allowed tools 和暴露路径，同时保持现有菜单匹配行为和返回负载形状。

备选方案是保留每次调用读取文件，因为文件很小。这个固定开销确实不大，但没有必要；缓存实现直接，行为风险也低。

## 风险 / 权衡

- 局部 MCP 初始化可能暴露的工具少于完整启动场景 -> 中间件和工具注册需要保持清晰降级行为，并在之后重试失败 server。
- 单例 checkpointer 在 PostgreSQL 重启后可能持有陈旧连接 -> 实现需要提供 reset/invalidate 入口，并在 reset 时关闭旧连接。
- 推荐追问仍可能在短超时内未完成 -> 追问属于辅助信息，不能作为正确性必需条件。
- 确定性图表规则在模糊数据上可能选择不够理想的图表 -> 保留可配置 LLM 兜底，并覆盖常见 DataFrame 形态测试。
- TTL 缓存在 prompt 或元数据变化后可能短暂返回旧路由 -> 使用短 TTL 和进程内缓存，进程重启即清空。

## 迁移计划

1. 增加共享懒加载提供者和缓存，同时保留当前公开函数和工具名称。
2. 更新 graph 模块，使用懒加载业务工具注册和缓存 checkpointer。
3. 改造 MCP 初始化内部实现，不改变导出的工具列表变量。
4. 增加 Text2SQL 图表判断和知识检索路由的确定性快速路径，默认行为保持安全。
5. 增加推荐追问配置，并更新中间件以避免阻塞主答案完成。
6. 为每个变更路径增加聚焦单元测试，并运行目标 lint/type/test 命令。

回滚方式直接：graph 模块可恢复到急切 `_resolve_tools()`，`get_checkpointer()` 可停止缓存，若出现非预期行为，可通过配置关闭可选快速路径。
