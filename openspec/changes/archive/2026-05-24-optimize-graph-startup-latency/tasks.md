## 1. 运行时初始化基线

- [x] 1.1 增加聚焦测试或导入探针，证明 graph import 不需要同步 MCP discovery。
- [x] 1.2 增加共享运行时 provider 模块或 helper 函数，为 graph middleware 提供懒加载业务工具访问。
- [x] 1.3 更新 `basic_qa`、`intelligent_analysis`、`data_analysis` graph setup，避免导入期 `_resolve_tools()` MCP 调用。
- [x] 1.4 验证 `langgraph.json` 中所有 graph 入口仍可导入，并暴露相同 graph 名称。

## 2. PostgreSQL Checkpointer 复用

- [x] 2.1 修改 `get_checkpointer()`，在进程本地状态中缓存第一次成功初始化的 checkpointer。
- [x] 2.2 确保 PostgreSQL 初始化失败时不会把失败实例缓存为可用 checkpointer。
- [x] 2.3 增加测试或 mock，证明多个 graph 请求复用一个 checkpointer 且 setup 只调用一次。

## 3. MCP 初始化韧性

- [x] 3.1 重构 MCP 初始化，按 server 跟踪状态、工具列表、工具名称、最后错误和 retry deadline。
- [x] 3.2 使用进程级锁和每个 server 的加载事件保护 MCP 初始化，避免并发调用方重复 discovery。
- [x] 3.3 使用 `asyncio.gather()` 并发加载 ipp 和 datacenter MCP 工具。
- [x] 3.4 保留现有导出的 MCP 工具列表变量和 permission tool 提取行为。
- [x] 3.5 增加完整成功、局部失败、失败退避和并发调用方测试。

## 4. Text2SQL 图表判断快速路径

- [x] 4.1 实现空结果、单值、大结果、时间序列、分类对比和模糊 DataFrame 的确定性图表选择规则。
- [x] 4.2 将 Text2SQL `ask()` 默认接入确定性图表选择。
- [x] 4.3 为模糊图表场景保留显式配置或内部选项，以便启用 LLM fallback。
- [x] 4.4 增加常见 DataFrame 形态单元测试，并验证确定性场景不会调用 `submit_prompt()`。

## 5. 知识检索路由快速路径

- [x] 5.1 为高置信度 policy query 增加确定性关键词/元数据路由。
- [x] 5.2 为高置信度通用 knowledge query 增加确定性关键词路由。
- [x] 5.3 增加短进程内 TTL 缓存，key 使用 normalized query 和相关路由输入。
- [x] 5.4 保留现有 LLM router，作为未缓存模糊 query 的 fallback。
- [x] 5.5 增加 policy 快速路径、knowledge 快速路径、缓存命中、缓存过期和 LLM fallback 测试。

## 6. 推荐追问延迟控制

- [x] 6.1 增加运行时/configurable 控制项，用于启用或禁用推荐追问生成。
- [x] 6.2 修改 `ExpandQuestionMiddleware`，使启用推荐追问时在 graph 生命周期内用短超时生成并推送事件。
- [x] 6.3 处理推荐追问超时或模型失败，并保证主答案不失败。
- [x] 6.4 增加 middleware 测试，覆盖禁用、启用和推荐追问失败路径。

## 7. Data-analysis Skill 元数据缓存

- [x] 7.1 在创建 `find_skill` 工具或首次使用时，按 skill name 预加载或缓存 data-analysis Skill 元数据。
- [x] 7.2 保持现有 matched 和 unmatched `find_skill` payload 形状不变。
- [x] 7.3 增加测试，证明重复命中菜单时不会重复解析同一个 `SKILL.md` frontmatter。

## 8. 验证

- [x] 8.1 对变更的 Python 文件运行 `ruff check` 和 `ruff format --check`。
- [x] 8.2 运行目标单元测试，覆盖 MCP 初始化、checkpointer 缓存、Text2SQL 图表判断、知识路由、推荐追问 middleware 和 data-analysis Skill discovery。
- [x] 8.3 运行 `openspec status --change optimize-graph-startup-latency`，确认变更已达到 apply-ready 状态。

## 9. Review 加固

- [x] 9.1 修复推荐追问 detached task 可能在 stream 生命周期结束后丢事件的问题，改为生命周期内有界等待并补测试。
- [x] 9.2 修复 MCP 初始化只在单 event loop 内互斥的问题，改为进程级互斥并补跨线程/跨 loop 并发测试。
- [x] 9.3 为 checkpointer 缓存增加显式失效入口，并在 reset/invalidate 时关闭旧连接。
