## Why

当前多个 Skill 已允许或要求使用空气质量数值预报，但各自对调用条件、预报与监测数据的边界、缺失降级和最终输出口径描述不一致。尤其在历史日期、当日主数据已覆盖全天、或页面主数据完整的场景中，模型可能过度调用预报或把预报结果混入监测事实，影响业务回答稳定性。

本变更需要将规则落实到具体 Skill 文件中，逐个判断是否保留、限制或移除空气质量数值预报要求，而不是依赖系统级提示词兜底。

## What Changes

- 逐个审查所有包含 `mcp_city_common_get_air_quality_forecast` 的 Skill，形成保留、限制、可选、移除四类处理清单。
- 在需要输出预测数据的 Skill 详细规则中明确空气质量数值预报的调用条件、禁止条件、数据使用边界和缺失降级话术。
- 对小时播报等当日实时/未覆盖全天场景，明确“仅当查询当日且主监测数据未覆盖全天时，才用空气质量数值预报补充当日剩余小时或未来短临趋势”。
- 对历史复盘、排名、日历、箱线图、空间分布等非预测主场景，弱化或移除主动调用空气质量数值预报的要求，避免无关扩展。
- 对确需未来风险研判的达标可行性、趋势分析、目标控制类 Skill，保留预报，但要求只用于未来风险补充，不覆盖已监测事实。
- 增加测试或静态检查，确保关键 Skill 的 detailed references 中包含明确的预报使用边界。

## Capabilities

### New Capabilities

- `air-quality-forecast-skill-rules`: 约束各 Skill 对空气质量数值预报数据的调用条件、使用边界、缺失降级和输出口径。

### Modified Capabilities

- `skill-content-basic`: 修改 Skill 内容规范，要求涉及空气质量数值预报的 Skill 在自身详细规则中声明业务适用性，不得仅依赖系统提示词或工具描述。

## Impact

- 影响 `src/basic_qa/skills/**/SKILL.md` 与 `references/*.md` 中包含空气质量数值预报工具的技能规则。
- 影响 `src/data_analysis/skills/**/SKILL.md` 与 `references/*.md` 中包含空气质量数值预报工具的技能规则。
- 影响 `src/intelligent_analysis/skills/**/SKILL.md` 与 `references/*.md` 中包含空气质量数值预报工具的技能规则。
- 不修改公共系统提示词作为主要约束，不改变 MCP 工具实现和图运行时架构。
- 可能增加或更新 Skill 内容静态测试，验证规则覆盖面和关键禁止条件。
