## ADDED Requirements

### Requirement: 显式多行政区自动拆分

系统 SHALL 识别用户问题中显式列出的多个行政区，将其拆分为多个区域请求项，并逐项完成行政区解析和权限校验。

#### Scenario: 并列城市查询自动拆分

- **WHEN** 用户提问 `2026年3月郑州市和平顶山市的PM2.5月均值分别是多少？`
- **THEN** 系统 SHALL 识别请求区域为 `郑州市` 和 `平顶山市`
- **AND** 系统 SHALL 分别解析两个城市的标准行政区信息
- **AND** 系统 SHALL 分别对两个城市执行权限校验
- **AND** 系统 SHALL 在权限上下文中记录 `region_mode = "multi_explicit"`
- **AND** 系统 SHALL 在修正文案中告知用户已自动拆分查询区域

#### Scenario: 多行政区对比查询自动拆分

- **WHEN** 用户提问 `2026年3月郑州市、平顶山市PM2.5月均值对比一下`
- **THEN** 系统 SHALL 识别该问题包含多个并列行政区
- **AND** 系统 SHALL 生成包含两个区域项的查询覆盖参数
- **AND** 系统 SHALL NOT 只按第一个行政区继续查询

### Requirement: 集合行政区自动展开

系统 SHALL 识别下辖区县、各区县、所有下级行政区等集合行政区查询，将父级行政区展开为下级区域列表，并逐项完成权限校验。

#### Scenario: 城市下辖区县查询自动展开

- **WHEN** 用户提问 `2026年3月郑州市下的区县的PM2.5月均值分别是多少？`
- **THEN** 系统 SHALL 识别父级行政区为 `郑州市`
- **AND** 系统 SHALL 将查询区域展开为郑州市下辖区县列表
- **AND** 系统 SHALL 分别对每个下辖区县执行权限校验
- **AND** 系统 SHALL 在权限上下文中记录 `region_mode = "collection"`
- **AND** 系统 SHALL 在修正文案中告知用户已自动展开郑州市下辖区县
- **AND** 系统 SHALL NOT 将该问题误当作郑州市本级汇总查询

#### Scenario: 各区县查询自动展开

- **WHEN** 用户提问 `2026年3月郑州市各区县空气质量分别怎么样？`
- **THEN** 系统 SHALL 将 `各区县` 识别为集合行政区语义
- **AND** 系统 SHALL 生成下辖区县区域列表
- **AND** 系统 SHALL 将实际允许查询的区县列表写入查询覆盖参数

### Requirement: 多区域权限结果聚合

系统 SHALL 聚合多个区域项的逐项权限结果，生成完整的允许区域、订正区域、拒绝区域和用户可见修正文案。

#### Scenario: 多区域全部允许

- **WHEN** 多个请求区域逐项权限校验均允许
- **THEN** 系统 SHALL 将所有请求区域写入 `allowed_regions`
- **AND** 系统 SHALL 将所有请求区域写入 `permission_query_overrides.regions`
- **AND** 系统 SHALL 继续执行后续查询

#### Scenario: 多区域部分需要行政区替换

- **WHEN** 多个请求区域中存在越权区域，且逐项规则返回 `fix_strategy = "replace_region"`
- **THEN** 系统 SHALL 将替换关系写入 `region_corrections`
- **AND** 系统 SHALL 将替换后的允许区域写入 `permission_query_overrides.regions`
- **AND** 系统 SHALL 在修正文案中说明被替换的原区域和替换后的区域
- **AND** 系统 SHALL 继续执行后续查询

#### Scenario: 多区域部分硬拒绝

- **WHEN** 多个请求区域中部分区域无法解析或无可用修正策略
- **THEN** 系统 SHALL 将这些区域写入 `rejected_regions`
- **AND** 系统 SHALL 对仍可查询的区域继续生成查询覆盖参数
- **AND** 系统 SHALL 在修正文案中说明部分区域未查询的原因

#### Scenario: 多区域全部硬拒绝

- **WHEN** 所有请求区域均无法解析或无可用修正策略
- **THEN** 系统 SHALL 返回整体拒绝结果
- **AND** 系统 SHALL NOT 执行后续数据查询

### Requirement: 支持形态保持兼容

系统 SHALL 保留当前已支持的单行政区查询和无行政区自动补全行为。

#### Scenario: 单城市查询继续单区域流程

- **WHEN** 用户提问 `2026年3月郑州市PM2.5月均值是多少？`
- **THEN** 系统 SHALL 按既有单行政区权限校验流程处理
- **AND** 系统 SHALL NOT 生成多区域查询覆盖参数

#### Scenario: 无行政区查询继续自动补全

- **WHEN** 用户提问 `昨天空气质量如何？`
- **THEN** 系统 SHALL 按既有逻辑使用用户默认关联区域自动补全查询区域
- **AND** 系统 SHALL 写入自动补全修正文案和查询覆盖参数
