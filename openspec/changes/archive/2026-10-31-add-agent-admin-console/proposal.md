# Agent 运营管理后台（Change 6）

## Why

台账、反馈和观测已经有可信数据与部分查询接口，但现有后台仍以身份管理入口和少量静态概览为主，运营人员无法连续完成“看趋势—找异常—查运行—处理反馈”。需要在现有 Agent 后台形成可用的运营流程，账号与企业管理继续由独立 IAM 承担。

## What Changes

- 复用 `admin/web` 的 Next.js 16、现有组件、企业切换及同源 BFF，建设总览、用户用量、对话/运行、反馈处理、业务与能力、性能/模型成本页面；不改为 Vue，不重写聊天前端。
- 增加跨台账/反馈/观测的一致读模型，服务端聚合、下钻、游标分页、数据覆盖与截止时间齐全；保留既有 API 兼容性。
- 修补企业运营查看原问题/回答版本的实际来源与授权缺口；正文独立授权、按需分页读取，不能只凭现有请求ID接口推断已支持他人原文。
- 添加简单反馈处理单（待处理、处理中、已处理、重新打开），与用户当前评价和满意率独立；写权限 `agent:feedback:manage` 为新增精确权限，不能用运营只读权限代替。
- 复用 Langfuse 现有受控 Trace 回读；共享项目不新增企业控制台直链。评测集、实验、评分和版本对比继续在 Langfuse 完成，不实施独立 Change 8 评测平台。
- 为 Change 7 固定查询窗口、分页、异步状态和页面扩展接口；三张公开图从当前注册与数据解析，新增业务不靠页面硬编码。

## Capabilities

### New Capabilities

- `agent-operations-console`：同一个中文运营后台的信息架构、页面流程、授权状态和异步隔离。
- `agent-admin-read-model`：跨事实域的一致查询、指标口径、正文访问和有界性能。
- `agent-feedback-triage`：独立于用户评价的反馈处理状态、幂等并发和审计。

### Modified Capabilities

无。继承 `agent-business-access`、`agent-observability-access`、`agent-feedback-metrics` 等主规格；本项扩展管理读模型和工作流程，不重定义原采集或业务执行权限。

## Impact

- 主要修改 `admin/api/app/routers/console.py`、`services/console/**`、`schemas_console.py`、`admin/web/app/(dashboard)/operations/**`、`admin/web/components/operations/**`、对应测试；实际模块路径见 design。
- 新增受控回答快照模块，`src/common/event_ledger/lifecycle.py` 的可见终答回调仅加一处快照接线；由集成负责人串行合入，不重写运行状态或公共事件。
- 有限接线涉及 `admin/api/app/main.py`、后台导航/API客户端/会话 epoch、设计与交互规范；反馈处理表接当前迁移 head，不预约固定 revision。
- IAM 仅需在权限目录注册并按企业角色显式授予新增反馈管理码，见 `permission-handoff.md`；本项不修改 IAM 仓库、身份协议或正式授权数据。
- 与 `integrate-intelligent-tracing-into-dev` 无先后功能依赖；共享文件与迁移必须按 [并行契约](../../../docs/handoffs/parallel-tracing-console-insights.md) 串行集成。
- 本轮只有计划；实施者不得因此自动部署、修改正式库、commit/push 或合并其他工作。
