## ADDED Requirements

### Requirement: 三级行政区分别提取

`PermissionExtractedSlots` SHALL 分别提取 `province`、`city`、`district` 三个字段，替代原有的 `region_text`。

#### Scenario: 提取完整路径

- **WHEN** 用户问题为"郑州市金水区的空气质量"
- **THEN** `province` SHALL 为 null（未提及）
- **AND** `city` SHALL 为 "郑州市"
- **AND** `district` SHALL 为 "金水区"

#### Scenario: 只提取省份

- **WHEN** 用户问题为"浙江省的PM2.5"
- **THEN** `province` SHALL 为 "浙江省"
- **AND** `city` SHALL 为 null
- **AND** `district` SHALL 为 null

#### Scenario: 只提取区县

- **WHEN** 用户问题为"西湖区"
- **THEN** `province` SHALL 为 null
- **AND** `city` SHALL 为 null
- **AND** `district` SHALL 为 "西湖区"

#### Scenario: 跨市查询

- **WHEN** 用户问题为"郑州市金水区的空气质量"
- **AND** 用户画像中的城市为"商丘市"
- **THEN** `city` SHALL 为 "郑州市"（用户提到的城市）
- **AND** `district` SHALL 为 "金水区"
- **AND** 调用 `resolve_region_scope` 时 `city` 参数 SHALL 为 "郑州市"

## MODIFIED Requirements

### Requirement: 消歧参数来源

消歧参数 `city`/`province` SHALL 来自 LLM 提取的三级字段，而非用户画像。

#### Scenario: 使用 LLM 提取的城市消歧

- **WHEN** LLM 提取到 `city="郑州市"`
- **THEN** 调用 `resolve_region_scope` 时 `city` SHALL 为 "郑州市"
- **AND** 调用 `resolve_region_scope` 时 `province` SHALL 为 LLM 提取的 `province` 或用户画像中的省份

#### Scenario: LLM 未提取城市时使用用户画像

- **WHEN** LLM 未提取到 `city`（即 `city` 为 null）
- **THEN** 调用 `resolve_region_scope` 时 `city` SHALL 为用户画像中的城市名称
