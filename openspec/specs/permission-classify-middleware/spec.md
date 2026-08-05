# 权限预分类与审查中间件规范

## Purpose

在主模型推理前完成权限需求预分类和必要的权限审查。中间件从运行配置读取 `user_id`，对用户问题进行规则预分类；需要审查时，先在中间件层预取确定性上下文，再调用轻量权限审查 agent 产出结构化 `PermissionResult`，最后把结果注入系统提示词供主模型消费。
## Requirements
### Requirement: 规则预分类

中间件 SHALL 在 `abefore_agent` 中读取最新用户消息，并复用 `src/common/permission_rules.py` 中的 `classify_permission_need` 写入 `state["permission_need"]`。

#### Scenario: 知识问答无需审查

- **WHEN** 最新用户消息是“PM2.5是什么？”这类知识问答或寒暄
- **THEN** 中间件 SHALL 写入 `permission_need = "no_check"`
- **AND** 中间件 SHALL 不加载 MCP，不调用权限审查 agent

#### Scenario: 数据查询需要审查

- **WHEN** 最新用户消息包含数据查询意图，例如时间、行政区和指标
- **THEN** 中间件 SHALL 写入 `permission_need = "need_check"` 或 `"uncertain"`
- **AND** 中间件 SHALL 继续执行权限审查流程

### Requirement: 用户身份提取与缺失降级

中间件 SHALL 优先使用 `state["user_id"]`，缺失时从 `config["configurable"]["user_id"]` 读取用户身份。

#### Scenario: 用户身份存在

- **WHEN** `state` 或运行配置中存在 `user_id`
- **THEN** 中间件 SHALL 将该值写入 `state["user_id"]`

#### Scenario: 用户身份缺失

- **WHEN** 权限需求不是 `no_check` 但无法获取 `user_id`
- **THEN** 中间件 SHALL 降级为 `permission_need = "no_check"`
- **AND** 中间件 SHALL 不调用权限审查 agent

### Requirement: 确定性上下文预取

当 `permission_need` 为 `need_check` 或 `uncertain` 且存在 `user_id` 时，中间件 SHALL 在运行确定性规则引擎前预取确定性上下文，并使用 slots 中的统一区域请求列表逐项解析行政区。

#### Scenario: 预取上下文成功且用户指定单个行政区

- **WHEN** MCP 工具可用且 slots 中 `region_mode = "single"` 且 `regions` 包含一个有效区域项
- **THEN** 中间件 SHALL 直接获取当前北京时间
- **AND** 中间件 SHALL 调用 MCP `get_user_profile(userId)`
- **AND** 中间件 SHALL 使用该区域项的 `text`、`level_hint`、`city_hint` 和 `province_hint` 调用 MCP `resolve_region_scope`
- **AND** 中间件 SHALL 将 `user_profile`、解析后的单个 `requested_region`、slots 和当前时间传入确定性规则引擎

#### Scenario: 预取上下文成功且用户指定多个行政区

- **WHEN** MCP 工具可用且 slots 中 `region_mode = "multi_explicit"` 且 `regions` 包含多个区域项
- **THEN** 中间件 SHALL 直接获取当前北京时间
- **AND** 中间件 SHALL 调用 MCP `get_user_profile(userId)`
- **AND** 中间件 SHALL 对 `regions` 中每个区域项分别调用 MCP `resolve_region_scope`
- **AND** 中间件 SHALL NOT 只解析第一个区域项
- **AND** 中间件 SHALL 为每个解析后的标准区域分别构造权限事实

#### Scenario: 预取上下文成功且用户未指定行政区

- **WHEN** MCP 工具可用且 slots 中 `region_mode = "auto_fill"` 且 `regions` 为空
- **THEN** 中间件 SHALL 直接获取当前北京时间
- **AND** 中间件 SHALL 调用 MCP `get_user_profile(userId)`
- **AND** 中间件 SHALL NOT 调用 `resolve_region_scope`
- **AND** 中间件 SHALL 使用 `user_profile.bound_region` 构造来源为 `auto_fill` 的区域项
- **AND** 中间件 SHALL 将自动补全区域、slots 和当前时间传入确定性规则引擎或等价权限事实构造流程

#### Scenario: 部分预取失败

- **WHEN** MCP 工具缺失、调用失败或返回无法解析
- **THEN** 中间件 SHALL 记录日志
- **AND** 中间件 SHALL 将失败的区域项写入逐项权限结果
- **AND** 中间件 SHALL 对仍可解析的区域项继续执行权限校验
- **AND** 中间件 SHALL 按 fail-closed 配置或权限事实不足策略处理全部失败场景

