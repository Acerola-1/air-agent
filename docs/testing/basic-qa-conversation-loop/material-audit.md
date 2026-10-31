# Basic QA Skill 材料与工具静态审计

材料为本次更新后的工作区版本，材料和业务登记字节由脚本快照锁定。只读静态检查，未连接服务、读取部署配置或验证实时发现/授权/参数。

复跑：`.venv/bin/python scripts/audit_basic_qa_skill_materials.py --check`；查看重新生成内容：`.venv/bin/python scripts/audit_basic_qa_skill_materials.py`。快照变化时先人工重审，再更新脚本内快照和报告。

材料版本按 `src/common/capabilities.py` 的相对路径排序 SHA-256 算法计算；此脚本读取失败即终止，不使用其读取失败时的空字节回退。每个 Skill 只有 `SKILL.md` 与单一 `references/rules.md`，不代表运行加载已验证。

静态登记中的短名可构造候选 `mcp:ipp:<名称>`，依据 `src/common/business_registry.py` 和 `src/common/mcp_scoped.py`；实际发现还须经过受保护连接、approved_query_tools、实时发现和权限交集，且短名跨服务重名时会失效。候选不代表当前可用或功能/参数等价。未登记项在当前 Basic QA 静态策略下不可用，不能映射到其他同名或相似工具。

加载分类：总纲 allowed-tools 全部为必需，optional-tools 为空；必需工具缺失阻止加载。下方说明保留数据调用条件与结果缺失时的领域规则；工具缺失与调用后数据缺失是不同情形。工具名称逐项与引用位置见下表。

## 九个 Skill

### air-quality-basic-query

- Skill ID：`skill:basic-qa:air-quality-basic-query`
- 材料版本：`sha256:be7a9bc1ccab39228c1dd1bfc3570d4a411638599f87372ef7255016a5777d13`
- 必需：`helper_get_latest_time`、`mcp_city_common_get_air_quality_forecast`、`mcp_city_common_get_air_quality_realtime_stat`、`mcp_weather_forecast_daily`、`mcp_weather_forecast_daily_history`、`mcp_weather_forecast_hourly`、`mcp_weather_forecast_hourly_history`
- 规则：主数据：实时空气质量；时间基准：helper。气象需调用但缺失可降级；当日未覆盖未来时段才请求空气预报，缺失标注局限。单事实问题先给结论与数值，分析类问题按专家结构展开；不生成图表或画布。

### broadcast-hour

- Skill ID：`skill:basic-qa:broadcast-hour`
- 材料版本：`sha256:d9af6c0b044b7680d317f61274faecf0f0f828ce7abc1438e2a9b769a896ec81`
- 必需：`broadcastHour`、`helper_get_latest_time`、`mcp_city_common_get_air_quality_forecast`、`mcp_weather_forecast_daily`、`mcp_weather_forecast_daily_history`、`mcp_weather_forecast_hourly`、`mcp_weather_forecast_hourly_history`
- 规则：主数据：broadcastHour；时间基准：helper。小时气象按时段调用；当日小时未覆盖全天才请求空气预报，缺失标注局限。火点/轨迹等补充缺失则剔除，不推定工具；不生成图表或画布。

### comparison-composition

- Skill ID：`skill:basic-qa:comparison-composition`
- 材料版本：`sha256:ddcd91c945de78561e7093559d3f557f2be3fddeeb80e37358d71f9a413d8cce`
- 必需：`helper_get_latest_time`、`integratedIndexRatio`、`mcp_city_common_get_air_quality_realtime_stat`、`mcp_weather_forecast_daily`、`mcp_weather_forecast_daily_history`、`mcp_weather_forecast_hourly`、`mcp_weather_forecast_hourly_history`、`text2sql_city_indexes_month_and_year`
- 规则：同比：text2sql 与实时工具均在流程中；占比：按需 integratedIndexRatio。两者均列为加载必需，具体业务用途按流程。时间基准：helper；气象缺失可降级；不生成图表或画布。

### compliance-feasibility

- Skill ID：`skill:basic-qa:compliance-feasibility`
- 材料版本：`sha256:ee76aff7cdf4e0a3a2f49668a7ff9cf1cb079b1969a94413ca6b43500cb4d8a3`
- 必需：`helper_get_latest_time`、`mcp_city_analysis_compliance_prediction`、`mcp_city_common_get_air_quality_forecast`、`mcp_city_common_get_air_quality_realtime_stat`、`mcp_weather_forecast_daily`、`mcp_weather_forecast_daily_history`、`mcp_weather_forecast_hourly`、`mcp_weather_forecast_hourly_history`
- 规则：主数据：compliance_prediction；时间基准：helper；实时为补充。气象缺失标注局限；仅当日未来未覆盖时请求预报，失败降级；不生成图表或画布。

### integrated-index-ratio

