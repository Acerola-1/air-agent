# 授权能力选择设计

## Context

动机和能力范围见proposal。基线2d6f7c8已包含3/5组合修复与归档；06b411f包含IAM协调评估。当前Skill元信息可由SKILL.md本地读取，业务图通过skill_router/skill_discovery自动路由；data-analysis另有菜单映射。显式业务图ToolNode及三张DeepAgents图仍有编译时工具集合，MCP工厂以tool.name做部分缓存键，授权后发现和同名跨服务存在接线风险。DeepAgents的/skills文件读取与子任务也是独立执行边界。

目前AdmitRequestIn没有选择字段，canonical_content_digest只覆盖问题/图/操作；原生create_run会从多层载荷核对台账预留。观测已有matched/selected/loaded/executed，但没有用户选择快照，loaded后执行来源可能被loaded-rules覆盖。不能仅加前端勾选或模型提示。

## Goals / Non-Goals

建立“用户意图→当前授权校验→不可变受理计划→六图实际执行→隔离回读”的链路。沿用当前身份与台账，不重写登录、租户、RBAC、MCP业务身份、不实现Java IAM或知识库entitlement。用户选择只能缩小已有权限；基础本地工具仍受现有Graph规则控制。

首版单选Skill、选择MCP子集；不做多Skill编排、任意URL接入、管理员配置编辑、能力推荐或新统计后台。IAM可以另工作区并行开发；本项只依赖可信RuntimeIdentity和可替换的授权接口，不直接查询新IAM Redis或令牌内部结构。

## Decisions

### 1. 目录从当前真实能力生成

本地读Skill frontmatter与材料指纹，不触发Embedding或远端模型。稳定ID为skill:<graph_id>:<目录slug>和mcp:<server_key>:<原始tool_name>，名称/描述只是展示。MCP仅经当前成员的ScopedMCPToolFactory发现，不用全局身份预热。成功服务可部分返回，故障服务显示暂不可用；未配置/未验证不能显示可运行。

原生GET /capabilities是custom app路由，必须显式调用既有_authenticate_custom等价鉴权，再检查活跃人类成员，不能误以为LangGraph Auth自动覆盖。rs/mt类内部主体不能读取个人目录。浏览器GET /api/capabilities通过既有会话/运行票据代理。分页、限额及错误见contract-extension。

元数据可缓存；授权结果不得跨请求正向TTL缓存，每次受理和真实出站调用实时复核。缓存/游标绑定tenant/user/member/graph、四修订号、ctx_version及绑定修订；浏览器还用单调epoch阻止A→B→A迟到覆盖。单个MCP服务2秒，总5秒，最多3并发；本地目录过滤与外部等待分开测量。

### 2. 受理快照是唯一执行依据

服务端规范化选择，检查当前授权、材料版本、图归属、依赖；与submission/attempt及受理event/outbox同事务持久化。生成不具授权能力的selection_id与SHA256摘要。运行端用既有受理引用取回可信计划，metadata/config里的副本仅作一致性比对，不能覆盖计划。

新增业务域agent_capability_selections表或等价独立模型，含租户/用户/成员、request/attempt/submission/graph、规范选择、材料版本、摘要版本和创建时间；复合关联保证不能跨scope引用。FORCE RLS、非owner角色、事务GUC和删除墓碑沿用当前业务库约束。按当前实际迁移head追加revision，不改已执行的0012系列。允许只存受控ID/版本，禁止保存凭据、完整Skill规则、原始工具参数。

幂等摘要v2包含规范选择。旧v1记录仅允许无选择/自动语义按原规则重放；旧显式语义不存在，不回填虚假选择。新提交即使省略字段也落规范auto快照。不同选择使用同提交键409；不能修改旧摘要、释放启动权或增加第二次受理。

### 3. 加载与执行双层收窄

显式Skill绕过语义候选选择，加载对应版本，不允许意图分类静默导向别的Skill；输入限长、授权、上下文预算继续生效。data-analysis只能显式选择当前菜单映射Skill，相异422。自动模式仍按原菜单/路由工作。

allowed-tools是能力上限，不自动等于必需依赖。只有已知材料/可验证manifest声明的required_tools才用于“缺依赖422”；不能推断所有允许工具都必需。无法证明必须调用时，保持用户指定子集，不自动扩大权限；必要时由模型解释能力不足。显式Skill与明确依赖冲突必须在启动前拒绝。

