# 三图统一用户正文流式输出

## Why

当前三个公开图具备SSE传输，但用户正文被全局隐藏标签、整稿清理/校验和整段完成事件阻断。真实Basic QA测试中36条完成记录的首正文到完成仅148–455ms；这不能作为真正逐步生成正文的证据，需要统一修复应用输出链路而非归因于LangGraph或换模型。

## What Changes

- 为`basic-qa`、`data-analysis`、`intelligent-tracing`建立同一套有身份、顺序和终态的正文增量协议，区分处理中、已校验部分正文、完整终答、停止/失败。
- 采用服务端显式发布安全正文的custom通道；隔离模型规划、工具参数、摘要、代码及DeepAgents子任务草稿。调整隐藏策略的作用域，不通过全局放开原始messages流解决问题。
- Basic QA以最终回答生成阶段替代现有最后一轮整段回答；数据分析将最终整理改成有界片段的生成、校验和即时发送，不先收齐全文再发布。
- DeepAgents保持四专员/冻结请求/claim allowlist/证据与知识引用边界，提议采用“冻结发布计划→逐节校验并发布→整份报告最终确认”；部分正文不等于`ValidatedReport.published`，画布/历史/台账仍只认最终确认的同版本报告。
- 模型兼容性按调用用途处理：工具规划可非流式，最终无工具正文使用真实流式；明确标记不支持流式的受限降级，禁止用sleep切分已完成全文伪造流式。
- 修复前端增量追加、去重、范围切换和部分失败交互；修正测试的首正文/末正文/流结束/台账确认/浏览器可见计时。
- 与旧done-only事件保持可识别兼容；新客户端识别协议版本，旧客户端保留完成时全文回退，不要求整体升级SDK或更换前端框架。

## Capabilities

### New Capabilities

- `agent-visible-output-streaming`：三图统一的安全正文生成、分段发布、客户端消费、终态与分层测量。
- `tracing-validated-section-streaming`：DeepAgents溯源图证据预检、不可变发布计划、逐节发布及最终报告一致性。

### Modified Capabilities

- `final-output-cleanup`：把终答流式输出落实为生成期间即时发布，替换“全部业务图必须经同一个清理中间件”的历史装配要求，并限制失败回退只能使用已验证内容。

## Impact

- 共享运行/输出：`src/common/{models,data_governance,output_contract,runtime_events}.py`、`src/common/identity/langgraph_auth.py`、业务图节点与构建器。
- 数据分析：`src/data_analysis/nodes.py::finalize_output`；溯源：`publishing.py`、`gate.py`、`claims.py`、`schemas.py`、`tools.py`和DeepAgents主/子任务边界。
- 前端：`frontend/lib/{langgraph-api,chat-store}.ts`及正文渲染/计时；BFF保持透传，受理/幂等/撤销不可回退。
- 新正文传输契约及必要副本/测试；这是携带受控正文的SSE协议，不把正文注册成普通业务事件或写入普通日志。
- 与知识引用、溯源集成、既有流式治理及产品性能工具的在飞改动串行集成；不改IAM/MCP/RAG外部仓库，不做权限策略重构、后台SQL优化、全量压测或模型更换。
- 本次只交计划。用户已明确选择DeepAgents“逐段校验后输出，最后确认整份报告；保留证据校验”。实施不得退回整份校验后仅拆片发送并宣称逐段生成完成。
