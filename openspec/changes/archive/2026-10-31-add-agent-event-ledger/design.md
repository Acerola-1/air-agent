# Design

## Context

动机见 proposal.md。2026-10-12 规划时，第一项身份 change 的代码正在同一工作区更新，因此这里只新增规划文件，不根据未验收实现冻结身份函数名。

已读取的现状：`src/common/runtime_events.py` 只发送 UI custom stream，不能保证持久化；聊天 `streamRun` 使用 SDK 流和 `onDisconnect: cancel`，目前流结束回调不区分所有平台终态；`chat-store.ts` 已有停止、导航取消及迟到 run_id 处理；代理已经控制线程访问。`admin/api` 有 SQLAlchemy/Alembic 与独立 FastAPI；Langfuse 目前只是可选 CallbackHandler 包装。六图的显式图/DeepAgents 路径不同，不能只接前三图。

本设计依赖第一项的可信 tenant/user/membership、上下文版本、可撤销 session、原生 API Auth 和 RLS；依赖通过前不实施共享身份、入口、迁移或前端改动。旧主规格中部分行为过时，本项以已验收源码、身份增量规格和消息治理契约为准，保留既有完整资料计算。

## Goals / Non-Goals

**Goals:**

- 从真实受理到最终状态形成可复查事实，并有可靠的后续反馈/观测关联。
- 首期固定六图运行基础与数据质量查询；事实与 outbox 原子写入，消费有幂等证据。
- 给第 3 项和第 5 项提供两个独立扩展面、契约样本及共享文件负责人。

**Non-Goals:**

- 不把所有 LLM、Skill、MCP span 采集塞入本项；只提供归属、事件登记和 Trace 映射接口。
- 不新增完整反馈交互、图表页面或问题聚类；不要求 Trace 服务在线才能回答。
- 不做跨库分布式事务或宣称上游工具恰好执行一次；受理歧义进入待确认，绝不自动重执行不可幂等操作。

## Decisions

### 1. 台账在业务 PG，身份仍由 admin/api 提供

复用业务 PostgreSQL，表由 `admin/api/migrations` 唯一维护；Agent 侧 `src/common/event_ledger/` 提供最小异步写入/查询适配，不导入后台 app，也不访问平台内部表。约束和协议有一份可版本化机器可读契约，以 JSON Schema/静态样本供 TS/Python 分别验证，不手写两套互相漂移的含义。

基本事实和 outbox 同事务，运行可直接写 PG；选择此路径可避免每个 token/节点依赖后台 HTTP。不采用浏览器日志作为运行事实，也不复用 Langfuse 作为业务事件库。连接与 worker 在 lifespan/首次使用时有界初始化，不在 graph 导入时连接数据库。

运行写入、普通读取、投递调度和迁移分离权限。跨租户调度只领取任务 ID/tenant_id/租约必要字段，再进入对应租户事务；不得给普通运行角色 BYPASSRLS。调度与校准服务使用单独受限角色或受限函数，不能因投递需要开放所有正文读取权限。

### 2. 数据对象与唯一约束

所有租户对象带 tenant_id，数据库复合外键保证关联不能跨租户；关联到第一项的全局 user 与租户 membership 时验证组合。下表为逻辑结构，最终类名可变，约束含义固定。

| 对象 | 最低内容与约束 |
|---|---|
| `agent_requests` | request_id、thread_id、user_message_id、graph_id、可信 user/membership、受理时间、事发部门/角色快照、来源版本；`tenant_id/request_id` 唯一 |
| `agent_submissions` | 提交/重试/重新生成/恢复的 operation_id、operation_kind、幂等键、规范内容摘要、request_id/attempt_id、受理状态；租户/成员/线程/键唯一 |
| `agent_run_attempts` | attempt_id、request_id、attempt_index、来源 attempt、平台 run_id、状态/修订版本/证据、排队/启动/结束时间；request 内 attempt_index 唯一，非空平台运行映射唯一 |
| `agent_run_resumes` | 恢复 operation_id、attempt_id、原 run_id、恢复次数/输入引用；不增加提问量，run_id 不变时不另建尝试 |
| `agent_answers` | answer_id、attempt_id、可用 message_id、内容版本/引用、partial/confirmed、确认时间；消息来源版本唯一 |
| `agent_trace_links` | attempt_id、provider、project_scope、trace_id、primary/source；提供方/项目/trace/尝试组合唯一 |
| `agent_events` | 类型/来源/版本信封、规范载荷摘要、关联、接收时间、修订依据；tenant/event_id 唯一 |
| `agent_outbox` | event_id、目标消费方、available_at、lease_owner/deadline、attempt_count、错误类别、完成/死信状态 |
| `agent_consumer_receipts` | tenant/consumer/version/event_id 唯一，与投影更新在同一事务 |
| `agent_content_refs` | 独立授权的原问题快照或合法引用、来源消息/版本、保留期；不进入事件载荷 |
| 删除/投影 | 会话删除标记、待清理任务、运行/覆盖率投影及口径版本；不会重放模型或工具 |

