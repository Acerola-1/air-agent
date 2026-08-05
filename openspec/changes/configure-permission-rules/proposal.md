## Why

当前权限规则中的规则版本、fail closed 策略、行政区层级白名单等关键参数仍分散在代码常量或环境变量读取逻辑中，和设计文档 8.3 中“规则配置化”的目标不一致。将这些参数收敛到启动时加载的配置类，可以降低规则调整成本，并让后续修改人员清楚每个配置项的含义和安全默认值。

## What Changes

- 新增权限规则启动时配置能力，集中管理规则版本、fail closed、行政区层级白名单、修正策略阈值等配置项。
- 新增带注释/字段说明的权限配置类，所有字段提供明确含义、默认值和安全约束。
- 调整权限规则引擎和权限审查中间件读取配置对象，不再直接依赖散落的模块常量。
- 配置仅在进程启动或模块加载时生效，本次不实现热加载。
- 配置缺失或非法时必须保持安全默认行为，不能因为配置问题放宽权限。

## Capabilities

### New Capabilities
- `permission-rule-configuration`: 定义权限规则启动时配置的加载、默认值、校验和使用要求。

### Modified Capabilities

## Impact

- 影响代码：
  - `src/common/permission/engine.py`
  - `src/common/permission/region.py`
  - `src/common/middleware/permission_classify_middleware.py`
  - `src/common/config/` 或新增 `src/common/permission/config.py`
- 影响测试：
  - 权限规则版本、fail closed、行政区白名单和豁免窗口相关单测需要覆盖配置默认值与覆盖值。
- 不新增外部服务依赖。
- 不改变对外 `PermissionResult` schema，仅改变其 `rule_version` 的来源。
