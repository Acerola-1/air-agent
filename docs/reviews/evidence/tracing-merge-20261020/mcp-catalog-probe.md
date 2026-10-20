# 真实 MCP 目录与最小样本（2026-10-20）

目的：为 `product → tool` 映射、参数构造和返回归一化提供真实证据（任务 3.1/3.2）。
方式：对部署配置中的 `https://www.mapairs.com` MCP 端点做只读 `tools/list` 和最小
`tools/call`；未写入、未使用浏览器地址或自造凭据。该探测是**匿名只读目录核对**，
不是“授权测试企业”运行证据；业务许可下的真实调用仍属 G1（8.3）未执行项。

## 目录

- `tools/list` 返回 109 个工具；本次冻结相关 schema 见 `mcp-selected-schemas.json`。
- 业务注册表 `IPP_APPROVED_QUERY_TOOLS` 与目录一致；`intelligent-tracing` 子集额外排除
  `mcp_station_analysis_trajectory`、`mcp_station_analysis_single_pscf`、
  `mcp_station_analysis_history_pscf`。

## 已验证（真实返回）

| 产品 | 工具 | 关键参数 | 返回形状 | 备注 |
|---|---|---|---|---|
| 站点目录 | `mcp_station_base_air_station_list` | `region`（中文名）、`stationType`（`S-10`） | `data[]`：`stationKey`、`name`、`regionKey`、`regionName`、`latitude`/`longitude`（字符串）、`distance` | 13 个国控站样本；坐标用于本地 Haversine 邻站筛选 |
| 站点小时序列 | `mcp_station_common_air_data` | `region`、`station`（stationKey 或站名）、`type=hourly`、`sTime`、`eTime` | `data[]`：`updateTime`、`stationKey`、`pm25`/`pm10`/`o3`/`no2`/`so2`/`co`（字符串） | 值可能为 `-` → 缺失；CO 平台口径按 mg/m3 处理 |
| 历史气象（NWP） | `mcp_weather_forecast_hourly_history` | `region`、`sTime`、`eTime`（`yyyy-MM-dd HH:00`） | `data[[...]]` 嵌套：`updateTime`、`windSpeed`、`windDirection`、`pblh`、`humidity`、`pressure`、`temperature` | u/v 由风速风向确定性推导；`pblh=-1` 是无效哨兵 |
| 网格目录 | `mcp_grid_base_grid_list_by_region` | `region` | `data[]`：`hash`、`name`、`areaKey`、`regionName` 等 | 947 行；无坐标 |
| 网格序列 | `mcp_grid_common_air_data` | `region`、`type=hourly`、`index`、`sTime`、`eTime` | `data[]`：`name`、`updateTime`、`value`、`level` | 2706 行，`name` 与网格目录一一匹配 |
| 网格几何 | `mcp_grid_base_grid_info_by_codes` / `mcp_grid_base_grid_info_by_hash` | `region` + 逗号连接 `name` / `hash` | 单条/列表：`longitude`、`latitude`、`name`、`areaName` | 一次调用取多个网格坐标 |

## 未验证/不可用（真实错误原文摘要）

| 候选 | 结果 | 处理 |
|---|---|---|
| `mcp_station_base_range_site_list` | 400：`Optional int parameter 'number' ... primitive type`（传字符串或整数均失败） | 不进入映射；邻站改用目录坐标 + Haversine |
| `mcp_station_base_lng_lat_near_national_province_station` | `longitude:不能为空`（字符串/数值均失败） | 不进入映射 |
| `mcp_grid_base_grid_by_station_proximity` | 后端 `DataBufferLimitException`（20 MB），500 m 也失败 | 不进入映射 |
| `mcp_grid_common_air_data` + `hash`/`gridHashType` | `gridHashType:网格参数类型指定错误`（`0/1/2/hash/gridHash/HASH` 均失败） | 网格按 `region` 取全市序列，坐标经 `info_by_codes` 补齐 |
| `mcp_station_analysis_trajectory` | 400：`cl:不能为空` | 业务注册表排除；不作为第一版能力 |
| `mcp_station_analysis_single_pscf` | 后端 500 `UnsupportedMediaTypeException` | 业务注册表排除 |

## 与设计的差异

1. 站点目录用 `stationKey`，不是 `stationCode`；报告/请求仍以 `stationKey` 作为站点代码。
2. 邻站目录工具不可用，改为“目录坐标 + 本地距离筛选 + 逐站序列”；最多 5 个邻站。
3. 网格工具返回区域级/网格级数值序列，几何需二次调用；`GridCell.row/col` 由经纬度按
   0.01° 量化得到，仅用于确定性排序/连片统计，不表示官方网格拓扑。
4. 网格按“时间窗内最大值”选前 24 个，不等于全量网格覆盖；`DataBundle.missing` 会保留缺失产品。

## 授权与门禁

`BusinessMCPDataSource` 只经 `common.mcp_scoped.execute_mcp_tool` 调用；业务子集、连接
`approved_query_tools`、本次已受理工具三者交集由执行层强制。缺少可信业务快照时在调用前
失败关闭（见 `tests/unit_tests/tracing/test_scoped_mcp_source.py`）。
