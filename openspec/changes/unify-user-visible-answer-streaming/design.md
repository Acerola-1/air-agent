# 三图正文流式输出设计

## Context

动机见proposal。当前HEAD为7711914；工作区另有性能Agent未提交的sender改动、product_chat_perf工具和201100-e2e4报告，本计划不覆盖它们。执行时重新冻结HEAD/依赖锁/官方镜像及工作区diff。

| 接线 | 已核实的现状 | 本次设计方向 |
|---|---|---|
| 公共入口 | langgraph_auth给运行追加nostream；models对携带tools的调用关闭自动流 | 保留内部Token隔离，以明确的正文发布器释放安全增量，兼容设置按模型调用用途生效 |
| Basic QA | business_graph/call_model调用统一ainvoke封装，附nostream/internal-draft，visible_result只发done | 工具规划与最终回答生成分开；最终回答使用无工具正文流和安全发布器 |
| 数据分析 | finalize_output虽用astream，但先累积full_cleaned，再检查引用政策和发布整段 | 保留菜单/计算边界，整理时有界组段、检查、发布，不再缓存整篇才开始 |
| 智能溯源 | DeepAgents lead+四专员；ReportGate校验整篇，render_report构造完整body；publishing在after_agent发done | 预检发布计划并逐节确定性渲染、校验、发布，最后生成同版本ValidatedReport |
| 前端/BFF | SDK已订阅values/messages-tuple/custom；BFF直接透传；chat-store忽略messages但支持delta/done | 沿用SDK及custom通道，增加版本化输出身份/顺序/中断；无需重写框架 |
| 测量 | sender增量解析，但complete时间在额外confirm后；未区分首帧来源 | 分离正文、流、业务确认与浏览器时钟，并记录首正文来自新协议或legacy回退 |

