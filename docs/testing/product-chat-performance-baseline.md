# 产品聊天真实性能基线工具

本工具用于 Basic QA 的**单用户、单并发、真实配置模型**首轮端到端探索基线：同源 BFF →
台账受理与冻结选择 → 官方 LangGraph API → 真实模型/真实 MCP → 流式回答。它不执行 R/F
并发、开放到达、长稳、故障注入或浏览器渲染，也不把后台 SQL 或直接 LLM 调用当作产品结果。

## 与统一活动工具的关系

- `scripts/unified_campaign` 保留默认关闭的真实模型/负载保护；`smoke` 仍只做 P/S。
- 本工具复用其固定题库加载、BFF 发送器、SSE 首正文解析、台账关联和 `basic-qa-perf.v1` 字段，
  新增 E/S 真实模型基线执行与截止时间判定。
- 真实模型必须由 `--enable-real-models` 且端点文件 `allow_real_models=true` 双重显式启用；
  正式端口/库名仍由既有 guard 与人工核对保护。

## 命令

```bash
.venv/bin/python -m scripts.product_chat_perf.run_baseline \
  --endpoints .local/<run>/endpoints.json \
  --performance-out docs/reviews/basic-qa-performance/<run_id> \
  --evidence-out .local/<受控证据目录> \
  --enable-real-models
```

Q4 的 50 条历史用户轮必须先通过同一 BFF/受理路径预置；可把历史预置视为准备步骤并用
`scripts.product_chat_perf.q4_parallel` 并行缩短准备时间，但 10 条 Q4 测量题仍必须单并发顺序发送，
历史轮单独记录、不计入 40 题分母。报告需注明该并发只用于准备，不是测量负载。

`CAMPAIGN_COOKIE` 环境变量保存 BFF 会话 cookie（值不写入端点文件或报告）。端点文件形状：

```json
{
  "layer": "E",
  "profile": "S",
  "allow_real_models": true,
  "allow_load": false,
  "bff_base_url": "http://127.0.0.1:<port>",
  "admin_base_url": "http://127.0.0.1:<port>",
  "tenant_id": "<synthetic tenant uuid>",
  "context_version": 2,
  "cookie_env": "CAMPAIGN_COOKIE",
  "containers": ["<self-owned container>"],
  "model_requested": "hy3",
  "mcp_source": "ipp shared_readonly real MCP",
  "request_interval_seconds": 0
}
```

## 测量口径

- `client_first_answer_ms`：BFF 提交前至增量 SSE 中第一段可展示 AI 正文；排除历史助手消息、
  工具 JSON、推理块和 `<think>` 内容。
- `client_complete_ms`：提交前至流完整结束；超过题库冻结截止时间（Q1 60s、Q2 120s、
  Q3/Q4 180s）记为晚到，不回填按时成功。
- `calls.jsonl`：服务端 `agent_model_observations` 与 `agent_tool_call_attempts` 的逐次物理尝试，
  按 attempt 关联到题目；模型 TTFT 与接口首正文分开。
- `resources.jsonl`：自有容器 3 秒采样 CPU/内存/网络/块 IO；不含客户端逐秒 CPU。
- `browser.jsonl`：本工具不执行浏览器，保持空文件并在 `evidence-index.json` 说明。

## 相关模块

- `run_baseline`：单并发顺序执行 Q1–Q3 与测量题，增量写 `requests.partial.jsonl`。
- `q4_parallel`：10 路并发预置 Q4 历史（准备），随后顺序测量 Q4。
- `merge_bundle`：合并分段请求、查询服务端调用观测并生成 bundle。
- `finalize`：按冻结截止时间判定按时/晚到，重建检查和中文报告。

## 输出

性能目录遵循 `basic-qa-perf.v1`：`manifest.json`、`summary.json`、`requests.jsonl`、
`calls.jsonl`、`resources.jsonl`、`checks.jsonl`、`browser.jsonl`、`evidence-index.json`、
`report.md`。回答正文只写受控证据目录的 `answers.jsonl`，不进入性能 bundle。

## 预检与失败

先跑 1–2 道代表题验证接线；预检请求单列 `precheck.jsonl`，不计入 40 题分母。工具中止时
保留 `requests.partial.jsonl` 和受控答案，修复后用新 run_id 复跑，不覆盖原目录。任何
HTTP 200、SSE 首包、工具 HTTP 200 或服务端终态都不能单独算完成成功；必须同时有非空正文、
完整结束、可信台账关联和截止时间内完成。
