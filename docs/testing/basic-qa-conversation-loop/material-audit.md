# Basic QA Skill 材料与工具静态审计

代码基线：`7d79526bb4cdd4c5d04911f62943fd11a50c9153`；材料为本次更新后的工作区版本，材料和业务登记字节由脚本快照锁定。只读静态检查，未连接服务、读取部署配置或验证实时发现/授权/参数。

复跑：`.venv/bin/python scripts/audit_basic_qa_skill_materials.py --check`；查看重新生成内容：`.venv/bin/python scripts/audit_basic_qa_skill_materials.py`。快照变化时先人工重审，再更新脚本内快照和报告。

材料版本按 `src/common/capabilities.py:89` 的相对路径排序 SHA-256 算法计算；此脚本读取失败即终止，不使用其读取失败时的空字节回退。`fast`/`expert` 与总纲均存在，不代表运行加载已验证。

静态登记中的短名可构造候选 `mcp:ipp:<名称>`，依据 `src/common/business_registry.py` 和 `src/common/mcp_scoped.py:170`；实际发现还须经过受保护连接、approved_query_tools、实时发现和权限交集，且短名跨服务重名时会失效。候选不代表当前可用或功能/参数等价。未登记项在当前 Basic QA 静态策略下不可用，不能映射到其他同名或相似工具。

依照用户确认的加载分类：总纲 allowed-tools 和模式引用的全部非 chart 工具均为必需，chart 工具为可选；必需工具缺失阻止加载，可选图表缺失不阻止正文加载。下方模式说明保留数据调用条件与结果缺失时的领域规则；工具缺失与调用后数据缺失是不同情形。工具名称逐项与引用位置见下表。

## 九个 Skill 与模式

### air-quality-basic-query

- Skill ID：`skill:basic-qa:air-quality-basic-query`
- 材料版本：`sha256:dbe6b949480bf3d4afab41e4f1c763ab03ba7eda355d6c46f869180879dd0037`
- 必需：`helper_get_latest_time`、`mcp_city_common_get_air_quality_forecast`、`mcp_city_common_get_air_quality_realtime_stat`、`mcp_weather_forecast_daily`、`mcp_weather_forecast_daily_history`、`mcp_weather_forecast_hourly`、`mcp_weather_forecast_hourly_history`
- 可选：`chart_city_concentration_line_chart`
- fast：主数据：实时空气质量；时间基准：helper。气象需调用但缺失可降级；当日未覆盖未来时段才请求空气预报，缺失标注局限；图表主数据成功后要求调用但失败不影响结论。
- expert：主数据：实时空气质量；时间基准：helper。气象需调用但缺失可降级；当日未覆盖未来时段才请求空气预报，缺失标注局限；图表主数据成功后要求调用但失败不影响结论。

### broadcast-hour

- Skill ID：`skill:basic-qa:broadcast-hour`
- 材料版本：`sha256:3133e078e75afe586cc12f6eb90ec726f6f3daac4f13bd67a85af0a385367847`
- 必需：`broadcastHour`、`helper_get_latest_time`、`mcp_city_common_get_air_quality_forecast`、`mcp_weather_forecast_daily`、`mcp_weather_forecast_daily_history`、`mcp_weather_forecast_hourly`、`mcp_weather_forecast_hourly_history`
- 可选：`chart_city_broadcastHour`
- fast：主数据：broadcastHour；时间基准：helper。气象仅用户要求解释时调用；当日小时未覆盖全天才请求空气预报，缺失标注局限；图表要求调用但失败可降级。
- expert：主数据：broadcastHour；时间基准：helper。小时气象按时段调用；当日小时未覆盖全天才请求空气预报，缺失标注局限；图表要求调用但失败可降级。火点/轨迹等补充缺失则剔除，不推定工具。

### comparison-composition

- Skill ID：`skill:basic-qa:comparison-composition`
- 材料版本：`sha256:a7b42de38370d848a5babb2dd7e3ec3d8a787c769d6c294fea4aa80de5490b82`
- 必需：`helper_get_latest_time`、`integratedIndexRatio`、`mcp_city_common_get_air_quality_realtime_stat`、`mcp_weather_forecast_daily`、`mcp_weather_forecast_daily_history`、`mcp_weather_forecast_hourly`、`mcp_weather_forecast_hourly_history`、`text2sql_city_indexes_month_and_year`
- 可选：`chart_city_composite_index_six_factor_ratio`、`chart_city_indexes_month_year_line_chart`
- fast：同比：text2sql 与实时工具均在流程中；占比：按需 integratedIndexRatio。两工具均列为加载必需，具体业务用途仍按原流程。时间基准：helper；气象缺失可降级；两类图表各自要求调用但失败不影响结论。
- expert：同比：text2sql 与实时工具均在流程中；占比：按需 integratedIndexRatio。两工具均列为加载必需，具体业务用途仍按原流程。时间基准：helper；气象缺失可降级；两类图表各自要求调用但失败不影响结论。

