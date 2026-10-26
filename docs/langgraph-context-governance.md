# LangGraph 上下文与资料治理运行说明

适用 `basic-qa`、`data-analysis` 与 `intelligent-tracing`。前两个显式节点图使用统一准备入口；溯源图保留 DeepAgents 自身摘要实现，并绑定各模型的触发水位、有效输入边界和近期原文预算。

## 模型容量配置

`.env.example` 是配置形状的来源。主模型名称、地址、密钥沿用 `MAIN_LLM_*`；摘要模型可通过 `SUMMARY_LLM`、`SUMMARY_LLM_BASE_URL`、`SUMMARY_LLM_API_KEY` 独立选择，留空复用主通道。

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `MODEL_MAX_CONTEXT_TOKENS` | 192000 | 主模型实际上下文窗口 |
| `CONVERSATION_SUMMARY_TRIGGER_RATIO` | 0.8 | 常规摘要水位 = 窗口 × 比例 |
| `MODEL_PROVIDER_INPUT_LIMIT_TOKENS` | 0（未声明） | 明确的服务商输入上限，可更紧 |
| `MAIN_LLM_MAX_OUTPUT_TOKENS` | 8192 | 主模型生成预留 |
| `MODEL_CONTEXT_SAFETY_TOKENS` | 4096 | 主模型安全余量 |
| `SUMMARY_MODEL_CONTEXT_TOKENS` | 192000 | 摘要模型实际窗口 |
| `SUMMARY_MODEL_OUTPUT_TOKENS` | 4096 | 每次摘要输出预算 |
| `SUMMARY_MODEL_SAFETY_TOKENS` | 2048 | 摘要安全余量 |
| `SUMMARY_MODEL_PROVIDER_INPUT_LIMIT_TOKENS` | 0（未声明） | 摘要通道服务商输入上限 |
| `CONVERSATION_SUMMARY_KEEP_TURNS` | 3 | 默认保留的完整最近轮次，容量不足可继续收缩 |
| `SUMMARY_MAX_BATCHES` | 256 | 完整文本分批上限 |
| `MAX_INPUT_CHARS` | 8000 | 单次用户文本上限（Unicode 码点，独立于 token 窗口） |

主模型常规水位为 `floor(窗口 × 比例)`；192000 对应 153600，1000000 对应 800000。有效输入边界为 `min(窗口 - 生成预留 - 安全余量, 服务商输入上限)`，192000 窗口默认是 179712。达到水位触发常规整理；超过有效边界时先做容量治理。旧的固定 120000 应用上限和 30000 早期历史阈值已停用；环境里仍保留这两个旧键时只在启动日志报告键名，不读取其值，也不影响工具 schema 配额或页面分母。

请求计量包含系统提示、消息正文、工具调用参数和工具定义。工具说明按当前预算逐级精简，参数类型、必填项、枚举和校验约束保持完整；能精简时以 `MODEL_TOOL_SCHEMA_BUDGET_PERCENT=40` 为工具定义的目标占比，给后续查询结果留出空间。该比例是精简目标，不能为达标删掉参数约束；完整请求仍超限时返回明确说明。页面提示分别显示系统规则、聊天与工具结果、工具定义和请求开销。有本次提供商 usage 时仪表优先显示实际主模型输入；准备阶段显示带 ≈ 的估算。

更换模型时只需更新 `MODEL_MAX_CONTEXT_TOKENS` 与 `CONVERSATION_SUMMARY_TRIGGER_RATIO`；输出预留、安全余量和明确的服务商上限仍按真实通道填写。服务不会根据模型名称猜测远端窗口。已知分词模型采用对应 tokenizer，也可分别通过 `MODEL_TOKENIZER_ENCODING`／`SUMMARY_TOKENIZER_ENCODING` 指定匹配的 tiktoken 编码。未知别名采用无需下载词表的多语言估算：ASCII 约三字符一个 token、中日韩约每字符 1.5 token，其他 Unicode 保留字节估算，并乘以 `MODEL_TOKEN_ESTIMATE_MARGIN`（默认 1.25）。这是带余量的近似值，不是严格上界；界面用 `≈` 标明，并将提供商实际 usage 单独显示。不能将 UTF-8 字节数直接当 token 数用于拒绝普通查询。

完整非结构化文本会按摘要模型的可用输入动态分批并分层合并，字符范围连续覆盖原文。摘要通道按自己的窗口和预留分块，不能用主窗口放大摘要请求后再声称完整处理；任意批次失败不推进摘要水位，也不返回伪完整的前缀摘要。

## 历史与页面

checkpoint 保留完整聊天／工具调用记录。成功摘要保存覆盖水位；模型调用时投影为内部摘要及未覆盖轮次。默认保留最近三轮；容量不足时按完整轮次继续收缩到两轮或一轮，当前用户问题和工具调用／结果配对保持完整。摘要失败、无效水位和旧线程不会造成静默丢历史。当前实现采用只读投影，不删除用于页面回看的历史消息。

