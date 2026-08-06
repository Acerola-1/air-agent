## ADDED Requirements

### Requirement: 业务图工厂必须接受 checkpointer 参数

系统 SHALL 为 6 个业务图（basic-qa、intelligent-analysis、data-analysis、intelligent-report、deep-research、intelligent-tracing）提供可注入 checkpointer 的工厂函数或模块级 `graph` 实例。checkpointer 参数 SHALL 默认为 `None`（不绑定任何持久化后端），调用方可在运行时显式传入具体 checkpointer 实例。

#### Scenario: 调用方不传 checkpointer

- **WHEN** 业务图工厂被调用且未传 checkpointer 参数
- **THEN** 编译出的 CompiledStateGraph SHALL NOT 包含任何自定义 checkpointer
- **AND** 该图 SHALL 能在 `langgraph dev` 模式下被平台成功 import 而不抛 `ValueError`

#### Scenario: 调用方显式传入 SQLite checkpointer

- **WHEN** 调用方显式传入 `get_checkpointer()` 返回的 AsyncSqliteSaver 实例
- **THEN** 编译出的 CompiledStateGraph SHALL 使用传入的 checkpointer 作为持久化后端
- **AND** 跨 thread 的状态 SHALL 持久化到本地 SQLite 文件

### Requirement: 业务图模块导入期不得触发 langgraph-api 校验失败

系统 SHALL 确保 6 个业务图在 import 阶段（模块级 `graph = ...` 表达式执行时）不调用任何带自定义 checkpointer 的 `compile()`。具体约束：模块级 import 不应在没有任何上下文的情况下调用 `get_checkpointer()`，避免在 langgraph-api 加载阶段触发 `AsyncSqliteSaver` 构造与平台校验冲突。

#### Scenario: 在 langgraph-api 模式下 import 业务图模块

- **WHEN** langgraph-api 通过 `langgraph.json` 配置 import `src/<name>/graph.py` 模块
- **THEN** 模块 SHALL NOT 抛 `ValueError: Your graph ... includes a custom checkpointer`
- **AND** 模块 SHALL NOT 调用 `get_checkpointer()` 返回的实例作为模块级 `graph` 的持久化后端
- **AND** 模块 SHALL 成功暴露 `graph` 变量供 langgraph-api 加载

#### Scenario: 自建入口加载同一业务图

- **WHEN** 同一业务图模块被 `web/server.py`（或继任入口）以非 langgraph-api 方式 import
- **THEN** 加载方 SHALL 通过工厂函数重新 compile 并显式传入 SQLite checkpointer
- **AND** 图 SHALL 具备本地持久化能力

### Requirement: DeepAgents 图的图工厂必须支持 checkpointer 注入

3 个 DeepAgents 图（intelligent-report、deep-research、intelligent-tracing）SHALL 提供统一的 `create_deep_business_graph(skills_dir, name, checkpointer=None)` 工厂函数，调用 `create_deep_agent(..., checkpointer=checkpointer)`。`create_deep_agent` 接受 `checkpointer=None` 时 SHALL 正常工作（不抛错、不注入默认 `MemorySaver`、不破坏 subagent / middleware 链）。

#### Scenario: 工厂以 checkpointer=None 构造

- **WHEN** `create_deep_business_graph(..., checkpointer=None)` 被调用
- **THEN** 返回的 CompiledStateGraph SHALL NOT 包含任何 checkpointer
- **AND** 内部 `create_deep_agent` SHALL 接受 `checkpointer=None` 而不报 TypeError

#### Scenario: 工厂以 SQLite checkpointer 构造

- **WHEN** `create_deep_business_graph(..., checkpointer=<sqlite_saver>)` 被调用
- **THEN** 返回的 CompiledStateGraph SHALL 使用传入的 checkpointer
- **AND** 跨 thread 状态 SHALL 持久化到本地 SQLite

### Requirement: 6 个图入口文件统一指向 langgraph-api 加载点

`langgraph.json` 的 `graphs` 配置 SHALL 指向 `src/langgraph_entry/<name>.py:graph`，每个入口文件 SHALL 从原 `src/<name>/graph.py` 工厂导出 `graph` 变量。`src/<name>/graph.py` SHALL 仅保留业务图构建逻辑，不再直接暴露 `graph` 变量给 langgraph-api 平台。

#### Scenario: langgraph-api 加载 6 个图入口

- **WHEN** `langgraph dev --config ./langgraph.json` 启动
- **THEN** 平台 SHALL 通过 `src/langgraph_entry/<name>.py:graph` 路径成功 import 全部 6 个图
- **AND** 每个入口 SHALL 在 5 秒内完成 module import
- **AND** `GET /assistants` SHALL 列出全部 6 个图作为 assistant
