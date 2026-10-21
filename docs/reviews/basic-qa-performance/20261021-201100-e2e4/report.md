# basic QA 真实聊天性能基线报告：20261021-201100-e2e4

规范：[basic QA 并发性能回归规范](../../../testing/basic-qa-performance-regression.md)。

## 1. 结论与范围

| 项目 | 本轮结果 |
| --- | --- |
| run_id / 规范版本 / schema | 20261021-201100-e2e4 / v1.2 / basic-qa-perf.v1 |
| 档位 | E / S（单用户、单并发、40 题各一次；非 R/F） |
| 声明范围内结论 | BASELINE_CANDIDATE（含已知依赖缺失与晚到样本） |
| 测试时间、时区 | 2026-10-21 20:10:34 — 2026-10-21 20:49:48（Asia/Shanghai；记录 UTC） |
| 计划/发送/按时完成成功 | 40 / 40 / 36 |
| 晚到但完成（不计按时成功） | 0：[] |
| 质量/结构检查通过 | 26/40 |
| 真实模型 | hy3（配置请求 hy3；经本机 OpenAI 兼容网关） |
| MCP | ipp shared_readonly real MCP (https://www.mapairs.com/product/datacenter/api2/mcp) |
| 浏览器 | 未执行；接口首正文不能替代浏览器首屏 |

真实/受控边界：主模型 `hy3` 经本机 OpenAI 兼容网关真实调用；工具为已批准的共享只读 ipp MCP 真实端点；IAM、PostgreSQL、Redis、LangGraph API 和 BFF 均为本轮自建的隔离服务，不使用正式业务库。知识库/RAG 未在本轮隔离栈提供，因此 Q1 的降级属于依赖缺失，不是模型知识能力结论。

一句话结论：普通用户在本轮单用户、单并发、真实模型和真实共享 MCP 下，按时完成 36/40 题；按时成功样本的接口首正文 P50/P95 为 28963.0/104350.0 ms，完整回答 P50/P95 为 29139.5/104572.25 ms。小样本 P95 只是探索值，不是稳定 SLA 或容量承诺。

## 2. 请求守恒与延迟

| 分类 | 计划/发送 | 按时成功 | 晚到 | 首正文 P50/P95 (ms) | 完整 P50/P95 (ms) |
| --- | --- | --- | --- | --- | --- |
| Q1 | 10/10 | 10 | 0 | 11042.0/29238.94999999999 | 11201.0/29444.59999999999 |
| Q2 | 10/10 | 7 | 0 | 55837.0/88482.09999999998 | 56017.0/88687.79999999999 |
| Q3 | 10/10 | 10 | 0 | 49865.0/141854.04999999996 | 50060.0/142058.99999999994 |
| Q4 | 10/10 | 9 | 0 | 10769.0/56327.399999999994 | 10944.0/56575.2 |

超时/失败分布：{"client_timeout": 4}。晚到成功保留实际完成耗时，但不回填按时成功；截止值不冒充完成耗时。

历史预置：另发送 50 条 Q4 历史用户轮，主结果 {"stream_completed": 50}；不计入 40 题分母，但保留在原始请求中。

最慢的按时成功/晚到题目：

| 题号 | 分类 | 首正文 ms | 完成 ms | 截止 s | 结果 |
| --- | --- | --- | --- | --- | --- |
| BQA-039 | Q4 | None | 180406 | 180 | client_timeout |
| BQA-030 | Q3 | 160561 | 160761 | 180 | completed_success |
| BQA-017 | Q2 | None | 120359 | 120 | client_timeout |
| BQA-019 | Q2 | None | 120355 | 120 | client_timeout |
| BQA-018 | Q2 | None | 120340 | 120 | client_timeout |
| BQA-021 | Q3 | 118990 | 119201 | 180 | completed_success |
| BQA-029 | Q3 | 99470 | 99696 | 180 | completed_success |
| BQA-016 | Q2 | 97861 | 98073 | 120 | completed_success |

| 分类 | 质量通过/发送 | 未通过原因（摘要） |
| --- | --- | --- |
| Q1 | 2/10 | {"failure_marker": 8, "missing:AQI/指数": 1, "missing:浓度": 2, "missing:PM2.5": 1, "missing:PM10": 1, "missing:直径": 1, "missing:首要污染物": 1, "missing:排放": 1, "missing:综合指数": 1, "missing:AQI": 1, "missing:不同/区别": 1, "missing:微克": 1, "missing:毫克": 1, "missing:1000": 1, "missing:8小时/8 小时/八小时": 1, "missing:平均": 1, "missing:分指数": 1, "missing:100%/百分比/占比": 1, "missing:企业": 1, "missing:来源/排放源": 1} |
| Q2 | 7/10 | {"empty_answer": 3} |
| Q3 | 9/10 | {"failure_marker": 1, "missing:优良": 1, "missing:CO/臭氧": 1, "no_number": 1} |
| Q4 | 8/10 | {"failure_marker": 1, "empty_answer": 1} |

## 3. 模型、工具与用量

### 3.1 摘要

| 项目 | 本轮实测 |
| --- | --- |
| 模型调用 | 323 次（90 条请求，约 3.6 次/请求），全部 `succeeded`；耗时 P50 6.5 秒、P95 33.9 秒 |
| 工具尝试 | 448 次（约 11.2 次/请求），成功 195、失败 253（56.5%）；耗时 P50 0.58 秒、P95 1.3 秒 |
| Token | 输入 7,913,844、输出 300,096、总计 8,213,940；323/323 次有 usage，覆盖率 100% |
| 费用 | 未采集：没有价格表；未知价格不记 0 |
| 资源 | 仅测量后空闲 8 秒快照（LangGraph 约 783 MB、PG 约 368 MB、Redis 约 15 MB）；Q1–Q3 测量窗未采集 |

### 3.2 机器可读明细

- 模型观测：{"logical_or_observed": 323, "succeeded": 323, "failed": 0, "by_role": {"main": 323}, "duration_p50_ms": 6519.0, "duration_p95_ms": 33894.99999999999, "ttft_p50_ms": null, "ttft_p95_ms": null}
- 工具尝试：{"attempts": 448, "succeeded": 195, "failed": 253, "by_tool": {"mcp_station_analysis_concentration_table": 25, "knowledge_search": 11, "websearch_tool": 1, "mcp_city_common_get_air_quality_realtime_stat": 78, "get_beijing_time": 4, "helper_get_latest_time": 18, "mcp_city_analysis_monitor": 19, "mcp_city_common_area_city_ranking": 5, "text2sql_yesterday_single_index_aqi_route": 9, "mcp_city_common_factor_area_ranking": 4, "mcp_station_analysis_index_ranking": 3, "mcp_station_common_air_data": 132, "mcp_city_common_factor_province_ranking": 6, "cityFactorRankings": 1, "mcp_city_analysis_nationalOrder": 2, "mcp_city_common_area_province_ranking": 5, "text2sql_today_single_city_aqi_route": 2, "mcp_station_base_air_station_list": 42, "mcp_station_analysis_concentration_ranking": 5, "mcp_city_base_get_child_district_by_parent": 1, "mcp_city_common_area_multi_ranking": 1, "mcp_city_analysis_calendar_day": 4, "text2sql_monthly_and_yearly_city_comparison": 5, "concentration_ranking": 2, "mcp_city_analysis_calculations": 4, "broadcastHour": 8, "helper_getLatestTime2": 8, "text2sql_city_indexes_month_and_year": 9, "mcp_station_common_good_days_query": 2, "integratedIndexRatio": 11, "compositeIndexSixFactorRatio": 5, "mcp_weather_forecast_daily_history": 1, "oneIndexManyCityLine": 1, "mcp_weather_forecast_hourly_history": 1, "mcp_city_common_get_air_quality_forecast": 10, "mcp_station_common_air_real_data": 1, "mcp_station_base_get_info_by_code_name": 1, "mcp_station_analysis_broadcast_hour": 1}, "duration_p50_ms": 579.0, "duration_p95_ms": 1299.7499999999986}
- 调用关联覆盖：771 次模型/工具调用中 681 次映射到本轮 sample（88.3%）；90 条未映射来自中止段/预检或无 attempt 关联。
- Token：{"model_observations": 323, "usage_provided": 323, "input_tokens": 7913844, "output_tokens": 300096, "total_tokens": 8213940, "missing_reason": null, "price": null, "cost": null, "cost_note": "未提供价格表，未知价格不记 0"}
- 资源：{"aa-perf-20261021-langgraph": {"samples": 2, "cpu_mean_percent": 0.18, "cpu_max_percent": 0.18, "memory_mean_bytes": 782784512, "memory_max_bytes": 782909440, "memory_limit_bytes": 1073741824}, "aa-perf-20261021-pg": {"samples": 2, "cpu_mean_percent": 0.07, "cpu_max_percent": 0.07, "memory_mean_bytes": 368136192, "memory_max_bytes": 368181248, "memory_limit_bytes": 1073741824}, "aa-perf-20261021-redis": {"samples": 2, "cpu_mean_percent": 0.14, "cpu_max_percent": 0.14, "memory_mean_bytes": 15345664, "memory_max_bytes": 15523840, "memory_limit_bytes": 1073741824}}

## 4. 质量、失败与边界

- 检查项：26/40 PASS；质量通过 26/40。
- 合成 gold 数值与真实 MCP 数据不可比；本轮质量规则检查流完成、台账关联、必要实体/日期/指标词、工具实际调用和数字存在性，不把真实数据值判成合成 gold 错误。
- Q1 的降级答复需单独看：概念题被路由到知识检索链路，当前隔离栈未提供知识库服务/授权，返回统一降级文本；这是依赖缺失与产品降级行为，不是模型知识能力结论。
- 未执行：浏览器渲染、R/F 并发、开放到达、长稳、故障注入、跨用户/跨企业隔离、价格与成本（无价格表，未知不记 0）。
- 回答正文仅保存在受限证据目录；性能 bundle 不含题目/回答正文。

## 5. 资源回收

本轮自建资源已全部回收：容器 `aa-perf-20261021-langgraph`、`aa-perf-20261021-pg`、`aa-perf-20261021-redis` 已停止并删除；IAM 25445、Admin API 25446、BFF 25448、宿主桥 25450 已停止，端口已释放；测试会话、随机凭据、cookie 和临时 env 已删除。自建 PostgreSQL 随容器删除，未连接 15432/19132 或正式业务库；未动共享 Langfuse、知识库或正式栈。回答正文保留在 `.local/perf-20261021/evidence-final/answers.jsonl` 受控目录，未进入公开 bundle。

## 6. 证据与复跑

证据文件：`manifest.json`、`summary.json`、`requests.jsonl`、`calls.jsonl`、`resources.jsonl`、`checks.jsonl`、`browser.jsonl`、`evidence-index.json`、`precheck.jsonl`、`aborted-runs/`。

复跑命令：

```bash
.venv/bin/python -m scripts.product_chat_perf.run_baseline \
  --endpoints <隔离端点.json> \
  --performance-out docs/reviews/basic-qa-performance/<run_id> \
  --evidence-out .local/<受控证据目录> --enable-real-models
```
