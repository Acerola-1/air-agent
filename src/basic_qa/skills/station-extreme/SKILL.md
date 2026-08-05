---
name: station-extreme
description: |
    查询城市监测站点的空气质量数据,支持查询最差站点、污染站点、AQI或污染物浓度最高站点、超过指定污染等级或阈值的站点名单。

    触发关键词：
    - 站点
    - 最差站点
    - 最差的站点
    - 哪个站点污染
    - 污染站点
    - 轻度污染站点
    - 站点极值
    - 浓度最高的站点
    - AQI最高站点
    - 超标站点
    - 国控站点

    站点极值场景：
    - 昨日某市空气质量最差的站点的是哪个站点？
    - 今日某市空气质量最差的国控站点及综指？
    - 昨天某市PM2.5浓度最高的站点名称及数值？
    - 今日某市AQI最高的站点是哪一个？
    - 昨日某市臭氧浓度最高站点及污染等级？

    污染站点名单场景：
    - 昨日某市哪个站点轻度污染了？
    - 今日某市AQI≥101的站点及污染等级？
    - 昨天某市PM10轻度污染的站点名单？
    - 今日某市有哪些站点达到中度污染？
    - 昨日某市PM2.5超标站点有哪些？

    站点基础数据查询：
    - 最近 7 天 某市的某站点 AQI怎么样？
    - 昨天 某站点的空气质量如何
    - 查询今天某市的国控站 AQI 排名

allowed-tools: helper_get_latest_time mcp_city_common_get_air_quality_realtime_stat mcp_station_common_air_quality_forecast mcp_station_common_air_data mcp_station_base_air_station_list mcp_station_base_range_site_list mcp_station_base_lng_lat_near_national_province_station mcp_station_base_get_info_by_code_name
---

# 站点极值与污染查询

## 详细规则入口

- 本文件只用于技能识别、路由摘要和通用约束；完整执行流程位于 `references/fast.md` 或 `references/expert.md`。
- 系统已按当前模式把对应详细规则内联在本提示词 `=====` 分隔线之后，直接按该规则执行数据获取、校验、缺失处理和输出组织；不要用本文件摘要替代详细规则。

## 执行边界

- 仅负责本技能的数据查询、结果校验和输出组织；回答面向普通业务用户，不暴露内部实现细节。
- 用户询问监测站点、国控站、省控站、市控站的最差、最高、最低、极值、污染等级、超标、站点名单时，属于本技能。
- 用户只询问城市整体 AQI、空气质量等级、首要污染物、城市排名或区域统计，不涉及站点时，不属于本技能。
- 当前技能未配置专用站点数据工具；没有运行上下文或上游结果提供站点主数据时，必须按数据缺失处理，不得编造站点名称或监测值。
- 如果需要站点基础信息,可自由使用`mcp_station_base_air_station_list`,`mcp_station_base_range_site_list`,
  `mcp_station_base_lng_lat_near_national_province_station`,`mcp_station_base_get_info_by_code_name`这些工具
- 主数据为空时，直接进入数据缺失处理，不得生成空模板、猜测指标或编造结论。