- Skill ID：`skill:basic-qa:integrated-index-ratio`
- 材料版本：`sha256:5a752cd18da7c38844cb4458e99acfdabd2c22dcfe45052ff79875b6b2225016`
- 必需：`helper_get_latest_time`、`integratedIndexRatio`、`mcp_city_common_get_air_quality_forecast`、`mcp_weather_forecast_daily`、`mcp_weather_forecast_daily_history`、`mcp_weather_forecast_hourly`、`mcp_weather_forecast_hourly_history`
- 规则：主数据：integratedIndexRatio；时间基准：helper。气象仅用户要求解释时调用；预报仅明确追问未来风险时可调用。遥感/产业等补充缺失剔除，不推定工具；不生成图表或画布。

### ranking-assessment

- Skill ID：`skill:basic-qa:ranking-assessment`
- 材料版本：`sha256:18f59fd48510de11e879bd90eb1bf5c20698a0cc25e2c6ef1aebd7844d6c58a0`
- 必需：`cityFactorRankings`、`helper_get_latest_time`、`mcp_city_common_get_air_quality_realtime_stat`
- 规则：主数据：cityFactorRankings；时间基准：helper；实时工具可作补充。补充字段缺失只分析已有事实；不生成图表或画布。

### regional-benchmark

- Skill ID：`skill:basic-qa:regional-benchmark`
- 材料版本：`sha256:8e4bb86a974e4f702ec67f8df3280a175d35125a7be7e75f7deb01d6f259b224`
- 必需：`helper_get_latest_time`、`mcp_city_analysis_calculations`、`text2sql_monthly_and_yearly_city_comparison`
- 规则：按场景分别需要 text2sql 月年对标主数据或 calculations 区域达标主数据；时间基准：helper；日级多城对标与该流程的对应关系未明确；不生成图表或画布。

### station-extreme

- Skill ID：`skill:basic-qa:station-extreme`
- 材料版本：`sha256:e6b58fb9e36cffaa7552326ea2a8f4930663638eb27d657db6cbb6c228dbddeb`
- 必需：`helper_get_latest_time`、`mcp_city_common_get_air_quality_realtime_stat`、`mcp_station_base_air_station_list`、`mcp_station_base_get_info_by_code_name`、`mcp_station_base_lng_lat_near_national_province_station`、`mcp_station_base_range_site_list`、`mcp_station_common_air_data`、`mcp_station_common_air_quality_forecast`
- 规则：必须先有上游或上下文站点主数据，否则直接缺失处理；实时城市、站点历史与预报仅称补充，不能推定其为主数据来源。站点基础工具可在需要基础信息时使用；不生成图表或画布。

### trend-analysis

- Skill ID：`skill:basic-qa:trend-analysis`
- 材料版本：`sha256:becda63dec282dd54b34000158eb7e1fc531331d03a5e094798a959dfa3fb5aa`
- 必需：`helper_get_latest_time`、`mcp_city_common_get_air_quality_forecast`、`mcp_city_common_get_air_quality_realtime_stat`、`mcp_weather_forecast_daily`、`mcp_weather_forecast_daily_history`、`mcp_weather_forecast_hourly`、`mcp_weather_forecast_hourly_history`、`oneIndexManyCityLine`
- 规则：主数据：oneIndexManyCityLine；时间基准：helper；实时为补充。气象需调用但缺失可降级；仅当日/未来趋势请求预报；不生成图表或画布。

## 工具名称对照（逐项）

