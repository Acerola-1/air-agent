# 实施交接：Agent 事件台账

状态：规划完整；协调者已接管补修第一项，并放行本项本地实施。第一项7.6真实企业MCP、10.3完整矩阵仍未完成，不代表生产通过/归档。台账尚未实现，不授权弱化身份基础。

## 开始前读取

1. 根 AGENTS.md、实际涉及子目录的约定；总体路线图、基线报告和匿名评估协议。
2. `add-tenant-identity-access` 的规格及独立验收结果，确认实际可信身份/RLS/运行授权、提交版本和缺失材料。
3. 本项 proposal/design/全部 specs/tasks；[并行安排](../../../docs/enterprise-agent-parallel-work.md)。

当前 HEAD `7b82c04` 是规划提交，实现仍在工作树。以 `docs/reviews/evidence/flash-01-repair-02/change2-source-baseline.json` 的非秘密代码哈希作为本地实施起点，并读取 `docs/reviews/flash-blind-01-repair-02-review.md`。本地依赖边界已由协调者放行；全阶段生产验收没有通过。本项实施者先核对指纹与接口，记录偏差，不把 HEAD 冒写成已验收实现提交。

## 复用与接入点

| 部分 | 范围 |
|---|---|
| `admin/api/` | 显式迁移、租户模型/服务/查询权限、独立 worker；新域模块不挤到身份模型里 |
| `src/common/event_ledger/` | 最小异步存储、事件验证、运行身份、lifecycle 适配，复用可信身份，不导入后台 app |
| 受授权运行入口 | 受理、幂等、run 关联与歧义恢复；覆盖 native 流式/非流式，proxy-only 不算完成 |
| `frontend/lib/langgraph-api.ts` / `chat-store.ts` / 代理 | 稳定提交键、关联回读、现有停止来源，不重做消息渲染 |
| `src/common/runtime_events.py` | 兼容现有 UI custom stream，新增台账协议不要破坏现有消费者 |
| `docs/contracts/agent-events-v1/` | 实现时交付机器可读 schema/样本、来源/类型登记、状态/指标及冻结记录 |

Trace 映射支持写入和查询，本项不要求完成 Langfuse 全链路采集。浏览器反馈/复制/完整计时和服务端 span/usage/成本仍由第 3/5 项实施。不要创建下一 change 的完整产品功能。

## 首要验证

- 必须核验实际官方镜像/SDK 的创建幂等、运行 ID/metadata 查询与恢复；不能凭一个模拟 SDK 推断支持。
- 任一“上游已受理，响应丢失”路径都不自动重执行；待确认是合法状态，假成功不合法。
- A/B 与同用户多企业，非 owner RLS、worker 跨租户交错、普通运营无原文权限。
- 六图开始/结束/失败/等待/恢复；停止意图、真实取消、断流分别记录。
- 事务回滚、重复/乱序、两 worker/租约、死信/重放、删除与恢复。
- fixture 核对请求/尝试/恢复/活跃/完成率/分位数/窗口；缺失不补零。

## 证据和交付

按任务就地补测试/说明，验证后勾选。真实 PG、真实运行 API、浏览器与模型/MCP 替身分开；既有全量失败参照基线而不掩盖新回归。不得删除原测试来美化结果或清空现有数据库卷。

报告：`docs/reviews/agent-event-ledger-acceptance.md`，逐 spec 场景列测试/证据，记录实际身份提交、接口及迁移、未完成项和匿名轮次。状态“待独立验收”，保留 diff，不自动提交、推送、合并或归档。

独立验收后由协调者填写 v1 冻结提交与第 3/5 公共文件所有者；两个实施者拿同一基线，在独立工作区/测试环境使用各自完整实施包启动。


## 已核对的身份衔接与保留门禁

- `src/common/identity/runtime.py`：可信 `RuntimeIdentity` 来自 `configurable.langgraph_auth_user`；user/tenant/membership/session及四种修订都要保留，不采信客户端metadata身份。
- `admin/api/app/services/tenant_context.py`、`src/common/identity/db.py`：复用现有租户事务和最小权限运行连接，不能用owner跨租户查询简化worker。
- BFF新线程服务端铸造UUID；平台2xx后以同会话ticket确认pending归属，409不能确认。事件受理应设计自己的提交幂等与可信关联，不恢复任意客户端线程认领。
- 前端`stopPending`及`pendingStopRun`用于停止未确认；run_id延迟时不能拿上一条终态冒充本次，也不能提前切企业。
- 模型/向量每次实际外呼检查运行授权；SDK隐式retries为0，框架摘要共享异步传输守卫。归档HTTP还核对实时会话和confirmed owner；mt-v1无归档权限。
- 当前迁移head0006，本项从0007开始。JSON续跑以来源指纹台账及阶段标记为依据，不重复授予角色或覆盖后续偏好；不要改写已执行迁移。
- 真实MCP继续安全禁用。本地台账可用明确标记的替身，身份完整矩阵与MCP仍需生产上线前完成；不为台账过测取消门禁。
- 本地验收基线、已知失败与证据以独立接管报告为准。第3/5仍等事件契约独立验收冻结后在独立工作区并行。
