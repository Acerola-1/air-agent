# Spec Delta

## ADDED Requirements

### Requirement: air-quality-basic-query 统一规则 Skill 总纲
系统 SHALL 在 `skills/air-quality-basic-query/SKILL.md` 维护实时空气质量查询 Skill 的总纲文件，YAML frontmatter 包含 name: air-quality-basic-query，覆盖问题 1、2、4，allowed-tools 为 helper_get_latest_time、mcp_city_common_get_air_quality_realtime_stat、mcp_city_common_get_air_quality_forecast、mcp_weather_forecast_daily、mcp_weather_forecast_hourly、mcp_weather_forecast_daily_history、mcp_weather_forecast_hourly_history，metadata.requires_weather 为 true。所有回答 MUST 尝试获取气象数据，获取失败时 MUST 标注“缺失气象数据，未分析气象对空气质量的影响”。

#### Scenario: air-quality-basic-query 气象缺失标注
- **WHEN** 调用气象工具获取数据失败或返回空
- **THEN** 输出末尾 MUST 标注“缺失气象数据，未分析气象对空气质量的影响”

#### Scenario: air-quality-basic-query 多城对比浓度差值量化
- **WHEN** 用户查询多城市同日空气质量对比
- **THEN** 输出 MUST 量化各核心污染物的浓度差值，如“PM2.5浓度高于XX市2微克/立方米”

#### Scenario: air-quality-basic-query 时效性标注
- **WHEN** Skill 层传入的数据时间戳晚于当前系统时间超过 2 小时
- **THEN** 输出 MUST 在结论后标注“数据更新时间：YYYY-MM-DD HH:MM:SS，请注意时效性”

#### Scenario: air-quality-basic-query 单城模板包含综合指数
- **WHEN** 用户查询单城市空气质量实况
- **THEN** 输出 MUST 包含综合指数数值

### Requirement: comparison-composition 统一规则 Skill 总纲
系统 SHALL 在 `skills/comparison-composition/SKILL.md` 维护同比占比分析 Skill 的总纲文件，覆盖问题 3、7，allowed-tools 为 helper_get_latest_time、integratedIndexRatio、mcp_city_common_get_air_quality_realtime_stat、text2sql_city_indexes_month_and_year、mcp_weather_forecast_daily、mcp_weather_forecast_hourly、mcp_weather_forecast_daily_history、mcp_weather_forecast_hourly_history，metadata.requires_weather 为 optional。所有回答 MUST 尝试获取气象数据，获取失败时 MUST 标注“气象数据暂缺”。

#### Scenario: comparison-composition 气象获取
- **WHEN** 执行同比/占比分析
- **THEN** MUST 尝试调用气象工具获取气象数据，缺失时标注“气象数据暂缺”

#### Scenario: comparison-composition 同比模板优良天数
- **WHEN** 同比分析主数据返回了优良天数字段
- **THEN** 输出 MUST 展示优良天数及同比变化

### Requirement: trend-analysis 统一规则 Skill 总纲
系统 SHALL 在 `skills/trend-analysis/SKILL.md` 维护趋势分析 Skill 的总纲文件，覆盖问题 14，metadata.requires_weather 为 true。所有回答 MUST 获取气象数据，获取失败时 MUST 标注“缺失气象数据，未分析气象对趋势的影响”。

#### Scenario: trend-analysis 气象必须获取
- **WHEN** 执行趋势分析
- **THEN** MUST 调用气象工具获取气象数据，MUST NOT 仅在用户明确要求时才补充

#### Scenario: trend-analysis 气象缺失标注
- **WHEN** 调用气象工具获取数据失败或返回空
- **THEN** 输出末尾 MUST 标注“缺失气象数据，未分析气象对趋势的影响”

## MODIFIED Requirements

### Requirement: 基础问答工具依赖分类
Basic QA Skill SHALL 将所有声明工具视为必需；MUST NOT 再声明绘图可选工具或 `chart_*`。缺失任一必需依赖时目标 Skill 不可作为完整已激活能力；工具可用但调用无结果仍按原 Skill 缺失规范回答。独立工具只能补足未覆盖需求，不能伪称已完成缺依赖的完整 Skill 流程。

#### Scenario: 必需工具缺失
- **WHEN** 某 Skill 缺少任一 required-tools，而其他有效能力仍存在
- **THEN** 仅目标加载失败，其他能力保留；不通过无来源替代工具冒充原流程已完成

#### Scenario: 业务数据为空
- **WHEN** 已激活 Skill 的主工具调用返回空
- **THEN** 按原领域规则说明缺失，不倒退成加载失败，不编造数值

#### Scenario: 不再声明绘图可选工具
- **WHEN** 检查任一 Basic QA Skill 的 frontmatter 与规则
- **THEN** 不存在 `chart_*`、绘图步骤或“可选绘图”分支

## REMOVED Requirements

### Requirement: air-quality-realtime Skill 总纲
**Reason**: 该需求绑定快速/专家模式措辞，已由统一规则版本取代；模式删除后不再保留按模式描述的气象标注要求。
**Migration**: 使用新增的“air-quality-basic-query 统一规则 Skill 总纲”，按 `references/rules.md` 执行，不保留模式分支。

### Requirement: comparison-composition Skill 总纲
**Reason**: 该需求按快速/专家模式分别描述气象获取行为，统一模式后不再成立。
**Migration**: 使用新增的“comparison-composition 统一规则 Skill 总纲”，只保留一份气象获取与缺失标注规则。

### Requirement: trend-analysis Skill 总纲
**Reason**: 该需求按快速/专家模式分别描述气象获取与缺失标注，统一模式后不再成立。
**Migration**: 使用新增的“trend-analysis 统一规则 Skill 总纲”，只保留一份气象获取与缺失标注规则。

### Requirement: SKILL.md 正文模式选择指引段落
**Reason**: Basic QA 不再有快速/专家模式，SKILL.md 只指向单一 `references/rules.md`，不再需要模式选择指引。
**Migration**: 将 SKILL.md 的“详细规则入口”改为指向 `references/rules.md`；删除 fast/expert 两个路径与 mode 标记说明。