原始问题是后续需求分析输入，在受理时按配置保存到受控原文表，或使用能保证内容版本及保留期的受控源引用；不能只存会随线程编辑漂移的引用并称其为原问题。回答默认保存关联和内容版本，全文通过原有授权线程/归档路径回读。普通事件、outbox 和死信禁止携带全文。删除权限要同时涵盖这些新原文引用和缓存。

### 3. 受理协议与跨进程歧义

浏览器生成 submission_key 和 user_message_id；它们是去重候选，不是可信身份。受理端根据第一项有效上下文及线程归属计算规范内容摘要，创建/复用 request 和 attempt；同键不同操作/内容返回 409。明确提交顺序：可信授权 → 持久化受理意图/事件/outbox → 受控启动平台运行 → 持久化平台关联。业务请求在台账受理事务失败时返回 503，不启动新运行。

运行创建存在两个系统间的非原子窗口。必须首先验证当前官方镜像/SDK 的调用方指定 run_id、幂等创建、metadata 查询、原生 hook 和恢复行为。优先用稳定预分配 run_id 加原生幂等受理；若当前镜像不支持，则用具有独占租约的受控创建入口、可信关联 metadata 与受理校准：上游响应丢失后停止自动重发，从受授权接口查询匹配，不能确认就保持 admission_unknown 并提供显式处理。租约到期不是允许重发的证据。

用户侧可由同源代理受理并透传关联；Agent 原生入口仍验证不可伪造的受理上下文及台账状态，拒绝没有 reservation 的运行创建。不要把 proxy-only 作为全入口方案。适配必须覆盖流式/非流式创建、重试与恢复；批量/cron 等未开放入口继续拒绝。内部票据只在请求边界验证，不把票据放进 metadata/checkpoint，可信非秘密 request/attempt ID 可以保存。resume 使用独立 operation 幂等，不伪装成新问题。

若只能通过受控创建网关实现，网关需避免再次递归进入自身受理逻辑，并仅给内部 caller 有效的创建上下文；外部无法伪造。验收看真实运行与台账一致，不以新增一个无人使用的封装函数算完成。

前端将受理关联和 attempt/answer 映射回读接入现有 API 与 store，不重写消息渲染。浏览器结束、onDone、signal.aborted 都只是客户端状态。不能增加一个会因重复提交重新生成的默认请求键，也不能在超时后自动创建第二个 run。

### 4. 六图生命周期与终态证据

主状态为 admitting/admission_unknown/queued/running/waiting_input/succeeded/failed/cancelled/timed_out/status_unknown。`cancel_requested` 是独立事实字段，不替代主状态；连接状态也独立。waiting_input 允许以恢复操作回到 running，同一原生运行保留身份和时间片记录。

采用乐观版本或行锁更新状态，保留转换证据；来源优先为经过认证的平台原生状态与相应时间、可信 runtime 回调、最后才是浏览器感知。回调作为及时补充，平台校准用于恢复与冲突处理。原生 interrupted 的含义结合中断详情与取消证据判定，不能只按字符串映射为用户取消；不足时标记待确认。

六图共用入口与 lifecycle 适配，涵盖运行开始、人工等待/恢复、异常和完成。只有最终输出确认/可见 message 版本进入 answer 表，工具/推理/推荐追问不作为终答。保留 `runtime_events.py` 原来的 type/version/processing_id 等 UI 契约，用独立持久协议或适配层，避免把业务 schema_version 强塞给所有已有 UI 消费者。

