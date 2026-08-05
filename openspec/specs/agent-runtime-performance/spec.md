# agent-runtime-performance Specification

## Purpose
TBD - created by archiving change optimize-graph-startup-latency. Update Purpose after archive.
## Requirements
### Requirement: 轻量 graph 导入
除显式配置的启动预热路径外，系统 SHALL 避免在 graph 模块导入阶段执行阻塞式外部 MCP 或 PostgreSQL I/O。

#### Scenario: 导入所有已配置 graph
- **WHEN** LangGraph 服务导入 `langgraph.json` 中列出的所有 graph
- **THEN** graph 导入 SHALL NOT 同步从远程 MCP server 拉取工具
- **AND** graph 导入 SHALL NOT 为每个 graph 模块分别创建 PostgreSQL checkpointer 连接

#### Scenario: 启动时 MCP 不可用
- **WHEN** graph 模块导入期间 MCP server 变慢或不可用
- **THEN** graph 导入 SHALL 在不等待所有 MCP server discovery 调用完成的情况下结束
- **AND** 系统 SHALL 保留足够运行时状态，以便后续重试 MCP discovery

### Requirement: checkpointer 初始化复用
系统 SHALL 在同一进程内复用 PostgreSQL checkpointer 初始化结果，避免多个 graph 模块为同一运行时各自创建连接并执行 setup。

#### Scenario: 多个 graph 请求 checkpointer
- **WHEN** 同一进程内两个或更多 graph 模块请求 checkpointer
- **THEN** 第一次成功初始化后，系统 SHALL 返回共享的已初始化 checkpointer 实例
- **AND** PostgreSQL setup SHALL NOT 按 graph 模块重复执行

#### Scenario: checkpointer 初始化失败
- **WHEN** 第一次 checkpointer 初始化因 PostgreSQL 不可用而失败
- **THEN** 系统 SHALL 暴露清晰的初始化错误或降级启动路径
- **AND** 系统 SHALL NOT 将失败的 checkpointer 缓存为可用实例

#### Scenario: checkpointer 缓存失效
- **WHEN** 系统显式重置或失效当前 checkpointer 缓存
- **THEN** 系统 SHALL 尽力关闭旧 checkpointer 持有的底层连接
- **AND** 后续获取 checkpointer SHALL 重新创建并 setup 新实例

### Requirement: 并发安全的 MCP 工具初始化
系统 SHALL 使用并发保护、并行 server 加载、按 server 状态和失败退避来初始化 MCP 工具。

#### Scenario: 并发请求触发 MCP 初始化
- **WHEN** 多个请求同时尝试初始化 MCP 工具
- **THEN** 同一 server 在同一时间 SHALL 只有一个初始化操作执行 discovery
- **AND** 其他调用方 SHALL 观察已完成或进行中的结果，而不是重复执行 discovery

#### Scenario: 跨线程或跨 event loop 并发初始化
- **WHEN** 同一进程内多个线程或不同 event loop 同时触发 MCP 初始化
- **THEN** 同一 server 的 discovery SHALL 仍然只执行一次
- **AND** 模块级工具列表和按 server 状态 SHALL 在进程级锁保护下更新

#### Scenario: 独立 MCP server 并行加载
- **WHEN** 系统初始化 ipp 和 datacenter MCP server 工具
- **THEN** server discovery 调用 SHALL 并发等待
- **AND** 慢 server SHALL NOT 在自身完成时间之外拖慢另一个 server 的 discovery

#### Scenario: 一个 MCP server 失败
- **WHEN** 一个 MCP server discovery 失败，另一个 server 成功
- **THEN** 成功 server 的工具 SHALL 保持可用
- **AND** 失败 server SHALL 标记为 unavailable，并记录 retry deadline
- **AND** 后续紧邻调用 SHALL NOT 在退避窗口允许前反复重试失败 server

### Requirement: 业务工具运行时懒可用
系统 SHALL 在 MCP 工具成功发现后于运行时提供 MCP-backed 业务工具，而不要求重新创建 graph。

#### Scenario: graph 创建后工具完成初始化
- **WHEN** graph 在 MCP 工具完全发现前已经创建
- **THEN** 运行时工具注册或中间件 SHALL 能向后续请求暴露新发现的 MCP 工具
- **AND** 现有工具名称和用户可见工具结果 schema SHALL 保持不变

#### Scenario: 工具暂时不可用
- **WHEN** 请求需要尚未发现的 MCP-backed 业务工具
- **THEN** 系统 SHALL 触发初始化，或通过现有 resilience middleware 返回清晰的降级响应
- **AND** 系统 SHALL NOT 因 graph 早于 MCP discovery 导入完成而失败

