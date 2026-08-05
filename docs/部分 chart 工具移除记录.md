# basic_qa Skills chart 工具删除记录

> 删除时间：2026-05-28
> 操作：从 `src/basic_qa/skills/` 下各 skill 的 `allowed-tools` 中移除所有以 `chart_` 开头的工具

---

## 删除详情

### 1. air-quality-basic-query

**文件路径：** `src/basic_qa/skills/air-quality-basic-query/SKILL.md`

**删除的工具：**
- `chart_city_concentration_line_chart`

**删除前：**
```yaml
allowed-tools: helper_get_latest_time mcp_city_common_get_air_quality_realtime_stat mcp_weather_forecast_daily mcp_weather_forecast_hourly mcp_weather_history_daily mcp_weather_history_hourly chart_city_concentration_line_chart
```

**删除后：**
```yaml
allowed-tools: helper_get_latest_time mcp_city_common_get_air_quality_realtime_stat mcp_weather_forecast_daily mcp_weather_forecast_hourly mcp_weather_history_daily mcp_weather_history_hourly
```

---

### 2. comparison-composition

**文件路径：** `src/basic_qa/skills/comparison-composition/SKILL.md`

**删除的工具：**
- `chart_city_indexes_month_year_line_chart`

**删除前：**
```yaml
allowed-tools: helper_get_latest_time integratedIndexRatio mcp_city_common_get_air_quality_realtime_stat text2sql_city_indexes_month_and_year  chart_city_indexes_month_year_line_chart
```

**删除后：**
```yaml
allowed-tools: helper_get_latest_time integratedIndexRatio mcp_city_common_get_air_quality_realtime_stat text2sql_city_indexes_month_and_year
```

---

### 3. compliance-feasibility

**文件路径：** `src/basic_qa/skills/compliance-feasibility/SKILL.md`

**删除的工具：**
- `chart_city_calendar_day_calculations`
- `chart_city_compliance_prediction`

**删除前：**
```yaml
allowed-tools: helper_get_latest_time mcp_city_analysis_compliance_prediction mcp_weather_forecast_daily mcp_weather_forecast_hourly mcp_weather_history_daily mcp_weather_history_hourly mcp_city_common_get_air_quality_realtime_stat chart_city_calendar_day_calculations chart_city_compliance_prediction
```

**删除后：**
```yaml
allowed-tools: helper_get_latest_time mcp_city_analysis_compliance_prediction mcp_weather_forecast_daily mcp_weather_forecast_hourly mcp_weather_history_daily mcp_weather_history_hourly mcp_city_common_get_air_quality_realtime_stat
```

---

### 4. ranking-assessment

**文件路径：** `src/basic_qa/skills/ranking-assessment/SKILL.md`

**删除的工具：**
- `chart_city_factor_rankings`

**删除前：**
```yaml
allowed-tools: helper_get_latest_time cityFactorRankings mcp_city_common_get_air_quality_realtime_stat chart_city_factor_rankings
```

**删除后：**
```yaml
allowed-tools: helper_get_latest_time cityFactorRankings mcp_city_common_get_air_quality_realtime_stat
```

---

### 5. regional-benchmark

**文件路径：** `src/basic_qa/skills/regional-benchmark/SKILL.md`

**删除的工具：**
- `chart_city_monthly_yearly_comparison`
- `chart_city_calendar_day_calculations`

**删除前：**
```yaml
allowed-tools: helper_get_latest_time mcp_city_analysis_calculations text2sql_monthly_and_yearly_city_comparison mcp_city_common_get_air_quality_realtime_stat chart_city_monthly_yearly_comparison chart_city_calendar_day_calculations
```

**删除后：**
```yaml
allowed-tools: helper_get_latest_time mcp_city_analysis_calculations text2sql_monthly_and_yearly_city_comparison mcp_city_common_get_air_quality_realtime_stat
```

---

### 6. trend-analysis

**文件路径：** `src/basic_qa/skills/trend-analysis/SKILL.md`

**删除的工具：**
- `chart_city_concentration_line_chart`

**删除前：**
```yaml
allowed-tools: helper_get_latest_time oneIndexManyCityLine mcp_weather_forecast_daily mcp_weather_forecast_hourly mcp_weather_history_daily mcp_weather_history_hourly mcp_city_common_get_air_quality_realtime_stat chart_city_concentration_line_chart
```

**删除后：**
```yaml
allowed-tools: helper_get_latest_time oneIndexManyCityLine mcp_weather_forecast_daily mcp_weather_forecast_hourly mcp_weather_history_daily mcp_weather_history_hourly mcp_city_common_get_air_quality_realtime_stat
```

---

## 未受影响的 Skill

以下 skill 的 `allowed-tools` 中原本就不包含以 `chart_` 开头的工具，无需修改：

- `station-extreme`

---

## 回滚方法

如需恢复被删除的工具，请根据上文的"删除前"内容，将对应 `allowed-tools` 行替换回原始值即可。
