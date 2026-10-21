# 三图正文流式实施与复现说明

日期：2026-10-21。工作区：`/Users/acerola/Dev/Python/air_agent`。分支 `dev`，冻结 HEAD `7711914`。

本目录只保存本次任务的实施说明、探针输出与浏览器证据；不覆盖性能 Agent 的 `scripts/unified_campaign/sender.py` 未提交改动、`scripts/product_chat_perf/` 和 `docs/reviews/basic-qa-performance/20261021-201100-e2e4/`。

## 1. 修复前实际链路

| 位置 | 实际行为 | 后果 |
|---|---|---|
| `common/identity/langgraph_auth._pin_ledger_ids` | 每个运行 config 加 `nostream` 标签 | 内部模型 token 不进入 `messages` 流；这是必要的隔离，不是 HTTP 不能流式 |
| `common/models.ModelRegistry.main_llm` | `disable_streaming="tool_calling"` | 绑定工具的调用非流式（供应商兼容），不能扩散到最终无工具回答 |
| `basic_qa.call_model` | 工具绑定模型直接生成正文，`visible_result` 只发 done | 用户正文在整段完成后才出现 |
| `data_analysis.finalize_output` | `astream` 清洗但先累积 `full_cleaned`，最后一次性发布 | 清洗期间无正文增量 |
| `intelligent_tracing.publishing` | `after_agent` 才发整篇 `final_output_done` | 报告整篇生成完才可见；工具/模型草稿被清空是正确的但正文无分节 |
| `frontend/chat-store` | `onCustom` 只处理 done，delta 无身份/序号/重放保护 | 无法安全消费多图/多运行/断线重连 |

`ainvoke` 不是“天然不流式”的原因；问题在于应用从未在生成期间发布安全正文。

## 2. 本次实现

- 契约：`docs/contracts/agent-output-stream-v1/`，`common/output_streaming/contract.py`，`frontend/lib/agent-output-stream.ts`。
- 发布器：`common/output_streaming/publisher.py`，单输出 `start → delta* → done|aborted`，稳定 `output_id/message_id`、单调 `seq`、全文 hash、幂等 done、每批授权探针。
- 安全缓冲：`common/output_streaming/safety.py`，跨 chunk `<think>`、JSON、`[[cite:...]]`、Unicode、边界上限。
- 最终回答：`common/output_streaming/answer.py`，无工具 `astream`；完整知识政策在末尾校验，已公开前缀只能追加，重写则 `policy_blocked` 中止。
- Basic QA：`call_model` 只规划（工具 schema，非流式），`answer_model` 无工具流式生成；`finish_planning` 终止信号；控制消息走 `single_shot`。
- 数据分析：`finalize_output` 生成-片段校验-发布，清洗失败在未公开正文时使用固定可读失败。
- 智能溯源：`ReportGate.precheck → FrozenPublicationPlan（仅冻结章节布局/claim/顺序）→ render_section（发布时才渲染本节）→ validate_section → confirm`；`section_publishing.py` 逐节渲染/校验/发布，最后保存 `ValidatedReport` 并 done；`after_agent` 复用 session，不重发。预检不生成完整 body。
- 前端：按 `thread_id/run_id/output_id/seq` 消费，重复/重放/缺口/冲突处理，done 合并同消息，中断保留“未完成”本地消息。
- 计时：`sender.py` 增加 `first_safe_delta_ms/last_safe_delta_ms/stream_end_ms/confirmation_end_ms/first_delta_source/delta_count/output_id/output_message_id/output_protocol_version/aborted_reason`；旧字段保留。

## 3. 兼容与回退

- 新事件保留旧 `final_output_done.message/message_id`；旧客户端仍显示完整终答，但新客户端将其标为 legacy，不能记为新协议流式。
- 旧历史/刷新继续从 `threads.getState` 读取可见 `AIMessage`；不伪造历史 delta。
- 新协议未改变数据库表；无需迁移。DeepAgents 发布 session 存在图状态中，不新增 PG 表；跨进程恢复依赖平台 checkpoint。
- 回退只影响新受理运行；已开始的新协议流不会中途切旧全文。关闭发布器时业务图仍可通过 `visible_result` 的旧 done 路径结束（兼容代码保留）。

## 4. 复跑命令

```bash
# 共享协议/发布器/安全/保护测试
.venv/bin/python -m pytest tests/unit_tests/test_output_stream_contract.py \
  tests/unit_tests/test_output_stream_publisher.py \
  tests/unit_tests/tracing/test_section_streaming.py \
  tests/unit_tests/tracing/test_streaming_guards.py -q

# 官方 langgraph-api 原生 HTTP/SSE（需先启动自有空 PG/Redis + openai_fixture）
PYTHONPATH=src .venv/bin/python -m scripts.streaming_probe.native_api_probe \
  --graph basic-qa --out docs/testing/streaming-unify-20261021/native-api/native-api-basic-qa.json

# tracing 官方原生入口：LANGSERVE_GRAPHS 指向
# scripts/streaming_probe/tracing_native_graph.py:graph，input 携带 request/scope
PYTHONPATH=src .venv/bin/python -m scripts.streaming_probe.native_api_tracing_probe \
  --out docs/testing/streaming-unify-20261021/native-api/native-api-intelligent-tracing.json

# 三图受控进程内 astream 探针（12 片段/3 章节屏障）
PYTHONPATH=src .venv/bin/python -m scripts.streaming_probe.native_probe \
  --graph all --out docs/testing/streaming-unify-20261021/native

# 真实 Next.js BFF 透传探针（受控上游）
PYTHONPATH=src .venv/bin/python -m scripts.streaming_probe.bff_probe \
  --graph basic-qa --out docs/testing/streaming-unify-20261021/bff-basic-qa.json

# 真实配置模型能力探针（只读取 MAIN_LLM_*，不打印密钥）
.venv/bin/python -m scripts.streaming_probe.gateway_probe \
  --out docs/testing/streaming-unify-20261021/gateway.json

# 真实配置 basic-qa / data-analysis
.venv/bin/python -m scripts.streaming_probe.real_config_probe \
  --graph basic-qa --out docs/testing/streaming-unify-20261021/real-basic-qa.json
```

## 5. 真实限制

- 智能溯源的四个专员真实执行依赖外部 MCP 站点/网格/气象数据源；本任务不修改外部 MCP/RAG，也不在正式 15432/19132 造数。受控四专员执行见 `native-intelligent-tracing.json`，真实外部依赖场景标为受阻，不伪报通过。
- 真实网关 `hy3` 的无工具正文流式已验证；工具绑定调用仍为供应商兼容的非流式（`disable_streaming="tool_calling"`），不能据此宣称工具参数也流式。
- 浏览器停止/撤权证据：`screenshots/basic-qa-stop-incomplete.png`、`basic-qa-revocation-incomplete.png`；两者均显示已公开的两段正文 + “未完成”标记，反馈不开放。撤权由受控隔离身份服务 `/probe/revoke` 驱动（非正式 Java IAM）。
- 浏览器证据使用 IAB + 真实 Next.js 页面 + 受控 SSE 上游；受控源头屏障只用于观测，不在产品发布器加入 sleep。
- `native-api_probe` 使用官方 `langgraph-api` 容器（自有空 PG/Redis + 受控 OpenAI 兼容模型服务）通过真实 HTTP/SSE 运行 basic-qa/data-analysis；`native_probe` 另用真实编译图的 `astream` 做同版本对照。BFF 探针使用真实 Next.js `app/api/[..._path]` 代理。