### Requirement: 权限审查 agent 任务描述

中间件 SHALL 将预取结果序列化为 JSON，并放入权限审查 agent 的 human message 的 `<prefetched_context>` 块中。

#### Scenario: 注入预取上下文

- **WHEN** 中间件调用权限审查 agent
- **THEN** human message SHALL 包含用户原始问题和 `userId`
- **AND** human message SHALL 包含 `<prefetched_context>` 块
- **AND** `<prefetched_context>` SHALL 包含 `beijing_time`、`user_profile`、`requested_region` 和 `permission_region_context`

#### Scenario: agent 不重复调用已下沉工具

- **WHEN** 权限审查 agent 收到 `<prefetched_context>`
- **THEN** agent SHALL 直接读取预置数据
- **AND** agent SHALL NOT 调用 `get_beijing_time` 或 `permission_region_context_tool`
- **AND** 当 MCP 结果已提供时，agent SHOULD NOT 重复调用 `get_user_profile` 或 `resolve_region_scope`

### Requirement: 权限审查 agent 配置与复用

中间件 SHALL 按当前 MCP 工具签名懒创建或重建权限审查 agent。

#### Scenario: 首次需要审查

- **WHEN** `permission_need` 为 `need_check` 或 `uncertain` 且尚未创建 agent
- **THEN** 中间件 SHALL 使用 `ModelRegistry.mimo_v2_5_pro`、`PERMISSION_CHECK_SYSTEM_PROMPT`、`PermissionResult` 和 `CodeInterpreterMiddleware(ptc=[])` 创建 agent
- **AND** agent 工具列表 SHALL 包含当前 MCP 动态工具
- **AND** agent 工具列表 SHALL NOT 包含 `get_beijing_time` 和 `permission_region_context_tool`

#### Scenario: MCP 工具签名变化

- **WHEN** 当前 MCP 工具名集合与缓存签名不同
- **THEN** 中间件 SHALL 重建权限审查 agent

### Requirement: 权限结果写入与提示词注入

中间件 SHALL 从确定性规则引擎返回值中提取 `PermissionResult`，并把聚合后的 `PermissionResult` 序列化写入 `state["permission_result"]`。当结果包含可执行区域、可执行时间截断或自动补全区域时，中间件 SHALL 额外写入 `state["permission_query_overrides"]`，供后续 skill 查找、参数抽取和工具调用使用。

#### Scenario: 结构化结果存在

- **WHEN** 规则引擎返回 `PermissionResult` 或等价 dict
- **THEN** 中间件 SHALL 写入 `state["permission_result"]`
- **AND** 主模型调用前 SHALL 注入 `<permission_context>` 到 system prompt

#### Scenario: 自动补全区域继续查询

- **WHEN** 用户未指定行政区且中间件使用 `bound_region` 自动补全区域
- **THEN** 中间件 SHALL 写入 `state["permission_result"].auto_filled = true`
- **AND** 中间件 SHALL 写入 `state["permission_result"].region_results`
- **AND** 中间件 SHALL 写入 `state["permission_result"].correction_text`
- **AND** 中间件 SHALL 写入 `state["permission_query_overrides"].regions`
- **AND** 主模型 SHALL 继续执行查询
- **AND** 后续 skill 查找、参数抽取和工具调用 SHALL 使用 `permission_query_overrides.regions` 作为查询行政区列表

#### Scenario: 多区域逐项权限校验继续查询

- **WHEN** slots 中 `regions` 包含多个区域项且至少一个区域项权限校验后可查询
- **THEN** 中间件 SHALL 写入 `state["permission_result"].region_results`
- **AND** 每个 `region_results` 项 SHALL 包含请求区域、解析区域、权限状态和实际查询区域
- **AND** 中间件 SHALL 写入 `state["permission_query_overrides"].regions`
- **AND** 主模型 SHALL 继续执行允许区域的数据查询
- **AND** 后续 skill 查找、参数抽取和工具调用 SHALL NOT 只使用第一个区域

#### Scenario: 行政区修正继续查询

- **WHEN** 任一区域项规则引擎返回 `permitted = false` 且 `fix_strategy = "replace_region"`
- **THEN** 中间件 SHALL 在对应 `region_results` 项中记录原请求区域和替换后的查询区域
- **AND** 中间件 SHALL 将替换后的查询区域写入 `state["permission_query_overrides"].regions`
- **AND** 主模型 SHALL 继续执行查询
- **AND** 后续 skill 查找、参数抽取和工具调用 SHALL 使用 `permission_query_overrides.regions` 替代原问题中的越权行政区

