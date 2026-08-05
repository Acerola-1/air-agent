## Why

项目依赖版本落后于 PyPI 最新版，其中 deepagents 0.6.8 落后 2 个 patch，langchain-core 落后 7 个 patch，langchain-openai 落后整个 minor 版本（1.1 → 1.3），langchain-quickjs 落后 minor 版本（0.1 → 0.2）。当前运行在测试分支，适合一次性全量升级并修复兼容性问题。

## What Changes

- **deepagents 0.6.8 → 0.6.10**：核心 agent 框架升级，影响 `create_deep_agent` 工厂函数和中间件协议
- **langchain 1.3.4 → 1.3.9**：patch 升级，bug 修复
- **langchain-core 1.4.0 → 1.4.7**：patch 升级，注意 `AIMessage.content` 类型可能从 `str` 变为 `list[str | dict]`
- **langchain-openai 1.1.12 → 1.3.2**：**BREAKING** minor 跳幅，`astream`/`ainvoke` 行为和 `bind_tools` 可能变化
- **langchain-quickjs 0.1.4 → 0.2.0**：**BREAKING** minor 跳幅，`CodeInterpreterMiddleware` 构造函数可能变化
- **langchain-mcp-adapters 0.2.2 → 0.3.0**：**BREAKING** minor 跳幅，MCP 工具发现/适配 API 可能变化
- **langgraph 1.2.4 → 1.2.5**：patch 升级
- **langgraph-checkpoint-postgres 3.0.5 → 3.1.0**：**BREAKING** minor 跳幅，checkpoint schema 可能需要迁移
- **langsmith 0.8.3 → 0.8.15**：patch 升级

## Capabilities

### New Capabilities
- `dependency-upgrade-compat`: 升级后适配兼容性——修复私有 API 变更、中间件签名变更、AIMessage.content 类型变更、checkpoint schema 迁移等

### Modified Capabilities
<!-- 无现有 spec 需要修改 -->

## Impact

- **11 个自定义中间件**（`src/common/middleware/`）：依赖 `AgentMiddleware` 基类和 `ModelRequest`/`ModelResponse`/`ToolCallRequest` 类型，deepagents 升级可能导致钩子签名变更
- **3 个中间件文件**依赖 `deepagents.middleware._utils.append_to_system_message`（私有 API），重命名/移除将导致 ImportError
- **5 个 graph 文件**（basic_qa, intelligent_analysis, deep_research, intelligent_report, intelligent_tracing）：调用 `create_deep_agent`，参数签名变更将导致启动失败
- **1 个 graph 文件**（data_analysis）：已改为 StateGraph 节点流，不依赖 deepagents，但依赖 langchain-openai 的 `astream`/`ainvoke`
- **5 个 graph 的中间件链**：引用 `CodeInterpreterMiddleware`，langchain-quickjs 升级可能导致中间件链断裂
- **PostgreSQL checkpoint 数据**：langgraph-checkpoint-postgres 升级可能需要 schema 迁移
- **MCP 工具刷新流程**：langchain-mcp-adapters 升级可能影响 `ensure_mcp_tools()` 行为
