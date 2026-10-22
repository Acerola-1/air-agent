# Air Agent 前端、后端与产品聊天全面测试报告

run_id：`20261022-094000-full`  
基线：`dev` / `52da249`（工作区有本轮测试工具修改，见 §8）  
规范：[basic QA 并发性能回归规范](../../../testing/basic-qa-performance-regression.md) v1.2、`basic-qa-perf.v1`  
时间：2026-10-22 01:19 — 09:40（Asia/Shanghai）。**实际墙钟约 8h20m，其中 7h17m 为 ego-browser 控制权暂停等待；主动执行约 1h12m，已超出 4 小时预算。** 因此本轮在 E/S 后停止新增负载，P/R、E/R、F 未执行，不能把本轮写成容量或全量验收通过。

## 1. 一句话结论

普通用户在本轮隔离栈里可以登录、选企业、切换三张图、看到逐段正文、停止生成、点赞/点踩和进入运营后台；但真实模型 40 题只有 **32/40 按时完成**、**28/40 质量检查通过**，最突出的失败是回答被“完整性策略”在正文中途打断（6 题）以及 Q2 工具权限/参数循环导致 2 题超时。首正文 P50/P95 为 **13.95s/67.07s**，完整回答 P50/P95 为 **17.66s/69.11s**；流式确有改善，但用户仍会在不少题上等十几秒到一分钟，且约五分之一的问题拿不到完整终答。

## 2. 普通用户实际能用到什么

| 能力 | 实际结果 | 证据 |
|---|---|---|
| 登录、企业选择、退出入口 | 可用；错误口令提示和 503 重试未实测 | 聊天前端截图、登录流程 |
| 三张图切换 | 基础问答 / 数据分析 / 溯源追踪都出现在选择器，切换后页面正常 | `screenshots/chat-home.png`、`chat-data-analysis.png`、`chat-tracing.png` |
| 普通问答逐段正文 | 可用；真实模型 BQA-002 收到 34 段，首段 7.54s、末段 11.45s | `requests.jsonl`、`chat-streaming.png` |
| 停止生成 | 可用；慢速受控流停止后显示“未完成 · 生成中断或尚未确认，此内容不是完整终答”，不冒充完成 | `chat-stop-incomplete.png` |
| 点赞/点踩/原因/意见 | 可用；点踩可保存原因和意见，运营后台可读取并完成“处理中→已处理” | `admin-feedback.png`、`admin-feedback-resolved.png`、`feedback-cas.json` |
| 复制 | 按钮点击后显示“已复制”；剪贴板内容未验证，不能算物理剪贴板通过 | 浏览器证据 |
| 数据分析 | 页面能切换、能发消息；**页面没有菜单选择器**，请求不带 `menu_name`，只能走通用/降级路径 | `chat-data-analysis.png`、前端代码 |
| 智能溯源 | 页面能切换、能发消息；示例请求只返回“请补充站点/时间/半径”的澄清，未生成完整报告和画布 | `chat-tracing.png` |
| 运营后台 | 总览、反馈处理、需求洞察可用；metrics-only 用户被页面拦截（API 200、页面 403） | `admin-overview.png`、`admin-insights.png` |
| 历史搜索 | 搜索既有会话返回“没有匹配的会话”，疑似搜索接线/索引问题 | 浏览器操作记录 |
| 桌面/390×844 | 页面布局可用，窄屏显示移动端会话按钮、输入区和“未完成”状态 | `chat-narrow-390x844.png` |

## 3. 首正文与完整回答要多久

### 3.1 P/S 受控冒烟（不代表真实模型）

- 40/40 测量题 `stream_completed`；另 50 条 Q4 历史轮和 2 条代表场景也 `stream_completed`，共 92 条全部建立台账关联。
- 接口首正文 P50/P95：**2.36s / 3.64s**；完整回答 P50/P95：**2.67s / 3.92s**。
- 受控模型窗口计数：**184 次模型调用，0 次 MCP 调用**；逐请求模型观测未采集（本轮观测开关在该 run 之后才启用）。
- 该档只能说明受控链路与 SSE/台账协议可用，**不能代表真实模型速度、容量或业务正确性**。

### 3.2 E/S 真实模型 40 题（单用户、单并发）

| 分类 | 计划/发送 | 按时完成 | 失败 | 首正文 P50/P95 (ms) | 完整 P50/P95 (ms) |
|---|---:|---:|---|---:|---:|
| Q1 | 10/10 | 7 | 3 次策略中断 | 10790 / 18020 | 14069 / 21211 |
| Q2 | 10/10 | 7 | 2 次超时、1 次策略中断 | 27575 / 53418 | 28736 / 54140 |
| Q3 | 10/10 | 9 | 1 次策略中断 | 17382 / 84120 | 20567 / 88588 |
| Q4 | 10/10 | 9 | 1 次策略中断 | 10647 / 47516 | 11992 / 49675 |
| **合计** | **40/40** | **32** | **6 aborted + 2 timeout** | **13950.5 / 67074.5** | **17660 / 69109.15** |

