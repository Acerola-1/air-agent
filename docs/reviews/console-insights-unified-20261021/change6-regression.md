# Change6 计数与删除回归（2026-10-21）

## 冒烟计数增量

新 P/S run `20261021-161640-5a659c` 在发送前/后各取一次 `/agent/metrics/summary`：

- 40 题计量 + 50 条多轮历史用户轮 + 2 条代表场景，共 92 条真实请求。
- `aggregate:change6-question-delta`：期望 92，实际 92，PASS。
- 92 条请求全部 `stream_completed`；历史 50、计量 40、代表 2 均保留在活动索引中。
- 证据：`docs/reviews/basic-qa-performance/20261021-ps-smoke-v3-fixed-sender/checks.jsonl`、同目录 `summary.json`。

同环境更早的 v2 run `20261021-161459-5a659c` 因隔离 IAM 内部限流中断（30 条完成、62 条 admission_failed），作为失败证据保留，不用于结论。

## 删除后计数

删除 BQA-040 的共享线程（覆盖 1 条计量 + 5 条历史用户轮，共 6 条请求）：

| 指标 | 删除前 | 删除后 | 结论 |
|---|---|---|---|
| Change6 提问数 | 235 | 235 | 墓碑不减少提问统计，符合既定口径 |
| 洞察请求数 | 230 | 224 | 6 条被删除请求从洞察索引消失 |
| 洞察原子意图 | 326 | 316 | 减少 10 个意图 |
| 洞察已归类意图 | 214 | 208 | 减少 6 个已归类意图 |
| 线程读取 | — | 404 | 删除后不可读 |

证据：`change7-live/delete-refresh.json`。

## 未重跑的部分

- 台账 worker 的租约/删除清理证据来自上一轮 `lease-takeover.json`，本轮没有重跑台账 worker；本轮新增的是 **Change7 归类 worker** 的租约接管证据（`change7-live/race-lease-takeover.json`），两者不能互相代替。
- 本轮后续竞态/浏览器测试又产生了新的合成请求，统计总数会继续增长；上表的 235 是删除当时的时间点值，不是本轮最终总数。
