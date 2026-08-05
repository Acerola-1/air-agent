## 动机

LangGraph Studio 和服务启动会通过模块导入加载多个 graph，其中部分导入路径会同步访问 MCP 服务和 PostgreSQL。只要 MCP、PostgreSQL 或本地模型服务变慢或抖动，启动耗时和请求尾延迟就会变得不可控。

本次变更减少启动期副作用，并移除高频路径中可避免的模型调用，使 agent 服务在正常负载下启动更稳定、降级更清晰、响应更快。

## 变更内容

- 将 MCP 业务工具加载移出 graph 模块导入路径，改为运行时懒加载，并保证并发安全和局部失败可降级。
- 复用 PostgreSQL checkpointer 初始化结果，避免每个 graph 导入都单独建立连接并执行初始化。
- 并行发现 MCP server 工具，用进程级锁保护初始化过程，并按 server 维护初始化状态和失败退避。
- 将 Text2SQL 默认图表类型判断改为基于 DataFrame 形态的确定性规则，仅在显式配置时使用 LLM 兜底。
- 知识检索先使用规则/关键词快速路由，再在低置信度时调用 LLM，并为重复 query 增加短 TTL 缓存。
- 推荐追问生成改为可配置，在 graph 生命周期内用短超时推送，避免分离任务丢失流事件。
- 预加载或缓存 data-analysis 菜单 Skill 元数据，避免 `find_skill` 每次重复读取和解析 `SKILL.md` frontmatter。

## 能力

### 新增能力

- `agent-runtime-performance`：约束 graph 启动、MCP 工具、checkpointer 复用、可避免 LLM 调用等运行时初始化、延迟和降级能力。

### 修改的能力

无。

## 影响范围

- 影响 graph 入口：`src/basic_qa/graph.py`、`src/intelligent_analysis/graph.py`、`src/data_analysis/graph.py`，以及其他 graph 复用的初始化模式。
- 影响共享运行时代码：`src/common/mcp_client.py`、`src/common/config/checkpointing.py`、`src/common/tools.py`、`src/common/config/vanna_sql_adapter.py`、`src/common/middleware/expand_question_middleware.py`、`src/data_analysis/skill_discovery.py`。
- 不预期改变 graph 名称、工具名称、LangGraph 可配置字段或用户可见工具结果格式。
- 运行时行为应提升 MCP 或 PostgreSQL 变慢时的启动容忍度，并降低 Text2SQL、知识检索和 data-analysis Skill 发现路径的请求延迟；推荐追问通过配置和短超时控制尾延迟。
