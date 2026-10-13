# 第 3/5 项文件与接口责任表（冻结候选中）

本表由 change `add-agent-event-ledger`（匿名02）提出，**协调者独立验收后填写实际冻结提交**
并确认所有者。冻结前第 3/5 项不得并行修改下方"集成者"列的任何文件。

## 共享接口所有者：唯一负责人（集成者）

| 区域 | 第 3 项（反馈）可独立做 | 第 5 项（观测）可独立做 | 集成者（唯一负责人） |
|---|---|---|---|
| 业务模块 | 独立反馈服务、表、路由、聊天交互组件 | 独立 observability 包、表、服务、Langfuse/模型/Skill/MCP 采集适配 | 模块注册、`main.py` 路由登记、权限字典 |
| 事件类型 | `browser.feedback`/`browser.copy`/`browser.timing` 的载荷字段扩充 + 局部样本 | `runtime.model_usage`/`runtime.ttft`/`runtime.cost`/`runtime.span`/`runtime.tool_call` 载荷扩充 + 局部样本 | `docs/contracts/agent-events-v1/event-registry.json` 总登记与 `enabled` 开启 |
| 共享入口 | 提交 chat-store/proxy 公共接线需求清单 | 提交同一运行公共 hook 需求清单 | `frontend/lib/chat-store.ts`、`frontend/app/api/[..._path]/route.ts`、`src/common/event_ledger/lifecycle.py` |
| 迁移 | 独立 revision（在自建测试库验证） | 独立 revision（在自建测试库验证） | Alembic head 合并/顺序化；不得复用 revision ID 或改写已执行 revision |
| 配置依赖 | 声明所需非秘密配置 | 声明所需非秘密配置/依赖 | 根 `.env.example`、`docker-compose.yaml`、`uv.lock`/`package-lock.json` |
| 契约样本 | 各自包内样本，跑同一组契约测试 | 各自包内样本，跑同一组契约测试 | `docs/contracts/agent-events-v1/samples/` 公共样本集 |

## 第 3/5 项可直接依赖的冻结面（v1 已实现）

| 面 | 位置 | 稳定性 |
|---|---|---|
| 信封与校验 | `event-envelope.schema.json`、`event-registry.json` | freeze_candidate |
| 状态/指标字典 | `states.json`、`metrics.json` | freeze_candidate |
| 事件写入（服务端） | 台账内部 runtime 写入端点 | freeze_candidate |
| 浏览器事件受理 | `POST /agent/events/browser`（逐项结果） | freeze_candidate |
| 回答版本回读 | `GET /agent/requests/{id}`（含 attempts/answers/traces 子结构） | freeze_candidate |
| 原问题原文（独立权限） | `GET /agent/requests/{id}/content`（`agent:content:read`；按请求精确读取） | freeze_candidate |
| 本人运行回读 | `GET /agent/runs`、`GET /agent/runs/{id}` | freeze_candidate |
| 提交受理 | `POST /agent/requests`（返回 request/attempt/**submission** id） | freeze_candidate |
| 启动权裁决（内部） | `POST /agent/runs/start-claim`（一次业务提交只能启动一次） | freeze_candidate |
| 受理结果回填（内部） | `POST /agent/runs/admission-confirmed` | freeze_candidate |
| 人工恢复映射（内部） | `POST /agent/runs/{attempt_id}/resume` | freeze_candidate |
| 会话删除标记 | `POST /agent/threads/deletion` | freeze_candidate |
| 待清理删除标记（内部） | `GET /agent/deletions/due` | freeze_candidate |
| 重放/死信运维 | CLI `python -m common.event_ledger replay`（dry-run + `--apply`，按 consumer_version） | freeze_candidate |

## 迁移链约定

- change 1 已执行 head：`0006`（`admin/api/migrations/versions/`）。
- **本项新迁移自 `0007` 起**，不改写已执行 revision。
- 第 3/5 项在各自环境中新建独立 revision；集成者合并 head 或按说明顺序化，禁止两边同 revision ID。

## 基线与工作区

第 3/5 项各自从**同一冻结提交**建立独立 Git worktree/checkout，独立测试库、端口、
Redis/队列前缀与输出目录；不接入彼此测试环境，不重置共享业务卷。挂起点：
`docs/reviews/evidence/flash-01-repair-02/change2-source-baseline.json`（本地实施起点指纹）。

## 待协调者填写

- [ ] 事件 v1 实际冻结提交：`<待填>`
- [ ] 第 3 项公共文件所有者：`<待填>`
- [ ] 第 5 项公共文件所有者：`<待填>`


## 协调者接管补充（2026-10-13）

管理域冻结候选OpenAPI为19条/agent路径；新增的两个服务投影路径属于原生自定义应用：GET /internal/ledger/threads/{id}/runs与.../{run_id}，由集成者持有。rs-v1不能直接读取原生kwargs/state/history。新增0010提交级映射，0007–0009保留。服务只读/受理未知不得依据空列表或租约重执行业务。当前混合工作区未提交，冻结提交仍待填写；不能从HEAD规划提交开启第3/5并行。
