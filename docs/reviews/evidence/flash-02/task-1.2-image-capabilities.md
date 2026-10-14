# 任务 1.2 镜像/受理能力核验（本地实施起点）

实施者：匿名02（change `add-agent-event-ledger`）。日期：2026-10-13。
对象：真实运行中的 `air-agent/langgraph-api:latest`（langgraph_api **0.15.1**，licensed），
以及真实业务 PG（`air-agent-postgres`，迁移 head **0006**）。
方式：`probe_run_admission.py` + `probe_run_metadata_resume.py`（本目录），
经可信运行票据（rt-v1，acc_alice / 企业A）真实调用原生 `/threads`、`/threads/{tid}/runs`。

> 只创建/删除本探针生成的合成空白线程，不读取历史用户对话，不重置业务卷。

## 结论表

| 能力 | 实测结果 | 对设计的影响 |
|---|---|---|
| 调用方指定 `run_id`（body `run_id`） | **不支持**：平台忽略该字段，返回自生成 UUIDv7（`prealloc_matches=false`） | design §3 "稳定预分配 run_id + 原生幂等受理" 路径**在本镜像不可用** |
| `POST /threads/{tid}/runs` 重复同 body | **不幂等**：每次都新建独立运行（两次均 200，两个不同 run_id） | 受理幂等必须由台账侧实现，不能依赖平台 |
| 流式入口 `runs/stream` 指定 run_id | **不支持**：SSE `metadata` 事件返回平台自生成 run_id | 流式入口同样走台账受理 + 校准 |
| `runs.stream` 首次 `metadata` 事件 | 返回 `{"run_id": ..., "attempt": 1}` | 代理可在流早期取得 run_id 写入台账关联 |
| run `metadata` 持久化 | **支持**：创建时传入的自定义 metadata 被持久化，GET run 可回读（`metadata_persisted=true`） | 可注入台账关联标记 `ledger_request_id/ledger_attempt_id`，作为"上游已受理"的校准依据 |
| 自定义 `configurable` 键 | **透传**：非保留键进入 `run.kwargs.config.configurable` 并可回读；保留键（`run_id`）由平台覆盖 | 台账可用 configurable 传递非秘密关联 ID |
| `GET /threads/{tid}/runs/{rid}` 字段 | 200，含 `run_id/thread_id/assistant_id/status/metadata/created_at/updated_at/multitask_strategy/kwargs` | 权威状态来源；`kwargs.config.configurable.langgraph_auth_user` 内含入站修订快照 |
| 未知 run 查询 / 取消 | 均 **404** | "对象缺失"与"平台不可用"必须区分（404 vs 连接错误 vs 5xx） |
| 取消**已完成**运行 | **404**（`no matching runs to cancel`），非 409/200 | 停止确认**不得**把 404 当"已终态"；也不能当"已取消" |
| 运行列表 | `GET /threads/{tid}/runs?limit=N` 200，含 status | 受理校准的枚举来源 |
| `runs/count` | 422（不支持该形态） | 不使用 |
| `POST /threads/{tid}/runs/search` | 405（不支持） | 不使用；校准用 list |
| 恢复入口 `POST /threads/{tid}/runs/{rid}` | **405 Method Not Allowed** | 该路径不是恢复入口 |
| 恢复入口 `command.resume` 新建运行 | 200，**新建独立 run_id**，同线程 | resume = 新平台运行；台账"同一尝试保留，平台运行映射新增一条"（design §4 的 run_id 保留语义按平台实际改写为"尝试不变、运行映射追加"） |

## 受理适配决策（对应 design §3）

平台不提供"调用方指定 run_id"与"幂等创建"，因此采用 design §3 的**受控创建 + 可信关联 metadata + 受理校准**路径：

1. 可信受理事务（台账 PG）先落 `request`/`submission`/`attempt`，返回 `request_id`/`attempt_id`；
2. 受控入口在创建原生运行前，把非秘密 `ledger_request_id`/`ledger_attempt_id` 注入
   run `metadata`（已验证可持久化回读）；
3. 平台 2xx 且取得 run_id 后写入 `run.admission_confirmed` 关联；
4. **响应丢失 / 未取得 run_id**：不自动重发，置 `admission_unknown`；校准流程在
   受授权线程上枚举运行，按 `ledger_request_id` metadata 匹配确认；无法确认则保持待确认并提供显式处理。
5. `resume` 使用独立 operation 幂等，新增平台运行映射但**不**新建逻辑提问。

## 保留边界（未完成，不作为通过）

- 真实企业 MCP（change 1 的 7.6）仍因外部材料缺失安全禁用；本核验未启用真实 MCP。
- 本核验只确认"受理/ID/metadata/恢复"能力，**不**等于六图长运行、成本/Token 采集或完整验收矩阵通过。
- 六图真实运行受理已有既有证据（`tests/integration_tests/test_live_tenant_claims.py::test_live_six_graphs_run_acceptance`）。

## 可复跑

```bash
export INTERNAL_API_TOKEN=$(grep -E '^INTERNAL_API_TOKEN=' .env | head -1 | cut -d= -f2-)
.venv/bin/python docs/reviews/evidence/flash-02/probe_run_admission.py
.venv/bin/python docs/reviews/evidence/flash-02/probe_run_metadata_resume.py
```

原始输出：`probe_run_admission.out.json`、`probe_run_metadata_resume.out.json`。