- 另发 50 条 Q4 历史用户轮，50/50 `stream_completed`，不计入 40 题分母；历史成本单列。
- 全部 40 题都有非空台账关联，说明“受理关联”不是主要问题；主要问题是正文完整性、工具权限和工具参数。
- 浏览器首正文另测：受控模型下首可见 **6.91s**、显示完成 **7.47s**；真实模型浏览器首可见本轮未测，接口首正文不能替代浏览器首屏。

## 4. 完成且正确多少

- 按时完成成功：**32/40（80%）**。
- 质量/结构检查通过：**28/40（70%）**。失败原因：6 题空回答、2 题超时、Q2/Q3/Q4 另有实体/数值/关键词缺失。
- 分类型质量：Q1 7/10、Q2 5/10、Q3 8/10、Q4 8/10。
- 质量规则只覆盖流完成、台账关联、必要实体/日期/指标词、工具实际调用和数字存在性；真实 MCP 数值与合成 gold 不可比，不能把本轮 28/40 当作严格语义正确率或模型能力总分。

## 5. 流式是否真正改善

**接口层：是。** 真实模型成功样本有多段逐段正文，例如 BQA-002 收到 34 段，首段 7.54s、末段 11.45s；BQA-001 8 段，首段 8.61s、末段 10.17s。首正文与末正文之间存在可观测间隔，不是“先攒完整答案再一次性打字”。

**但有两个限定：**
1. P/S 受控模型只产生 1 段 `final_output_delta`（`delta_count=1`），不能用它证明多段流式；多段证据来自 E/S 真实模型。
2. 6 题在正文中途被 `policy_requires_complete_answer` 打断，用户先看到部分文字，随后得到“未完成”而不是终答；这是流式安全策略的产品缺陷，不是模型慢。

## 6. 并发测到哪一步

- 只完成 **P/S 单用户冒烟** 和 **E/S 单用户真实模型基线**。
- **P/R（受控并发 1/5/10 × 3 轮）、E/R、F 均未执行。** 原因：
  1. E/S 已出现 **8/40（20%）失败**，超过规范 5.4 的 10% 停止条件，不能继续升压；
  2. 现有 `scripts/unified_campaign` 只有 P/S `smoke`，没有 P/R 并发负载子命令；`run_baseline` 也明确只做 E/S；
  3. 4 小时预算已超，不能新发压。
- 因此本轮没有任何“并发容量”“可持续到达率”“生产 SLO”结论；不能把 P/S 的 2.7s 当并发能力。

## 7. 关键缺陷（详细见 defect-list.md）

| 严重度 | 缺陷 | 影响 |
|---|---|---|
| 高 | 非 policy 问题在生成“具体法规文字”时被 `policy_requires_complete_answer` 中途 aborted（6/40，Q1 3/10） | 用户看到半截答案后无终答；是 E/S 最大失败来源 |
| 高 | Skill 可见工具与 basic-qa 业务工具白名单不一致，产生 117 次 `permission_denied`；Q2 工具循环导致 2 次 120s 超时 | 慢、耗 Token、部分题拿不到数据 |
| 中 | `mcp_city_common_get_air_quality_realtime_stat` 出现 87 次 `validation_rejected` | 重复无效尝试，拉长首正文和完成时间 |
| 中 | 数据分析前端没有菜单选择器，请求不带 `menu_name` | 菜单驱动确定性分析在 UI 不可达，只能走通用/降级 |
| 中 | metrics-only 用户 API `/agent/metrics/summary` 200，但运营页面显示“没有运营查看权限” | 四权限分离在 UI 不完整 |
| 中 | 历史搜索对既有会话返回“没有匹配的会话” | 用户找不到历史会话 |
| 中/待确认 | 运营+正文角色读取他用户回答正文返回 404，owner 读取 200 | 运营后台跨用户正文能力可能不可用，或隐私策略未说明 |
| 工具 | `run_baseline` 把 PG 容器/库硬编码为已删除的 `aa-perf-20261021-pg/air_agent_business`，导致 `calls.jsonl` 为空 | 已改为读 `PERF_PG_CONTAINER/PERF_PG_DATABASE` 并从本轮观测重导出 725 条调用 |
| 工具 | `client_complete_ms` 曾在台账 confirm 之后取值 | 已改为流结束计时，新增 `client_confirmation_end_ms` 分离 |
| 工具 | P/S `smoke` 在整轮发送后才创建 evidence 目录，目录已存在会白跑一整轮 | 已改为发送前预检；保留失败 run 日志 |

