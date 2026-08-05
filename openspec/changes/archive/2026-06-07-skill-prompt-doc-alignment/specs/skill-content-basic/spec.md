## MODIFIED Requirements

### Requirement: air-quality-realtime Skill 总纲
系统 SHALL 在 `skills/air-quality-basic-query/SKILL.md` 维护实时空气质量查询 Skill 的总纲文件，YAML frontmatter 包含 name: air-quality-basic-query，覆盖问题 1、2、4，allowed-tools 为 helper_get_latest_time、mcp_city_common_get_air_quality_realtime_stat、mcp_city_common_get_air_quality_forecast、mcp_weather_forecast_daily、mcp_weather_forecast_hourly、mcp_weather_forecast_daily_history、mcp_weather_forecast_hourly_history，metadata.requires_weather 为 true。快速模式 MUST 尝试获取气象数据，获取失败时 MUST 标注"缺失气象数据，未分析气象对空气质量的影响"。

#### Scenario: air-quality-basic-query 快速模式气象缺失标注
- **WHEN** 快速模式调用气象工具获取数据失败或返回空
- **THEN** 输出末尾 MUST 标注"缺失气象数据，未分析气象对空气质量的影响"

#### Scenario: air-quality-basic-query 多城对比浓度差值量化
- **WHEN** 用户查询多城市同日空气质量对比
- **THEN** 输出 MUST 量化各核心污染物的浓度差值，如"PM2.5浓度高于XX市2微克/立方米"

#### Scenario: air-quality-basic-query 时效性标注
- **WHEN** Skill 层传入的数据时间戳晚于当前系统时间超过 2 小时
- **THEN** 输出 MUST 在结论后标注"数据更新时间：YYYY-MM-DD HH:MM:SS，请注意时效性"

#### Scenario: air-quality-basic-query 单城模板包含综合指数
- **WHEN** 用户查询单城市空气质量实况
- **THEN** 输出 MUST 包含综合指数数值

### Requirement: comparison-composition Skill 总纲
系统 SHALL 在 `skills/comparison-composition/SKILL.md` 维护同比占比分析 Skill 的总纲文件，覆盖问题 3、7，allowed-tools 为 helper_get_latest_time、integratedIndexRatio、mcp_city_common_get_air_quality_realtime_stat、text2sql_city_indexes_month_and_year、mcp_weather_forecast_daily、mcp_weather_forecast_hourly、mcp_weather_forecast_daily_history、mcp_weather_forecast_hourly_history，metadata.requires_weather 为 optional。快速模式和专家模式 MUST 尝试获取气象数据，获取失败时 MUST 标注"气象数据暂缺"。

#### Scenario: comparison-composition 快速模式气象获取
- **WHEN** 快速模式执行同比/占比分析
- **THEN** MUST 调用气象工具获取气象数据，缺失时标注"气象数据暂缺"

#### Scenario: comparison-composition 专家模式气象获取
- **WHEN** 专家模式执行同比/占比分析
- **THEN** MUST 调用气象工具获取气象数据，不得写"不得主动调用天气工具"

#### Scenario: comparison-composition 同比模板优良天数
- **WHEN** 同比分析主数据返回了优良天数字段
- **THEN** 输出 MUST 展示优良天数及同比变化

### Requirement: compliance-feasibility Skill 总纲
系统 SHALL 在 `skills/compliance-feasibility/SKILL.md` 维护达标可行性研判 Skill 的总纲文件，覆盖问题 10、11，allowed-tools 为 helper_get_latest_time、mcp_city_analysis_compliance_prediction、mcp_weather_forecast_daily、mcp_weather_forecast_hourly、mcp_weather_forecast_daily_history、mcp_weather_forecast_hourly_history、mcp_city_common_get_air_quality_realtime_stat、mcp_city_common_get_air_quality_forecast，metadata.requires_weather 为 true。研判结论 MUST 仅使用"大概率可实现""存在难度""无法实现"三种。气象缺失标注 MUST 使用"缺失气象数据，研判结论存在局限"。

#### Scenario: compliance-feasibility 研判结论用语
- **WHEN** Skill 输出达标可行性研判结论
- **THEN** 结论 MUST 仅使用"大概率可实现""存在难度""无法实现"之一，MUST NOT 使用"可行""有风险""不可行"

#### Scenario: compliance-feasibility 保良模板格式
- **WHEN** 用户查询保良可行性
- **THEN** 输出 MUST 包含累计浓度、未来逐小时控制阈值（TODO: 工具暂不支持逐小时控制阈值查询）、当前小时数据

#### Scenario: compliance-feasibility 气象缺失标注措辞
- **WHEN** 达标研判场景气象数据缺失
- **THEN** 输出 MUST 标注"缺失气象数据，研判结论存在局限"

### Requirement: ranking-assessment Skill 总纲
系统 SHALL 在 `skills/ranking-assessment/SKILL.md` 维护排名考核查询 Skill 的总纲文件，覆盖问题 6、8，metadata.requires_weather 为 false。同比变化幅度超过±100%时 MUST 标注"同比变化异常，建议核对数据"。

#### Scenario: ranking-assessment 同比异常标注
- **WHEN** 排名/考核数据中同比变化幅度超过±100%
- **THEN** 输出 MUST 标注"同比变化异常，建议核对数据"

### Requirement: regional-benchmark Skill 总纲
系统 SHALL 在 `skills/regional-benchmark/SKILL.md` 维护区域对标统计 Skill 的总纲文件，覆盖问题 5、9，metadata.requires_weather 为 false。达标城市名单 MUST 推荐按拼音首字母排序。

#### Scenario: regional-benchmark 达标城市名单排序
- **WHEN** 输出区域达标城市名单
- **THEN** 名单 MUST 推荐按拼音首字母排序

### Requirement: station-extreme Skill 总纲
系统 SHALL 在 `skills/station-extreme/SKILL.md` 维护站点极值查询 Skill 的总纲文件，覆盖问题 12、13，metadata.requires_weather 为 false。MUST 优先展示国控站点数据，无国控站点时展示省控站点并标注"无国控站点数据，展示省控站点结果"。

#### Scenario: station-extreme 国控/省控降级展示
- **WHEN** 站点数据中无国控站点但有省控站点数据
- **THEN** 输出 MUST 展示省控站点数据，并标注"无国控站点数据，展示省控站点结果"

### Requirement: trend-analysis Skill 总纲
系统 SHALL 在 `skills/trend-analysis/SKILL.md` 维护趋势分析 Skill 的总纲文件，覆盖问题 14，metadata.requires_weather 为 true。快速模式 MUST 获取气象数据，获取失败时 MUST 标注"缺失气象数据，未分析气象对趋势的影响"。

#### Scenario: trend-analysis 快速模式气象必须获取
- **WHEN** 快速模式执行趋势分析
- **THEN** MUST 调用气象工具获取气象数据，MUST NOT 仅在用户明确要求时才补充

#### Scenario: trend-analysis 快速模式气象缺失标注
- **WHEN** 快速模式调用气象工具获取数据失败或返回空
- **THEN** 输出末尾 MUST 标注"缺失气象数据，未分析气象对趋势的影响"