| 材料名称 | 静态稳定 ID / 状态 | 引用位置 |
|---|---|---|
| `broadcastHour` | `mcp:ipp:broadcastHour`（静态登记；动态未验证） | `src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/broadcast-hour/references/rules.md:32` |
| `cityFactorRankings` | `mcp:ipp:cityFactorRankings`（静态登记；动态未验证） | `src/basic_qa/skills/ranking-assessment/SKILL.md:32`、`src/basic_qa/skills/ranking-assessment/references/rules.md:25` |
| `helper_get_latest_time` | `mcp:ipp:helper_get_latest_time`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/rules.md:21`、`src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/broadcast-hour/references/rules.md:28`、`src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/rules.md:22`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/rules.md:23`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/rules.md:27`、`src/basic_qa/skills/ranking-assessment/SKILL.md:32`、`src/basic_qa/skills/ranking-assessment/references/rules.md:21`、`src/basic_qa/skills/regional-benchmark/SKILL.md:32`、`src/basic_qa/skills/regional-benchmark/references/rules.md:21`、`src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/rules.md:22` |
| `integratedIndexRatio` | `mcp:ipp:integratedIndexRatio`（静态登记；动态未验证） | `src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/rules.md:27`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/rules.md:31` |
| `mcp_city_analysis_calculations` | `mcp:ipp:mcp_city_analysis_calculations`（静态登记；动态未验证） | `src/basic_qa/skills/regional-benchmark/SKILL.md:32`、`src/basic_qa/skills/regional-benchmark/references/rules.md:26` |
| `mcp_city_analysis_compliance_prediction` | `mcp:ipp:mcp_city_analysis_compliance_prediction`（静态登记；动态未验证） | `src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/rules.md:27`、`src/basic_qa/skills/compliance-feasibility/references/rules.md:42` |
| `mcp_city_common_get_air_quality_forecast` | `mcp:ipp:mcp_city_common_get_air_quality_forecast`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/rules.md:27`、`src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/broadcast-hour/references/rules.md:34`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/rules.md:30`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/rules.md:44`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/rules.md:29` |
| `mcp_city_common_get_air_quality_realtime_stat` | `mcp:ipp:mcp_city_common_get_air_quality_realtime_stat`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/rules.md:25`、`src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/rules.md:26`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/rules.md:28`、`src/basic_qa/skills/ranking-assessment/SKILL.md:32`、`src/basic_qa/skills/ranking-assessment/references/rules.md:25`、`src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/references/rules.md:26`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/rules.md:27` |
| `mcp_station_base_air_station_list` | `mcp:ipp:mcp_station_base_air_station_list`（静态登记；动态未验证） | `src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/SKILL.md:56` |
| `mcp_station_base_get_info_by_code_name` | `mcp:ipp:mcp_station_base_get_info_by_code_name`（静态登记；动态未验证） | `src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/SKILL.md:57` |
| `mcp_station_base_lng_lat_near_national_province_station` | `mcp:ipp:mcp_station_base_lng_lat_near_national_province_station`（静态登记；动态未验证） | `src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/SKILL.md:57` |
| `mcp_station_base_range_site_list` | `mcp:ipp:mcp_station_base_range_site_list`（静态登记；动态未验证） | `src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/SKILL.md:56` |
| `mcp_station_common_air_data` | `mcp:ipp:mcp_station_common_air_data`（静态登记；动态未验证） | `src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/references/rules.md:27` |
| `mcp_station_common_air_quality_forecast` | `mcp:ipp:mcp_station_common_air_quality_forecast`（静态登记；动态未验证） | `src/basic_qa/skills/station-extreme/SKILL.md:38`、`src/basic_qa/skills/station-extreme/references/rules.md:28` |
| `mcp_weather_forecast_daily` | `mcp:ipp:mcp_weather_forecast_daily`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/rules.md:26`、`src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/rules.md:28`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/rules.md:29`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/rules.md:32`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/rules.md:28` |
| `mcp_weather_forecast_daily_history` | `mcp:ipp:mcp_weather_forecast_daily_history`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/rules.md:26`、`src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/rules.md:28`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/rules.md:29`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/rules.md:32`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/rules.md:28` |
| `mcp_weather_forecast_hourly` | `mcp:ipp:mcp_weather_forecast_hourly`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/rules.md:26`、`src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/broadcast-hour/references/rules.md:33`、`src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/rules.md:28`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/rules.md:29`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/rules.md:32`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/rules.md:28` |
| `mcp_weather_forecast_hourly_history` | `mcp:ipp:mcp_weather_forecast_hourly_history`（静态登记；动态未验证） | `src/basic_qa/skills/air-quality-basic-query/SKILL.md:32`、`src/basic_qa/skills/air-quality-basic-query/references/rules.md:26`、`src/basic_qa/skills/broadcast-hour/SKILL.md:27`、`src/basic_qa/skills/broadcast-hour/references/rules.md:33`、`src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/rules.md:28`、`src/basic_qa/skills/compliance-feasibility/SKILL.md:32`、`src/basic_qa/skills/compliance-feasibility/references/rules.md:29`、`src/basic_qa/skills/integrated-index-ratio/SKILL.md:24`、`src/basic_qa/skills/integrated-index-ratio/references/rules.md:32`、`src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/rules.md:28` |
| `oneIndexManyCityLine` | `mcp:ipp:oneIndexManyCityLine`（静态登记；动态未验证） | `src/basic_qa/skills/trend-analysis/SKILL.md:29`、`src/basic_qa/skills/trend-analysis/references/rules.md:26` |
| `text2sql_city_indexes_month_and_year` | `mcp:ipp:text2sql_city_indexes_month_and_year`（静态登记；动态未验证） | `src/basic_qa/skills/comparison-composition/SKILL.md:31`、`src/basic_qa/skills/comparison-composition/references/rules.md:26` |
| `text2sql_monthly_and_yearly_city_comparison` | `mcp:ipp:text2sql_monthly_and_yearly_city_comparison`（静态登记；动态未验证） | `src/basic_qa/skills/regional-benchmark/SKILL.md:32`、`src/basic_qa/skills/regional-benchmark/references/rules.md:25` |

## 待解决的设计阻断

- 九份材料均声明 `required-tools`；运行时是否按稳定 ID 与实时连接共同校验仍须实现并验收。
- 图表与画布工具已退役，材料不再声明或引用；用户要求图表时用 Markdown 表格表达。
- 站点主数据需要可信上游来源；站点历史/预报在材料里仅作补充。不得未经领域确认将其改判为主数据。
- regional-benchmark 日级多城对标来源仍未明确；任何改变领域语义须先补内容 delta。
- text2sql 名称在 `ipp` 静态登记中，不能据此声称本地旧 Text2SQL 工具存在；当前真实连接、远端 schema 和发现仍未验证。
