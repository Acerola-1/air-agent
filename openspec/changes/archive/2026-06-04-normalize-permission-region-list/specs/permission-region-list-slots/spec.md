## ADDED Requirements

### Requirement: 权限 slots 使用统一区域请求列表

权限抽取结果 SHALL 使用 `regions` 列表表达用户请求的行政区语义，单区域、多区域、无区域自动补全和集合/下钻查询都 SHALL 使用该列表模型，不得依赖单值 `province/city/district/target_level` 作为权威区域输入。

#### Scenario: 单区域问题输出单项列表

- **WHEN** 用户提问 `2026年3月郑州市PM2.5月均值是多少？`
- **THEN** slots SHALL 输出 `region_mode = "single"`
- **AND** slots SHALL 输出 `regions` 列表且只包含一项
- **AND** 该项 SHALL 包含 `text = "郑州市"` 和 `level_hint = "city"`

#### Scenario: 多区域问题输出多项列表

- **WHEN** 用户提问 `2026年3月郑州市和平顶山市的PM2.5月均值分别是多少？`
- **THEN** slots SHALL 输出 `region_mode = "multi_explicit"`
- **AND** slots SHALL 输出 `regions` 列表且包含 `郑州市` 和 `平顶山市`
- **AND** 每个区域项 SHALL 独立包含 `text`、`level_hint` 和 `source = "explicit"`
- **AND** slots SHALL NOT 将最终区域语义压缩为单个 `city`

#### Scenario: 无区域问题输出自动补全模式

- **WHEN** 用户提问 `昨天空气质量如何？`
- **THEN** slots SHALL 输出 `region_mode = "auto_fill"`
- **AND** slots SHALL 输出空 `regions` 列表
- **AND** 中间件 SHALL 使用用户可信画像补全默认区域

### Requirement: 区域请求项携带独立层级与消歧上下文

每个区域请求项 SHALL 独立携带解析所需的层级提示和上下文提示，使混合层级和重名区县可以逐项解析。

#### Scenario: 混合层级多区域

- **WHEN** 用户提问 `郑州市和金水区PM2.5分别是多少？`
- **THEN** slots SHALL 输出两个区域项
- **AND** `郑州市` 区域项 SHALL 包含 `level_hint = "city"`
- **AND** `金水区` 区域项 SHALL 包含 `level_hint = "district"`
- **AND** `金水区` 区域项 SHOULD 包含 `city_hint = "郑州市"` 或等价消歧上下文

#### Scenario: 同名区县上下文

- **WHEN** 用户提问 `郑州市金水区和平顶山市卫东区空气质量分别怎么样？`
- **THEN** slots SHALL 输出两个区县级区域项
- **AND** 每个区县项 SHALL 独立包含对应 `city_hint`
- **AND** 中间件 SHALL 使用每项上下文分别调用行政区解析

### Requirement: 区域来源用于解释和审计

每个区域请求项 SHALL 包含 `source` 字段，用于说明该区域是用户显式输入、系统自动补全、集合父级、集合展开子项或权限替换结果。

#### Scenario: 显式区域来源

- **WHEN** 用户明确提到 `郑州市`
- **THEN** 对应区域项 SHALL 包含 `source = "explicit"`

#### Scenario: 自动补全来源

- **WHEN** 用户未指定行政区且系统使用用户绑定区域补全
- **THEN** 补全区域项 SHALL 包含 `source = "auto_fill"`
- **AND** 用户可见修正文案 SHALL 说明该区域来自自动补全

### Requirement: 集合行政区表达父级与目标下级

集合/下钻行政区查询 SHALL 在区域请求项中表达父级区域和目标下级层级，避免把父级区域误作为最终查询区域。

#### Scenario: 城市下辖区县

- **WHEN** 用户提问 `2026年3月郑州市下的区县的PM2.5月均值分别是多少？`
- **THEN** slots SHALL 输出 `region_mode = "collection"`
- **AND** `regions` SHALL 包含父级区域项 `text = "郑州市"`
- **AND** 父级区域项 SHALL 包含 `role = "collection_parent"`
- **AND** 父级区域项 SHALL 包含 `child_level = "district"`
- **AND** 中间件 SHALL 展开区县后再逐项执行权限校验
