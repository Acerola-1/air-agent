## Context

项目使用 DeepAgents + LangChain + LangGraph 技术栈，当前锁定版本落后 PyPI 最新版。核心依赖链：

```
deepagents → langchain, langchain-core, langsmith
langchain-quickjs → deepagents, langchain-core, langgraph
langchain-openai → langchain-core
langgraph-checkpoint-postgres → langgraph
langchain-mcp-adapters → langchain-core
```

项目有 11 个自定义 AgentMiddleware 子类、5 个 create_deep_agent 调用、1 个 StateGraph 节点流实现。测试分支 `dev_data_analysis_upgrade` 适合全量升级。

## Goals / Non-Goals

**Goals:**
- 将 9 个核心依赖升级到 PyPI 最新版
- 修复升级后的所有兼容性问题（import 错误、API 签名变更、行为变化）
- 确保全部 6 个 graph 能正常编译启动
- 确保现有测试通过

**Non-Goals:**
- 不做功能新增或重构
- 不修改 Skill 文件或业务逻辑
- 不做性能优化

## Decisions

### 决策 1：全量一次性升级，不做分轮次

**选择**：一次性 `pip install --upgrade` 全部 9 个包
**理由**：测试分支，兼容性问题不可避免，分轮次只会增加安装次数，不如一次升完再统一修
**替代方案**：分 3 轮（低风险→中风险→高风险）— 太保守，测试分支不需要

### 决策 2：兼容性修复策略——先编译通过，再运行验证

**选择**：按「import → 编译 → 单元测试 → 集成测试」顺序逐层修复
**理由**：升级后的兼容性问题 90% 是 import 错误和类型签名变更，编译能快速暴露
**具体流程**：
1. `pip install --upgrade` 升级全部包
2. `python3 -m compileall src/` 全量编译，修复 import 错误
3. `ruff check src/` 修复 lint 问题
4. `make test` 运行单元测试，修复行为变更
5. 手动验证 graph 启动

### 决策 3：`append_to_system_message` 私有 API 处理

**选择**：先检查 0.6.10 是否保留该函数，若保留则不动；若移除则内联实现一个等效的 `append_to_system_message`
**理由**：3 个中间件依赖此函数，内联实现成本极低（函数逻辑就是拼接 system message）
**替代方案**：等 deepagents 官方暴露公共 API — 不可控

### 决策 4：`AIMessage.content` 类型变更处理

**选择**：项目已有 `get_message_content()` 工具函数统一提取 content 为 `str`，升级后全局搜索直接访问 `.content` 的地方，确保都走 `get_message_content()` 或做 `str()` 兜底
**理由**：langchain-core 1.4.x 已将 `content` 类型改为 `str | list[str | dict]`，直接 `.strip()` 会报错

### 决策 5：checkpoint-postgres 升级后 schema 迁移

**选择**：先升级，如果 checkpoint 读取失败则清空测试库的 checkpoint 数据（测试环境，无生产数据）
**理由**：测试分支不保留历史对话状态

## Risks / Trade-offs

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| `deepagents.middleware._utils.append_to_system_message` 被移除 | 3 个中间件 ImportError | 内联实现等效函数 |
| `AgentMiddleware` 新增必需抽象方法 | 11 个子类启动报错 | 逐个实现缺失方法 |
| `create_deep_agent` 参数签名变更 | 5 个 graph 启动失败 | 按新签名适配 |
| `CodeInterpreterMiddleware` 构造函数变更 | 5 个 graph 中间件链断裂 | 按 langchain-quickjs 0.2.0 新 API 适配 |
| `AIMessage.content` 类型变为 list | 直接 `.strip()` 报 TypeError | 统一走 `get_message_content()` |
| `langchain-openai` astream 行为变化 | data_analysis 流式推送异常 | 逐场景验证 |
| checkpoint schema 不兼容 | 已有对话历史丢失 | 测试环境可接受，必要时清空 |
| MCP 工具发现 API 变更 | 工具注册失败 | 按 0.3.0 新 API 适配 `ensure_mcp_tools()` |