停止保存 origin（user_stop/navigation/tenant_switch/transport_disconnect/permission_revocation/unknown）和已知 phase（queued/processing/output/unknown）；停止先到则关联 attempt，run_id 确认后补取消。UI 确认响应不等于平台已取消；完成与停止竞争保留双方事实。第 2 项接入既有停止/导航来源与服务端确认，不加入点赞/复制及完整浏览器计时埋点（第 3 项）。

补偿 worker 分批扫描超过间隔无进展的受理/运行，通过最小授权平台状态接口读取元数据。不要用过期用户票据发起业务执行；独立服务校准只能查询确定范围运行状态，不能读取全量消息或恢复业务权限。无对象/平台不可用/身份撤销不同错误，状态未知仍可查询。超时必须有实际 deadline/平台超时证据，时长很长本身不等于超时。

### 5. 版本化事件、outbox 与重放

信封 v1：schema_version、event_id、event_type、occurred_at（UTC）、received_at（DB）、source、tenant_id/user_id/membership_id、适用 thread/request/attempt/run/answer/message ID、关联版本、允许字段。服务端身份覆盖客户端值，逐个对象校验；source 是服务端认证生产者类别，不接受浏览器自行声明 runtime。

基础事件族：request.accepted、run.admission_confirmed、run.state_observed、run.cancel_requested、run.resumed、answer.confirmed、answer.partial、trace.linked、resource.deleted。浏览器扩展与模型/工具观测只预留名称和契约，本项不能显示已经采集。

事件 ID 冲突以规范摘要判定；浏览器事件未来提供批量逐项结果，默认上限 50 条、每条 16 KiB、整批 256 KiB，可更严格配置。普通服务端单条限制同样有界。字段白名单不允许任意 metadata 存全文/票据。未知版本和未启用类型明确拒绝。Trace 项目范围只保存非秘密逻辑标识。

事实投影需要的初始对象/状态与事件/outbox 同事务提交，异步消费处理衍生投影。outbox 以行锁/租约（如 SKIP LOCKED）领取，处理后确认；消费者 receipt 和投影同事务。至少一次交付配合消费幂等，不宣传跨数据库恰好一次。进程重启租约恢复，有限指数退避加抖动，毒事件死信，限领取批量/并发/每租户配额，关闭时停止领取并有界等待。

默认补偿扫描 30 秒、超过 60 秒无事实进展开始校准、每批 100 条；投递最多 8 次后死信，租约 60 秒。这是可配置实施初值，不承诺平台在 60 秒内必有终态；真实验收记录选择和依据。监控 pending/oldest_age/retry/dead_letter/reconciliation_lag，日志只写非秘密 ID 和错误类别。

运行已受理后遇到数据库故障不把已有答案改为业务失败，也不悄悄丢弃。写入有限重试，worker 从平台校准恢复可恢复事实；无法恢复的 callback 明细标记采集缺失，第 5 项另做观测耐久策略。不能为每 token 同步写台账；本项只写关键生命周期。worker 不依赖 Langfuse 才能运行，也不使用 detached task 作为唯一可靠投递。

删除标记优先于投影重建/重投。重放只恢复业务投影，不发模型/MCP 请求；范围 tenant/window/consumer/version 显式，操作审计，先 dry-run。失败和复跑有稳定游标，不跨租户遍历原文。

### 6. 查询与事件扩展接口

以下为逻辑接口，以已验收路由前缀适配；实施交付必须冻结 OpenAPI/JSON Schema 和字段，不能靠这一表推测未实现路径。

| 接口 | 调用者及行为 |
|---|---|
| 提问受理与关联回读 | 可信入口/本人，受理返回 request_id/attempt_id/状态，重复返回已有对象，不在浏览器暴露票据 |
| `GET /agent-requests/{id}` 与回答版本子资源 | 本人；企业运营需单独权限，跨范围统一 404 |
| `GET /agent-runs` 与详情 | 企业 `agent:operations:read` 等明确权限，过滤/分页只在授权企业；默认无原文 |
| `POST /agent-events/browser` | 当前活跃成员、自有回答/尝试/有效企业上下文；v1 类型白名单及逐项结果，不能写平台事实 |
| 内部 runtime 写入与 Trace 映射 | 服务端可信协议，校验受理上下文、来源权限及复合关联；浏览器不可调用 |
| `GET /agent-metrics/summary` | 企业统计权限，返回基础事实计数/状态/延迟/覆盖率及口径版本，未采集字段明确标识 |
| 重放/死信/清理工具 | 独立运维权限和审计，显式租户/时间/消费版本、dry-run；首期可以 CLI，不做后台页面 |

