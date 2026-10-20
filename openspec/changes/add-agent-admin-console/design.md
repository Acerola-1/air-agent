# Design

## Context

检查基线为2026-10-20的`dev@baa1b24`。`langgraph.json`实际三图：basic-qa、data-analysis、intelligent-tracing；原路线的六图列表和旧MCP绑定均不是当前授权依据。未启动或修改正式服务。

| 当前实现 | 可复用事实 | 尚需补齐 |
|---|---|---|
| `admin/web` | Next16.3/React19、Radix/shadcn风格组件、SessionProvider、同源API代理 | 首页主要是身份/权限介绍，无Agent运营页面，无独立后台测试命令 |
| `routers/ledger.py` | `/agent/runs`、单attempt、metrics、本人request映射 | 统一筛选、趋势、用户聚合、运营跨成员正文；不能给本人接口加UI就冒称运营可用 |
| `routers/feedback.py` | 当前本人反馈回读、企业操作历史、受限意见、统计 | 企业当前反馈列表、简单处理单；现有历史不能作为当前评级累计 |
| `services/observability.py` | 模型列表、run观测、统计、Decimal成本、受控Trace | Skill/工具跨运行分页与维度聚合；run前200项截断不能作为全量汇总 |
| `agent_content_refs` | 受理时原问题快照、request精确引用/保留期 | 回答表仅消息ID/版本；`lifecycle._on_graph_end`已能确定可见终答但未存正文 |
| 远程身份/分库 | 实时IAM、业务引用、RLS与事务GUC | 新权限注册交接、所有新表的最小运行角色授权 |

不存在已同步的`agent-event-ledger`同名主规格。事件/生命周期还需参考活动`add-agent-event-ledger/specs`与当前代码，不能通过后台change提前归档历史身份/台账change。

## Goals / Non-Goals

**Goals:** 一个可日常使用的中文后台，事实可下钻、费用可复算、正文可控；给7提供稳定壳和DTO。

**Non-Goals:** IAM重写/Vue迁移、MCP服务端、模型/图业务改造、告警预算、向量分析、付费账单、导出全集、评测平台。新反馈处理只管理当前操作者的处理状态，不做跨团队派单/通知系统。

## Decisions

### D1. 沿用现有Next后台与HTTP查询边界

新增`routers/console.py`、`schemas_console.py`、`services/console/{scope,queries,metrics,content,feedback_triage}.py`，不向Next暴露数据库或IAM凭据。BFF保持`cache:no-store`及当前admin应用cookie，浏览器只访问同源`/api/agent/console/**`。相比另建Vue服务，这保留已验收会话/企业切换与现有UI，减少本次范围。

业务菜单按本地静态路由清单和`ctx.permissions`显示，不要求IAM菜单表具有Agent页面；账号/企业CRUD跳已配置IAM后台。后台API不从菜单可见性推导授权。

### D2. 一次确定页面结构，6先交付事实页面

| 路由 | 页面重点 | 下钻 |
|---|---|---|
| `/operations` | 请求、活跃、完成/停止、体验、usage/费用、覆盖与日趋势 | 同窗口业务/运行/反馈 |
| `/operations/users` | 用量/活跃/费用；身份名来自现有引用，缺名显示ID | 用户过滤的运行列表，无身份CRUD |
| `/operations/runs`及`/[attemptId]` | 逻辑问题、attempt、恢复、回答版本与时间线 | 按需读正文、观测与Trace |
| `/operations/feedback` | 当前评价、原因、处理状态、历史 | 精确回答/处理单 |
| `/operations/capabilities` | 业务/Skill阶段/工具逻辑调用和重试、失败类别 | 原运行与调用明细 |
| `/operations/performance` | 服务耗时、浏览器首正文、模型TTFT分别展示 | 各自样本明细与缺失 |
| `/operations/costs` | 模型/用户/业务/币种小计和覆盖 | 逐调用当前计费修订 |
| `/operations/insights` | 由7实施，不在6伪造入口或KPI | 7接壳时再启用 |

