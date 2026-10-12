# LangGraph 上下文与资料治理运行说明

适用 `basic-qa`、`intelligent-analysis`、`data-analysis`。三个 DeepAgents 图的内部流程与工具注册保持原有方式。

## 模型容量配置

`.env.example` 是配置形状的来源。主模型名称、地址、密钥沿用 `MAIN_LLM_*`；摘要模型可通过 `SUMMARY_LLM`、`SUMMARY_LLM_BASE_URL`、`SUMMARY_LLM_API_KEY` 独立选择，留空复用主通道。

| 配置 | 默认值 | 含义 |
|---|---:|---|
| `MODEL_MAX_CONTEXT_TOKENS` | 192000 | 主模型实际上下文窗口 |
| `MODEL_CONTEXT_BUDGET_TOKENS` | 120000 | 主模型输入预算上限 |
| `MAIN_LLM_MAX_OUTPUT_TOKENS` | 8192 | 主模型生成预留 |
| `MODEL_CONTEXT_SAFETY_TOKENS` | 4096 | 主模型安全余量 |
| `SUMMARY_MODEL_CONTEXT_TOKENS` | 192000 | 摘要模型实际窗口 |
| `SUMMARY_MODEL_OUTPUT_TOKENS` | 4096 | 每次摘要输出预算 |
| `SUMMARY_MODEL_SAFETY_TOKENS` | 2048 | 摘要安全余量 |
| `CONVERSATION_SUMMARY_THRESHOLD_TOKENS` | 30000 | 尚未覆盖的早期历史触发阈值 |
| `CONVERSATION_SUMMARY_KEEP_TURNS` | 3 | 保留的完整最近轮次，含当前轮 |
| `SUMMARY_MAX_BATCHES` | 256 | 完整文本分批上限 |

主请求可用输入为 `min(输入预算上限, 模型窗口 - 生成预留 - 安全余量)`。请求计量包含系统提示、消息正文、工具调用参数和工具定义。工具说明按当前预算逐级精简，参数类型、必填项、枚举和校验约束保持完整；能精简时以 `MODEL_TOOL_SCHEMA_BUDGET_PERCENT=40` 为工具定义的目标占比，给后续查询结果留出空间。该比例是精简目标，不能为达标删掉参数约束；完整请求仍超限时返回明确说明。页面提示分别显示系统规则、聊天与工具结果、工具定义和请求开销。有本次提供商 usage 时仪表优先显示实际主模型输入；准备阶段显示带 ≈ 的估算。

更换模型时同时更新相应窗口、输出预留与预算配置。服务不会根据模型名称猜测远端窗口。已知分词模型采用对应 tokenizer，也可分别通过 `MODEL_TOKENIZER_ENCODING`／`SUMMARY_TOKENIZER_ENCODING` 指定匹配的 tiktoken 编码。未知别名采用无需下载词表的多语言估算：ASCII 约三字符一个 token、中日韩约每字符 1.5 token，其他 Unicode 保留字节估算，并乘以 `MODEL_TOKEN_ESTIMATE_MARGIN`（默认 1.25）。这是带余量的近似值，不是严格上界；界面用 `≈` 标明，并将提供商实际 usage 单独显示。不能将 UTF-8 字节数直接当 token 数用于拒绝普通查询。

完整非结构化文本会按摘要模型的可用输入动态分批并分层合并，字符范围连续覆盖原文。`120000` 是默认主模型输入预算，已经没有“只读原文前 12 万字符”的处理。提高摘要窗口后批次容量会相应变化；任意批次失败不推进摘要水位，也不返回伪完整的前缀摘要。

## 历史与页面

checkpoint 保留完整聊天／工具调用记录。成功摘要保存覆盖水位；模型调用时投影为内部摘要及未覆盖轮次。摘要失败、无效水位和旧线程不会造成静默丢历史。当前实现采用只读投影，不删除用于页面回看的历史消息。

页面显示历史整理／资料处理的进行中、完成、失败状态。历史整理完成显示输入前后变化和保留轮次；内部摘要没有展示或编辑入口。容量显示最近准备的模型输入，刷新读取后端保存的指标。工具 JSON 与内部调用草稿不会进入回答正文，诊断由用户主动展开。终答确认后一次发布；这会增加首字等待时间，等待期间显示处理状态。

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

1. 同步锁定依赖：`uv sync --all-extras`。新增显式 `psycopg[binary]==3.3.4`，解决宿主验收环境缺少 libpq；已有锁定包版本未升级。
2. 确认后端 `.env` 与 `frontend/.env.local` 的 `AUTH_SECRET` 一致且保密。测试登录凭据由操作者本地输入，不写入计划、文档、测试文件或截图。
3. 使用 Apple Container 与现有 `run-local.sh` 启动；修改 `src/` 后通过 `container-compose up -d --profile prod langgraph-api` 重新加载。保留 PG 命名卷，等待 `/ok` 和六个助手发现接口就绪再验收。
4. 本机模型网关地址应采用当前容器默认网关。网桥可能变化；本次实际地址为 `192.168.65.1`，已修正本地 `.env` 的旧模型地址。示例 `192.168.64.1` 不能当作所有运行的固定地址。
5. 前端代理验证登录和线程归属，并注入签名资料作用域。原有 LangGraph API 使用 `noop`、定位为受信本地后端；本次 Web 授权验证不等于将 2024 端口直接暴露给不受信网络时已具备生产鉴权。

针对性 Python 验证见 `tests/unit_tests/test_{context_guard,conversation_summarizer,artifact_db,subagent_summarizer,business_graph_nodes,bounded_computation}.py` 和 `tests/integration_tests/test_governance_toolnode.py`。前端运行 `npm --prefix frontend run test:contracts` 与 TypeScript 检查；生产构建已通过 `npm --prefix frontend run build -- --webpack`。

真实联调显式运行 `AIR_AGENT_LIVE_TESTS=1 .venv/bin/python -m pytest tests/integration_tests/test_governance_live.py -q`。它只创建验收专用线程，使用本地已有测试用户和签名配置，包含真实模型、真实 MCP 400、PG 全量尾部计算、历史压缩、三个图、归档授权／到期／删除。成功计算和压缩线程留作页面回看；测试输出不保存授权 token。

最新验证结果、截图与完整套件的既有失败见 [实施验收报告](reviews/2026-10-11-langgraph-governance-acceptance.md)。
