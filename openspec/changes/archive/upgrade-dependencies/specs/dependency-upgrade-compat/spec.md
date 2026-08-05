# dependency-upgrade-compat

## 概述

升级 9 个核心依赖包后，修复所有兼容性问题，确保项目编译通过、测试通过、6 个 graph 正常启动。

## 升级目标版本

| 包名 | 当前版本 | 目标版本 |
|------|---------|---------|
| deepagents | 0.6.8 | 0.6.10 |
| langchain | 1.3.4 | 1.3.9 |
| langchain-core | 1.4.0 | 1.4.7 |
| langchain-openai | 1.1.12 | 1.3.2 |
| langchain-quickjs | 0.1.4 | 0.2.0 |
| langchain-mcp-adapters | 0.2.2 | 0.3.0 |
| langgraph | 1.2.4 | 1.2.5 |
| langgraph-checkpoint-postgres | 3.0.5 | 3.1.0 |
| langsmith | 0.8.3 | 0.8.15 |

## 需求

### REQ-1：全部依赖升级到目标版本

执行 `pip install --upgrade` 将上述 9 个包升级到目标版本，运行 `pip freeze` 确认版本正确。

### REQ-2：编译零错误

`python3 -m compileall src/` 零编译错误。修复所有因 API 变更导致的 import 错误、类型错误、签名不匹配。

### REQ-3：Lint 零错误

`ruff check src/` 零 lint 错误。

### REQ-4：`append_to_system_message` 兼容

3 个中间件文件（time_context_middleware.py、mode_routing_middleware.py、permission_classify_middleware.py）依赖 `deepagents.middleware._utils.append_to_system_message`。若 0.6.10 移除此私有 API，则内联实现等效函数替换。

### REQ-5：`AIMessage.content` 类型兼容

langchain-core 1.4.x 将 `AIMessage.content` 类型从 `str` 改为 `str | list[str | dict]`。所有直接访问 `.content` 并调用 `.strip()` 的代码必须改用 `get_message_content()` 或做 `str()` 兜底。

### REQ-6：中间件基类兼容

11 个 `AgentMiddleware` 子类必须与 deepagents 0.6.10 的中间件协议兼容。若新增必需抽象方法，则逐个实现。

### REQ-7：`create_deep_agent` 签名兼容

5 个 graph 文件的 `create_deep_agent` 调用必须与 deepagents 0.6.10 的参数签名兼容。

### REQ-8：`CodeInterpreterMiddleware` 兼容

langchain-quickjs 0.2.0 的 `CodeInterpreterMiddleware` 构造函数若变更，5 个 graph 的中间件链需适配。

### REQ-9：checkpoint 兼容

langgraph-checkpoint-postgres 3.1.0 若 schema 不兼容，可清空测试数据库的 checkpoint 数据。

### REQ-10：MCP 适配兼容

langchain-mcp-adapters 0.3.0 若 API 变更，`ensure_mcp_tools()` 需适配。

### REQ-11：requirements.lock.txt 更新

升级完成后更新 `requirements.lock.txt` 反映新的锁定版本。