#### Scenario: 时间截断继续查询

- **WHEN** 任一区域项规则引擎返回 `permitted = false` 且 `fix_strategy = "truncate_time"`
- **THEN** 中间件 SHALL 在对应 `region_results` 项中记录合法时间范围
- **AND** 若所有可查询区域的合法时间范围一致，中间件 SHALL 写入 `state["permission_query_overrides"].time_span`
- **AND** 若合法时间范围不一致，中间件 SHALL 在逐项结果中保留各自合法时间范围，不得静默扩大为全局时间范围

#### Scenario: 硬拒绝短路

- **WHEN** 所有区域项均无法解析、无权访问或无可用修正策略
- **THEN** 中间件 SHALL 写入整体拒绝的 `permission_result`
- **AND** 中间件 SHALL NOT 写入可执行的 `permission_query_overrides.regions`
- **AND** 主模型 SHALL NOT 继续执行 skill 查找或工具调用

#### Scenario: 无需权限上下文

- **WHEN** `permission_need = "no_check"` 或不存在 `permission_result`
- **THEN** 中间件 SHALL NOT 注入 `<permission_context>`

#### Scenario: 权限上下文包含审查细节

- **WHEN** 中间件注入 `<permission_context>`
- **THEN** `<permission_context>` SHALL 包含完整 `permission_result`
- **AND** `<permission_context>` SHALL 包含 `permission_result.region_results`
- **AND** `<permission_context>` SHALL 包含 `permission_result.matched_rules`
- **AND** `<permission_context>` SHALL 包含 `permission_result.debug_context`
- **AND** `<permission_context>` SHALL 包含 `permission_query_overrides`（如果存在）
- **AND** `<permission_context>` SHALL 明确指示主流程在 `permission_query_overrides.regions` 存在时必须使用完整区域列表

### Requirement: 失败降级

权限审查失败 SHALL 不阻断主模型推理。

#### Scenario: 分类失败

- **WHEN** `classify_permission_need` 抛出异常
- **THEN** 中间件 SHALL 写入 `permission_need = "no_check"`

#### Scenario: agent 失败

- **WHEN** agent 构建失败、调用异常或未返回结构化结果
- **THEN** 中间件 SHALL 降级为 `permission_need = "no_check"`
- **AND** 中间件 SHALL 记录异常日志

### Requirement: 中间件区域请求规划

权限预分类与审查中间件 SHALL 在行政区解析前，根据原始用户问题和 slots 结果生成区域请求计划，表达单区域、多区域、集合区域或无区域自动补全形态。

#### Scenario: 多行政区问题生成多区域计划

- **WHEN** 最新用户消息包含多个并列请求行政区，例如 `2026年3月郑州市和平顶山市的PM2.5月均值分别是多少？`
- **THEN** 中间件 SHALL 生成 `mode = "multi_explicit"` 的区域请求计划
- **AND** 区域请求计划 SHALL 包含 `郑州市` 和 `平顶山市` 两个请求项
- **AND** 中间件 SHALL 分别调用行政区解析能力解析每个请求项

#### Scenario: 集合行政区问题生成展开计划

- **WHEN** 最新用户消息包含下辖区县或各区县集合语义，例如 `2026年3月郑州市下的区县的PM2.5月均值分别是多少？`
- **THEN** 中间件 SHALL 生成 `mode = "collection"` 的区域请求计划
- **AND** 区域请求计划 SHALL 包含父级行政区 `郑州市`
- **AND** 区域请求计划 SHALL 包含目标下级层级 `district`
- **AND** 中间件 SHALL 调用下级行政区展开能力获取区域列表

#### Scenario: 单行政区问题保持单区域计划

- **WHEN** 最新用户消息只包含一个明确行政区，例如 `2026年3月郑州市PM2.5月均值是多少？`
- **THEN** 中间件 SHALL 生成 `mode = "single"` 的区域请求计划
- **AND** 中间件 SHALL 继续执行既有单区域权限上下文预取流程

#### Scenario: 无行政区问题保持自动补全计划

- **WHEN** 最新用户消息未指定行政区，例如 `昨天空气质量如何？`
- **THEN** 中间件 SHALL 生成 `mode = "auto_fill"` 的区域请求计划
- **AND** 中间件 SHALL 继续执行既有用户默认关联区域自动补全流程

