## 1. 统一配置与数据库专属表基础设施

- [x] 1.1 在 `.env.example` 与 `.env` 中添加 5 个统一环境变量声明：`MODEL_MAX_CONTEXT_TOKENS=192000`、`MAX_INPUT_CHARS=8000`、`TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS=20000`、`MODEL_CONTEXT_BUDGET_TOKENS=120000`、`SUMMARY_LLM=`
- [x] 1.2 在 `src/common/config/settings.py` 的 `Settings` 类中添加对应的字段与默认值校验
- [x] 1.3 创建 `src/common/storage/artifact_db.py` 模块，定义 `tool_artifact_storage` 表结构及初始化函数 `init_artifact_storage_table()`
- [x] 1.4 在 `artifact_db.py` 中实现异步存储方法 `save_tool_artifact(tool_call_id, tool_name, raw_payload, summary) -> str`（返回唯一 artifact_id）与读取方法 `get_tool_artifact(artifact_id)`，自动自适应 PG 与 SQLite 连接


## 2. 独立 Subagent 总结提炼子代理

- [x] 2.1 创建 `src/common/business_graph/subagent_summarizer.py` 模块
- [x] 2.2 编写 Subagent 专用提示词模板，内嵌“目标实体精准抽取”与“未匹配显式警示”强约束规范
- [x] 2.3 实现异步函数 `summarize_large_tool_output(user_question, tool_name, tool_args, raw_output) -> str`，使用独立单次调用（默认使用 `ModelRegistry.main_llm` 或 `SUMMARY_LLM`）
- [x] 2.4 实现超时与异常安全降级逻辑：若 Subagent 推理异常，自动提取首尾 2500 字符切片 + 降级警示信息，确保绝不抛出异常阻断流程


## 3. 工具执行包装层（Tool Output Guard）

- [x] 3.1 修改 `src/common/business_graph/tool_wrappers.py`，导入 `artifact_db` 与 `subagent_summarizer`
- [x] 3.2 在 `composed_tool_wrapper` 的异常捕获块中，显式排除 `GraphBubbleUp` 异常（`except GraphBubbleUp: raise`），确保 HITL 中断正常向上冒泡
- [x] 3.3 在 `composed_tool_wrapper` 的结果后置处理中接入超限判断：当返回为 `ToolMessage` 且字符数 > `TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS` 时，执行落库 + Subagent 总结流程
- [x] 3.4 将提炼后的文本写回 `ToolMessage.content`，将轻量元数据字典（含 `artifact_id`）写入 `ToolMessage.artifact`
- [x] 3.5 确保对 `Command` 对象与非字符型返回值的兼容性处理


## 4. 图入口输入守卫与模型调用前上下文修剪

- [x] 4.1 创建 `src/common/context_guard.py`，实现 `prepare_model_context(messages, budget_tokens) -> list[AnyMessage]` 纯函数
- [x] 4.2 在 `context_guard.py` 中实现轮次划分算法：确保最后一条 HumanMessage 之后的当前轮次工具消息 100% 豁免，仅修剪超预算的历史旧轮次工具 content，保持 tool_call_id 配对
- [x] 4.3 在 `src/common/business_graph/nodes.py` 的 `classify_intent` 方法中增加输入长度校验：当最新用户提问 > `MAX_INPUT_CHARS` 时，返回直接结束状态
- [x] 4.4 修改 `src/common/business_graph/builder.py`：当输入超长触发拦截时，支持直接路由到 `__end__`，不调用模型
- [x] 4.5 在 `src/common/business_graph/nodes.py` 的 `call_model` 方法中，送入模型前调用 `prepare_model_context` 对输入消息做只读修剪
- [x] 4.6 同步在 `src/data_analysis/nodes.py` 中接入输入超长校验与 `call_model` 上下文修剪


## 5. 测试验证与验收

- [x] 5.1 编写单元测试 `tests/unit_tests/test_artifact_db.py`：测试 `tool_artifact_storage` 表的建表、写入与按 ID 查询
- [x] 5.2 编写单元测试 `tests/unit_tests/test_subagent_summarizer.py`：测试 Subagent 的目标抽取提炼、未匹配警示与降级行为
- [x] 5.3 编写单元测试 `tests/unit_tests/test_context_guard.py`：测试当期轮次豁免原则、历史旧工具占位修剪、tool_call_id 一致性保持
- [x] 5.4 编写端到端集成测试 `tests/integration_tests/test_mcp_500k_guard.py`：模拟参数为空触发 50 万字符超大输出场景，验证：
  - [x] 5.4.1 主图未发生上下文超窗异常
  - [x] 5.4.2 原始 50 万字符成功存入数据库专属表
  - [x] 5.4.3 `ToolMessage.content` 成功被 Subagent 浓缩至安全长度（< 1500 字符）
  - [x] 5.4.4 前面调用的正常工具数据未被误裁，完整保留
  - [x] 5.4.5 Checkpointer 存储的 checkpoint 大小保持轻量
- [x] 5.5 运行 `make lint` 确保代码符合 Ruff 与格式化规范

## 6. 多轮会话大模型摘要压缩 (Conversation Summarization)

- [x] 6.1 在 `.env.example` 与 `src/common/config/settings.py` 中新增 `CONVERSATION_SUMMARY_THRESHOLD_TOKENS=30000` 与 `CONVERSATION_SUMMARY_KEEP_TURNS=3`
- [x] 6.2 在 `src/common/business_graph/state.py` 的 `BusinessGraphState` 中添加 `summary: NotRequired[str]` 状态字段
- [x] 6.3 创建 `src/common/conversation_summarizer.py` 模块，实现提示词、早期消息轮次抽取函数、`summarize_conversation_history` 异步方法及超时安全降级
- [x] 6.4 更新 `src/common/context_guard.py`，支持结合 `summary` 进行早期轮次安全过滤与摘要注入
- [x] 6.5 修改 `src/common/business_graph/nodes.py` 和 `src/data_analysis/nodes.py`，在模型准备与调用前触发会话摘要更新并注入系统上下文
- [x] 6.6 编写单元测试 `tests/unit_tests/test_conversation_summarizer.py`，验证摘要触发逻辑、提示词构建、降级与轮次保留
- [x] 6.7 运行全量单元与集成测试，并通过 `make lint` 格式化规范检查
- [x] 6.8 重启 Apple Container 容器服务与前端，端到端实测验证