## 8. 环境、身份与工具变更

- 隔离资源：自建 PG（IAM 25541 / 业务 25543）、Redis（25542 / 25544）、Java IAM 25545、Admin API 25546、官方 LangGraph API 25547、BFF 25548、受控模型 25549、宿主桥 25550、Admin Web 25551；全部绑定回环，未连接正式 15432/19132。
- 身份：2 企业（`full-a`、`full-b`），每企业合法/未授权用户；另建 metrics-only、content+ops、feedback+ops 用户；IAM Flyway 到 **V28**，业务 Alembic 到 **0029**。
- 真实模型：配置请求 `hy3`，经本机 OpenAI 兼容网关；provider 实际名称无法独立证明。
- 真实 MCP：`ipp` shared_readonly，`https://www.mapairs.com/product/datacenter/api2/mcp`；知识库/RAG 未启动，Q1 知识链路为降级路径，必须单列，不能算模型知识能力结论。
- 本轮测试工具修改（未提交）：
  - `scripts/unified_campaign/sender.py`：`client_complete_ms` 改为流结束；新增 `seq_status`、`done_hash_match`、`output_started`、`aborted_reason` 校验；
  - `scripts/unified_campaign/cli.py`：smoke evidence 目录预检；
  - `scripts/product_chat_perf/run_baseline.py`：PG 目标改为 `PERF_PG_CONTAINER/PERF_PG_DATABASE`，移除历史死地址；
  - 新增 `scripts/product_chat_perf/load.py`：P/R 受控并发运行器（预热/计量/停止规则/线程硬截止），本轮因预算未执行；
  - 新增 `tests/unit_tests/test_perf_tool_calibration.py`；`tests/unit_tests/test_unified_campaign.py` 38 passed。
- 前端契约测试：`npm --prefix frontend run test:contracts` 66 passed；前端/后台生产构建通过。
- 计费：无价格表，费用为 **null/未知，不记 0**；E/S 观测到 367/373 次 usage，覆盖 98.4%，总 Token **7,332,708**（输入 7,109,847 / 输出 222,861）。

## 9. 后续工具补充（第 2–3 轮）

- `scripts/product_chat_perf/load.py` 已补齐 P/R 受控并发运行器（1/5/10 × 3、预热/计量门槛、60 秒 10% 停止规则、Q4 历史、资源采样）；本轮用 mock sender 验证了轮次循环可终止，`test_perf_load.py` 5 项通过，但**未在真实隔离栈执行**。
- `sender.py` 已补线程安全硬截止，`test_deadline_is_enforced_in_worker_threads` 通过；44 项工具单测通过（含原有校准/冒烟测试）。

## 10. 下一步最重要的三件事

1. **修流式完整性策略**：在开始发正文前判定是否必须走 policy-bound 完整答案路径；对非 policy 问题出现具体法规文字时，不能中途 abort 让用户看到半截。用 BQA-003/004/006 复跑验证。
2. **统一 Skill 工具与 basic-qa 业务白名单**：把 `mcp_city_analysis_monitor`、`helper_getLatestTime2`、`text2sql_*` 等从 Skill 可见集中移除或纳入业务白名单；修复 `mcp_city_common_get_air_quality_realtime_stat` 参数生成；用 BQA-017/018 的 0 次越权外呼和 120s 内完成做验收。
3. **补 UI 与权限缺口**：数据分析菜单选择器、历史搜索、metrics-only 页面门禁、跨用户正文策略；然后用同一 40 题重跑 E/S，再按规范做 P/R（1/5/10 × 3 轮）和 F 计划。

## 10. 交付与复跑

- 本目录：`manifest.json`、`summary.json`、`defect-list.md`、`scenario-matrix.md`、`commands.md`、`screenshots/`、`evidence/`。
- 性能原始 bundle：`docs/reviews/basic-qa-performance/20261022-ps-full/`、`docs/reviews/basic-qa-performance/20261022-es-full/`。
- 受限正文与大日志：`.local/full-20261022/evidence-ps/`、`.local/full-20261022/evidence-es/`（公开报告不含正文）。
- 复跑命令见 `commands.md`。

## 11. 明确未完成

- P/R、E/R、F、开放到达、长稳、突发、故障注入矩阵未执行。
- 真实模型浏览器首屏未测；IME/物理剪贴板未测；Markdown/表格/引用分块未用真实答案验收；重试/重新生成未点验。
- 跨租户 tenant-B API 请求链未创建；只直接验证了 RLS 行隔离。
- Change 7 的 assign/split/delete、worker 取消/租约接管未执行；rename/merge 已用 5 个来源验证。
- 价格/费用未知；真实知识库依赖缺失。
- 实际墙钟超出 4 小时预算；原因为 7h17m 浏览器控制权暂停，必须在后续轮次重新计时并补齐。