### Requirement: 中间件批量权限校验

权限预分类与审查中间件 SHALL 对区域请求计划中的每个实际区域项独立构造权限事实并执行确定性权限校验。

#### Scenario: 显式多区域逐项校验

- **WHEN** 区域请求计划包含 `郑州市` 和 `平顶山市`
- **THEN** 中间件 SHALL 为 `郑州市` 构造一份 `PermissionFacts` 并调用规则引擎
- **AND** 中间件 SHALL 为 `平顶山市` 构造一份 `PermissionFacts` 并调用规则引擎
- **AND** 中间件 SHALL NOT 只校验其中一个城市

#### Scenario: 展开后的区县逐项校验

- **WHEN** 区域请求计划展开出多个下辖区县
- **THEN** 中间件 SHALL 为每个区县分别构造 `PermissionFacts`
- **AND** 中间件 SHALL 为每个区县分别调用规则引擎
- **AND** 中间件 SHALL NOT 使用父级城市权限结果替代区县逐项权限结果

### Requirement: 中间件聚合权限上下文

权限预分类与审查中间件 SHALL 将逐项权限结果聚合为完整 `state["permission_result"]` 和 `state["permission_query_overrides"]`，供主流程继续查询。

#### Scenario: 多区域覆盖参数写入

- **WHEN** 多个区域项存在一个或多个允许查询区域
- **THEN** 中间件 SHALL 写入 `state["permission_query_overrides"].regions`
- **AND** `state["permission_query_overrides"].regions` SHALL 包含实际允许查询的区域列表
- **AND** 中间件 SHALL 写入 `state["permission_result"].allowed_regions`
- **AND** 主流程 SHALL 使用 `regions` 而不是只使用单个 `region`

#### Scenario: 聚合结果包含订正细节

- **WHEN** 一个或多个区域项发生行政区替换、时间截断或拒绝
- **THEN** 中间件 SHALL 将逐项订正写入 `state["permission_result"].region_corrections`
- **AND** 中间件 SHALL 将无法查询的区域写入 `state["permission_result"].rejected_regions`
- **AND** 中间件 SHALL 在 `state["permission_result"].correction_text` 中汇总说明自动拆分、展开和权限订正

#### Scenario: 全部区域不可查询时硬拒绝

- **WHEN** 聚合后不存在任何允许查询区域
- **THEN** 中间件 SHALL 写入硬拒绝权限结果
- **AND** 中间件 SHALL NOT 写入可执行的 `permission_query_overrides.regions`
- **AND** 主流程 SHALL NOT 继续执行数据查询

### Requirement: 中间件向主流程注入多区域指令

权限预分类与审查中间件 SHALL 在 `<permission_context>` 中注入多区域权限上下文，并明确要求后续流程使用批量区域覆盖参数。

#### Scenario: 多区域权限上下文注入

- **WHEN** `state["permission_query_overrides"].regions` 存在
- **THEN** `<permission_context>` SHALL 包含 `permission_query_overrides.regions`
- **AND** `<permission_context>` SHALL 包含 `permission_result.region_mode`
- **AND** `<permission_context>` SHALL 明确指示主流程必须使用 `regions` 作为查询行政区列表
- **AND** `<permission_context>` SHALL 明确指示主流程不得只取第一个区域

### Requirement: 权限上下文保留细分修正文案

权限预分类与审查中间件 SHALL 将规则引擎生成的细分 `correction_text` 原样写入权限上下文，并要求后续主流程在最终回复中体现该文案。

#### Scenario: 注入行政区矩阵文案

- **WHEN** 规则引擎返回包含行政区矩阵化说明的 `PermissionResult.correction_text`
- **THEN** 中间件 SHALL 将该文案写入 `<permission_context>`
- **AND** 中间件 SHALL NOT 将其替换为通用“因权限限制已调整查询区域”文案
- **AND** 主模型系统提示 SHALL 要求最终回复包含该修正说明

#### Scenario: 注入时间截断文案

- **WHEN** 规则引擎返回 `fix_strategy = "truncate_time"` 且 `correction_text` 包含合法时间范围
- **THEN** 中间件 SHALL 将 `permission_query_overrides.time_span` 和该 `correction_text` 一并注入权限上下文
- **AND** 主流程 SHALL 使用截断后的时间范围继续查询

#### Scenario: 硬拒绝返回细分文案