控制台按当前企业提供聚合，不提供跨企业排行。没有ops权限的统计用户可访问聚合，但运行/用户身份/正文下钻明确禁用；各分区权限分别判断，不能因一个403隐藏已有统计权。

### D3. 后台v1查询协议

完整参数与响应在`api-contract.md`，6为协议负责人，7复用，不导入未来的6模块实现作为后端编译前提。

- 全部规范化为`start/end`带时区ISO时间、`window=today|last_7_days|last_30_days|custom`，服务端回读UTC边界与业务时区；默认近7业务日包含今日，最长90天。当前窗口不完整必须标明。
- 共用`business_code/graph_id/user_id/model/provider/skill_id/service_key/tool_id/status`适用矩阵；不支持的过滤422。禁止跨表宽连接导致计数膨胀：各事实先按权威键聚合，再做一对一关联。
- 使用单次只读repeatable-read事务或等效快照固定同一响应；签发分页上下文含`as_of`、过滤、企业、排序、版本，默认50最多200。并发修改仍以最新墓碑优先，不能为了快照恢复删除数据。
- 服务端直接聚合全窗口，不分页拉全量到浏览器。高基数group列表单独分页；固定排序加稳定ID避免同时间戳漏页。
- 来源不同的指标独立cohort：请求受理时间、attempt受理/终态、回答确认时间、模型开始时间不可混合。每一分区有自己的metric_version/样本/缺失/覆盖/lag，不能用一个全局100%掩盖缺口。

### D4. 权限与正文落地

统计=`agent:metrics:read`，可识别企业运行/用户明细=`agent:operations:read`；他人问题、回答、意见、处理说明正文还需`agent:content:read`。价格配置仍单独`agent:pricing:manage`，6只显示费用，不暗加定价页面。历史数据读取不附加当前业务使用权，不把业务撤销变成无法查询历史；身份/企业/成员仍必须有效。

统计端点选择group_by=user_id或指定他人user_id筛选时也必须ops，不能绕过用户明细权限；整体distinct_users计数仍是metrics可读的非标识聚合。

新读模块独立设置经过权限校验的事务GUC，复用现有隔离规则但不直接调用只有本人语义的`read_request_content`冒充跨成员授权。查询显式同租户关联和墓碑过滤，不因平台角色设置全局读旁路。引用用户名仅作展示，不从本地旧roles/users获取权限。

**回答快照是6的必要小扩展：** 新表`agent_answer_contents`以(tenant,answer,content_version)唯一，含request/attempt/thread/user、确认版本、safe text、摘要、采集时间和retention。服务端`_on_graph_end`使用现有`_visible_final_message`确认后，调用新增`src/common/event_ledger/answer_content.py`持久化；只处理终答文本块，不保存reasoning、toolcall或完整state。写前复查身份/业务与墓碑，绑定当前实际图/attempt；复用确认answer_id后独立写快照，不改变已有answer事件、状态与统计事务语义。快照失败保持答案与确认事实，标`capture_failed`供后台解释，不能把已有答案变成业务失败。max64Ki字符，超长显示`truncated`和摘要，不宣称全文。

相同answer/version/hash写入幂等；相同版本不同hash必须拒绝并记录安全诊断，不覆盖旧版本。新快照表使用本域正文写事务，不把全文塞入公共answer事件或outbox。

该接线由集成负责人串行加入`lifecycle.py`，不重写`store.py`的生命周期函数；溯源作者需确保最终发布的同一ValidatedReport正文进入此回调，不能采集被门禁拒绝的草稿。三图各有真实正向样本才算采集完成。旧回答不从任意checkpoint或共享Langfuse补抓，返回`not_collected`；新回答快照端到端不能以此状态逃避验收。

原问题按已有content_ref精确版本读取；正文默认收起、每页最多8000字符，认证/墓碑每次重查，Cache-Control no-store。保护HTML/Markdown安全渲染，不加载远程跟踪图片。正文全文搜索暂不做，元数据搜索和精确ID查找足够。

### D5. 轻量反馈处理

