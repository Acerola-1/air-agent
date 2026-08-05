---
name: trend-analysis
description: |
    分析城市 AQI、综合指数或污染物浓度在连续多日、近N天、过去一周、指定日期范围内的变化趋势、走势、峰值、低值和波动特征。

    触发关键词：
    - 趋势
    - 变化趋势
    - 走势
    - 变化情况
    - 过去一周
    - 最近几天
    - 近N天
    - 1号到10号
    - 连续多日
    - PM2.5趋势
    - 臭氧趋势
    - AQI变化

    多日趋势场景：
    - 过去一周某市PM2.5浓度变化趋势如何？
    - 过去五天某市O3-8h浓度走势图？
    - 本月1号到10号，某市AQI变化情况？
    - 最近7天某市综合指数走势如何？
    - 2026年5月1日至5月8日某市PM10浓度是升还是降？
    - 近三天某市臭氧峰值和低值分别是多少？
    - 上周某市空气质量等级变化情况？

allowed-tools: helper_get_latest_time oneIndexManyCityLine mcp_weather_forecast_daily mcp_weather_forecast_hourly mcp_weather_forecast_daily_history mcp_weather_forecast_hourly_history mcp_city_common_get_air_quality_realtime_stat mcp_city_common_get_air_quality_forecast
---

# 趋势分析

## 详细规则入口

- 本文件只用于技能识别、路由摘要和通用约束；完整执行流程位于 `references/fast.md` 或 `references/expert.md`。
- 系统已按当前模式把对应详细规则内联在本提示词 `=====` 分隔线之后，直接按该规则执行数据获取、校验、缺失处理和输出组织；不要用本文件摘要替代详细规则。

## 执行边界

- 仅负责本技能的数据查询、结果校验和输出组织；回答面向普通业务用户，不暴露内部实现细节。
- 用户询问连续多日、近N天、过去一周、日期范围内的 AQI、综合指数、空气质量等级或污染物浓度趋势、走势、变化情况、峰值、低值、均值、升降波动时，属于本技能。
- 用户只询问单日或实时空气质量、同比较去年、排名位次、达标可行性或站点极值时，不属于本技能。
- 主数据为空时，直接进入数据缺失处理，不得生成空模板、猜测指标或编造结论。
