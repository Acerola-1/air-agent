## ADDED Requirements

### Requirement: 启动时权限规则配置
系统 SHALL 提供集中式权限规则配置类，并在进程启动或模块首次加载时完成配置解析。配置修改后 SHALL 通过重启应用进程生效，系统 MUST NOT 要求或实现运行时热加载。

#### Scenario: 使用默认配置启动
- **WHEN** 未提供权限规则相关环境变量或配置文件
- **THEN** 系统 SHALL 使用安全默认配置完成启动
- **AND** 规则版本 SHALL 默认为 `V2.3`
- **AND** fail closed SHALL 默认为启用
- **AND** 行政区层级白名单 SHALL 与当前默认矩阵一致

#### Scenario: 配置修改需要重启生效
- **WHEN** 运维人员在进程运行中修改权限规则配置文件或环境变量
- **THEN** 当前进程中的权限规则配置 SHALL 保持启动时加载的配置快照
- **AND** 修改后的配置 SHALL 在应用进程重启后生效

### Requirement: 权限配置字段说明
权限规则配置类 SHALL 为每个配置字段提供清晰的字段说明，说明字段用途、默认值、安全含义以及修改影响，方便后续维护人员调整配置。

#### Scenario: 维护人员查看配置类
- **WHEN** 维护人员打开权限规则配置类源码
- **THEN** 维护人员 SHALL 能从字段注释或 docstring 中理解 `rule_version`、`fail_closed`、`region_level_whitelist`、`time_truncation_min_ratio`、`confirmation_required` 的含义
- **AND** 字段说明 SHALL 标明该配置为启动时加载，修改后需要重启生效

### Requirement: 规则版本配置
系统 SHALL 从权限规则配置读取规则版本，并将该版本写入权限规则引擎生成的 `PermissionResult.rule_version`。

#### Scenario: 默认规则版本
- **WHEN** 权限规则配置未覆盖规则版本
- **THEN** 权限规则引擎生成的 `PermissionResult.rule_version` SHALL 为 `V2.3`

#### Scenario: 覆盖规则版本
- **WHEN** 启动时权限规则配置将规则版本设置为 `V2.4`
- **THEN** 权限规则引擎生成的 `PermissionResult.rule_version` SHALL 为 `V2.4`

#### Scenario: 非法规则版本
- **WHEN** 启动时权限规则配置提供空字符串规则版本
- **THEN** 系统 SHALL 回退到默认规则版本 `V2.3`
- **AND** 系统 SHALL 记录配置非法的日志

### Requirement: Fail Closed 配置
系统 SHALL 从权限规则配置读取 fail closed 策略，并在权限审查中间件规则引擎异常时按该策略处理。

#### Scenario: 默认 fail closed
- **WHEN** 权限规则引擎在权限审查中间件中抛出异常且未覆盖 fail closed 配置
- **THEN** 中间件 SHALL 生成拒绝访问的权限结果
- **AND** 中间件 SHALL NOT 将该请求标记为无需审查

#### Scenario: 显式关闭 fail closed
- **WHEN** 启动时权限规则配置将 fail closed 设置为关闭
- **AND** 权限规则引擎在权限审查中间件中抛出异常
- **THEN** 中间件 SHALL 按宽松降级路径处理
- **AND** 中间件 SHALL 将该请求标记为无需审查

#### Scenario: 非法 fail closed 配置
- **WHEN** 启动时 fail closed 配置值无法解析为布尔值
- **THEN** 系统 SHALL 回退为启用 fail closed
- **AND** 系统 SHALL 记录配置非法的日志

### Requirement: 行政区层级白名单配置
系统 SHALL 从权限规则配置读取行政区层级白名单，并使用该白名单判断用户行政区层级是否允许访问请求行政区层级。

#### Scenario: 默认行政区层级白名单
- **WHEN** 未覆盖行政区层级白名单配置
- **THEN** 省级用户 SHALL 允许访问省级和市级数据
- **AND** 市级用户 SHALL 允许访问省级、市级和区县级数据
- **AND** 区县级用户 SHALL 允许访问市级和区县级数据

#### Scenario: 覆盖行政区层级白名单
- **WHEN** 启动时权限规则配置将区县级用户白名单设置为仅允许 `district`
- **THEN** 区县级用户请求市级数据时 SHALL 被视为层级不允许
- **AND** 区县级用户请求区县级数据时 SHALL 被视为层级允许

#### Scenario: 非法行政区层级白名单
- **WHEN** 启动时行政区层级白名单包含未知层级或非列表值
- **THEN** 系统 SHALL 忽略非法层级配置或回退到安全默认值
- **AND** 系统 SHALL NOT 因非法配置扩大用户可访问层级
- **AND** 系统 SHALL 记录配置非法的日志

### Requirement: 修正策略阈值配置
系统 SHALL 在权限规则配置中提供修正策略阈值字段，包括时间截断最小比例和确认式替换开关。若当前业务逻辑尚未消费某个字段，该字段 SHALL 保留默认值和字段说明，但 MUST NOT 改变现有权限判定行为。

#### Scenario: 默认修正策略阈值
- **WHEN** 未覆盖修正策略阈值配置
- **THEN** `time_truncation_min_ratio` SHALL 默认为 `0.5`
- **AND** `confirmation_required` SHALL 默认为启用

#### Scenario: 非法时间截断比例
- **WHEN** 启动时 `time_truncation_min_ratio` 小于 `0.0` 或大于 `1.0`
- **THEN** 系统 SHALL 回退到默认值 `0.5`
- **AND** 系统 SHALL 记录配置非法的日志
