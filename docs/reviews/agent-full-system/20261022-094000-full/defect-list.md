# 缺陷清单（按严重度）

## D1（高）流式完整性策略在非 policy 问题中途中断回答

- 现象：用户先看到多段正文，约 7–23s 后收到 `final_output_aborted`，`reason_code=policy_requires_complete_answer`，没有终答。
- 影响：E/S 40 题中 6 题（BQA-003/004/006/019/022/038），Q1 3/10；用户看到半截内容且无法继续。
- 证据：`旧性能证据已清理`、`calls.jsonl`（6 条 model failed）、LangGraph 日志 `output aborted ... reason=policy_requires_complete_answer detail=unit_policy`；代码 `src/common/output_streaming/answer.py` 的 `_validate_public_unit`。
- 复现：真实模型下发送 BQA-003“什么是AQI？…”；首段可显示，随后 aborted。
- 建议：正文开始前按问题/技能判定是否 policy-bound；若答案含具体法规文字，应改走完整答案路径，不应先流后撤。

## D2（高）Skill 可见工具与 basic-qa 业务白名单不一致，导致权限拒绝和超时

- 现象：模型反复调用 `mcp_city_analysis_monitor`、`helper_getLatestTime2`、`text2sql_today_single_city_aqi_route`、`text2sql_yesterday_single_index_aqi_route`、`mcp_city_common_area_province_ranking`，被 `McpAuthorizationError` 拒绝；BQA-017、BQA-018 最终 120s 超时。
- 数据：352 次工具尝试中 117 次 `permission_denied`；Q2 两项超时。
- 证据：`calls.jsonl`、`agent_tool_call_attempts.error_category=permission_denied`、`src/common/business_registry.py` 的 basic-qa 白名单。
- 复现：真实模型下发送 BQA-017/018；日志出现多次 `MCP 调用被授权层拒绝`。
- 建议：Skill 的 allowed-tools 必须与 business_registry 的白名单取交集生成；或把这些工具纳入 basic-qa 业务范围并证明只读安全。

## D3（中）MCP 参数校验失败重复出现

- 现象：`mcp_city_common_get_air_quality_realtime_stat` 86 次尝试中 55 次失败，87 次 `validation_rejected`；模型反复重试无效参数。
- 影响：拉长 Q2/Q3 延迟，消耗 Token；错误分类正确但没有及时止损。
- 证据：`calls.jsonl`、`agent_tool_call_attempts`；BQA-015/016 出现 `failure_marker`/缺少实体。
- 建议：对同一工具连续参数拒绝设置重试上限，并把工具 schema/示例反馈给模型；在 Skill 中补参数模板。

## D4（中）数据分析前端没有菜单选择器

- 现象：切换到 `data-analysis` 后页面只有输入框，没有 `menu_name` 选择；请求未携带 `menu_name`，后端只能按通用/降级路径处理。
- 影响：菜单驱动的确定性分析在真实 UI 不可达，用户无法选择 `CityHourBroadcast` 等菜单。
- 证据：`screenshots/chat-data-analysis.png`；`frontend/lib/chat-store.ts` 发送路径无 menu_name；`src/data_analysis/nodes.py` 的“降级为通用分析”分支。
- 建议：在数据图输入区增加菜单 selector，并把 `config.configurable.menu_name` 传到 BFF。

## D5（中）metrics-only 用户被运营页面拦截

- 现象：`full-a-metrics` 的 `/agent/metrics/summary` 返回 200，但 `/operations` 和 `/operations/performance` 页面显示“当前账号没有运营查看权限”。
- 影响：统计权限与运营权限在 UI 层没有分离；只读指标用户无法看指标页。
- 证据：`evidence/permission-separation.json`；浏览器截图/页面文本。
- 建议：运营 layout 的门禁按子页所需权限判断，metrics 页面只要求 `agent:metrics:read`。

## D6（中）历史搜索对既有会话返回“没有匹配的会话”

- 现象：搜索框输入既有会话标题或“新会话”后，列表为空并显示“没有匹配的会话”，但清空搜索后列表恢复。
- 影响：用户无法按标题找到历史会话。
- 证据：浏览器操作记录；`ThreadSidebar` 搜索接线。
- 建议：核对 `searchThreads` 的 graph/user filter 和标题索引；补真实浏览器回归。

## D7（中/待确认）运营+正文角色读取他用户正文返回 404

- 现象：`full-a-contentops`（`agent:operations:read` + `agent:content:read`）读取 `full-a-legal` 的 request content 得到 404“对象不存在”；owner `full-a-legal` 同请求 200。
- 影响：运营后台跨用户正文读取可能不可用；若策略是仅 owner 可读，应在接口/文档明确，并调整运营权限设计。
- 证据：`evidence/permission-separation2.json`。
- 建议：确认预期是“运营可读本企业受控正文”还是“仅本人”；补跨用户正向控制。

## D8（工具）run_baseline 硬编码已删除的 PG 容器/库

- 现象：E/S 完成后 `calls.jsonl` 为空，`summary.model_calls` 全 0；代码 `_psql_json` 固定 `aa-perf-20261021-pg` / `air_agent_business`。
- 影响：观测/Token/费用覆盖被判为未采集。
- 修复：改为 `PERF_PG_CONTAINER` / `PERF_PG_DATABASE`，本轮从 375 条模型观测和 352 条工具尝试重导出 725 条调用；未覆盖历史 run。
- 证据：`git diff scripts/product_chat_perf/run_baseline.py`、`calls.jsonl`。

## D9（工具）client_complete_ms 曾包含台账 confirm 延迟

- 现象：原逻辑在 confirm 之后取完整回答时间，延迟确认会让完成耗时跟着增长。
- 修复：`client_complete_ms` 改为流结束；新增 `client_confirmation_end_ms`；单测 `test_confirm_latency_is_outside_completion_clock` 通过。
- 证据：`scripts/unified_campaign/sender.py`、`tests/unit_tests/test_perf_tool_calibration.py`。

## D10（工具）smoke 证据目录在整轮发送后才创建

- 现象：evidence 目录预先存在时，P/S 先完整发送 92 条才在写盘时 `FileExistsError`，整轮结果丢失。
- 修复：发送前 `mkdir(exist_ok=False)` 预检；失败 run 日志保留。
- 证据：`.local/full-20261022/ps.log`、`scripts/unified_campaign/cli.py`。

## 非缺陷/环境限制

- 知识库 8099 未启动：Q1 知识检索走降级路径，不能据此判断模型知识能力。
- 无价格表：费用未知，不记 0；Token 覆盖率 367/373。
- 真实模型 provider 名称未独立识别；只记录配置请求 `hy3`。