每次业务模型发送前都会重新构造并计量完整请求，包括工具循环回来的请求、规划补充、最终回答和最终清洗。服务商明确报上下文超限时，恢复从本次已成功整理的摘要与覆盖位置继续，仅对输入确实减小的请求重发一次；不重放已完成工具、整图、用户受理或已发布正文。恢复耗尽不会重新进入通用重试，成功整理的状态仍由节点持久化。

页面显示历史整理／资料处理的进行中、完成、失败状态。历史整理完成显示输入前后变化和实际保留轮次；内部摘要没有展示或编辑入口。容量显示最近准备的模型输入，刷新读取后端保存的指标。工具 JSON 与内部调用草稿不会进入回答正文，诊断由用户主动展开。终答通过现有流式发布协议输出，已发布部分正文时不自动重放。

停止会发起服务端 interrupt 并取消当前连接；过期回调受运行代次隔离。用户上翻时停止自动跟随。会话回读显示加载状态，失败有重新读取入口；发送失败保留重新发送入口。

## 归档和只读计算

超长工具结果先完整写入 PG，提交成功才返回引用。结构化 JSON 返回字段／数组路径／全量记录数与明确标记的导航样本；样本不能作为统计依据。`compute_artifact` 只读取当前授权用户、线程的原始资料，运行同步 JavaScript 函数体，必须完整扫描问题范围并返回结果及 `scope`。原文与派生结果关联保存，回读每段最多 8000 字符。

| 配置 | 默认值 | 说明 |
|---|---:|---|
| `TOOL_OUTPUT_SUMMARY_THRESHOLD_CHARS` | 20000 | 工具返回超过此长度启用治理 |
| `DATA_PROCESSING_TIMEOUT_SECONDS` | 90 | 单份资料的生成、修正、执行阶段总时限 |
| `QUICKJS_TIMEOUT_SECONDS` | 5 | JavaScript 执行时限 |
| `QUICKJS_MEMORY_MIB` | 64 | QuickJS heap 配额 |
| `QUICKJS_MAX_INPUT_BYTES` | 16777216 | 单次完整输入上限 |
| `QUICKJS_MAX_RESULT_BYTES` | 131072 | 单次计算结果上限 |
| `QUICKJS_MAX_CORRECTIONS` | 1 | 首次执行后最多一次代码修正 |
| `TOOL_ARTIFACT_RETENTION_DAYS` | 30 | 原始和派生载荷保留期，0 表示不自动到期 |
| `FINAL_OUTPUT_CLEANUP_TIMEOUT_SECONDS` | 45 | 数据分析终答清洗时限 |

每次计算使用独立子进程中的 QuickJS runtime，不注入网络、文件、shell、包加载或 Python 回调；取消和超时回收进程，不跨轮保存 heap。记录覆盖计数由宿主生成。代码仍可能产生业务逻辑错误，覆盖计数不等于任意脚本的结果已经数学证明；固定污染等级函数及验收样本另有明确测试。

当前固定规则仅实现仓库给定的已知边界。未提供的新版 PM 边界、PM2.5 日均边界以及 SO2 小时值恰好 800 的重叠定义会明确拒绝计算，不自行补造规则。补充经确认的业务规则后可扩展固定函数。

PG 配置存在但不可用时明确失败，禁止静默切换 SQLite。SQLite 仅用于未配置 PG 的隔离开发测试。旧归档没有可信归属时不开放读取；到期会清空载荷并返回 410。聊天、摘要及调用记录保持到手动删除线程；通过 Web 删除线程会先清理关联原始／派生资料与计算配额。已经到期的数据不能通过聊天引用恢复。

## 本地运行与验证

当前环境以 [正式运行清单](CURRENT-RUNTIME.md) 为准。唯一启动／状态入口为 `.venv/bin/python deployment/run_separated_local.py start/status`，公开图以 `langgraph.json` 为准。身份只来自独立Java IAM，失败关闭；不使用历史本地账号、noop鉴权、旧网桥地址或全量compose重建环境。

针对性Python验证见 `tests/unit_tests/test_{context_guard,conversation_summarizer,context_recovery,compaction_boundaries,model_capacity,input_limits,turn_tool_governance}.py`，以及相关业务节点和溯源测试。前端运行 `npm --prefix frontend run test:contracts`、TypeScript检查与生产构建。

PostgreSQL检查点验证入口为 `tests/integration_tests/test_window_compaction_checkpoint.py`，仅接受显式配置的自建临时库，不读取正式DSN。其他真实模型／浏览器联调同样使用自有资源，结束后回收；不保留测试聊天或复用退役验收环境。

当前修复结果、复跑命令和未完成验收见 [验收记录](testing/model-window-compaction/acceptance.md)。