官方依据（2026-10-21查证）：[LangGraph Streaming](https://docs.langchain.com/oss/python/langgraph/streaming)、[Agent Server Streaming API](https://docs.langchain.com/langsmith/streaming)、[Frontend overview](https://docs.langchain.com/oss/javascript/langchain/frontend/overview)。messages模式可在模型invoke/ainvoke时捕获流；nostream是事件隔离机制，不等同于HTTP流能力。新事件API可用不意味着本次必须升级SDK；沿用当前锁和官方镜像协议。

## Goals / Non-Goals

**Goals:** 用户在长回答/后续章节仍生成时看到首段安全正文；三图复用协议和消费逻辑；保持原生/API/BFF、取消/恢复、引用与终答一致性；DeepAgents采用用户已确认的逐段校验方案。

**Non-Goals:** 不更换模型、不调整业务/MCP人员权限、不新增知识库服务、不优化后台SQL、不自动运行40题/并发R/F或容量测试，不重写IAM/前端或替换全部DeepAgents；不承诺流式一定缩短工具取数等待。

## Decisions

### D1. 一个custom正文通道，内部Token保持隐藏

拒绝直接取消所有nostream或把onMessagesDelta收到的内容全部展示：那会让规划、代码和专员草稿进入正文。共享发布器从明确的最终正文源或已验证章节接收内容，通过get_stream_writer/custom输出。内部模型可继续nostream；显式最终模型astream产生的本地chunk经安全发布器发送，不依赖自动messages回调。原生用户直接订阅messages不能因修复新增内部Token泄漏。

模型调用按`planning`、`answer`、`cleanup`、`subagent`用途区分：工具规划可保留disable_streaming=tool_calling及重试前授权；answer/cleanup不携带工具schema，实际使用可流式的模型接口。先用本机网关验证纯文本/多工具/正文三路径，不盲目改全局开关。网关返回伪SSE/整段缓冲时声明受限，不在前端模拟输出。ainvoke不是天然不流式，修改点以实际标签、SDK和帧证据为准。

### D2. 版本化正文事件与最终确认分层

在`docs/contracts/agent-output-stream-v1/`建立独立SSE契约、样本及Python/TS校验来源；暂定新增字段`output_protocol_version=1`，不重用通用runtime事件的`version`。保留旧事件名和`message`字段，新增start/aborted。事件均由服务端绑定可信tenant/context/graph/thread/run/attempt与稳定output_id/message_id，正文序号单调；不信任浏览器声明的主体。

| 事件 | 关键载荷 | 语义 |
|---|---|---|
| final_output_start | 身份、output_id/message_id、seq=0、delivery_mode | 开始同一输出；mode为token或validated_section，不是模型内部消息 |
| final_output_delta | 身份、seq、message；溯源另带section_id/plan_digest | 本次新增、已可公开的文本；按UTF-8业务文字拼接，不传内部思考 |
| final_output_done | 身份、seq、完整message、content_sha256；可带report_id/revision | 正文完整且校验通过，结束正文流；不是仅凭此就认定台账业务确认 |
| final_output_aborted | 身份、seq、可公开reason_code | 输出中断；停止后不再接受delta/done，不携带原始异常或草稿 |

同一output最多一个终态；done全文等于按序连接的delta，不能默默替换已公开前缀。旧事件无新版本字段走legacy完成回退，不伪造历史delta。正文SSE可携带受控正文，但普通台账事件/日志不得新增正文；只有业务元数据变动才同步原事件登记、各validator和副本，避免误把SSE内容注册为普通统计事件。

done与台账确认独立：页面可以显示完整但确认中的正文，点赞/成本/终答统计继续等待可信answer确认；模型或流结束不自动等于业务成功。所有最终正文快照仍走既有持久化及删除守卫，不从浏览器部分流补造最终答案。

### D3. Basic QA和数据分析的最终生成适配

Basic QA工具循环只负责规划、取数与生成受控回答材料；在已具备材料或达到合理工具终止条件时进入明确的`answer`阶段。用无工具的最终请求生成正文，替换原先最后一轮最终自然语言生成；不得默认“原模型生成完全文，再额外生成一遍”。确需保留原稿时记录新增调用成本并解释。简单直接回答不应强制走两次模型。

data-analysis保留菜单选择、确定性计算和数据边界；finalize_output接入发布器，改为片段产生→政策检查→即时发送，不把astream仅用作全量收集器。短固定失败/澄清通过同一生命周期结束，可单段发送，单独标明。

共同安全缓冲器以完整语句/段落/引用单元为基本检查边界；普通安全文本可小段输出，法规/数值/单位/引用必须作为足够完整的单元交给既有knowledge policy等同校验。跨chunk的think标签、工具数据、引用标记不能漏出半截；缓冲有字节/段落上限和取消检查，超限返回明确失败，不无限堆积。不能通过分段规避整篇限制，也不能先公开未知内容再期望done覆盖。

已输出后中途失败不自动切换另一模型重生成并拼接；未输出时的有界重试/回退仍需逐次授权。若最后检查会改变先前正文，设计必须把相关条件提前到首段前置检查，或只公开局部可独立证明的单元，不能把最终替换当作校验。

### D4. DeepAgents分节发布，保留证据语义

保持冻结请求→station-data/grid-data→meteo-analyst→evidence-reviewer→报告的编排依赖。内部子任务完成时可以发“已完成站点资料分析”等服务端映射进度，但不发其自然语言草稿；首正文只在证据与发布预检之后开始。

将现有`ReportGate.validate_and_render`和`claims.render_report`拆分为三项职责：

1. **预检与冻结**：校验整个ReportPlan、allowlist、证据ID/修订/产品语义、知识引用和必要限制/反证；冻结有摘要的发布计划，预先分配report_id、章节顺序、章节claim集合和必需限制。后续不得扩大claim等级或换scope。跨章条件需在此检查，并将必需限制放在首个相关事实之前或同一公开单元，避免用户先看到不完整的高风险断言。
2. **逐节确定性渲染与校验**：从冻结claim参数按现有受控模板生成章节，保留数值、单位、日期和语义；逐节检查后直接yield，不先构建完整body再split。普通格式片段可紧随章节发送；不引入自由LLM改写报告正文，不为制造流式增加模型调用。预检不依赖完整body时可提前；真正需要整篇输入的检查必须重构为等价的全局预检+逐节检查，并用反例证明等价，不能删除该检查。
3. **最终确认**：累计已公开合法章节用于最终body/hash/canvas，复查全局一致性、当前授权、引用、删除及修订后保存唯一ValidatedReport，完成后才开放画布和最终报告引用。正常未完成或中断阶段保留独立的发布会话/manifest，不冒充`ValidatedReport.published=True`。

优先在`build_validated_report`工具的异步执行期间完成上述预检/逐节发布/最终保存，工具仅在最终保存成功后返回既有validated/report_id结果，后续画布调用仍按现有报告ID工作。`after_agent`复用同一发布会话确认，不再重复发送全文；未调用报告工具时的确定性回退也走同一管线。不能保留“工具先构造整份报告→after_agent再分片”的旧顺序。若实现为独立发布节点，必须提供等价的工具返回、暂停/恢复及画布顺序兼容证据。

“逐节”是生成和发布顺序的改变，不保证渲染本来很快的报告在真实环境必然产生秒级间隔。禁止人为延时美化体验。用可阻塞的第二节renderer证明：第一节已到浏览器时，后续章节尚未生成。数据取数与四专员耗时仍属于正文前等待，真实时序必须如实展示。

发布会话以tenant/thread/attempt/report_id/revision/plan_digest隔离，记录章节序号/内容摘要和终态；恢复重放复用同一冻结计划，已发章节去重，不复用模型/中间件实例的全局set作为唯一去重依据。若需跨重启恢复，采用受控PG元数据及既有运行状态，新增字段/表必须有前向迁移、FORCE RLS和空库/存量测试；不将未确认正文写入普通事件表。新请求/新attempt不可继承旧会话。中断后的历史不伪造成完整报告；已有合法片段的保留与清理遵循正文权限、保留期和墓碑。

### D5. 客户端、取消与分块授权

前端复用langgraph-api→chat-store和当前组件；维护按scope/epoch/output_id索引的输出状态、最后seq及有界待处理集合。重复相同帧忽略，重复序号内容不同或缺口触发可信快照回读；不能把另一运行、旧企业或A→B→A旧代次写进新状态。done合并当前临时消息，不追加第二条；legacy快照及刷新继续可读。

消费端可按绘制节奏合并渲染更新，但不等全文或等Markdown表格全部完成才显示；保持滚动、窄屏、键盘、取消和内容复制体验，部分正文标“未完成”，不启用完整终答反馈。停止意图和服务端停止待确认保持原语义，断连恢复不新建run或重提业务请求。Markdown采用现有安全渲染，不执行HTML/远程脚本。

后端每个已校验段/章节发送前，以及每次模型/工具物理发送和最终确认前，重查运行状态/身份许可/墓碑；不能只在HTTP握手时检查。以有界批段控制授权开销而非每个字符HTTP内省，但不能在失权后继续排空缓存。取消/失权/删除后的迟到块全链路丢弃，恢复不能重放已删内容。

### D6. 时间点和验收证据

测试端独立记录`t_submit`、`t_first_safe_delta`、`t_last_safe_delta`、`t_stream_end`、`t_confirmation_end`；浏览器用本地performance.now记录首可见绘制。模型TTFT独立；跨进程只用关联ID，不相减不同时钟或P95。标记首正文来源new_delta/legacy_done/state_fallback；只有new_delta可证明新协议流式。

控制样本固定至少12个片段或3个章节。源端日志用序号/长度/hash和屏障状态证明“未完成”，不记录推理或用户原文。统一测试工具必须识别所有新协议帧，complete旧字段迁移为明确的正文/确认定义，保留历史记录不重写。本次仅做必要流式功能与少量真实配置模型验收，不重跑77分钟/8M Token的40题基准或后台负载。

验收矩阵见acceptance.md；每个场景有合法正向对照。先用官方镜像、隔离PG/Redis/IAM和受控慢源验证三个图，再每图少量真实配置场景、浏览器同源链路；模型不支持或知识服务缺失只阻塞对应真实组合，不能以mock替代并勾为真实通过。

### D7. 规格与并行集成

最终清理主规格仍描述六个DeepAgent中间件，与三图实现不符；本change的MODIFIED增量作为新要求。旧silent-agent-output及fix-langgraph-streaming-context-governance保留“内部内容不公开”的本意，实施时修正文档中的类名/旧节点说明，不恢复六图或过时finalize路径。已完成历史报告不覆盖。

不改性能Agent未提交工作，先协调sender字段/新契约集成；共享模型、langgraph_auth、output_contract、前端chat-store、溯源gate/publishing和迁移head只能由一个集成者串行接线。可独立开发适配器，但最终三图矩阵必须使用同一HEAD/锁/镜像。若部分输出状态需迁移，revision接执行时head，不改已执行迁移。

## Risks / Trade-offs

- [分段检查漏掉跨段限制] → 全局计划先检、危险断言完整单元后检、跨章/跨chunk反例；失败内容不能先出流。
- [DeepAgents专员Token被误当正文] → custom唯一公开通道、主/子任务输出身份分离、原生API负例与前端零草稿帧检查。
- [输出阶段造成额外整轮模型开销] → 以最终生成替换现有最终轮次，报告调用数/usage差异，不默认双生成。
- [供应商流式工具参数缺失] → 规划/正文角色隔离，受控与真实网关协议探针分列；保留工具非流式兼容，不靠换模型绕过应用缺陷。
- [先公开片段使最终校验失败不可收回] → 公开片段须独立合法，整篇失败标中断且不发布最终报告，不伪造成功或默默换稿。
- [部分状态持久化带来隐私负担] → 保存必要manifest/摘要，正文最小授权、保留期和墓碑；不扩大普通日志/事件采集。

## Migration Plan

先发布可识别新版本且兼容旧done的客户端，再集成共享发布器和三图适配；混合版本客户端/后端验证后统一默认新协议。回退只影响新受理运行，已开始流不得中途切旧全文；标明受限回退，旧运行按既定协议结束。部分发布状态迁移如有新增，使用前向兼容字段，不downgrade删除事实。正式部署、迁移、归档及Git交付按执行时授权，本次不操作正式栈。

## 独立补修：可恢复发布的具体落点（2026-10-22）

- `build_validated_report` 内调用继承父图 checkpointer 的 `tracing_publication` 子图。初始化先保存冻结计划、证据元数据和稳定输出身份，开始帧、每个章节及最终确认分别作为节点提交；不再等工具结束才保存全部发布进度。检查点复用平台既有隔离与删除机制，不新增旁路存储。
- 冻结证据仅保存已登记的受控元数据，恢复时按企业/用户/线程/业务/上下文/分析修订复核，重建 Gate 所需索引；不依赖上一进程的内存 registry，也不复制原始工具载荷。每批和最终保存前继续实时授权及墓碑检查。
- 原生 GraphBubbleUp/interrupt 原样上抛，不能被工具异常重试识别为503/连接超时；普通失败落为带进度的终态，after_agent 不重建另一份报告。不同受理尝试/epoch 不得恢复旧发布进度。
- 发布器 message_id 统一使用该次 report_id，历史 AIMessage、可信终答映射与流式完成一致；output_id 为独立传输身份。已完成会话再次读取只返回保存报告，不再次发送章节或done。
- 数据分析已公开正文后的外层超时只保留 aborted；零正文失败才可发固定受控文案，前端仅在前一个输出已中断且零正文时接纳该替代输出。知识问答保留规划/检索/产物工具的执行机会，不能为了直达流式生成而删除检索。
