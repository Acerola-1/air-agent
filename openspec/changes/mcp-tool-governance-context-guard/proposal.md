## Why

当前系统通过 MCP 调用外部公共平台提供的业务工具（几十至上百个工具，参数众多）。部分 MCP Server 基于旧版 Spring AI 实现，工具参数 JSON Schema 约束不够严格。

在生产运行中出现严重可靠性问题：当模型在参数生成中遗漏或将过滤参数（如 `region`）错误设置为空时，MCP Server 将其解释为查询全国全量数据并返回超过 50 万字符的大型 JSON。这些失控的原始数据原样写入 LangGraph State 并全量注入模型上下文，导致单轮推理 Token 异常消耗、模型上下文窗口（Context Window）直接溢出崩溃。

同时，当前系统在多轮对话管理中缺乏会话上下文控制与压缩机制（`call_model` 裸送全部历史消息），导致连续多轮对话的工具原始数据持续累积。直接简单粗暴截断输出又会带来“丢失关键业务数据”的严重风险。

因此，亟需在 LangGraph 工具消费层与上下文管理层建立通用的治理与工程机制：既保障海量数据“零截断丢失”，又将主模型上下文锁死在安全预算以内。

## What Changes

1. **统一环境变量规范**：
   - 在 `.env` / `settings.py` 中显式定义：`MODEL_MAX_CONTEXT_TOKENS=192000`（对齐当前 HY3 模型的 192K 窗口）、`MAX_INPUT_CHARS=8000`（用户单次输入安全上限）、`TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS=20000`（触发 Subagent 总结的单工具输出阈值）、`MODEL_CONTEXT_BUDGET_TOKENS=120000`（多轮模型推理只读安全预算）及可选的 `SUMMARY_LLM`。
2. **图入口输入守卫（Input Guard）**：
   - 在图入口节点检测用户输入，若单次提问超过 `MAX_INPUT_CHARS`，直接优雅生成分段提问指引并路由至 `END`，不调用模型、不浪费 Token。
3. **工具执行层透明 Subagent 总结（Tool Output Subagent Offload）**：
   - 在公共工具包装层（`composed_tool_wrapper`）中挂载输出守卫：
     - 若工具输出 ≤ 20,000 字符，原样放行；
     - 若工具输出 > 20,000 字符（如 50 万字符），先将原始数据持久化至数据库专属表，然后透明调度拥有独立 192K 空间的 Subagent 总结子代理；
     - Subagent 结合用户当前提问执行“精准目标抽取 + 缺失显式警示”提炼；
     - 将提炼后的高浓度结论（约 1k 字符）赋给 `ToolMessage.content`，主图无感、主上下文轻量，数据 100% 被消化。
4. **数据库专属表（`tool_artifact_storage`）持久化原始数据**：
   - 建立专用数据表保存超大原始数据载荷，自适应支持生产 Postgres 与本地 SQLite，`ToolMessage.artifact` 仅存储主键引用，避免 Checkpointer 数据库膨胀。
5. **模型推理前只读上下文修剪（Model Context Guard）**：
   - 在 `call_model` 节点将消息发送给 LLM 之前执行纯函数只读修剪：
     - 当期轮次的新工具数据享有“绝对豁免权”，100% 完整保留；
     - 仅当历史多轮累积超过安全预算时，将历史已完成轮次的旧工具原始 JSON 替换为占位符，清空对应历史 `AIMessage.tool_calls` 参数，保证 tool_call_id 协议闭合且 Token 预算可控。
6. **多轮纯文本大模型会话摘要压缩（Conversation Summarization）**：
   - 在多轮历史累积达到阈值时（待压缩早期历史 Token > 30,000），自动触发大模型会话摘要机制；
   - 保留最近 3 轮活跃对话不压缩，将其余更早轮次使用大模型提炼为滚雪球全局摘要存入 `state["summary"]`，模型推理前作为系统背景注入并替换早期原始纯文本，从根本上防止极端超长纯对话撑爆 192K。

## Capabilities

### New Capabilities

- `tool-output-subagent-guard`: 在 ToolNode 拦截层实现超大输出自动数据库落库与透明 Subagent 智能总结提炼，不丢失数据且不撑爆主上下文。
- `model-context-guard`: 在模型调用前对多轮对话消息进行只读修剪，保护当前轮次关键数据，压缩历史旧工具载荷。
- `conversation-summarizer`: 在多轮长会话中利用大模型对早期问答进行渐进式摘要压缩，保留高层意图并大幅降低活跃上下文 Token。
- `tool-artifact-db-storage`: 专用的超大工具结果数据库存储机制，兼容 PG 与 SQLite，支持轻量引用与数据回溯审计。

### Modified Capabilities

- （无已有 spec 变更，这是新引入的通用横切治理能力）

## Impact

- **后端配置**：
  - 更新 `.env.example` 与 `.env`
  - 更新 `src/common/config/settings.py`
- **新建模块**：
  - 新建 `src/common/storage/artifact_db.py`：数据库专属表读写与初始化
  - 新建 `src/common/business_graph/subagent_summarizer.py`：Subagent 智能抽取提炼器
  - 新建 `src/common/context_guard.py`：模型调用前只读上下文修剪器
- **修改模块**：
  - 修改 `src/common/business_graph/tool_wrappers.py`：接入专属表落库与 Subagent 自动总结，透传 `GraphBubbleUp`
  - 修改 `src/common/business_graph/nodes.py`：入口输入长度检查，`call_model` 接入上下文修剪
  - 修改 `src/data_analysis/nodes.py`：入口输入长度检查，`call_model` 接入上下文修剪
- **数据库**：
  - 自动创建 `tool_artifact_storage` 表（生产 PG / 本地 SQLite），无需手动执行 DDL 迁移
- **依赖与框架**：
  - 零依赖变更，不需要升级 LangGraph，不修改 MCP Server 与 Client，不改变现有 StateGraph 拓扑