新增`agent_feedback_cases`（每tenant/answer唯一，state、revision、reviewed_feedback_revision、处理者与时间）、`agent_feedback_case_actions`（操作ID/摘要/前后修订/安全审计）、`agent_feedback_case_notes`（独立正文RLS/retention）。状态与action同事务。操作为start/resolve/reopen；resolve需非空结果说明、0到1000字符以外422。源反馈出现新negative revision时`needs_review=true`，不静默重写用户评价/历史。

写入需ops+`agent:feedback:manage`，带说明时还需content；详见`permission-handoff.md`。引入独立权限而非让`operations:read`写，是为了保持原只读账号边界。处理单不新增公共事件，使用本域事务审计；9可在后续接派单与复评，不把已处理当任务已解决。

### D6. UI契约与真实性

沿用灰白语义token，`admin/web/app/globals.css`是后台运行时样式所有者，不能复制聊天页的宽度/字体到所有表格。复用Button/Dialog/Table、SessionProvider、TenantSwitcher；新增共用`components/operations/{QueryToolbar,MetricState,DataTable,ContentReader}`及`lib/operations/{client,query,scope}`，后续7复用。图表优先简单可访问SVG/表格，只有确有需要才由6统一引入图表依赖，不改根Python锁。

URL只放非敏感时间/维度与页游标，原文与搜索词不放URL。搜索300ms、IME组合保护、即时清除，AbortController+scope epoch丢旧结果；需修补现有client把所有409都视为企业切换的行为：只对context_changed触发session刷新，普通revision_conflict留在表单。新API显式返回错误code，旧未分型端点保持原兼容。

实现时同步扩展DESIGN/UX-CONTRACT的后台owner与数据状态，而非改写聊天规范。中文zh-CN、Asia/Shanghai；日期预设及自定义带标签的文本日期字段（YYYY-MM-DD，明确包含结束日并转排他边界），不暗依赖浏览器弹窗语言。390×844和1440×900、键盘/焦点/输入法分别验收；源码静态审计不替代浏览器。

### D7. 负载与缺失

普通列表/聚合默认请求超时5s，受控Trace8s，均在BFF10s总界限内；不做无界并行扇出。基准自有PG：10万requests/30万attempts/100万观测、固定30天、35次×2轮、并发10，测本地SQL/API与IAM/外部等待分离。目标常规列表本地P95≤500ms，聚合≤1500ms，正文≤500ms；达不到必须优化或列真实阻塞，不靠缩样本/假缓存宣称通过。只为实际EXPLAIN热点加索引，不提前建数仓。

## Risks / Trade-offs

- [原始metrics.json保留change3/5尚未采集的历史标签] → 新admin-metrics-v1按实际来源返回，不直接复制旧标签；不重写公共registry和生成副本。
- [run观测前200项被误当全集] → 详情标truncated并提供分页，聚合SQL走完整授权事实。
- [共享Langfuse/缺回答正文] → 受控回读与独立终答快照；历史缺失明确，绝不扩大平台访问范围。
- [新权限在IAM尚未注册] → 新写功能拒绝，使用真实IAM测试注册/授予作正对照；生产需按交接清单配置，不SQL seed正式root。
- [与溯源改同收尾/迁移] → 并行契约明确小补丁串行应用，最终集成再验一次终答一致性。

## Migration Plan

1. 冻结DTO/路由与权限矩阵，完成只读服务及有界查询，旧API保持；前端先接真实可用分区。
2. 在合并时当前head追加本域表/索引迁移（非owner授权+FORCE RLS+复合约束），空库和存量副本升级均验证。
3. 串行接入终答快照，三图分别跑新终答；旧数据不自动回填原文。
4. 测试IAM通过正常管理面注册/授予新码，反馈处理端到端验证；正式目录与授予交接给授权管理员。
5. 组合实际后台/PG/远程IAM/BFF与删除/撤权验收；与溯源最终版本组合复跑后交付，不自动发布共享环境。
6. 回退使用前端路由/采集开关与上一版本代码，保留兼容新增表；不自动downgrade正式库，停用采集不停止已删内容清理。
