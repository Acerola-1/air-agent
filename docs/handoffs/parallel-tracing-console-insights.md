# 溯源集成、Change 6 与 Change 7 的并行边界

核对日期：2026-10-20，基线 `dev@baa1b24`，工作区开始时干净。读取了 `integrate-intelligent-tracing-into-dev` 的 proposal/design/tasks/四份delta；其61项任务尚未实施，不能当成新版图已发布。

## 结论

**可以并行编写计划，也可以在独立工作区按下表开发；不能在同一工作区同时实施或并发合并共享文件。** 6、7不依赖新版溯源报告、四专员或0.7.28 API；只依赖现有台账/反馈/观测事实。溯源迁移和依赖升级会影响最终联调环境，因此“没有产品阻塞”不等于“零集成成本”。

| 归属 | 独占开发区域 | 不能顺手改动 |
|---|---|---|
| 溯源实施者 | `src/intelligent_tracing/**`、依赖/锁/镜像、业务工具子集、runtime发布守卫 | 后台运营读模型、问题簇、反馈处理单 |
| Change 6 | `admin/api/app/{routers/console.py,services/console/**,schemas_console.py}`、`admin/web/**/operations`（不含insights）、`components/operations/**`、`lib/operations/**` | 图/工具/报告实现、根依赖锁、`src/common/event_ledger/store.py` |
| Change 7 | `admin/api/app/demand_insights/**`、`routers/demand_insights.py`、`schemas_demand.py`、`operations/insights/**`、`components/demand-insights/**` | 图/MCP/依赖升级、6的公共组件与统计口径 |

6允许从新读模块查询现有表，但不重写ledger受理/终态；新回答正文用本域快照模块和共用收尾回调的一处接线采集。7通过已登记原问题快照及墓碑读取，不从checkpoint或ValidatedReport提取用户需求。更换溯源内部实现不产生新的逻辑问题；四专员是内部观测维度，不能计成四次提问或四个独立业务。

## 必须串行协调的文件

- `admin/api/app/main.py`：6、7各自提供小型router注册补丁，由集成负责人串行应用。
- `admin/api/migrations/versions/`：新增迁移由集成负责人在**合并时当前head**接链；不能同时创建相同revision或修改已执行revision。溯源若新增报告表迁移也遵守此约束。
- `src/common/event_ledger/lifecycle.py`：6仅在现有确认终答后接`answer_content`模块；溯源需确保回调收到同一ValidatedReport终答。先各自提供补丁，串行确认“门禁拒绝草稿不落库/终答版本一致”；不同时改发布判断。此处是明确的集成接缝，不能宣称完全无运行时交集。
- `admin/web/components/app-sidebar.tsx`、公共client/session、`DESIGN.md`、`UX-CONTRACT.md`：6是后台公共契约负责人；7提供insights菜单小补丁，溯源仅提供聊天/报告相关补丁。
- 根`pyproject.toml`、`uv.lock`、Dockerfile/镜像、`langgraph.json`：溯源负责；6、7不引入LangChain/DeepAgents依赖。不为统计修改业务注册表。后台JS锁由6维护，7复用依赖，不并发安装/重写同一锁文件。
- 删除worker接线、迁移恢复清单、全局文档/OpenAPI快照：各change提交本域补丁清单，集成负责人合并后统一生成；禁止覆盖他方生成副本。

各实施对话从包含本计划的提交建独立 `codex/` 分支/worktree，各用自建空PG/Redis和独立端口，不共享15432或19132。当前任务仅写计划、无Git提交；计划需由协调者纳入共同起点后派发，或显式复制这几份非秘密计划到另一工作区，不能只说“checkout baa1b24”就假定新计划存在。

## 最小稳定契约与集成门禁

1. 三公开图以当前 `langgraph.json` 为准，页面从注册/事实动态显示；旧版本记录按事实版本展示，不回填新业务身份。
2. `tenant/user/membership/request/attempt/submission/answer` 的含义和复合关联不变；业务使用权不等于运营/原文读取权，撤权即时、故障503失败关闭。
3. `agent_requests`与原问题`agent_content_refs`是7的来源；6/7不依赖溯源报告表结构。报告/内部claim不进入普通事件或后台摘要。
4. 溯源可以扩展台账只读生命周期SELECT；6/7查询模块不修改该函数。若对方改变事件/状态语义、业务ID、删除契约或这些表字段，暂停相应集成，先补契约再继续，不能自行兼容猜测。
5. 依赖升级完成后，把6/7回归在最终0.7.28锁/官方镜像重跑；若先合入6，可在0.7.5先验收后台，不能冒充已覆盖升级后的图。
6. 合并验收：三图各完成一轮受控运行；同一请求重试/恢复统计不增；新版溯源出现新增模型/工具观测时页数/Token守恒；停止/删除/撤权后后台与7样本均不泄漏。真实模型/真实MCP不可用则实测门禁保留，不编造成功。

## 后续路线调整

保留6、7编号。8不作为本轮自研评测平台：评测集、评分、实验/版本对比使用Langfuse，6提供反馈查看和基本处理单；共享Langfuse项目仍不能给企业用户直链。9继续覆盖告警、预算与改进闭环，10负责最终交付加固。完整6可独立交付；7后端可并行，7页面集成须等6公共壳/DTO验收，不能只跑mock页面就归档7。
