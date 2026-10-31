# Spec Delta

## MODIFIED Requirements

### Requirement: 数值预测数据独立步骤
涉及当天或未来数据的 Skill（air-quality-basic-query、compliance-feasibility、trend-analysis）SHALL 在单一 `references/rules.md` 的数据获取流程中，将空气质量数值预报（mcp_city_common_get_air_quality_forecast）作为独立步骤呈现，包含调用条件、用途说明和降级规则。

#### Scenario: 当天数据查询时调用数值预报
- **WHEN** 用户查询当天空气质量数据且主监测数据未覆盖未来时段
- **THEN** Skill MUST 调用 mcp_city_common_get_air_quality_forecast 补充未来时段数据，预报数据仅用于未发生时段的风险补充

#### Scenario: 历史数据查询时不调用数值预报
- **WHEN** 用户查询历史日期的空气质量数据
- **THEN** Skill MUST NOT 主动调用 mcp_city_common_get_air_quality_forecast

#### Scenario: 纯历史趋势分析不调用数值预报
- **WHEN** 用户请求的趋势区间完全在历史范围内
- **THEN** Skill MUST NOT 主动调用 mcp_city_common_get_air_quality_forecast
