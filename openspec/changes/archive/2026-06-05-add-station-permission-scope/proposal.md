## Why

当前权限审查只围绕用户问题中的行政区执行校验，站点类查询虽然已经有业务 Skill 和站点基础工具，但权限层还没有把“站点归属行政区”纳入规则引擎。根据 AI 智能问数权限方案，站点数据权限应继承站点所属行政区，站点类型只作为查询筛选条件，不作为权限边界。

## What Changes

- 扩展权限槽位抽取，识别站点查询对象、站点名称列表、站点类型列表；行政区仍使用现有结构化 `regions` 和 `region_mode` 槽位。
- 在权限中间件中增加站点归属解析步骤，复用现有 `RegionRequestPlan` 的 single、multi_explicit、collection、auto_fill 区域计划，调用由业务侧实现的站点归属工具，按“站点名称 + 已解析查询区域 + 站点类型”获取标准站点信息和归属行政区信息。
- 支持用户一次询问多个城市、多个区域或父级下辖子区域的站点问题：按行政区计划中的最终区域项分别解析站点归属，并合并结果进入权限事实。
- 在规则引擎中复用现有时间豁免和行政区规则：完全豁免时站点不受行政区限制；非完全豁免时按站点归属行政区校验。
- 支持多站点部分可访问：后续查询只使用有权限的站点集合，并生成“仅展示可访问站点”的修正文案。
- 对站点缺少有效行政区归属的情况安全拒绝该站点数据，并提示“该数据暂不可用”。
- 不增加站点类型权限；标准站、国控、省控、市控站、乡镇站、微站、TVOC站、粉尘站、高密度站仅作为查询筛选条件。

## Capabilities

### New Capabilities

- 无

### Modified Capabilities

- `permission-classify-middleware`: 权限中间件需要抽取站点相关槽位、调用站点归属解析工具，并向规则引擎传入站点权限事实。
- `permission-result-schema`: 权限结果需要表达可访问站点集合、站点裁剪数量和站点不可用/部分越权文案，供后续查询和最终回复使用。

## Impact

- 影响 `src/common/permission/slots.py`、`src/common/permission/facts.py`、`src/common/permission/engine.py`、`src/common/permission/result.py`。
- 影响 `src/common/middleware/permission_classify_middleware.py` 中的 slots 抽取、`RegionRequestPlan` 构造后站点归属解析、规则引擎调用和 `permission_query_overrides` 构造。
- 需要新增或封装一个站点归属 MCP 工具调用，工具输入输出使用自然语言字段，不要求权限层使用行政区 code。
- 需要新增单元测试覆盖站点槽位抽取模型、站点归属事实构造、完全豁免、部分可访问、站点归属缺失、多城市多次解析、集合区域展开后解析等场景。
