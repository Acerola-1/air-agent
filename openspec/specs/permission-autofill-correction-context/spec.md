# permission-autofill-correction-context Specification

## Purpose
TBD - created by archiving change permission-autofill-correction-context. Update Purpose after archive.
## Requirements
### Requirement: 无行政区查询自动使用用户默认关联区域
当用户问题需要权限审查但未指定省、市或区县时，系统 SHALL 使用 `get_user_profile.bound_region` 作为默认查询区域，并将该区域传递给后续查询流程。

#### Scenario: 用户未指定行政区且存在 bound_region
- **WHEN** 用户问题为“查一下昨天的空气质量”且 slots 中 `province`、`city`、`district` 均为空
- **AND** `get_user_profile` 返回 `bound_region.name = "新郑市"`、`bound_region.level = "district"`
- **THEN** 系统 SHALL 自动补全查询区域为“新郑市”
- **AND** 系统 SHALL NOT 调用 `resolve_region_scope` 解析空行政区
- **AND** 主流程 SHALL 通过 `permission_query_overrides.region` 收到“新郑市”的结构化区域信息

#### Scenario: 用户未指定行政区且 bound_region 缺失
- **WHEN** 用户问题需要权限审查且 slots 中没有行政区
- **AND** `get_user_profile` 未返回有效 `bound_region`
- **THEN** 系统 SHALL NOT 凭空生成查询区域
- **AND** 系统 SHALL 按用户画像缺失或权限事实不足策略拒绝或安全降级

### Requirement: 自动补全区域生成用户可见修正文案
当系统自动使用用户默认关联区域补全查询区域时，系统 SHALL 生成用户可见的修正文案，说明查询区域已自动补全。

#### Scenario: 自动补全生成文案
- **WHEN** 系统将未指定行政区的查询自动补全为“新郑市”
- **THEN** `PermissionResult.auto_filled` SHALL 为 `true`
- **AND** `PermissionResult.correction_text` SHALL 包含“已自动补全查询区域”
- **AND** `PermissionResult.correction_text` SHALL 包含补全后的区域名称“新郑市”

#### Scenario: 最终回复包含自动补全文案
- **WHEN** `PermissionResult.auto_filled = true`
- **AND** `PermissionResult.correction_text` 非空
- **THEN** 主流程最终回复 SHALL 告知用户系统已自动补全查询区域
- **AND** 主流程 SHALL 继续回答用户的数据查询

### Requirement: 自动补全不是行政区越权替换
无行政区自动补全 SHALL 与用户指定越权行政区后的替换修正区分开；自动补全本身 SHALL NOT 使用 `fix_strategy = "replace_region"` 表示。

#### Scenario: 默认区域权限通过
- **WHEN** 用户未指定行政区
- **AND** 系统补全的 `bound_region` 本身在用户权限范围内
- **THEN** `PermissionResult.fix_strategy` SHALL 为空字符串
- **AND** `PermissionResult.auto_filled` SHALL 为 `true`
- **AND** `PermissionResult.permitted` SHALL 表示补全后区域和时间规则的真实判定结果

#### Scenario: 用户指定越权行政区
- **WHEN** 用户明确指定了越权行政区
- **AND** 系统将该行政区替换为有权限的区域
- **THEN** `PermissionResult.fix_strategy` SHALL 为 `replace_region`
- **AND** `PermissionResult.auto_filled` SHALL 为 `false`

### Requirement: 自动补全区域强制进入查询覆盖参数
当系统自动补全查询区域时，系统 SHALL 写入 `permission_query_overrides.region`，供后续 skill 查找、参数抽取和工具调用强制使用。

#### Scenario: 自动补全写入查询覆盖参数
- **WHEN** 系统自动补全区域为“新郑市”
- **THEN** `state["permission_query_overrides"].region.name` SHALL 为“新郑市”
- **AND** `state["permission_query_overrides"].region.level` SHALL 为 `district`
- **AND** `state["permission_query_overrides"].auto_filled` SHALL 为 `true`
- **AND** 后续查询 SHALL 使用该区域，而不是继续按原问题中的空行政区执行

### Requirement: 权限上下文包含完整补全与审查信息
权限审查完成后，系统 SHALL 将自动补全标记、补全区域、修正文案、匹配规则和 debug 上下文注入主流程可读取的权限上下文。

#### Scenario: 主流程读取完整权限上下文
- **WHEN** 权限审查完成且触发自动补全
- **THEN** `<permission_context>` SHALL 包含 `permission_result.auto_filled`
- **AND** `<permission_context>` SHALL 包含 `permission_query_overrides.region`
- **AND** `<permission_context>` SHALL 包含 `permission_result.correction_text`
- **AND** `<permission_context>` SHALL 包含 `permission_result.matched_rules`
- **AND** `<permission_context>` SHALL 包含可用于排障的 `debug_context`

