## Context

权限方案设计文档 8.3 要求规则参数从配置文件加载，支持无需改代码调整。当前实现中，权限规则版本在 `engine.py` 中硬编码为 `RULE_VERSION = "V2.3"`，行政区层级白名单在 `region.py` 中硬编码为 `ALLOWED_LEVELS`，权限审查中间件的 fail closed 策略由模块导入时读取环境变量得到。上述实现可运行，但配置入口分散，后续修改规则时需要理解多个模块，且缺少统一字段说明和校验边界。

本次变更采用“启动时配置化”：进程启动或模块首次导入时加载配置，运行中不热加载。权限配置属于安全敏感配置，配置缺失、格式错误或取值非法时必须保持安全默认值，不能因为配置错误放宽权限。

## Goals / Non-Goals

**Goals:**

- 提供集中、可读、带字段说明的权限规则配置类。
- 统一管理 `rule_version`、`fail_closed`、行政区层级白名单、修正策略阈值。
- 保持现有默认行为不变：规则版本默认 `V2.3`，fail closed 默认开启，行政区白名单默认使用当前矩阵。
- 让规则引擎、中间件、行政区辅助逻辑从配置对象读取规则参数。
- 为配置覆盖值增加校验和单测，确保非法配置不会导致越权放行。

**Non-Goals:**

- 不实现配置热加载。
- 不引入远程配置中心、数据库配置或管理后台。
- 不改变 `PermissionResult` 的结构。
- 不改变当前权限判定算法、修正文案模板或 MCP 事实获取流程。
- 不在本次完整实现设计文档 8.3 中所有豁免窗口热调整能力；豁免窗口可保留当前计算算法，必要时只抽取默认参数。

## Decisions

### Decision 1: 新增专用权限配置类

新增 `common.permission.config` 或同等位置的权限配置模块，定义类似 `PermissionRuleConfig` 的配置类，并导出启动时构造的配置对象。配置类字段需要用注释或 docstring 说明业务含义、默认值和安全注意事项。

建议字段：

- `rule_version: str`：写入 `PermissionResult.rule_version` 的规则版本号，用于日志、测试和规则审计。
- `fail_closed: bool`：权限审查异常时是否拒绝访问，默认 `true`。
- `region_level_whitelist: dict[str, set[str]]`：用户行政区层级到可访问请求层级的白名单。
- `time_truncation_min_ratio: float`：时间截断后合法范围占比低于该值时触发备选提示的阈值，默认 `0.5`。若当前代码尚未使用该策略，可先作为配置项保留并测试默认值。
- `confirmation_required: bool`：行政区或时间修正是否需要确认式替换，默认 `true`。若当前代码尚未使用，可先作为配置项保留。

原因：权限配置和通用 LLM、数据库、MCP 配置关注点不同，专用配置类更容易表达安全默认值和字段含义，也能避免 `common.config.settings.Config` 继续膨胀。

备选方案：直接把字段加到 `common.config.settings.Config`。该方案实现更少，但权限配置包含结构化白名单和规则语义，放入通用环境配置类后注释和校验会变得分散。

### Decision 2: 配置来源采用环境变量优先，结构化配置可选

第一版支持环境变量覆盖核心标量项，例如 `PERMISSION_RULE_VERSION`、`PERMISSION_FAIL_CLOSED`。结构化白名单可用 JSON 环境变量或可选 YAML 文件路径承载，例如 `PERMISSION_CONFIG_PATH` 指向 `permission_config.yaml`。如果实现 YAML 文件，必须使用项目已有 `yaml.safe_load` 依赖，不新增重依赖。

加载优先级建议为：

1. 安全默认值。
2. YAML 文件中的权限配置。
3. 环境变量覆盖同名核心字段。

原因：环境变量符合当前项目配置习惯；YAML 适合表达设计文档 8.3 中的结构化白名单和阈值。二者结合可以满足本次“启动时配置化”，同时不要求配置中心。

备选方案：只支持 YAML。该方案与设计文档示例一致，但部署环境中修改单个版本号或 fail closed 需要维护文件，不如环境变量方便。

### Decision 3: 配置校验必须安全失败

配置类必须校验：

- `rule_version` 为空时回退默认值。
- `fail_closed` 非法字符串时回退 `true`。
- `region_level_whitelist` 的 key 和 value 只能是 `province`、`city`、`district`。
- 白名单缺少某个用户层级时，该层级回退默认白名单或空集合，不得扩权。
- `time_truncation_min_ratio` 必须在 `0.0` 到 `1.0` 之间，非法时回退默认 `0.5`。

原因：权限配置错误比普通配置错误风险更高。安全默认行为应优先保证“不误放权”。

备选方案：配置非法时直接启动失败。该方案更严格，但本项目权限中间件在异常时已经倾向 fail closed；启动失败可能影响非权限路径可用性。第一版采用日志告警 + 安全默认值更稳。

### Decision 4: 规则引擎通过函数读取配置值

`engine.py` 中构造 `PermissionResult` 时应通过配置对象或轻量函数读取 `rule_version`，避免继续写死模块常量。为了兼容测试和已有 import，可短期保留 `RULE_VERSION` 作为默认值或配置快照别名，但业务构造路径应以配置对象为准。

`region.py` 的 `is_region_level_allowed` 应从配置对象读取 `region_level_whitelist`，默认值保持现有矩阵。

`permission_classify_middleware.py` 的异常降级判断应从同一个配置对象读取 `fail_closed`，避免中间件和规则引擎出现两个不同来源。

原因：统一配置来源后，测试、日志和运行时行为更容易推断。

### Decision 5: 不做热加载，明确重启生效

配置对象在进程启动或模块加载时创建，修改 YAML 或环境变量后需要重启应用进程才生效。文档、配置类注释和测试命名都应体现这一点。

原因：权限规则热加载涉及并发读写、一致性快照、错误配置回滚和审计。当前诉求明确不需要热加载，启动时配置化能满足维护便利性，并保持实现复杂度低。

## Risks / Trade-offs

- [Risk] 配置项增加后，测试直接 import 旧常量可能失败。→ Mitigation：保留兼容别名或同步调整单测，新增配置默认值断言。
- [Risk] YAML/JSON 白名单配置写错导致权限矩阵变化。→ Mitigation：严格校验合法层级，非法项忽略并记录日志，默认值不能被非法配置扩权。
- [Risk] `time_truncation_min_ratio` 和 `confirmation_required` 当前可能未被业务逻辑消费。→ Mitigation：第一版可以先纳入配置类和默认值说明，任务中明确是否接入现有逻辑；未接入时不得声称改变修正策略行为。
- [Risk] 同时支持环境变量和 YAML 可能产生覆盖顺序误解。→ Mitigation：在配置类 docstring 和 `.env.example` 或配置示例中写明优先级。
