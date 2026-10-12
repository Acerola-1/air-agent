# Air Agent 前后端与上下文治理评估

本文保留实施前的只读评估证据。修复与最新真实联调结果见 [实施验收报告](2026-10-11-langgraph-governance-acceptance.md)；以下环境状态和行号对应初次评估基线。

评估日期：2026-10-11（北京时间）  
代码基线：`2ec4b9f`；重点覆盖 2026-10-10 的 `a122f63` 与 `2ec4b9f`。  
范围：`basic-qa`、`intelligent-analysis`、`data-analysis` 及当前聊天前端。其余三个入口使用 DeepAgents，只确认类型，未评估其内部实现。  
本轮没有修改应用代码、配置、数据库或容器；新增此评估报告。

## 1. 结论

需要优化。当前最需要处理的是流式消息分类、摘要覆盖边界、全量数据抽取和预算兜底。工具包装、原始载荷外置、模型调用前上下文投影的总体方向可继续保留，但目前还不能保证“大数据不漏看、上下文不超窗、内部消息不进入正文”。

当前聊天主链路是 `LangGraph SDK → 自写 Zustand store → ChatPanel`。虽然项目仍安装 assistant-ui 的相关包，聊天页面已经没有使用它的 LangGraph Runtime。生态支持的评价需要落到这条自写消息处理链路上。

“错误 JSON 短暂显示后消失”的机制已在隔离环境复现：工具消息进入 `messages` 流；前端不检查角色和节点，直接追加到回答正文；随后 `values` 快照清空临时正文，把工具结果归入默认折叠的卡片。内部摘要模型的输出也会沿相同路径进入正文。

生产运行验证未完成：本次只读检查中，前端 3000 端口返回登录重定向，浏览器显示登录页；后端 2024 端口不可达，Apple Container 列表请求返回服务连接错误。不能据此确认真实会话、真实 MCP Server、运行中镜像与生产 PG 的行为。以下“已复现”均指本地隔离样本，未调用真实业务 MCP 或推理模型。

## 2. 当前自动压缩到底做了什么

| 层次 | 当前实现 | 前端表现 |
|---|---|---|
| 单工具输出精简 | 超过默认 20,000 字符，先保存原始载荷，再单次调用摘要模型，替换 ToolMessage.content，artifact 中保留引用 | 等待期间普通生成占位；内部摘要可能进入临时正文；节点结束后工具卡显示摘要，并在存在 artifact_id 时显示落库徽标 |
| 会话历史摘要 | 数据查询路径在 prepare_model 中检查早期历史，默认超过约 30,000 tokens 时生成 summary，保留最近 3 轮（包含当前正在进行的轮次）作为模型输入 | 没有“正在压缩／压缩完成／压缩失败”专门状态；内部摘要可能短暂显示；聊天历史仍保留 |
| 模型调用前只读修剪 | prepare_model_context 对消息副本做投影，超默认 120,000 预算时优先替换历史工具载荷 | 没有修剪事件；旧工具原文仍在聊天状态中；后续 usage 有时变小，但无法解释变化原因 |

**实际代码没有 RemoveMessage，也没有移除 checkpoint 中的历史 messages。** 当前是“完整历史仍保留，模型只读取投影后的消息”，与仓库设计文档一致。这种选择有助于保留聊天记录，但状态和快照仍会增长；不能把它当成 checkpoint 已经变小。

