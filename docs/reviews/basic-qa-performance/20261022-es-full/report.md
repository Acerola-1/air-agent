# basic QA 真实聊天性能基线报告：20261022-es-full

规范：[basic QA 并发性能回归规范](../../../testing/basic-qa-performance-regression.md)。

## 1. 结论与范围

| 项目 | 本轮结果 |
| --- | --- |
| run_id / 规范版本 / schema | 20261022-es-full / v1.2 / basic-qa-perf.v1 |
| 档位 | E / S（单用户、单并发、40 题各一次；非 R/F） |
| 声明范围内结论 | BASELINE_CANDIDATE（含已知依赖缺失与晚到样本） |
| 测试时间、时区 | 2026-10-22 01:38:42 — 2026-10-22 02:19:52（Asia/Shanghai；记录 UTC） |
| 计划/发送/按时完成成功 | 40 / 40 / 32 |
| 晚到但完成（不计按时成功） | 0：[] |
| 质量/结构检查通过 | 28/40 |
| 真实模型 | hy3（配置请求 hy3；经本机 OpenAI 兼容网关） |
| MCP | ipp shared_readonly real MCP |
| 浏览器 | 未执行；接口首正文不能替代浏览器首屏 |

一句话结论：普通用户在本轮单用户、单并发、真实模型和真实共享 MCP 下，按时完成 32/40 题；按时成功样本的接口首正文 P50/P95 为 13950.5/67074.5 ms，完整回答 P50/P95 为 17660.0/69109.15 ms。小样本 P95 只是探索值，不是稳定 SLA 或容量承诺。

## 2. 请求守恒与延迟

| 分类 | 计划/发送 | 按时成功 | 晚到 | 首正文 P50/P95 (ms) | 完整 P50/P95 (ms) |
| --- | --- | --- | --- | --- | --- |
| Q1 | 10/10 | 7 | 0 | 10790.0/18019.899999999994 | 14069.0/21211.099999999995 |
| Q2 | 10/10 | 7 | 0 | 27575.0/53418.299999999996 | 28736.0/54139.6 |
| Q3 | 10/10 | 9 | 0 | 17382.0/84120.0 | 20567.0/88588.4 |
| Q4 | 10/10 | 9 | 0 | 10647.0/47515.600000000006 | 11992.0/49675.4 |

超时/失败分布：{"runtime_aborted": 6, "client_timeout": 2}。晚到成功保留实际完成耗时，但不回填按时成功；截止值不冒充完成耗时。

历史预置：另发送 50 条 Q4 历史用户轮，主结果 {"stream_completed": 50}；不计入 40 题分母，但保留在原始请求中。

最慢的按时成功/晚到题目：

| 题号 | 分类 | 首正文 ms | 完成 ms | 截止 s | 结果 |
| --- | --- | --- | --- | --- | --- |
| BQA-017 | Q2 | None | 120649 | 120 | client_timeout |
| BQA-018 | Q2 | 118128 | 120540 | 120 | client_timeout |
| BQA-022 | Q3 | 103441 | 105657 | 180 | runtime_aborted |
| BQA-030 | Q3 | 94116 | 99812 | 180 | completed_success |
| BQA-024 | Q3 | 69126 | 71753 | 180 | completed_success |
| BQA-029 | Q3 | 65396 | 66946 | 180 | completed_success |
| BQA-015 | Q2 | 54234 | 55009 | 120 | completed_success |
| BQA-019 | Q2 | None | 54304 | 120 | runtime_aborted |

| 分类 | 质量通过/发送 | 未通过原因（摘要） |
| --- | --- | --- |
| Q1 | 7/10 | {"empty_answer": 3} |
| Q2 | 5/10 | {"failure_marker": 1, "missing:洛阳": 1, "missing:国控": 1, "missing:站": 1, "no_number": 1, "empty_answer": 3} |
| Q3 | 8/10 | {"empty_answer": 1, "missing:PM2.5": 1, "missing:趋势": 1, "no_number": 1} |
| Q4 | 8/10 | {"missing:优良": 1, "missing:增加": 1, "empty_answer": 1} |

## 3. 模型、工具与用量

