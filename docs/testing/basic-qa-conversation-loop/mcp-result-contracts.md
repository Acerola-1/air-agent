# 历史实验记录：已撤销的结果字段校验

2026-10-28用户明确撤销这套超出需求的回答字段校验。下文仅保留先前采样和实验事实，所述适配器及结束校验已从本次实现移除，不是当前运行要求。

# Basic QA MCP 结果结构观察与适配边界

## 来源与范围

2026-10-28 由主任务通过真实 IAM 和现有 `ScopedMCPFactory` 进行只读采样。私有样本保留于 `.local/bqa-contract-recovery-20261028/`，不复制响应值、账号、连接参数或原始请求到本文。文件 SHA-256 标识采样副本，不是供应商版本或完整合同。下表结果为本地解析观察，不代表服务所有响应都有效或模型回答已验收；样本成功外层状态和 HTTP 成功均不能替代业务数据判断。

| 样本 | SHA-256 | 本次适配观察 |
| --- | --- | --- |
| `sample-broadcastHour.json` | `fac298a0f5b1dba62e97988958a1a6a72b2b94185132b5e3995c11fc4705c3ec` | 提取数值；城市、时间只绑定请求 |
| `sample-cityFactorRankings.json` | `262ea90b07d37f6b74b87af163503be77e79347c35e2cfd63f3b916e750a3ded` | 匹配行的城市来自响应；时间只绑定请求 |
| `sample-integratedIndexRatio.json` | `14e81e66d2b9c368ebb8578705aa79e3a9d5014b2f65182915aeb80658866726` | 匹配表格行的城市来自响应；时间只绑定请求 |
| `sample-mcp_city_analysis_calculations.json` | `fcf3664ca07580b7fe93dded2c6410c2babcf20abc4f01be08c5a5110a877ea4` | 城市来自响应；指标和时间只绑定请求 |
| `sample-mcp_city_analysis_compliance_prediction.json` | `1c33a73bb22b4286088b4c98165d298a601d16a05e84f7990b4b2c4bdf96c0f3` | 城市、指标来自响应；时间只绑定请求 |
| `sample-mcp_city_common_get_air_quality_realtime_stat.json` | `8ca00446d12bac4a05471a71767a93882412841107e5265e7a3f68dda4ef3adf` | 双层状态；城市、时间和数值字段来自响应 |
| `sample-mcp_station_common_air_data.json` | `711231f3ea2a0503fb69189acb4d51f378e8612c9a595d1e3da30db96dfba8b8` | 城市使用响应的 `parentRegionName`，保留站点、时间和数值字段 |
| `sample-mcp_weather_forecast_hourly_history.json` | `1c14d771e1adaeb9f574e4a2d8763c212cdeb557eedb05c550dae36792cbdebd` | 嵌套列表；城市、时间和数值字段来自响应 |
| `sample-text2sql_monthly_and_yearly_city_comparison.json` | `31ca472a8f43dd4b75771fdc595dd61a5afb54b3f57f1b62b50642030ea730e9` | 嵌套列表；匹配行的城市来自响应，时间只绑定请求 |
| `sample-oneIndexManyCityLine.json` | `432ed31e04fe59611b7aa0467c40d9df6b3519bad8e77bcb6c65b16a31634059` | 按观察到的矩阵维度对齐；短时间标签保留原文但未验证年份 |
| `sample-text2sql_city_indexes_month_and_year.json` | `ffe26abf9307c47e7c208200d2d19438d6c574bb75b2738acb057603c08e2060` | 按观察到的列式数据对齐；短时间标签保留原文但未验证年份 |
| `sample-helper_get_latest_time.json` | `e2234a0329b47d6f1744a456312677d772f1cd65f28641a4afe4b6b967cfaa63` | 通用时间助手，不作为业务数据证据 |

## 适配约束

`adapt_basic_qa_evidence(tool_name, arguments, result)` 仅接受已观察到的工具形状、指定层数的 `code=200` 且 `success=true` 包装、非空匹配行及有限数值；非法数值、布尔值、未知结构和业务失败都不能生成有效数值。城市按请求逐项精确比较，含响应时间的行还需处于请求区间；日期精度不足以证明小时区间时拒绝。站点请求有明确站名时，响应站名必须匹配。工具结果中多城市混合的行只保留请求城市；响应未给城市的播报结果只允许单城市请求绑定。

返回中的 `city.source`、`time.source`、`metric_source` 是 `response` 或 `request_bound`。后者仅表示可信执行记录中的调用参数，**不是**响应独立确认该维度；`time.value` 在时间未由响应验证时为 null，请求边界单独存于 `request_binding`。计算结果的指标也只来自参数；`value_field` 保留数值来自的字段。`status=ok` 仅表示至少一个数值通过结构及已实现匹配，不代表结果满足问题全部维度、业务语义正确或允许终答。

适配器本身不建立工具调用、身份、许可、历史证据归属或用户问题到请求参数的可信链；接入者必须从可信执行记录传入参数与响应，按每个回答声明核对所需城市、站点、时间、指标、Skill 及证据来源。请求绑定不能升级为响应验证。缺失或未支持的维度须输出局限，不能仅依据 `ok`、非空 JSON 或外层成功结束。趋势/统计序列只接受已识别的列和对齐长度；含 `MM-DD` 的时间标签保留在 `time_label` 并标记 `verified=false`，不能推断年份。完整年/月标签仅当整个周期落在请求区间时标记已验证。排名与预测等数据仅提取观察到的数值字段，不保证排名、达标或因果结论的业务解释；未实现供应商通用合同、单位与质量控制校验。

测试夹具为根据观察结构人工构造的最小脱敏示例，不使用采样值；覆盖双层失败、空/未知结构、错城、时间不匹配、精度不足、站点不匹配、有限数值与非法数值、请求绑定来源。