- **WHEN** 权限结果为硬拒绝且 `PermissionResult.correction_text` 非空
- **THEN** 中间件的拒绝回复 SHALL 使用该 `correction_text`
- **AND** 中间件 SHALL NOT 回退到默认“因数据权限限制，您无权访问该数据。”

### Requirement: 多区域聚合保留逐项修正文案

当多区域或集合区域查询产生多个逐项权限结果时，中间件 SHALL 保留每个区域项的细分修正文案，并在聚合文案中包含必要的逐项说明。

#### Scenario: 多区域部分替换

- **WHEN** 多区域查询中某个区域项发生行政区替换
- **THEN** 对应 `region_results` 项 SHALL 保留该区域项的细分 `correction_text`
- **AND** 聚合后的 `PermissionResult.correction_text` SHALL 包含该替换说明
- **AND** 聚合文案 SHALL NOT 只保留“部分区域未查询”这类泛化说明

#### Scenario: 集合区域部分拒绝

- **WHEN** 集合区域展开后的部分子区域无法查询
- **THEN** 对应 `region_results` 项 SHALL 保留拒绝或修正文案
- **AND** 允许区域 SHALL 继续写入 `permission_query_overrides.regions`
- **AND** 聚合文案 SHALL 明确哪些区域未查询或被替换

### Requirement: 站点权限槽位抽取

系统 SHALL 在权限槽位抽取中识别站点类查询，并输出站点权限审查所需的结构化槽位，包括查询对象类型、站点名称列表和站点类型列表；行政区 SHALL 继续使用现有 `regions`、`region_mode` 和 `PermissionRegionRequest` 结构表达。

#### Scenario: 抽取区域站点类型查询

- **WHEN** 用户问题为“帮我查询一下郑州市下国控站的 AQI 排名”
- **THEN** 权限槽位 SHALL 标记该问题为站点数据查询
- **AND** 权限槽位 SHALL 包含行政区“郑州市”
- **AND** 权限槽位 SHALL 包含站点类型“国控站”
- **AND** 权限槽位 SHALL NOT 将“国控站”作为权限类型

#### Scenario: 抽取具体站点查询

- **WHEN** 用户问题为“郑州市水利监测站今天空气质量”
- **THEN** 权限槽位 SHALL 标记该问题为站点数据查询
- **AND** 权限槽位 SHALL 包含行政区“郑州市”
- **AND** 权限槽位 SHALL 包含站点名称“水利监测站”

#### Scenario: 抽取多区域站点查询

- **WHEN** 用户问题为“郑州市和洛阳市下国控站 AQI 排名”
- **THEN** 权限槽位 SHALL 保留“郑州市”和“洛阳市”两个查询区域
- **AND** 权限槽位 SHALL 包含站点类型“国控站”

### Requirement: 站点归属事实解析

系统 SHALL 在构造 PermissionFacts 前，对站点类查询复用现有 `RegionRequestPlan` 的最终区域项调用站点归属解析工具，获取每个候选站点的标准站点信息和所属行政区信息。

#### Scenario: 单区域站点列表解析

- **WHEN** 权限槽位包含区域“郑州市”、站点类型“国控站”且不包含具体站点名称
- **THEN** 系统 SHALL 调用站点归属解析工具一次
- **AND** 工具入参 SHALL 包含 `region = "郑州市"` 和 `station_types = ["国控站"]`
- **AND** 系统 SHALL 将返回的站点列表写入权限事实

#### Scenario: 具体站点解析

- **WHEN** 权限槽位包含区域“郑州市”和站点名称“水利监测站”
- **THEN** 系统 SHALL 调用站点归属解析工具
- **AND** 工具入参 SHALL 包含 `region = "郑州市"` 和 `station_names = ["水利监测站"]`
- **AND** 系统 SHALL 将返回站点的所属行政区写入权限事实

#### Scenario: 多区域拆分解析

- **WHEN** 权限槽位包含多个区域
- **THEN** 系统 SHALL 先构造 `RegionRequestPlan`
- **AND** 系统 SHALL 对计划中的每个最终查询区域分别调用站点归属解析工具
- **AND** 每次工具调用 SHALL 只传入一个区域
- **AND** 系统 SHALL 合并所有区域返回的站点事实

#### Scenario: 集合区域站点解析

- **WHEN** 权限槽位表示父级下辖子区域查询
- **THEN** 系统 SHALL 先按现有集合区域逻辑展开下辖子区域
- **AND** 系统 SHALL 对展开后的每个子区域分别调用站点归属解析工具
- **AND** 系统 SHALL NOT 在无法展开子区域时退回父级汇总站点查询