平台账号不自动获原文；原文使用 `agent:content:read` 独立权限及第一项当前授权。同用户 A/B 与历史离职成员的事发归属不会因调岗改写。查询 limit 默认 50、最大 200，排序/游标稳定；不默认返回所有事件。

冻结契约置于 `docs/contracts/agent-events-v1/`（实现时创建，包含事件登记、JSON Schema、OpenAPI 摘要、样本、状态表、指标字典及变更记录）。其消费方适配另放在各自包中；后续需改变主版本时先更新 change/样本，由负责人集成，不能随意扩充共享自由字典。

### 7. 首期指标与可见质量

| 项 | 口径 |
|---|---|
| 提问/活跃 | 已确认平台受理的 request 去重；仅 reservation/admission_unknown 不计有效提问，另列受理中；租户窗口内 user 去重 |
| 尝试/恢复 | 运行尝试与恢复次数分别统计，内部工具 retry 不新建 attempt |
| 技术完成率 | succeeded / (succeeded+failed+cancelled+可信 timed_out)，以窗口中受理尝试队列为默认 cohort；展示各状态及未结束数 |
| 用户停止 | user_stop 意图 / 已开始尝试；明确 queued-stop 另列，实际 cancelled 是另一指标 |
| 服务耗时 | 服务端受理至实际终态墙钟或同进程单调时钟，不混浏览器；人工等待时间另列，禁止用回调接收延迟伪造执行时间 |
| 延迟分位数 | 有效明细 P50/P95/P99，样本数/缺失数并列；不平均每日 P95 |
| 观测/交互 | Trace 映射覆盖率可读；未采集反馈/复制/usage/成本/模型 TTFT/浏览器首内容返回 null 与 not_collected |

数据库 UTC，查询业务时区默认 Asia/Shanghai，所有窗口 `[start,end)`。默认 summary 是“窗口内受理的尝试”的 cohort，历史 cohort 接收迟到终态后可修订；另做“窗口内结束”视图时必须明确命名。request 时间为首次成功受理时刻，不因重新生成改写；尝试各有自己的受理时间。sample_count/missing_count/status/measurement_source/metric_version/as_of/projected_at/data_lag 构成最小质量说明。

浏览器首可见终答正文用 performance.now 同页面时钟测量，第 3 项接入；模型 TTFT/usage/成本第 5 项接入。缓存/推理是 usage 子集，不可重复加到总量。成本缺价格/usage 时不是零；LLM/业务评价只对证据样本算解决率。这些作为扩展字典，首期不虚构实现。

### 8. 并行实施边界

第一项验收后实施第二项；第二项独立验收并冻结上述 v1 契约后，第三项和第五项从同一基线启动，使用独立 checkout、数据库/schema、端口、队列前缀和输出目录。各 Agent 不接入彼此测试环境、不重置共享卷。共享文件的更改由指定集成者负责，不能让两个 Agent 都有 merge/提交共享变更的默认权限。

| 所有者 | 可以独立做的工作 | 交给集成者的接线请求 |
|---|---|---|
| 第 3 项 | 独立反馈服务/表/路由、聊天交互模块、browser 类型登记扩展、本人回答回读消费、浏览器契约样本 | chat-store/proxy 公共接线、main 路由登记、身份权限字典、事件总登记 |
| 第 5 项 | 独立 observability 包/表/服务、Langfuse/模型/Skill/MCP 采集适配、服务端类型扩展、usage/价格实现 | 同一运行公共 hook、MCP 公共封装、main/配置/权限、事件总登记 |
| 集成者 | 公共接口、入口、事件总登记、共享配置、迁移链及组合回归 | 接收两个 change 的明确补丁需求并集成 |