### Requirement: Text2SQL 图表类型确定性选择
当结果形态规则可以决定图表类型时，系统 SHALL 避免默认调用 LLM 仅用于判断 Text2SQL 图表类型。

#### Scenario: SQL 结果为空或单值
- **WHEN** Text2SQL 返回空 DataFrame、一行或一列
- **THEN** visualization type SHALL 为 `none`，且不调用 LLM 做图表分类

#### Scenario: SQL 结果较大
- **WHEN** Text2SQL 返回行数超过配置的图表阈值
- **THEN** visualization type SHALL 为 `table`，且不调用 LLM 做图表分类

#### Scenario: SQL 结果为时间序列
- **WHEN** Text2SQL 返回至少一个时间类列和至少一个数值列，且行数在图表阈值内
- **THEN** visualization type SHALL 为 `line`，且不调用 LLM 做图表分类

#### Scenario: SQL 结果为分类对比
- **WHEN** Text2SQL 返回分类列和数值列，且行数在图表阈值内，并且不存在更强的时间序列信号
- **THEN** visualization type SHALL 为 `bar`，且不调用 LLM 做图表分类

#### Scenario: SQL 结果模糊
- **WHEN** 确定性图表规则无法高置信度选择类型
- **THEN** 系统 SHALL 返回确定性兜底类型，或仅在显式 fallback 配置启用时调用 LLM

### Requirement: 知识检索快速路由
系统 SHALL 在使用 LLM fallback 前，通过确定性规则和短 TTL 缓存路由知识检索 query。

#### Scenario: query 命中政策指标
- **WHEN** 知识检索 query 包含高置信度政策、标准、法规或文档元数据指标
- **THEN** 系统 SHALL 路由到 policy retrieval，且不调用 routing LLM

#### Scenario: query 命中通用知识指标
- **WHEN** 知识检索 query 包含高置信度技术概念、污染物解释或治理知识指标
- **THEN** 系统 SHALL 路由到 knowledge retrieval，且不调用 routing LLM

#### Scenario: query 路由命中缓存
- **WHEN** 同一 normalized query route 在配置 TTL 内再次被请求
- **THEN** 系统 SHALL 复用缓存路由决策
- **AND** 系统 SHALL NOT 为该决策调用 routing LLM

#### Scenario: query 路由模糊
- **WHEN** 确定性路由置信度低，且不存在缓存条目
- **THEN** 系统 SHALL 调用 routing LLM fallback
- **AND** 系统 SHALL 将路由结果缓存到配置 TTL 内

### Requirement: 推荐追问可选且生命周期可靠
系统 SHALL 让推荐追问生成可配置，并在 graph 生命周期内可靠推送或有界跳过。

#### Scenario: 禁用推荐追问
- **WHEN** 运行时配置禁用推荐追问生成
- **THEN** expand-question middleware SHALL 跳过本地模型调用
- **AND** 主答案流程 SHALL 正常完成

#### Scenario: 启用推荐追问
- **WHEN** 运行时配置启用推荐追问生成
- **THEN** 推荐追问生成 SHALL 在当前 `aafter_agent` 生命周期内运行，避免 detached task 丢失 stream writer
- **AND** 生成的追问 SHALL 在可用时仍通过现有 stream event 形状发出
- **AND** 等待时间 SHALL 受短超时控制

#### Scenario: 推荐追问生成失败
- **WHEN** 推荐追问模型调用失败或超时
- **THEN** 系统 SHALL 记录失败日志
- **AND** 系统 SHALL NOT 让主答案失败

### Requirement: 缓存 data-analysis Skill 元数据
系统 SHALL 缓存 `find_skill` 使用的 data-analysis 菜单 Skill 元数据，使菜单命中请求不再反复读取和解析静态 `SKILL.md` frontmatter。

#### Scenario: 创建 data-analysis find_skill 工具
- **WHEN** data-analysis `find_skill` 工具被创建
- **THEN** 系统 SHALL 预加载或准备已知菜单 Skill 元数据缓存
- **AND** 缓存 SHALL 包含暴露的 Skill 路径和 allowed tool 名称

#### Scenario: 重复命中同一菜单
- **WHEN** 重复请求命中同一个 data-analysis 菜单 Skill
- **THEN** `find_skill` SHALL 返回与之前相同的 payload 形状
- **AND** 系统 SHALL NOT 每次调用都读取并解析同一个 `SKILL.md` frontmatter

#### Scenario: 未知菜单查询
- **WHEN** 菜单名未命中已配置的 data-analysis Skill
- **THEN** `find_skill` SHALL 保持现有 unmatched 响应 payload
- **AND** 系统 SHALL NOT 需要读取任何 Skill 文件

