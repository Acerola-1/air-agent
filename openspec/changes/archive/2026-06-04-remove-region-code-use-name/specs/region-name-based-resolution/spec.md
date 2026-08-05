# Region Name-Based Resolution Spec

## Purpose

定义基于中文名称的行政区解析和消歧规范，替代基于内部 `code` 的解析方式。

## Requirements

### Requirement: 删除 code 字段

`get_user_profile` 和 `resolve_region_scope` 两个 MCP 工具的返回 SHALL 删除所有 `code` 字段，仅保留 `name` 和 `level`。

#### Scenario: get_user_profile 返回无 code

- **WHEN** 调用 `get_user_profile("u123")`
- **THEN** 返回结果 SHALL 不包含 `code` 字段
- **AND** `bound_region` SHALL 仅包含 `level` 和 `name`
- **AND** `city` SHALL 仅包含 `name`
- **AND** `province` SHALL 仅包含 `name`

#### Scenario: resolve_region_scope 返回无 code

- **WHEN** 调用 `resolve_region_scope` 成功解析行政区
- **THEN** 返回结果 SHALL 不包含 `code` 字段
- **AND** `region` SHALL 仅包含 `level` 和 `name`
- **AND** `city` SHALL 仅包含 `name`
- **AND** `province` SHALL 仅包含 `name`

### Requirement: resolve_region_scope 消歧参数改为中文名

`resolve_region_scope` 的入参 SHALL 删除 `context_city_code` 和 `context_province_code`，替换为 `city` 和 `province`（可选，中文名）。

#### Scenario: 按城市名消歧

- **WHEN** 调用 `resolve_region_scope` 查询"金水区"
- **AND** 传入 `city="郑州市"`
- **THEN** 工具 SHALL 返回郑州市的金水区
- **AND** 工具 SHALL NOT 返回南昌市的金水区

#### Scenario: 按省份名消歧

- **WHEN** 调用 `resolve_region_scope` 查询"朝阳区"
- **AND** 传入 `province="北京市"`
- **THEN** 工具 SHALL 返回北京市的朝阳区
- **AND** 工具 SHALL NOT 返回长春市的朝阳区

#### Scenario: 无消歧参数

- **WHEN** 调用 `resolve_region_scope` 查询"杭州市"
- **AND** 不传入 `city` 或 `province`
- **THEN** 工具 SHALL 正常返回杭州市
- **AND** 返回结果 SHALL 包含 `city` 和 `province` 信息

### Requirement: 行政区比较基于 name

Python 侧所有行政区比较逻辑 SHALL 基于 `name` 而非 `code`。

#### Scenario: 同省判断

- **WHEN** 用户画像 `province.name="河南省"`
- **AND** 请求行政区 `province.name="河南省"`
- **THEN** `_same_province` SHALL 返回 `True`

#### Scenario: 同市判断

- **WHEN** 用户画像 `city.name="郑州市"`
- **AND** 请求行政区 `city.name="郑州市"`
- **THEN** `_same_city` SHALL 返回 `True`

#### Scenario: 不同省份

- **WHEN** 用户画像 `province.name="河南省"`
- **AND** 请求行政区 `province.name="浙江省"`
- **THEN** `_same_province` SHALL 返回 `False`

### Requirement: 层级推断基于 level 字段

行政区层级 SHALL 基于 `level` 字段（`province`/`city`/`district`），而非 `code` 编码规则。

#### Scenario: 省级层级

- **WHEN** `bound_region.level="province"`
- **THEN** 用户层级 SHALL 识别为省级

#### Scenario: 地市级层级

- **WHEN** `bound_region.level="city"`
- **THEN** 用户层级 SHALL 识别为地市级

#### Scenario: 区县级层级

- **WHEN** `bound_region.level="district"`
- **THEN** 用户层级 SHALL 识别为区县级