不能同时改一个 models.py 堆叠全部新表。新域用独立模型/路由模块，公共注册点只由集成者落地。Alembic 两条并行分支在各自环境可测试，集成者合并 head 或有依据地顺序化；不得两边使用同 revision ID，不能无说明重写已应用迁移。冻结依赖 uv.lock/package-lock 修改也由集成者处理。

完成第三/第五后集成验收再做第六后台。第七需求分析与第八质量可在第六接口稳定后并行，前提是页面/服务文件分域；若共用对话详情/菜单仍由集成者接线。第十的验收矩阵/负载脚本可随阶段准备，但最终交付回归须等待实现集成。

## Risks / Trade-offs

- 受理台账新增关键路径依赖 → 事务有界、数据库故障不接收新运行；已受理运行保持原体验并暴露采集缺失，不冒充无损。
- 原生 hook/幂等行为因镜像不同 → 实施任务先验证具体入口；受理歧义不能重发，所有未覆盖入口拒绝开放。
- callback 与平台时间证据不一致 → 分开 occurred/received，保存来源和修订版本，无法确定时用 unknown。
- worker 需要处理多租户 → 限调度能力与每租户事务，真实非 owner RLS/连接池交错测试。
- 全面埋点扩大本项 → 只接关键生命周期，交互和 span 交给两个有契约的后续 change。
- 第 1 项未完成且同工作区变动 → 现在仅计划；实施前钉住已验收提交和实际身份字段，更新交接中的映射。

## Migration Plan

1. 核对第一项验收和提交、实际 RLS/session/运行授权接口，保存 v1 身份字段映射；未通过不修改其代码。
2. 新建台账表/索引/权限/worker 配置，先在专用 PG 演练，不重建已有卷或 LangGraph 内部表。
3. 迁移 dry-run 明确旧线程/运行可关联的数量和未知项；不生成历史点赞、复制、Token 或模型时间。历史导入标记 historical/来源/缺失，不与本次真实采集混算。
4. 校准旧已知运行仅用支持的原生 API；无可信租户或请求的记录保持受限。不扫描或导出全文到证据文件。
5. 在受控入口部署新受理与 lifecycle、启动 worker，按六图真实请求验证 run/台账一致；旧活跃运行单列切换窗口，未关联不伪造为新采集。
6. 上线前验证投递重放、删除和恢复；故障停止新受理但不破坏现有答案，恢复后校准。依赖 worker 未就绪时暴露采集健康，不能隐藏积压。
7. 回滚采用关闭新入口/worker并保留表与事实；已有新写后优先前向修复，禁止 downgrade 丢失台账或恢复绕过隔离的旧入口。隔离恢复演练必须重施删除标记。

## 验收与交接

必须真实 PG（非 owner RLS）、当前官方 LangGraph API 与六图、浏览器的停止/导航/回读；模型/MCP 可用受控替身验证生命周期并标明，至少保留可复跑真实运行服务证据。断流、受理响应丢失、进程重启、终态乱序、重复投递、死信重放、A/B 同用户、权限撤销、删除竞争和 UTC/业务日边界有明确断言。

基础计数使用小型确定 fixture（重复提交、重新生成、恢复、内部 retry、等待、取消、未知）人工核对期望，并验证 summary 和明细一致。真实业务平台材料仍不足时不冒用身份 change 的模拟授权作为真实 MCP 证据；本项不要求补齐第 5 项全链路采集。

交付 `docs/reviews/agent-event-ledger-acceptance.md`、契约包、对应测试和脱敏联调证据，标记“待独立验收”。通过后记录冻结提交和契约版本，才将第 3/5 两个完整实施包同时交给独立 Agent。


## 协调者接管后的本地实施放行

原文“第一项验收后实施”的依赖以本节当前状态解释：协调者已直接补修并独立复核身份/RLS/认领/运行授权关键接口，允许本项本地实现；第一项7.6真实MCP和10.3完整矩阵尚未完成，不等同生产验收或归档。HEAD7b82c04只是规划提交，冻结起点见handoff指定的工作树哈希清单。实施前核对实际字段及偏差；不弱化本项全部验收场景，不把未完成身份验收冒写成通过。事件v1独立验收冻结仍是第3/5并行前提。
