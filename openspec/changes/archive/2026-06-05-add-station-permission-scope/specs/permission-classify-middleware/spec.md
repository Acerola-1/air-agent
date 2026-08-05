## ADDED Requirements

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
- **AND** 最终回复 SHALL 告知用户“该数据暂不可用”