#### Scenario: 自动补全区域站点解析

- **WHEN** 用户问题未指定行政区且站点查询需要权限审查
- **THEN** 系统 SHALL 使用用户绑定行政区构造可信默认查询区域
- **AND** 系统 SHALL 使用该自动补全区域调用站点归属解析工具
- **AND** 权限结果 SHALL 保留自动补全语义

#### Scenario: 站点归属缺失

- **WHEN** 站点归属解析工具返回某个站点但该站点缺少有效行政区归属
- **THEN** 系统 SHALL 将该站点标记为不可用
- **AND** 系统 SHALL NOT 使用用户问题中的查询区域替代该站点归属行政区
- **AND** 系统 SHALL NOT 使用自动补全区域或来源区域项替代该站点归属行政区

### Requirement: 站点权限上下文注入

系统 SHALL 将站点权限审查结果和可执行站点覆盖参数注入主流程，确保后续 Skill 查询只能使用权限允许的站点集合。

#### Scenario: 注入可访问站点集合

- **WHEN** 权限审查产生可访问站点集合
- **THEN** `permission_query_overrides` SHALL 包含该站点集合
- **AND** 注入给主模型的权限上下文 SHALL 明确要求后续查询使用该站点集合

#### Scenario: 站点部分越权提示

- **WHEN** 多站点查询中仅部分站点可访问
- **THEN** 权限上下文 SHALL 包含站点裁剪修正文案
- **AND** 最终回复 SHALL 告知用户仅展示可访问站点数据

#### Scenario: 站点数据不可用短路

- **WHEN** 站点查询中所有候选站点均缺少有效行政区归属
- **THEN** 系统 SHALL 拒绝继续执行站点数据查询
- **AND** 最终回复 SHALL 告知用户”该数据暂不可用”

### Requirement: 权限上下文注入契约
`PermissionClassifyMiddleware` SHALL 在 `<permission_context>` 注入说明中明确：发生权限修正、拒绝、截断、过滤、自动补全或部分区域处理时，`permission_result.correction_text` 是最终答复中用户可见权限披露的主要依据，`permission_result.reason` 是解释原请求为什么被拦截或修正的辅助依据。最终答复 MUST 在这些需要披露的场景中，以自然语言向用户说明发生了什么变化，以及实际查询范围是什么。

主流程 MUST NOT 要求按字段名、JSON 或内部权限对象形式输出 `reason` 与 `correction_text`；`reason` 只用于补充生成用户可理解的原因说明。

#### Scenario: 权限修正后最终答复包含修正说明
- **WHEN** 权限中间件修正了用户查询区域（如”洛阳市”→”郑州市”），主流程完成查询后输出最终答复
- **THEN** 最终答复 MUST 基于 `correction_text` 包含自然语言的权限修正说明，解释为什么查询的是修正后的区域
- **AND** 最终答复 MAY 结合 `reason` 补充用户可理解的原因说明

#### Scenario: 替换场景下说明实际查询区域
- **WHEN** 权限结果为区域替换
- **THEN** 最终答复 MUST 说明实际查询的区域范围

#### Scenario: 部分拒绝场景下说明可用和不可用范围
- **WHEN** 权限结果为多区域部分拒绝
- **THEN** 最终答复 MUST 说明哪些区域可用、哪些区域不可用

#### Scenario: 自动补全场景下说明补全内容
- **WHEN** 权限结果为时间或区域自动补全
- **THEN** 最终答复 MUST 说明实际查询的时间或区域范围

#### Scenario: 普通允许查询不要求权限披露
- **WHEN** 权限结果为允许访问且没有修正、拒绝、截断、过滤、自动补全或部分区域处理
- **THEN** `<permission_context>` MUST NOT 要求最终答复解释原请求为什么被拦截或修正
- **AND** 最终答复 MAY 直接回答用户业务问题

### Requirement: 权限上下文注入不承担通用内部信息清理
`<permission_context>` 注入说明 MUST NOT 承担通用内部信息清理职责；通用内部字段、JSON、对象结构等最终输出清理 SHALL 由 `FinalOutputCleanupMiddleware` 承担。权限上下文仅需说明 `reason` 与 `correction_text` 的生成用途，不应重复主流程通用”不暴露内部”约束。

#### Scenario: 权限上下文不包含通用内部信息禁止
- **WHEN** 读取 `<permission_context>` 注入文本
- **THEN** MUST NOT 包含通用的”严禁泄露内部信息””不得输出工具、SQL、路径、源码”等最终输出清理约束
