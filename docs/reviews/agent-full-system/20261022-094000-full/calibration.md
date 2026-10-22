# 阶段 0：测试工具校准

工具不是已证明正确的裁判。本轮先做确定性校准，再跑产品。

## 校准断言与结果

| # | 断言 | 结果 | 证据/修复 |
|---|---|---|---|
| 1 | SSE 只认本次 thread/run/output/message；历史助手、进度、工具 JSON、推理不计首正文 | PASS | `test_sender_ignores_history_and_tool_arguments_for_first_answer`；受控真实 run 首段与历史隔离 |
| 2 | `start/delta/done/aborted` 生命周期可区分；连续 seq、重放/缺口、hash/全文不一致不能算成功 | PASS（修复） | 新增 `seq_status`、`done_hash_match`、`output_started`；`test_seq_gap_is_a_protocol_error_not_success`、`test_done_hash_mismatch_is_a_protocol_error`、`test_aborted_stream_never_counts_as_completed_success`、`test_missing_done_after_new_protocol_start_is_incomplete` |
| 3 | `client_complete_ms` 不得因 confirm 延迟增长 | PASS（修复） | 完成时钟改为流结束；`client_confirmation_end_ms` 单列；`test_confirm_latency_is_outside_completion_clock`（confirm 0.8s，complete <0.5s，confirmation >=0.75s） |
| 4 | 首正文必须在源未完成时已到客户端；并发线程也必须有硬截止，不能只靠主线程信号计时器 | PASS（受控器/真实模型；新增线程 Timer） | `test_first_delta_arrives_before_source_completes`：首段 <400ms、流结束 >=550ms；`test_deadline_is_enforced_in_worker_threads` 验证工作线程超时；E/S BQA-002 有 34 段 |
| 5 | 预检、Q4 历史、故障、重复传输、计量 cohort/请求计数可核对；P/R 预热/计量门槛有可执行工具 | PARTIAL | E/S 90 条请求全部有 attempt/request/run；P/S 92 条；Q4 历史 50 单独；新增 `scripts/product_chat_perf/load.py`（未在预算内执行）；故障注入未执行 |
| 6 | 资源采集持续窗口、异常仍落盘；不能拿结束空闲快照 | PASS | E/S 5 容器 × 450 个 3s 样本（约 22.5 分钟窗口）；P/S 仅有 pre/post 快照，已标明 |
| 7 | 历史硬编码地址/死路径、旧模型/旧服务名清理 | PASS（修复） | `run_baseline._psql_json` 原固定 `aa-perf-20261021-pg`/`air_agent_business`；改为 `PERF_PG_CONTAINER`/`PERF_PG_DATABASE`；E/S 从 375 模型观测+352 工具尝试重导出 725 条 calls |
| 8 | smoke evidence 目录预检，避免整轮白跑 | PASS（修复） | evidence 目录在发送前 `mkdir(exist_ok=False)`；保留 `ps.log` 失败证据 |

## 测试命令与结果

```bash
.venv/bin/python -m pytest tests/unit_tests/test_perf_tool_calibration.py tests/unit_tests/test_unified_campaign.py -q
# 38 passed in 11.28s

npm --prefix frontend run test:contracts
# 66 tests, 66 pass
```

## 已知工具边界

- `scripts/unified_campaign` 仍没有 P/R 并发子命令；本轮补了 `scripts/product_chat_perf/load.py`，但预算已超，未执行；`run_baseline` 明确只做 E/S。
- `container stats` 能取 CPU/内存/网络/块 IO，但取不到应用连接池等待/队列深度；`resources.jsonl` 中这些字段为 null。
- P/S run 在观测开关启用前完成，逐请求模型调用未采集；只能用受控服务窗口计数 184 次。
- 浏览器首正文与接口首正文是独立时钟；本轮真实模型浏览器时钟未测，受控模型测到 6905.9/7471.2ms。