模型schema披露、真实MCP dispatch、load_skill/find_skill、文件读取和子Agent都消费同一计划。工具allowlist空集合不披露/调用MCP，基础本地计算/画布等仍由原Graph控制。保持MCP原始稳定ID，模型可见名采用合法且碰撞安全的别名；单服务既有唯一名称尽量兼容，不允许dict最后写入覆盖另一服务同名工具。

显式图要解决授权后动态发现的ToolNode调度；DeepAgents要对SDK动态工具、/skills受限读取与子任务继承做真实执行验证。仅限制顶层提示词、过滤schema、或给工具改名不足以通过验收。图导入不远端发现/Embedding预热；保留六个导出Graph对象类型，避免with_listeners变成RunnableBinding导致GraphLoadError。

### 4. 重试与恢复以不可变历史为准

retry继承当前attempt计划，每次出站仍检查当前授权。regenerate默认继承；新submission明确选择时可新建合法计划和attempt，不新增逻辑提问。resume同attempt的新submission必须继承原选择和材料版本；原材料变更或授权撤销时409/403，要求用户重新提交。不能自动换Skill、复用旧授权或改写旧回答来源。

### 5. 前端只表达选择与已受理事实

在当前ChatPanel输入区增加紧凑“技能：自动 / 工具：自动”入口，复用现有按钮、弹层与样式，不新增组件库。目录加载/失败/刷新/空态、中文描述、当前已选摘要、取消和“不使用MCP工具”明确。支持键盘焦点、Escape、屏幕阅读标签、390×844布局和中文输入法。

草稿按身份/企业/图/ctx_version/epoch隔离；切换清空并中止旧请求，旧响应不能覆盖。运行期间已受理选择锁定，下一轮草稿独立；失败发送只在同epoch保留草稿。显示服务端回读的已受理选择，不把本地勾选伪装成执行结果。

### 6. 观测关联与兼容扩展

选择事实、Skill阶段、MCP实际调用分别记录。新增server来源capability.selection_accepted事件及runtime.span安全关联字段，具体白名单见contract-extension；三份validator、登记源及生成副本同步。浏览器不能上报可信选择事实。用户选中但取消、未加载、不调用时，不增加实际使用/Token/成本；保留已有调用重试和Decimal计费语义。新快照在既有有权查询中回读，旧记录not_collected。

选择、观测与事件的删除优先级一致；备份恢复后重施墓碑，晚到写入不能复活，外部Langfuse不输出完整规则或凭据。默认不另加高基数价格/用量指标维度。

## Risks / Trade-offs

- 动态工具调度与SDK文件访问差异→先用受控MCP和真实SDK做Gate0探针，再接六图；不能削弱范围后勾完成。
- 同名工具/旧缓存键污染→稳定ID与作用域缓存重构，双服务同名及跨企业反例。
- 选择摘要升级破坏已有重放→v1/v2分支，旧记录只允许原自动语义，双提交并发证明。
- HTTP授权延迟及撤销竞态→当前身份/出站实时检查；元数据缓存不缓存正向授权；后续IAM替换适配层。
- 第三方服务不可达→5秒有界目录和503失败关闭；生产企业MCP未验证要单列，受控服务器结果不可冒充生产接入。
- 大范围回归→保存3/5证据与基线失败集合，新增反例和全套相关检查；不靠删除测试或全局skip过关。

## Migration Plan

1. Gate0记录源码/契约指纹、真实迁移head、现有测试基线；验证动态调度、同名工具与DeepAgents文件/子任务边界。
2. 先追加兼容表、RLS、契约扩展和服务端保护，再部署运行端，最后部署前端；默认AGENT_CAPABILITY_SELECTION_ENABLED=false。
3. 独立环境打开开关，完成迁移从零/升级、六图入口、恢复、隔离、实际MCP和UI验收，再讨论上线开通。本change交付不自动启用生产MCP。
4. 回退开关只阻止新的显式选择受理；含显式选择的请求应明确拒绝，不能静默退为auto。已受理/在飞运行必须继续执行原计划约束直至终结；不能回滚到不认识快照的旧运行二进制后继续这些运行。
5. 保留新增数据和审计，优先前向修复；不以删表或重置共享库作回退。IAM接入是另一变更的授权与验收。
