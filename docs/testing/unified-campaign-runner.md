# 统一验收活动运行说明

本工具把 Basic QA 固定题库、BFF 发送、性能 schema、真实观测/资源采集和 Change6/7 核对放在同一个 campaign 里。离线计划和单测不代表隔离环境已经跑通。

## 默认关闭

- 只接受 P 层、S 档。R/F、真实模型、后台规模和组合负载即使写进端点文件也不会启动。
- 进程同时传入 `--enable-real-models` 或 `--enable-load`，并且端点文件对应开关也为 true，才会放开后续范围。当前任务没有这个授权。
- 任何端点或 DSN 使用端口 `15432`、`19132`，或库名 `air_agent_business`、`iam_identity`，命令直接退出 2。

## 命令

在仓库根目录执行：

```bash
.venv/bin/python -m scripts.unified_campaign plan --out <新目录>
.venv/bin/python -m scripts.unified_campaign iam-catalog \
  --migration-dir <IAM仓库>/server/src/main/resources/db/migration \
  --out <新目录>/iam-catalog.json
.venv/bin/python -m scripts.unified_campaign smoke \
  --endpoints <隔离端点.json> \
  --performance-out docs/reviews/basic-qa-performance/<新run_id> \
  --evidence-out docs/reviews/evidence/run/<新目录>
.venv/bin/python -m scripts.unified_campaign insights-export \
  --admin-base-url http://127.0.0.1:<隔离管理端口> \
  --tenant-id <企业ID> --context-version <上下文版本> \
  --cookie-env CAMPAIGN_COOKIE --out <新目录>
.venv/bin/python -m scripts.unified_campaign reconcile \
  --campaign <plan目录> --sent <sent.json> \
  --metrics <change6度量.json> --sources <insights-export/sources.json> \
  --out <核对结果.json>
```

`plan` 只生成 campaign 和独立预期，不发送请求。`smoke` 在 cookie 环境变量缺失或 BFF 不可达时写 BLOCKED 报告并退出 2，不把未发送记成成功。性能原始文件使用 `basic-qa-perf.v1`；题目和回答正文不写入这些文件。

隔离端点文件只保存回环地址和 `cookie_env` 名称。cookie 值放在该环境变量里，不写入文件。可选字段：

| 字段 | 作用 |
|---|---|
| `request_interval_seconds` | 相邻样本间隔；隔离 IAM 的内部端点限流较小时用于限速，不影响单请求指标 |
| `containers` | 要采样的隔离容器名；冒烟前后各取一次累计计数，写 CPU/网络/块 IO 增量与内存快照 |
| `controlled_stats_url` | 受控依赖 `/stats`；记录窗口内 chat/MCP/Embedding 调用计数（不含逐请求关联） |

## 发送器口径

- SSE 按行增量解析；首字时间只认本次运行中第一条可见 AI 正文，历史助手消息、工具参数、推理块和 `<think>` 内容不计入。
- `values` 事件只作回退，且先记录运行开始时的消息 ID，历史 AI 消息永不当作本次回答。
- 终态区分 `stream_completed`、`no_answer`、`unconfirmed`、`association_mismatch`、`runtime_failed`、`client_timeout`、`transport_failed`、`admission_failed`、`reused`；台账确认与流内 metadata 的 request/attempt/submission 不一致时判失败。
- `checks.jsonl` 按请求记录 stream/answer/association，并汇总 measured 结果、首字覆盖、关联覆盖、Change6 提问增量和模型调用覆盖；历史与代表场景单独按 cohort 记录，不并入 40 题结论。

## 核对口径

- Change6 使用统计接口的提问总数增量（发送前后差值），不用被测聚合 SQL 当答案。
- Change7 索引按 **request → intent → assignment/cluster** 核对：不同意图可以分属不同簇；同一意图不能有多个有效簇；同一 `(request_id, split_index)` 不能对应多个意图；`pending_confirmation`/`missing_content` 缺口只计请求覆盖，不计意图身份。
- `insights-export` 通过产品 API 从问题榜、簇来源和未归类接口导出意图级索引；导出不完整时 `export-summary.json` 的 `complete=false`，核对结果应视为受阻。

## 已知边界

- 真实 IAM、官方 API、Redis、BFF、worker 和浏览器仍要由隔离栈提供。工具不会调用 `deployment/run_separated_local.py`，因此不会碰到正式栈。
- Q4 历史用户轮按真实受理发送，助手夹具正文不注入。P 层受控模型不读取 gold，所以 Q4 继承断言不能据此判通过。
- 观测导出依赖 `AGENT_OBSERVABILITY_ENABLED=true`；未开启时 `calls.jsonl` 写 `not_collected`，不伪造调用。受控模型不返回 usage 时 `usage_status=not_provided`，不记 0。
- IAM 目录已补 `agent:insights:manage` 时 `iam-catalog` 返回 0；目录缺失时退出 2 并给出未应用的 V28 幂等片段。
- 2026-10-21 实测：40 题 P/S run `20261021-161640-5a659c` 为 40/40 `stream_completed`、281 项 PASS；同环境的 v2 run `20261021-161459-5a659c` 因隔离 IAM 内部端点限流中断（10 条 admission_failed），作为失败证据保留，不用于结论。