官方文档同时提供了 summary + RemoveMessage 和独立 summarized_messages / RunningSummary 的用法。选哪一种取决于历史展示与存储需求；需要先明确聊天历史和模型工作上下文的边界。[LangGraph 官方 Memory 文档](https://docs.langchain.com/oss/python/langgraph/add-memory#summarize-messages)

如果以后直接从 state.messages 删除旧消息，当前前端会在下一个 values 快照时用剩余消息覆盖整个聊天列表；重新打开线程也只读取当前 state.messages。必须先接好独立聊天历史来源，否则旧气泡会消失。RemoveMessage 修改当前图状态，也不等于物理清除数据库里所有历史 checkpoint。

## 3. 已验证的问题与优先级

P1：直接影响答案正确性、上下文可用性或正文展示，应优先修复。P2：可靠性、监控与恢复问题，应在持续使用前补齐。

### F1 / P1：消息增量不分类，工具错误和内部摘要进入正文

位置：[chat-store.ts:199](/Users/acerola/Dev/Python/air_agent/frontend/lib/chat-store.ts:199)、[langgraph-api.ts:106](/Users/acerola/Dev/Python/air_agent/frontend/lib/langgraph-api.ts:106)、[ChatPanel.tsx:283](/Users/acerola/Dev/Python/air_agent/frontend/components/chat/ChatPanel.tsx:283)。

onMessagesDelta 解构时只保留 msg，丢弃 metadata，也没有判断 type。只要 content 能提取文本，就追加 streamingText。onValues 随后把 streamingText 清空，并用 parseMessages 重建完整消息列表；ToolMessage 被归入工具卡，卡片默认折叠。

隔离验证：输入一条 `type=tool` 的 400 JSON 后，streamingText 等于完整 JSON；输入下一次 values 后，streamingText 变为空，错误留在工具卡。真实本地 LangGraph ToolNode 的 messages 流也确实发出了该工具 JSON；内部摘要模型在 prepare_model 下发出 AI 消息，前端同样接纳其正文。

建议：按 role/type、langgraph_node、用途标签和消息 ID 路由；工具消息进入工具状态，主回答才进入正文。摘要／抽取模型调用增加 `nostream` 标记，前端再做来源过滤。不要依靠下一次快照“擦掉”不该显示的内容。[LangGraph 官方 Streaming 文档](https://docs.langchain.com/oss/python/langgraph/streaming#filter-by-node)

### B1 / P1：只读前 120,000 字符，目标在尾部时摘要无法知道它存在

位置：[subagent_summarizer.py:41](/Users/acerola/Dev/Python/air_agent/src/common/business_graph/subagent_summarizer.py:41)、[test_mcp_500k_guard.py:52](/Users/acerola/Dev/Python/air_agent/tests/integration_tests/test_mcp_500k_guard.py:52)。

raw_output 被截成前 120,000 字符。原始数据完整落库，只能证明“保存完整”，不能证明摘要模型“读取完整”。目标实体、峰值、排名或特定时间点在后半段时，模型可能错误报告目标不存在或遗漏关键指标。

隔离验证：500,021 字符原始载荷包含尾部唯一目标，摘要提示词不含该目标。现有 50 万字符集成测试把海淀区放在尾部，却用 AsyncMock 直接返回“找到海淀区”的答案；测试验证了存储与替换，没有验证提示词覆盖或抽取正确性。

建议：结构化 JSON 先进行全量实体／时间过滤或精确聚合；无法确定性处理时分块遍历并保留覆盖证据。只有完整检查过目标范围才能断言“不存在”。摘要失败应明确表示信息不足，不能用首尾样本替代全量结论。

### B2 / P1：120K 是软预算，当前轮次仍可超过配置窗口

位置：[context_guard.py:33](/Users/acerola/Dev/Python/air_agent/src/common/context_guard.py:33)、[context_guard.py:111](/Users/acerola/Dev/Python/air_agent/src/common/context_guard.py:111)、[nodes.py:313](/Users/acerola/Dev/Python/air_agent/src/common/business_graph/nodes.py:313)、[settings.py:127](/Users/acerola/Dev/Python/air_agent/src/common/config/settings.py:127)。

修剪只处理历史工具载荷；当前轮次全部豁免，修剪完成也没有超预算兜底。估算没有计入 tool_calls.args、模型绑定的工具 schema，系统提示词也在修剪之后才追加。MODEL_MAX_CONTEXT_TOKENS 目前仅声明，没有参与硬边界校验。

隔离验证：20 个工具结果各 19,900 字符，均低于单工具精简阈值；当前轮模型输入估算为 218,902 tokens，仍全部放行，超过配置的 192,000 窗口。这个结果证明保护算法没有封顶，不是对真实 HY3 tokenizer 的测量。

建议：同时控制单工具、单轮累计和完整模型请求预算；包含系统提示、工具定义和调用参数，预留生成／推理额度。当前轮超限时通过结构化抽取、分批消费或清晰的数据不足结果处理，最终调用前再次校验。不要通过无限放行来保护当前轮数据。

### B3 / P1：知识／闲聊路径跳过摘要更新，却继续按最近 3 轮裁历史

位置：[nodes.py:230](/Users/acerola/Dev/Python/air_agent/src/common/business_graph/nodes.py:230)、[context_guard.py:75](/Users/acerola/Dev/Python/air_agent/src/common/context_guard.py:75)。

chitchat 和 knowledge 在 prepare_model 中提前返回，不执行后面的摘要更新。只要 state 已有 summary，prepare_model_context 就按当前的最近 3 轮重新切分。连续几轮知识／闲聊后，刚移出最近 3 轮的内容可能从未写入旧 summary，却已经被模型输入排除。

隔离验证：旧摘要只覆盖第 1 轮，第 2 轮有一个唯一决策；进入第 5 轮 knowledge 路径时摘要调用数为 0，该决策仍在原始聊天历史中，却不在模型上下文中。

建议：所有意图路径共享摘要管理，记录摘要覆盖到的消息 ID／轮次，只排除已经成功归入摘要的消息。摘要失败时保留未覆盖内容或采取有明确语义的恢复策略。

### B4 / P2：ToolNode 的错误处理先于包装器，MCP 重试和统一降级被绕过

位置：[builder.py:121](/Users/acerola/Dev/Python/air_agent/src/common/business_graph/builder.py:121)、[tool_wrappers.py:175](/Users/acerola/Dev/Python/air_agent/src/common/business_graph/tool_wrappers.py:175)。data-analysis 的 ToolNode 使用相同配置。

handle_tool_errors=True 会在 handler 内部把工具异常变成 status=error 的 ToolMessage。包装器等待 handler 时没有收到异常，因此不会执行 except 中的 MCP 重试与统一 503 降级，原始异常描述仍可能传出。

隔离验证：登记为 MCP 的工具抛 RuntimeError，真实 ToolNode + 包装器只调用一次，并返回带原始异常描述的默认错误消息。直接 mock handler 抛异常的重试单元测试没有覆盖这层真实组合。

另一个错误类型是“传输成功但业务 JSON 返回 code=400 / success=false”：当前没有对应的结果归一化，隔离样本的 ToolMessage.status 为 success，前端显示 completed。

建议：明确由哪一层处理异常与重试；仅对可重试的连接／服务故障重试，参数校验错误交给模型纠正。统一业务失败状态与可读错误说明，保留原始响应用于诊断详情。

### F2 / P2：上下文仪表不等于当前模型上下文，也不知道压缩发生过

位置：[chat-store.ts:455](/Users/acerola/Dev/Python/air_agent/frontend/lib/chat-store.ts:455)、[ContextMeter.tsx:14](/Users/acerola/Dev/Python/air_agent/frontend/components/chat/ContextMeter.tsx:14)、[langgraph-api.ts:111](/Users/acerola/Dev/Python/air_agent/frontend/lib/langgraph-api.ts:111)。

calculateTotalTokens 优先返回最后一条 assistant 的 totalTokens。这是某次推理的输入加输出用量，不是后续调用的工作上下文；历史 usage 存在时，新用户输入和刚返回的工具数据没有被加进去。没有 usage 时，兜底估算又使用全部可见历史，而后端可能只读摘要和最近 3 轮。

隔离验证：最近 usage=120 后再添加 8,000 字符用户输入，仪表计算仍是 120。组件把窗口 192K、安全线 120K 和模型 HY3 写死，没有同步环境配置。API 层只向 store 传 values.messages，summary 或后续新增的压缩统计均被忽略，也没有订阅 custom 压缩事件。

建议：后端提供“本次有效输入估算／实际输入、窗口与预算、估算来源、压缩状态、保留轮次、压缩前后用量”。最近推理的输入／输出消耗与上下文容量分开显示。压缩成功后不要继续显示未经核实的“历史数据完好保留／容量充裕”。

### F3 / P2：data-analysis 原始回答与清洗回答被当作两条终答

位置：[data_analysis/nodes.py:392](/Users/acerola/Dev/Python/air_agent/src/data_analysis/nodes.py:392)、[data_analysis/nodes.py:496](/Users/acerola/Dev/Python/air_agent/src/data_analysis/nodes.py:496)、[chat-store.ts:379](/Users/acerola/Dev/Python/air_agent/frontend/lib/chat-store.ts:379)。

call_model 先追加原始 AIMessage，finalize_output 再追加新的清洗 AIMessage；前端只在上一条 assistant 正文为空时合并。正常非空原稿与终稿会都留下，原稿中的工具过程／错误也可能先展示。前端未消费 final_output_delta/done 的 custom 契约。

隔离验证：同一轮非空原稿加非空终稿被 parseMessages 解析成两条助手正文。

建议：该图明确唯一可见答案通道，使用稳定 ID 更新或独立 final_answer 字段；渲染层过滤原稿。工具／摘要治理与最终正文契约应统一。

### F4 / P2：SSE error 事件被当成正常结束

位置：[langgraph-api.ts:109](/Users/acerola/Dev/Python/air_agent/frontend/lib/langgraph-api.ts:109)。

消费循环只处理 values 和 messages。当前 SDK 的 runs.stream 转发 SSE 事件；图运行失败可通过 event=error 发送，不一定抛出 HTTP 或网络异常。该事件被跳过后，onDone 无错误完成。

隔离验证：传入合法的 SSE error 事件，最终 onDone 接收到的 error 为空。

建议：显式处理运行 error、interrupt、完成和断流等状态；保留用户输入与已确认的内容，给出可重试动作。停止按钮目前只 abort 浏览器请求，未管理 run_id 或调用取消接口；是否真正停止后台工作还需要运行中 API 验证，不能凭按钮状态判断。

### F5 / P2：旧 run 的完成回调会修改新会话状态

位置：[chat-store.ts:235](/Users/acerola/Dev/Python/air_agent/frontend/lib/chat-store.ts:235)。

切换会话／新建会话时取消旧流，但回调没有 thread_id / run_id / generation 检查。旧请求随后执行 onDone，会把全局 abortRef 清空并设置 isStreaming=false，覆盖刚启动的新请求状态。

隔离验证：新会话正在生成时执行旧流 onDone，isStreaming 从 true 变为 false。

建议：每次 run 使用独立身份；只允许当前活动 run 更新当前会话状态。异步历史加载与线程列表加载也应采用同样的过期请求保护。

### B5 / P2：摘要不是增量滚动，失败降级会丢掉关键结论

位置：[conversation_summarizer.py:179](/Users/acerola/Dev/Python/air_agent/src/common/conversation_summarizer.py:179)、[conversation_summarizer.py:148](/Users/acerola/Dev/Python/air_agent/src/common/conversation_summarizer.py:148)。

原始历史始终留在 state，should_summarize 统计全部早期历史。一旦越过阈值，后续数据查询轮通常会再次对整段早期历史加已有摘要总结，重复消耗，摘要调用自身输入也没有预算保护或覆盖水位。

失败降级只保留前 5 个用户问题的各前 50 字，不保留助手业务结论，却仍返回非空摘要供上下文裁剪使用。摘要的 800 字符、工具摘要的 1500 字符限制仅写在提示词里，没有输出校验；SUMMARY_LLM 配置也未接入模型选择。

建议：增量摘要记录覆盖范围；摘要失败不推进覆盖水位；设置独立输入与输出预算，并验证必要事实、实体、时间、单位和引用是否保留。SUMMARY_LLM 要真正生效或从配置说明中去掉。

### B6 / P2：存储失败仍给出成功归档语义，回读链路没有接入

位置：[tool_wrappers.py:208](/Users/acerola/Dev/Python/air_agent/src/common/business_graph/tool_wrappers.py:208)、[artifact_db.py:143](/Users/acerola/Dev/Python/air_agent/src/common/storage/artifact_db.py:143)、[ToolStepsBlock.tsx:99](/Users/acerola/Dev/Python/air_agent/frontend/components/chat/ToolStepsBlock.tsx:99)。

写库异常后包装器设置 artifact_id="storage_failed"，仍替换原始 ToolMessage；前端只检查 ID 是否存在，会显示落库徽标。摘要失败文本也无条件声称“原始数据已完整落库保存”。

PG 已成功初始化后若某次写入失败，SQLite 回退不保证备用表存在。隔离模拟“已初始化 PG + 写入时 PG 不可用”，回退插入抛 no such table: tool_artifact_storage。此时包装器继续摘要，原始载荷可能没有任何可靠副本。容器配置没有挂载该 SQLite 兜底文件位置，不能把它当作稳定的生产归档。

save_tool_artifact 调用发生在生成摘要前，未写回摘要列；get_tool_artifact 目前只有函数和导出，没有被业务图工具或前端 API 使用。模型没有读取归档的工具，界面也只有徽标，没有查看原始归档的能力。

建议：存储结果返回真实后端与成功／失败状态，失败时不作成功声明；生产 PG 和开发 SQLite 的策略分开。补全摘要与原始记录关联、thread/run/user 归属，以及按需读取／分页接口和访问检查。删除聊天时也需要明确关联归档的保留策略。

## 4. 推荐的前端压缩展示

保留现有工具折叠卡和轻量上下文胶囊即可，先完善状态与语义。

| 状态 | 用户看到的文字 | 必须满足的事实 |
|---|---|---|
| 会话摘要进行中 | 正在整理较早的对话… | 正在运行摘要调用，正文不显示摘要草稿 |
| 会话摘要成功 | 已整理较早的对话，保留最近 3 轮 | 显示实际保留轮次；模型继续处理；完整聊天记录能从历史来源读取 |
| 摘要失败 | 对话整理未完成，正在保留已有信息继续处理 | 后端确实没有把未覆盖消息当成已摘要消息删除或过滤 |
| 工具精简中 | 正在整理查询结果… | 精简阶段有事件与 tool_call_id 对应 |
| 工具已归档 | 查询结果已保存，可查看完整数据 | 原始载荷实际保存成功，且确实有可访问的回读入口 |
| 工具业务错误 | 查询参数需要调整／暂未获取到数据 | 区分参数校验、临时服务故障与数据为空；原始 JSON 放在可展开的诊断详情 |

压缩事件可以显示为一条轻量的状态记录，完成后收起。上下文仪表显示当前模型工作上下文；会话累计 token 消耗另列。用户不需要知道内部节点、数据库表或参数名才能判断是否可以继续提问。

## 5. 实施顺序与验收

1. 修复流式路由：角色、节点、用途、消息 ID；内部模型不对正文流；处理 SSE error 与旧 run 回调；修复 data-analysis 唯一终答。
2. 修复答案正确性：全量目标提取／分块覆盖，摘要覆盖边界跨意图生效；失败不伪造“已提炼／已归档”。
3. 建立完整请求预算：计入系统提示、工具定义、参数、最新数据及生成预留；摘要调用自身也受控。
4. 补全归档和聊天历史：可靠保存、可读回、所有权与生命周期；决定使用历史保留投影还是精简工作 state，再同步前端。
5. 接上真实压缩状态与容量统计，再处理长会话滚动和可访问性。

验收至少包含以下样本：

- 400 业务校验 JSON、工具抛异常、SSE error：任何一帧都不把原始诊断当终答；工具状态准确。
- 会话摘要、超大工具提炼：内部模型内容不进入正文；开始／完成／失败状态可辨识。
- 50 万字符且目标只在尾部：目标指标来自完整范围，记录检查覆盖率；目标真的不存在时才报告缺失。
- 多个略低于单工具阈值的输出：累计预算触发保护，完整请求不超过配置边界。
- 数据查询触发摘要后连续知识／闲聊：尚未归入摘要的事实能继续被回答使用。
- 摘要超时、PG 写入故障：事实和原始载荷不会被误标为已经安全归档。
- 中止后立刻切换会话并重新发送：旧回调不能结束新生成；后台运行是否取消可由 run 状态验证。
- data-analysis 一轮只展示一个终答；重新打开历史线程结果一致。
- 真正移除工作 state 历史后的刷新与重开：完整聊天记录仍能读取，容量显示与模型输入一致。
- 长内容、主动向上滚动、键盘展开工具详情和窄屏：滚动不抢夺阅读位置，控件可操作。

## 6. 本轮验证与覆盖

[隔离验证结果与检查记录](/Users/acerola/Dev/Python/air_agent/docs/reviews/2026-10-11-langgraph-assessment-evidence.json)记录了实际观测值；其中请求、工具、模型结果均为合成样本。

- 有针对性的现有测试：27 项通过（context_guard、conversation_summarizer、subagent_summarizer、artifact_db、tool_wrapper_retry、data_analysis_output_guard、模拟 50 万字符集成链路）。其中归档测试使用临时 SQLite，模型结果使用模拟；不证明生产 PG 或真实模型抽取保真。
- 前端 TypeScript 检查通过。
- 8 个核心后端文件的 Ruff 检查通过。
- 新增临时隔离探针，执行真实本地 LangGraph ToolNode／messages 回调和当前前端 store／API 处理函数，验证上述消息污染、预算、摘要边界、重试、SSE 错误、会话竞态与存储回退路径；没有修改业务测试文件。
- test_business_graph_nodes.py 无法收集：仍导入已不存在的 common.business_graph.output_cleanup；同时引用已不存在的 _route_after_permission。未宣称整个单元测试集通过。
- UI 静态审计另发现登录表单未声明自有校验机制；聊天工具子项使用仅可点击的 div，token 到达时强制平滑滚到底部。这些是后续可用性项，尚未完成登录后浏览器验收。
- 未运行生产构建、全仓库测试或真实端到端业务调用。
- OCR 对两个最近提交预览出 25 个可审阅文件：22 个已审阅，3 个跳过，覆盖率 88%。跳过的是 .agents/skills/.openspec-target（工具配置指针）、frontend/next-env.d.ts（生成类型声明）、OpenSpec .openspec.yaml（schema 元数据），不属于运行逻辑。被默认过滤的相关测试和治理规格另外读取检查。

现有通过测试与新增隔离验证的价值不同：前者证明实现的已有正常路径，后者揭示尚未被回归测试约束的组合边界。优先把这些失败机制补成有实际消息流、真实 ToolNode 组合和输入覆盖断言的测试，再接受修复。