### compliance-feasibility

- Skill ID：`skill:basic-qa:compliance-feasibility`
- 材料版本：`sha256:6c34b58e03686c023c9234f995f73dd15963ee27c033429af920661cfdf05301`
- 必需：`helper_get_latest_time`、`mcp_city_analysis_compliance_prediction`、`mcp_city_common_get_air_quality_forecast`、`mcp_city_common_get_air_quality_realtime_stat`、`mcp_weather_forecast_daily`、`mcp_weather_forecast_daily_history`、`mcp_weather_forecast_hourly`、`mcp_weather_forecast_hourly_history`
- 可选：无
- fast：主数据：compliance_prediction；时间基准：helper；实时为补充。气象缺失标注局限；仅当日未来未覆盖时请求预报，失败降级。
- expert：主数据：compliance_prediction；时间基准：helper；实时为补充。气象缺失标注局限；仅当日未来未覆盖时请求预报，失败降级。专家模式要求的核心字段严于快速模式。

### integrated-index-ratio

- Skill ID：`skill:basic-qa:integrated-index-ratio`
- 材料版本：`sha256:036310227ca69eb668f4154cd2cd46453624a1cca1586467b69fabd09b9bb1ff`
- 必需：`helper_get_latest_time`、`integratedIndexRatio`、`mcp_city_common_get_air_quality_forecast`、`mcp_weather_forecast_daily`、`mcp_weather_forecast_daily_history`、`mcp_weather_forecast_hourly`、`mcp_weather_forecast_hourly_history`
- 可选：`chart_city_integrated_index_percentage`
- fast：主数据：integratedIndexRatio；时间基准：helper。气象仅用户要求解释时调用；预报仅明确追问未来风险时可调用；图表要求调用但失败不影响结论。
- expert：主数据：integratedIndexRatio；时间基准：helper。气象按时段调用；预报仅明确追问未来风险时可调用；图表要求调用但失败不影响结论。遥感/产业等补充缺失剔除，不推定工具。

### ranking-assessment

- Skill ID：`skill:basic-qa:ranking-assessment`
- 材料版本：`sha256:0cbc2ead849fceb12d9efecd6dcde79deffe20e089f5b91eec6e20b10519cf19`
- 必需：`cityFactorRankings`、`helper_get_latest_time`、`mcp_city_common_get_air_quality_realtime_stat`
- 可选：`chart_city_factor_rankings`
- fast：主数据：cityFactorRankings；时间基准：helper；实时工具可作补充。图表要求调用但失败不影响结论。
- expert：主数据：cityFactorRankings；时间基准：helper；实时工具可作补充。图表要求调用但失败不影响结论；补充字段缺失只分析已有事实。

### regional-benchmark

- Skill ID：`skill:basic-qa:regional-benchmark`
- 材料版本：`sha256:7dccba5f85f2a64f289cffc7248e7575f5396e81f03dda53de4236e8bb71a050`
- 必需：`helper_get_latest_time`、`mcp_city_analysis_calculations`、`text2sql_monthly_and_yearly_city_comparison`
- 可选：`chart_city_calendar_day_calculations`、`chart_city_monthly_yearly_comparison`
- fast：按场景分别需要 text2sql 月年对标主数据或 calculations 区域达标主数据；时间基准：helper。对应图表要求调用但失败不影响结论；日级多城对标与该流程的对应关系未明确。
- expert：按场景分别需要 text2sql 月年对标主数据或 calculations 区域达标主数据；时间基准：helper。对应图表要求调用但失败不影响结论；日级多城对标与该流程的对应关系未明确。

### station-extreme

- Skill ID：`skill:basic-qa:station-extreme`
- 材料版本：`sha256:4dc422d7afd94c832809037108b98141f71538e53c07f460ae446771ee8aee0e`
- 必需：`helper_get_latest_time`、`mcp_city_common_get_air_quality_realtime_stat`、`mcp_station_base_air_station_list`、`mcp_station_base_get_info_by_code_name`、`mcp_station_base_lng_lat_near_national_province_station`、`mcp_station_base_range_site_list`、`mcp_station_common_air_data`、`mcp_station_common_air_quality_forecast`
- 可选：无
- fast：必须先有上游或上下文站点主数据，否则直接缺失处理；实时城市、站点历史与预报仅称补充，不能推定其为主数据来源。站点基础工具可在需要基础信息时使用，材料未表述为主数据来源。
- expert：必须先有上游或上下文站点主数据，否则直接缺失处理；实时城市、站点历史与预报仅称补充，不能推定其为主数据来源。站点基础工具可在需要基础信息时使用，材料未表述为主数据来源。

