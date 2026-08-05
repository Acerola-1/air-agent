## ADDED Requirements

### Requirement: 数值预测数据独立步骤
涉及当天或未来数据的 Skill（air-quality-basic-query、compliance-feasibility、trend-analysis）SHALL 在 fast.md 和 expert.md 的数据获取流程中，将空气质量数值预报（mcp_city_common_get_air_quality_forecast）作为独立步骤呈现，包含调用条件、用途说明和降级规则。

#### Scenario: 当天数据查询时调用数值预报
- **WHEN** 用户查询当天空气质量数据且主监测数据未覆盖未来时段
- **THEN** Skill MUST 调用 mcp_city_common_get_air_quality_forecast 补充未来时段数据，预报数据仅用于未发生时段的风险补充

#### Scenario: 历史数据查询时不调用数值预报
- **WHEN** 用户查询历史日期的空气质量数据
- **THEN** Skill MUST NOT 主动调用 mcp_city_common_get_air_quality_forecast

#### Scenario: 纯历史趋势分析不调用数值预报
- **WHEN** 用户请求的趋势区间完全在历史范围内
- **THEN** Skill MUST NOT 主动调用 mcp_city_common_get_air_quality_forecast

### Requirement: 数值预测数据双向规则——有数据必须分析
当 Skill 成功获取到空气质量数值预报数据时，MUST 在输出中结合预报数据进行分析（如未来趋势研判、达标风险补充），不得忽略已获取的预报数据。

#### Scenario: 达标研判场景获取到预报数据
- **WHEN** compliance-feasibility Skill 获取到未来空气质量数值预报数据
- **THEN** 输出 MUST 结合预报数据分析未来达标风险，作为研判依据的补充

#### Scenario: 趋势分析场景获取到预报数据
- **WHEN** trend-analysis Skill 获取到未来空气质量数值预报数据
- **THEN** 输出 MUST 结合预报数据分析未来趋势走向，作为趋势研判的补充

#### Scenario: 实况查询场景获取到预报数据
- **WHEN** air-quality-basic-query Skill 获取到当日未来时段预报数据
- **THEN** 输出 MUST 结合预报数据补充未来短临趋势说明

### Requirement: 数值预测数据双向规则——无数据必须标注局限
当 Skill 无法获取空气质量数值预报数据（工具不可用、调用失败、数据为空）时，MUST 在注意事项中标注局限说明，不得静默跳过。

#### Scenario: 达标研判场景预报数据不可用
- **WHEN** compliance-feasibility Skill 无法获取空气质量数值预报数据
- **THEN** 输出 MUST 在注意事项中标注"暂无空气质量数值预报数据，研判未考虑未来空气质量数值预报"

#### Scenario: 趋势分析场景预报数据不可用
- **WHEN** trend-analysis Skill 无法获取空气质量数值预报数据
- **THEN** 输出 MUST 在注意事项中标注"暂无空气质量数值预报数据，后续趋势仅基于已监测变化和可用气象条件作保守判断"

#### Scenario: 实况查询场景预报数据不可用
- **WHEN** air-quality-basic-query Skill 无法获取空气质量数值预报数据
- **THEN** 输出 MUST 在注意事项中标注"暂无空气质量数值预报数据，后续趋势仅基于已监测变化和可用气象条件作保守判断"

### Requirement: 数值预报数据不得覆盖已监测事实
数值预报数据 SHALL 仅用于未发生时段的风险补充，MUST NOT 覆盖、替代或重算已监测事实（小时值、日累计、峰值、均值、AQI、等级、首要污染物、排名、占比）。

#### Scenario: 预报数据与监测数据冲突
- **WHEN** 数值预报数据与已监测数据存在差异
- **THEN** Skill MUST 以已监测数据为准，预报数据仅作为未来趋势参考

### Requirement: 数值预报调用失败降级规则
数值预报工具调用失败时，SHALL 自动重试不超过 1 次；重试仍失败时，基于已有监测数据和可用气象数据降级回答，MUST NOT 提及工具名、接口名或错误细节。

#### Scenario: 首次调用失败自动重试
- **WHEN** mcp_city_common_get_air_quality_forecast 首次调用失败
- **THEN** Skill SHALL 自动重试 1 次

#### Scenario: 重试仍失败时降级回答
- **WHEN** 重试后仍无法获取预报数据
- **THEN** Skill SHALL 基于已有监测数据和可用气象数据降级回答，并标注局限说明
