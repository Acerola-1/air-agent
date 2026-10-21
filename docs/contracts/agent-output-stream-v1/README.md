# agent-output-stream-v1 正文流式契约

本契约定义三个公开图（`basic-qa`、`data-analysis`、`intelligent-tracing`）唯一的用户正文 SSE 通道。正文只通过 LangGraph custom stream 的 `final_output_*` 事件发布；普通业务事件、台账事件和日志不得携带正文。

## 事件生命周期

```
final_output_start → final_output_delta* → final_output_done
final_output_start → final_output_delta* → final_output_aborted
```

- 每个输出有稳定的服务端 `output_id` 与 `message_id`，由发布器生成；浏览器不能指定。
- `seq` 从 `start=0` 开始严格 +1，客户端按序拼接增量。
- `done.message` 必须等于所有已发布 delta 的按序拼接；`content_sha256` 是该全文的 SHA-256。
- 同一输出最多一个终态；`aborted` 之后不得再出现 delta/done。
- 旧客户端继续识别 `final_output_done` 的 `message`/`message_id` 字段；新客户端按 `output_protocol_version` 区分新旧协议。旧 done-only 永不计为新协议流式证据。

## 字段

所有事件都携带：`version`（既有 runtime event 信封版本）、`output_protocol_version=1`、`created_at`、`processing_id`、`thread_id`、`run_id`、`graph_id`、`tenant_id`、`output_id`、`message_id`、`seq`、`delivery_mode`、`type`。

| 事件 | 额外字段 | 语义 |
|---|---|---|
| `final_output_start` | `delivery_mode` | 开始一个输出，`seq=0` |
| `final_output_delta` | `message`、可带 `section_id`/`plan_digest`/`replay` | 本次新增、已通过必要校验的公开文本 |
| `final_output_done` | `message`、`content_sha256`、可带 `report_id`/`revision`/`plan_digest`/`report_sha256` | 正文完整且最终确认 |
| `final_output_aborted` | `reason_code`、`partial_chars` | 输出中断；已发布前缀明确未完成 |

`delivery_mode`：

- `token`：无工具最终回答真实流式生成，增量按安全单元发布。
- `validated_section`：DeepAgents 逐节确定性渲染、逐节校验、逐节发布。
- `single_shot`：短固定答复/受控失败消息一次发布；明确不是流式证据。
- `policy_bound`：问题需要具体法规/限值原文，先生成完整回答再经知识政策收敛为一个受控单元；明确不是流式证据。

`section_id` 与 `plan_digest` 用于 DeepAgents 分节去重；`replay=true` 表示该节在恢复前的发布会话中已经确认，客户端不得重复追加文本。

## 信任边界

- 身份字段来自平台注入的 `configurable.langgraph_auth_user`，不是浏览器声明；`tenant_id` 为空只允许受控测试。
- 发布器在每个增量批次和最终确认前重新检查运行身份/许可/墓碑；撤权、删除、取消后不再发送并发出 `aborted`（若尚未终态）。
- 正文不写入普通业务事件登记、指标或日志；`content_sha256` 只用于一致性校验。

## 正文安全

- `<think>`/推理块跨 chunk 时整体扣留，绝不公开半截。
- 以 `{`/`[` 开头的内部 JSON 先缓冲到完整值，再按错误载荷处理。
- `[[cite:...]]` 引用标记必须完整；未知 citation 所在单元不发布。
- 具体法规/限值不能先公开再撤回：问题级预检命中时走 `policy_bound`；流中出现具体法规文本时发布器中止（`policy_blocked`），不发出成功 done。
- 缓冲区有硬上限；无法形成安全边界的源失败关闭，不无限堆积。

## 样本与校验

- `final-output-stream.schema.json`：JSON Schema（2020-12）。
- `samples/basic-qa-token.jsonl`：12 片段协议的 3 片段缩略样本。
- `samples/tracing-sections.jsonl`：DeepAgents 分节与最终报告身份。
- `samples/aborted.jsonl`、`samples/legacy-done.jsonl`。
- Python 校验：`common.output_streaming.contract.OutputStreamValidator`。
- TypeScript 消费：`frontend/lib/agent-output-stream.ts`（`AgentOutputStream`）。

复跑：

```bash
.venv/bin/python -m pytest tests/unit_tests/test_output_stream_contract.py -q
node frontend/tests/agent-output-stream-contract.test.cjs
```