### trend-analysis

- Skill ID：`skill:basic-qa:trend-analysis`
- 材料版本：`sha256:ef3f5c6e509d9e6a9d804c253894f89c69cc247621642053055aa05b27d7eaa8`
- 必需：`helper_get_latest_time`、`mcp_city_common_get_air_quality_forecast`、`mcp_city_common_get_air_quality_realtime_stat`、`mcp_weather_forecast_daily`、`mcp_weather_forecast_daily_history`、`mcp_weather_forecast_hourly`、`mcp_weather_forecast_hourly_history`、`oneIndexManyCityLine`
- 可选：`chart_city_concentration_line_chart`
- fast：主数据：oneIndexManyCityLine；时间基准：helper；实时为补充。气象需调用但缺失可降级；仅当日/未来趋势请求预报；图表要求调用但失败不影响结论。
- expert：主数据：oneIndexManyCityLine；时间基准：helper；实时为补充。气象需调用但缺失可降级；仅当日/未来趋势请求预报；图表要求调用但失败不影响结论。

## 工具名称对照（逐项）

| 材料名称 | 静态稳定 ID / 状态 | 引用位置 |
|---|---|---|
| `broadcastHour` | `mcp:ipp:broadcastHour`（静态登记；动态未验证） | `src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/broadcast-hour/references/expert.md:11`、`src/basic_qa/skills/broadcast-hour/references/fast.md:11` |
| `chart_city_broadcastHour` | 未登记，当前策略不可用；无已验证稳定 ID | `src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/broadcast-hour/references/expert.md:21`、`src/basic_qa/skills/broadcast-hour/references/fast.md:21` |
| `chart_city_calendar_day_calculations` | 未登记，当前策略不可用；无已验证稳定 ID | `src/basic_qa/skills/regional-benchmark/SKILL.md:32`、`src/basic_qa/skills/regional-benchmark/references/expert.md:36`、`src/basic_qa/skills/regional-benchmark/references/fast.md:38` |
| `chart_city_composite_index_six_factor_ratio` | 未登记，当前策略不可用；无已验证稳定 ID | `src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/expert.md:37`、`src/basic_qa/skills/comparison-composition/references/fast.md:40` |
| `chart_city_concentration_line_chart` | 未登记，当前策略不可用；无已验证稳定 ID | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/expert.md:31`、`src/basic_qa/skills/air-quality-basic-query/references/fast.md:36`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/expert.md:37`、`src/basic_qa/skills/trend-analysis/references/fast.md:40` |
| `chart_city_factor_rankings` | 未登记，当前策略不可用；无已验证稳定 ID | `src/basic_qa/skills/ranking-assessment/SKILL.md:32`、`src/basic_qa/skills/ranking-assessment/references/expert.md:34`、`src/basic_qa/skills/ranking-assessment/references/fast.md:36` |
| `chart_city_indexes_month_year_line_chart` | 未登记，当前策略不可用；无已验证稳定 ID | `src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/expert.md:36`、`src/basic_qa/skills/comparison-composition/references/fast.md:39` |
| `chart_city_integrated_index_percentage` | 未登记，当前策略不可用；无已验证稳定 ID | `src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/expert.md:20`、`src/basic_qa/skills/integrated-index-ratio/references/fast.md:20` |
| `chart_city_monthly_yearly_comparison` | 未登记，当前策略不可用；无已验证稳定 ID | `src/basic_qa/skills/regional-benchmark/SKILL.md:32`、`src/basic_qa/skills/regional-benchmark/references/expert.md:35`、`src/basic_qa/skills/regional-benchmark/references/fast.md:37` |
| `cityFactorRankings` | `mcp:ipp:cityFactorRankings`（静态登记；动态未验证） | `src/basic_qa/skills/ranking-assessment/SKILL.md:32`、`src/basic_qa/skills/ranking-assessment/references/expert.md:26`、`src/basic_qa/skills/ranking-assessment/references/fast.md:28` |
| `helper_get_latest_time` | `mcp:ipp:helper_get_latest_time`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/expert.md:17`、`src/basic_qa/skills/air-quality-basic-query/references/fast.md:22`、`src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/broadcast-hour/references/expert.md:7`、`src/basic_qa/skills/broadcast-hour/references/fast.md:7`、`src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/expert.md:22`、`src/basic_qa/skills/comparison-composition/references/fast.md:25`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/expert.md:23`、`src/basic_qa/skills/compliance-feasibility/references/fast.md:26`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/expert.md:7`、`src/basic_qa/skills/integrated-index-ratio/references/fast.md:7`、`src/basic_qa/skills/ranking-assessment/SKILL.md:32`、`src/basic_qa/skills/ranking-assessment/references/expert.md:22`、`src/basic_qa/skills/ranking-assessment/references/fast.md:24`、`src/basic_qa/skills/regional-benchmark/SKILL.md:32`、`src/basic_qa/skills/regional-benchmark/references/expert.md:22`、`src/basic_qa/skills/regional-benchmark/references/fast.md:24`、`src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/expert.md:22`、`src/basic_qa/skills/trend-analysis/references/fast.md:25` |
| `integratedIndexRatio` | `mcp:ipp:integratedIndexRatio`（静态登记；动态未验证） | `src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/expert.md:27`、`src/basic_qa/skills/comparison-composition/references/fast.md:30`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/expert.md:11`、`src/basic_qa/skills/integrated-index-ratio/references/fast.md:11` |
| `mcp_city_analysis_calculations` | `mcp:ipp:mcp_city_analysis_calculations`（静态登记；动态未验证） | `src/basic_qa/skills/regional-benchmark/SKILL.md:32`、`src/basic_qa/skills/regional-benchmark/references/expert.md:27`、`src/basic_qa/skills/regional-benchmark/references/fast.md:29` |
| `mcp_city_analysis_compliance_prediction` | `mcp:ipp:mcp_city_analysis_compliance_prediction`（静态登记；动态未验证） | `src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/expert.md:27`、`src/basic_qa/skills/compliance-feasibility/references/expert.md:38`、`src/basic_qa/skills/compliance-feasibility/references/fast.md:30`、`src/basic_qa/skills/compliance-feasibility/references/fast.md:41` |
| `mcp_city_common_get_air_quality_forecast` | `mcp:ipp:mcp_city_common_get_air_quality_forecast`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/expert.md:23`、`src/basic_qa/skills/air-quality-basic-query/references/fast.md:28`、`src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/broadcast-hour/references/expert.md:13`、`src/basic_qa/skills/broadcast-hour/references/fast.md:13`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/expert.md:30`、`src/basic_qa/skills/compliance-feasibility/references/fast.md:33`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/expert.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/fast.md:28`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/expert.md:29`、`src/basic_qa/skills/trend-analysis/references/fast.md:32` |
| `mcp_city_common_get_air_quality_realtime_stat` | `mcp:ipp:mcp_city_common_get_air_quality_realtime_stat`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/expert.md:21`、`src/basic_qa/skills/air-quality-basic-query/references/fast.md:26`、`src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/expert.md:26`、`src/basic_qa/skills/comparison-composition/references/fast.md:29`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/expert.md:28`、`src/basic_qa/skills/compliance-feasibility/references/fast.md:31`、`src/basic_qa/skills/ranking-assessment/SKILL.md:32`、`src/basic_qa/skills/ranking-assessment/references/expert.md:26`、`src/basic_qa/skills/ranking-assessment/references/fast.md:28`、`src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/references/expert.md:27`、`src/basic_qa/skills/station-extreme/references/fast.md:29`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/expert.md:27`、`src/basic_qa/skills/trend-analysis/references/fast.md:30` |
| `mcp_station_base_air_station_list` | `mcp:ipp:mcp_station_base_air_station_list`（静态登记；动态未验证） | `src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/SKILL.md:56` |
| `mcp_station_base_get_info_by_code_name` | `mcp:ipp:mcp_station_base_get_info_by_code_name`（静态登记；动态未验证） | `src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/SKILL.md:57` |
| `mcp_station_base_lng_lat_near_national_province_station` | `mcp:ipp:mcp_station_base_lng_lat_near_national_province_station`（静态登记；动态未验证） | `src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/SKILL.md:57` |
| `mcp_station_base_range_site_list` | `mcp:ipp:mcp_station_base_range_site_list`（静态登记；动态未验证） | `src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/SKILL.md:56` |
| `mcp_station_common_air_data` | `mcp:ipp:mcp_station_common_air_data`（静态登记；动态未验证） | `src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/references/expert.md:28`、`src/basic_qa/skills/station-extreme/references/fast.md:30` |
| `mcp_station_common_air_quality_forecast` | `mcp:ipp:mcp_station_common_air_quality_forecast`（静态登记；动态未验证） | `src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/references/expert.md:29`、`src/basic_qa/skills/station-extreme/references/fast.md:31` |
| `mcp_weather_forecast_daily` | `mcp:ipp:mcp_weather_forecast_daily`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/expert.md:22`、`src/basic_qa/skills/air-quality-basic-query/references/fast.md:27`、`src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/expert.md:28`、`src/basic_qa/skills/comparison-composition/references/fast.md:31`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/expert.md:29`、`src/basic_qa/skills/compliance-feasibility/references/fast.md:32`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/expert.md:12`、`src/basic_qa/skills/integrated-index-ratio/references/fast.md:12`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/expert.md:28`、`src/basic_qa/skills/trend-analysis/references/fast.md:31` |
| `mcp_weather_forecast_daily_history` | `mcp:ipp:mcp_weather_forecast_daily_history`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/expert.md:22`、`src/basic_qa/skills/air-quality-basic-query/references/fast.md:27`、`src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/expert.md:28`、`src/basic_qa/skills/comparison-composition/references/fast.md:31`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/expert.md:29`、`src/basic_qa/skills/compliance-feasibility/references/fast.md:32`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/expert.md:12`、`src/basic_qa/skills/integrated-index-ratio/references/fast.md:12`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/expert.md:28`、`src/basic_qa/skills/trend-analysis/references/fast.md:31` |
| `mcp_weather_forecast_hourly` | `mcp:ipp:mcp_weather_forecast_hourly`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/expert.md:22`、`src/basic_qa/skills/air-quality-basic-query/references/fast.md:27`、`src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/broadcast-hour/references/expert.md:12`、`src/basic_qa/skills/broadcast-hour/references/fast.md:12`、`src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/expert.md:28`、`src/basic_qa/skills/comparison-composition/references/fast.md:31`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/expert.md:29`、`src/basic_qa/skills/compliance-feasibility/references/fast.md:32`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/expert.md:12`、`src/basic_qa/skills/integrated-index-ratio/references/fast.md:12`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/expert.md:28`、`src/basic_qa/skills/trend-analysis/references/fast.md:31` |
| `mcp_weather_forecast_hourly_history` | `mcp:ipp:mcp_weather_forecast_hourly_history`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/expert.md:22`、`src/basic_qa/skills/air-quality-basic-query/references/fast.md:27`、`src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/broadcast-hour/references/expert.md:12`、`src/basic_qa/skills/broadcast-hour/references/fast.md:12`、`src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/expert.md:28`、`src/basic_qa/skills/comparison-composition/references/fast.md:31`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/expert.md:29`、`src/basic_qa/skills/compliance-feasibility/references/fast.md:32`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/expert.md:12`、`src/basic_qa/skills/integrated-index-ratio/references/fast.md:12`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/expert.md:28`、`src/basic_qa/skills/trend-analysis/references/fast.md:31` |
| `oneIndexManyCityLine` | `mcp:ipp:oneIndexManyCityLine`（静态登记；动态未验证） | `src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/expert.md:26`、`src/basic_qa/skills/trend-analysis/references/fast.md:29` |
| `text2sql_city_indexes_month_and_year` | `mcp:ipp:text2sql_city_indexes_month_and_year`（静态登记；动态未验证） | `src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/expert.md:26`、`src/basic_qa/skills/comparison-composition/references/fast.md:29` |
| `text2sql_monthly_and_yearly_city_comparison` | `mcp:ipp:text2sql_monthly_and_yearly_city_comparison`（静态登记；动态未验证） | `src/basic_qa/skills/regional-benchmark/SKILL.md:32`、`src/basic_qa/skills/regional-benchmark/references/expert.md:26`、`src/basic_qa/skills/regional-benchmark/references/fast.md:28` |

## 待解决的设计阻断

- 九份材料现分别声明 `required-tools` 和 `optional-tools`；运行时是否按稳定 ID 与实时连接共同校验仍须实现并验收。
- 模式引用的图表已补入允许声明并列为可选；这里发现的图表均不在 Basic QA 业务工具登记。不能凭相似名称补齐或视为已经发现，缺失不阻止正文加载。
- 站点主数据需要可信上游来源；站点历史/预报在材料里仅作补充。不得未经领域确认将其改判为主数据。
- regional-benchmark 日级多城对标来源仍未明确；任何改变领域语义须先补内容 delta。
- text2sql 名称在 `ipp` 静态登记中，不能据此声称本地旧 Text2SQL 工具存在；当前真实连接、远端 schema 和发现仍未验证。
