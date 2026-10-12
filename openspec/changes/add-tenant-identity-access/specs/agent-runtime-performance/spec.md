# 企业授权作用域下的运行性能增量

## MODIFIED Requirements

### Requirement: 轻量 graph 导入
除显式配置的无敏感身份启动预热路径外，系统 SHALL 避免在 graph 模块导入阶段执行阻塞式外部 MCP 或 PostgreSQL I/O。企业认证的 MCP discovery SHALL 仅在可信授权作用域可用后执行，不得通过导入时的全局凭据预热。

#### Scenario: 导入所有已配置 graph
- **WHEN** LangGraph 服务导入 `langgraph.json` 中列出的所有 graph
- **THEN** graph 导入 SHALL NOT 同步从远程 MCP server 拉取工具
- **AND** graph 导入 SHALL NOT 为每个 graph 模块分别创建 PostgreSQL checkpointer 连接

#### Scenario: 启动时 MCP 不可用
- **WHEN** graph 模块导入期间 MCP server 变慢或不可用
- **THEN** graph 导入 SHALL 在不等待所有 MCP server discovery 调用完成的情况下结束
- **AND** 系统 SHALL 保留足够运行时状态，以便后续在可信授权作用域重试 MCP discovery

#### Scenario: 导入时没有企业身份
- **WHEN** 服务导入图但尚未接收已认证请求
- **THEN** 不得使用某企业凭据初始化所有企业共用的认证工具列表

### Requirement: 并发安全的 MCP 工具初始化
系统 SHALL 使用并发保护、并行加载、按授权作用域状态和失败退避初始化 MCP 工具。作用域至少包括企业、外部主体授权范围、服务及绑定版本；同一作用域的 discovery 去重，不同作用域的凭据、工具列表和错误状态 MUST 隔离。

#### Scenario: 并发请求触发 MCP 初始化
- **WHEN** 多个同一授权作用域的请求同时尝试初始化 MCP 工具
- **THEN** 同一 server 与授权作用域在同一时间 SHALL 只有一个初始化操作执行 discovery
- **AND** 其他调用方 SHALL 观察该作用域已完成或进行中的结果，而不是重复执行 discovery

#### Scenario: 跨线程或跨 event loop 并发初始化
- **WHEN** 同一授权作用域在同一进程内多个线程或不同 event loop 触发 MCP 初始化
- **THEN** 同一 server 与授权作用域的 discovery SHALL 仍然只执行一次
- **AND** 授权工具列表和状态 SHALL 在进程级并发保护下更新，不进入无身份全局列表

#### Scenario: 独立 MCP server 并行加载
- **WHEN** 系统初始化多个已授权服务或独立授权作用域的工具
- **THEN** discovery 调用 SHALL 并发等待
- **AND** 慢服务/作用域 SHALL NOT 在自身完成时间之外拖慢其他服务/作用域的 discovery

#### Scenario: 一个 MCP server 失败
- **WHEN** 一个授权服务/作用域 discovery 失败，另一个成功
- **THEN** 成功作用域的工具 SHALL 保持可用
- **AND** 失败作用域 SHALL 标记为 unavailable，并记录独立 retry deadline
- **AND** 后续该作用域请求 SHALL NOT 在退避窗口允许前反复重试失败 server

#### Scenario: 同一服务的两企业并发
- **WHEN** A/B 以不同授权绑定并发发现同一 MCP 服务的工具
- **THEN** 工具与状态分别缓存，A 的认证失败不污染 B，授权绑定不会互换

### Requirement: 业务工具运行时懒可用
系统 SHALL 在可信授权作用域中完成 MCP discovery 后提供当前成员可用的 MCP-backed 工具，而不要求重新创建 graph。工具列表与执行许可 SHALL 随授权变化更新，不能把另一作用域的发现结果或启动时静态列表作为当前授权。

#### Scenario: graph 创建后工具完成初始化
- **WHEN** graph 在当前授权作用域的 MCP 工具完成发现前已经创建
- **THEN** 运行时工具注册或执行适配 SHALL 能向后续已授权请求提供新发现的工具
- **AND** 现有工具名称和用户可见工具结果 schema SHALL 保持兼容，主体字段按可信契约注入

#### Scenario: 工具暂时不可用
- **WHEN** 已授权请求需要尚未发现的 MCP-backed 工具
- **THEN** 系统 SHALL 在该作用域触发初始化或返回明确有界的不可用/降级响应
- **AND** 系统 SHALL NOT 因 graph 早于 MCP discovery 导入完成而失败
- **AND** 授权缺失 MUST NOT 通过匿名、全局或其他企业身份降级执行

#### Scenario: 运行时授权变化
- **WHEN** 成员许可被撤销或绑定轮换
- **THEN** 后续披露和实际执行使用当前许可，旧作用域工具不能绕过检查
