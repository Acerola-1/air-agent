## ADDED Requirements

### Requirement: 权限结果使用矩阵化修正文案
`PermissionResult.correction_text` SHALL 使用确定性模板表达权限修正原因和修正结果，并按照用户层级、越权场景和时间粒度生成细分文案。

#### Scenario: 省级用户区县上卷至地市
- **WHEN** 省级用户请求区县明细数据且规则引擎将查询区域上卷至所属地市
- **THEN** `PermissionResult.correction_text` SHALL 说明省级用户无法查看区县明细数据
- **AND** 文案 SHALL 说明已展示该区县所属地市的汇总数据

#### Scenario: 省级用户外省地市或区县上卷至省份
- **WHEN** 省级用户请求外省地市或外省区县数据且规则引擎将查询区域上卷至所属省份
- **THEN** `PermissionResult.correction_text` SHALL 说明无法查看外省地市或区县数据
- **AND** 文案 SHALL 说明已展示请求区域所属省份的省级汇总数据
- **AND** 文案 SHALL 提示如需查看明细可查询最近 7 天数据

#### Scenario: 地市用户本省其他区县替换
- **WHEN** 地市用户请求本省其他地市的区县数据且规则引擎替换为本市常访问区县或关联区域
- **THEN** `PermissionResult.correction_text` SHALL 说明地市用户无法查看其他市区县数据
- **AND** 文案 SHALL 说明已展示用户所在城市的可访问区域数据

#### Scenario: 地市用户外省替换
- **WHEN** 地市用户请求外省任意层级数据且规则引擎替换为用户关联地市
- **THEN** `PermissionResult.correction_text` SHALL 说明无法查看外省数据
- **AND** 文案 SHALL 说明已展示用户所在城市数据

#### Scenario: 区县用户省级汇总替换
- **WHEN** 区县用户请求省级汇总数据且规则引擎替换为用户关联区县
- **THEN** `PermissionResult.correction_text` SHALL 说明区县用户无法查看省级汇总数据
- **AND** 文案 SHALL 说明已展示用户所在区县明细数据
- **AND** 文案 SHALL 提示如需查看省级汇总可查询最近 7 天数据

#### Scenario: 区县用户外市替换
- **WHEN** 区县用户请求外市任意层级数据且规则引擎替换为用户关联区县
- **THEN** `PermissionResult.correction_text` SHALL 说明无法查看外市数据
- **AND** 文案 SHALL 说明已展示用户所在区县数据

### Requirement: 月年粒度越权使用专门文案
当月粒度或年粒度查询发生行政区替换时，`PermissionResult.correction_text` SHALL 使用月年统计专门模板，明确月年统计数据不支持跨行政区查看。

#### Scenario: 月粒度越权替换
- **WHEN** `time_granularity` 为 `month` 或 `month_count` 且规则引擎返回 `fix_strategy = "replace_region"`
- **THEN** `PermissionResult.correction_text` SHALL 包含“月/年统计数据不支持跨行政区查看”或等价说明
- **AND** 文案 SHALL 说明已切换至有权限的替代区域数据

#### Scenario: 年粒度越权替换
- **WHEN** `time_granularity` 为 `year` 或 `year_count` 且规则引擎返回 `fix_strategy = "replace_region"`
- **THEN** `PermissionResult.correction_text` SHALL 包含“月/年统计数据不支持跨行政区查看”或等价说明
- **AND** 文案 SHALL 说明已切换至有权限的替代区域数据

### Requirement: 时间截断使用豁免窗口文案
当规则引擎返回 `fix_strategy = "truncate_time"` 时，`PermissionResult.correction_text` SHALL 说明原查询日期范围部分超出豁免窗口，并说明已截断至合法时间范围。

#### Scenario: 时间段部分超出豁免窗口
- **WHEN** 用户请求的时间段与豁免窗口部分相交
- **THEN** `PermissionResult.fix_strategy` SHALL 为 `truncate_time`
- **AND** `PermissionResult.correction_text` SHALL 说明查询日期范围部分超出豁免窗口
- **AND** 文案 SHALL 包含 `legal_time_span` 表示的合法时间范围
- **AND** 文案 SHALL NOT 表示行政区被替换

### Requirement: 站点修正文案保持专用模板
站点权限裁剪和站点归属缺失 SHALL 继续使用站点专用文案，不得被行政区通用模板覆盖。

#### Scenario: 站点部分越权
- **WHEN** 站点权限审查返回 `fix_strategy = "filter_stations"`
- **THEN** `PermissionResult.correction_text` SHALL 包含“仅展示您可访问的”站点数据提示
- **AND** 文案 SHALL 提示如需查看其他区域站点可查询最近 7 天数据

#### Scenario: 站点归属缺失
- **WHEN** 站点归属缺失或无效导致数据不可用
- **THEN** `PermissionResult.correction_text` SHALL 包含“该数据暂不可用”