- 模型观测：{"logical_or_observed": 373, "succeeded": 367, "failed": 6, "by_role": {"other": 348, "main": 25}, "duration_p50_ms": 3998.0, "duration_p95_ms": 19014.19999999999, "ttft_p50_ms": 1143.0, "ttft_p95_ms": 2031.5000000000005}
- 工具尝试：{"attempts": 352, "succeeded": 105, "failed": 247, "by_tool": {"knowledge_search": 4, "websearch_tool": 5, "mcp_city_common_get_air_quality_realtime_stat": 86, "mcp_city_analysis_monitor": 22, "helper_get_latest_time": 20, "helper_getLatestTime2": 6, "text2sql_yesterday_single_index_aqi_route": 7, "mcp_city_common_area_province_ranking": 10, "text2sql_today_single_city_aqi_route": 4, "mcp_city_common_factor_province_ranking": 7, "mcp_city_analysis_nationalOrder": 6, "mcp_city_common_factor_area_ranking": 4, "mcp_city_base_get_child_district_by_parent": 3, "parse_region_tool": 1, "text2sql_city_indexes_month_and_year": 19, "mcp_station_base_air_station_list": 25, "mcp_station_common_air_data": 41, "compute_artifact": 18, "mcp_station_analysis_concentration_ranking": 4, "mcp_city_common_area_city_ranking": 5, "cityFactorRankings": 3, "broadcastHour": 5, "mcp_station_common_good_days_query": 4, "get_beijing_time": 3, "mcp_city_analysis_calendar_month": 1, "integratedIndexRatio": 7, "create_artifact": 1, "mcp_weather_forecast_hourly_history": 2, "text2sql_monthly_and_yearly_city_comparison": 4, "mcp_station_analysis_concentration_table": 5, "mcp_station_analysis_index_ranking": 2, "mcp_station_analysis_index_stations_24h_avg": 2, "mcp_city_common_get_air_quality_forecast": 3, "mcp_weather_forecast_hourly": 1, "mcp_city_analysis_calendar_day": 3, "mcp_station_common_air_real_data": 1, "concentration_ranking": 3, "mcp_city_analysis_nationalOrderCompareBar": 2, "compositeIndexSixFactorRatio": 2, "mcp_station_analysis_broadcast_hour": 1}, "duration_p50_ms": 484.0, "duration_p95_ms": 935.8499999999966}
- Token：{"model_observations": 373, "usage_provided": 367, "input_tokens": 7109847, "output_tokens": 222861, "total_tokens": 7332708, "missing_reason": "部分尝试未返回 usage 或未采集", "price": null, "cost": null, "cost_note": "未提供价格表，未知价格不记 0"}
- 资源：{"aa-full-20261022-biz-pg": {"samples": 450, "cpu_mean_percent": 0.67, "cpu_max_percent": 2.88, "memory_mean_bytes": 314908362, "memory_max_bytes": 352800768, "memory_limit_bytes": 2147483648}, "aa-full-20261022-graph-redis": {"samples": 450, "cpu_mean_percent": 0.16, "cpu_max_percent": 0.24, "memory_mean_bytes": 14877245, "memory_max_bytes": 15515648, "memory_limit_bytes": 536870912}, "aa-full-20261022-iam-pg": {"samples": 450, "cpu_mean_percent": 0.21, "cpu_max_percent": 1.22, "memory_mean_bytes": 163184339, "memory_max_bytes": 173928448, "memory_limit_bytes": 1073741824}, "aa-full-20261022-iam-redis": {"samples": 450, "cpu_mean_percent": 0.16, "cpu_max_percent": 0.26, "memory_mean_bytes": 13727061, "memory_max_bytes": 13934592, "memory_limit_bytes": 536870912}, "aa-full-20261022-langgraph": {"samples": 450, "cpu_mean_percent": 1.72, "cpu_max_percent": 12.5, "memory_mean_bytes": 765393014, "memory_max_bytes": 812900352, "memory_limit_bytes": 2147483648}}

## 4. 质量、失败与边界

- 检查项：28/40 PASS；质量通过 28/40。
- 合成 gold 数值与真实 MCP 数据不可比；本轮质量规则检查流完成、台账关联、必要实体/日期/指标词、工具实际调用和数字存在性，不把真实数据值判成合成 gold 错误。
- Q1 的降级答复需单独看：概念题被路由到知识检索链路，当前隔离栈未提供知识库服务/授权，返回统一降级文本；这是依赖缺失与产品降级行为，不是模型知识能力结论。
- 未执行：浏览器渲染、R/F 并发、开放到达、长稳、故障注入、跨用户/跨企业隔离、价格与成本（无价格表，未知不记 0）。
- 回答正文仅保存在受限证据目录；性能 bundle 不含题目/回答正文。

## 5. 证据与复跑

证据文件：`manifest.json`、`summary.json`、`requests.jsonl`、`calls.jsonl`、`resources.jsonl`、`checks.jsonl`、`browser.jsonl`、`evidence-index.json`、`precheck.jsonl`、`aborted-runs/`。

复跑命令：

```bash
.venv/bin/python -m scripts.product_chat_perf.run_baseline \
  --endpoints <隔离端点.json> \
  --performance-out docs/reviews/basic-qa-performance/<run_id> \
  --evidence-out .local/<受控证据目录> --enable-real-models
```
