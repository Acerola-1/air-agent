# Tasks

## 0. 基线与集成分支

- [x] 0.1 复核 dev HEAD、`langgraph.json` 三图、当前业务授权/MCP 入口、feature 提交与两分支差异；把 HEAD、版本、三图和基线测试记录到新证据目录，验证记录中的图清单与 `langgraph.json` 一致
- [x] 0.2 从当前 dev 新建 `feature/intelligent-tracing-dev-merge`，以 `--no-ff` merge `feature/intelligent-tracing-pm25`；验证 merge 无冲突、集成分支可导入三图，并保留 merge commit 供对比
- [x] 0.3 统一文档口径：把 `AGENTS.md` 的“六图”旧描述改为以 `langgraph.json` 三图为准；验证文档与 `langgraph.json`、`Dockerfile` 的图清单一致
- [x] 0.4 建立本次 G0/G1/G2 证据目录和临时资源清单，明确不 seed/truncate/downgrade 正式库、不 compose down 共享栈；验证记录中有资源所有者、端口/库作用域和回收方式

## 1. Phase 0：dev 依赖升级

- [x] 1.1 收紧 `pyproject.toml`：`deepagents>=0.7.28,<0.8`、`langchain-quickjs>=0.3.9,<1.0`，并对齐 langchain/core/langgraph 直写下限；验证 `uv lock` 能解析目标版本且声明不再允许 0.7.5
- [x] 1.2 用 Python 3.13 重新生成 `uv.lock`，确认 deepagents 0.7.28、langchain 1.4.6、core 1.6.12、langgraph 1.2.16、checkpoint 4.3.0、langsmith 0.14.11、quickjs 0.3.9；验证 `uv sync --frozen --all-extras --python 3.13` 安装结果与锁一致
- [x] 1.3 固定 Dockerfile 基础镜像 digest、明确新镜像 tag，并核对 `/api/constraints.txt` 与锁内核心包版本；构建镜像并验证 `/ok` 200、启动日志注册三图、`intelligent-tracing` 未被跳过
- [x] 1.4 在 `155214a` 上分别用 0.7.5 与 0.7.28 跑全量单测，按失败集合比较并记录既存 flaky；验证没有新增 tracing/业务权限/图接线失败，且 0.7.28 三图冷导入零网络请求
- [x] 1.5 自建空库验证迁移全部应用、`dirty=false`、checkpoint/store/thread/run 表存在；在自建存量副本演练 checkpoint 4.1.1→4.3.0 和回滚限制，验证不在共享正式库执行 downgrade
- [x] 1.6 将 Phase 0 作为独立提交（依赖/锁/镜像/迁移证据），验证该提交不包含 tracing 业务改造，可单独回滚

## 2. 集成 tracing 与契约适配

- [x] 2.1 在集成分支移植 feature 的 `src/intelligent_tracing/**`、测试、SKILL 和证据；验证 0.7.28 下 tracing 目录测试通过，且 `langgraph.json` 仍只有三图
- [x] 2.2 给 `TracingRequest`、`RuntimeScope`、`EvidenceRecord` 增加 `business_code`/`business_policy_version`/`business_permission`/实际 `graph_id` 绑定；验证伪造、缺失或跨业务字段在数据查询前被拒绝
- [x] 2.3 把 `capability_snapshot_ref` 限定为技能/工具选择引用，不再作为业务授权证明；验证缺少可信业务快照时 freeze、报告和发布均失败关闭
- [x] 2.4 改造图入口：生产默认注入 `StationCatalogProvider` 和 `BusinessMCPDataSource`，移除默认 `UnavailableDataSource`/空站点目录；验证真实入口能冻结请求并进入数据专员，而不是直接返回数据不可用
- [x] 2.5 移植 `TenantRuntimeGuardMiddleware(inject_mcp_tools=False)` 的 tracing 用法；验证 lead 最终工具集没有 MCP/文件/execute/GP，basic-qa 与 data-analysis 默认行为不变
- [x] 2.6 更新 feature 的图配置/导入测试到 dev 三图基线；验证测试通过且不会在后续恢复其他图时静默漏测

## 3. 真实站点目录与 MCP 数据

- [ ] 3.1 在授权测试企业做真实 `tools/list`，保存 station catalog、目标站序列、邻站目录/序列、网格、历史气象、NWP 的 inputSchema 和最小调用样本；验证记录明确区分已验证与未验证候选
- [x] 3.2 冻结 `product → tool` 映射、每类参数 builder 和返回 normalizer；验证每个映射至少有一条真实样本和一条 fixture 对齐，不共用 `{station,start,end,radius_km}` 万能参数
- [x] 3.3 实现 `StationCatalogProvider`：经 `execute_mcp_tool` 获取授权站点目录，按企业/业务/区域作用域缓存并支持同名消歧；验证无业务快照、无权限、无连接时零 MCP 请求失败关闭
- [x] 3.4 实现 `BusinessMCPDataSource`：所有取数走 `execute_mcp_tool`/`resolve_connection`/`ScopedMCPToolFactory`，不接受裸 HTTP 或通用 executor；验证业务子集、连接 `approved_query_tools`、本次选择三者交集生效
- [x] 3.5 保留并改造请求级共享快照缓存：key 增加 business_code/业务策略版本，命中前仍复查身份和业务；验证撤权后命中缓存也失败关闭，A/B 并发缓存不串用
- [x] 3.6 在 `business_registry` 为 `intelligent-tracing` 增加窄工具子集，排除 `mcp_station_analysis_trajectory`、`mcp_station_analysis_single_pscf`、`mcp_station_analysis_history_pscf`；验证 registry 测试、目录不披露、实际外呼为 0，其他业务不受影响
- [x] 3.7 实现真实 MCP 的错误/超时/分页/上限/重试归一化：只重试瞬时错误，权限/参数错误不重试；验证失败返回显式数据不可用而不是空数组或成功

## 4. 四专员、状态与预算

- [x] 4.1 移植 station-data/grid-data 零模型确定性图，保持每图只回写自己的 findings；验证 `structured_response` 与父 findings 分开更新，原始数组不回写主状态
- [x] 4.2 验证两数据专员同轮并发、反序完成、单侧失败、第二轮修订和旧 revision 迟到；验证无共享字段覆盖、无重复累加，旧结果不发布
- [x] 4.3 移植 meteo-analyst 与 evidence-reviewer：meteo 只用受限工具、数值由确定性分析产生；reviewer 实际工具集只有结构化审查输出；验证 `tools=[]` 不是唯一证明
- [x] 4.4 落实 task 数、补查数、并发、模型调用、总超时和瞬时重试预算；验证超限停止追加调查并发布带 `budget_exhausted` 的已验证部分报告
- [x] 4.5 子任务关联已有 request/submission/attempt/platform run 和工具终态；验证不伪造新顶层 request/attempt，真实委派可逐项追踪

## 5. 六项污染物分析

- [x] 5.1 新增 `analysis-policy-v2`（phase=2、六项启用、独立 QC/异常/评价口径），不静默扩大 v1；验证证据和报告都记录 v2，六项均可请求
- [x] 5.2 移植字段级质控、时区/整点/窗口/重复/覆盖处理；验证 `pblh=-1`、合法负 u/v、真实零值、单位换算、跨时区气象和半点时间戳反例
- [x] 5.3 移植异常/基线/覆盖逻辑；验证无异常、覆盖不足、基线不足、真实异常和无法规阈值五类结果分开
- [x] 5.4 移植空间格局与过程解释；验证零有效邻站为 unknown、单同步邻站不报 multi_station_sync、传输关联必须有附加时序/风场证据和替代解释
- [x] 5.5 移植风场/滞后：来向/u-v/扇区/静风/圆周统计、上风向梯度和 0–6h 滞后；验证静风、方向不稳定、覆盖不足时拒绝方向级结论，不生成源距离
- [x] 5.6 完成六项污染物画像与评价：CO `mg/m3`、O3 日最大 8h/跨日/缺测、PM2.5 24h 滑动补充、PM10/NO2/SO2 各自口径；验证不同单位/平均时段不直接排序
- [x] 5.7 固化 P01–P18 六项相关 fixture/数据快照、期望事实、允许/禁止 claims 和正向控制；验证 P14–P18 只按 G0 契约覆盖，真实 IAM/MCP/浏览器未执行时明确标记
- [ ] 5.8 对六项污染物各跑真实数据最小闭环；验证每项有正常、缺失/边界和至少一条拒绝/限制样本，未取得真实数据的项记未执行

## 6. 报告门禁、持久化与画布

- [x] 6.1 移植 claim allowlist、语义门禁和受控模板；验证伪造/错配/跨 scope/旧 revision 证据、无禁词越级归因和缺固定限制均被拒绝
- [x] 6.2 保留合法完整率/同步率百分比和“无法计算贡献率”的拒绝说明；验证合法统计不被禁词误拦，报告正文、画布和持久化共用同一 `ValidatedReport`
- [x] 6.3 接正式持久化报告存储（替换默认 `InMemoryReportStore`）；验证 Postgres 保存、按 scope/摘要回读、篡改/tamper 检测和过期/删除行为
- [x] 6.4 接真实画布发布：只接受已验证报告引用，服务端渲染并写 artifact/事件；验证任意 HTML/Markdown content 旁路被拒绝，重开画布与报告摘要一致
- [x] 6.5 聊天只发布验证报告，沿用 `final_output` 事件；验证模型自由文本、子代理正文和内部草稿不可见，合法报告可被真实前端消费
- [x] 6.6 历史回读绑定 scope/修订/证据/策略版本/摘要；验证重开、跨线程、跨企业、A-B-A 和不可验证旧版本不会回退到未验证 AIMessage

## 7. 发布前台账与生命周期

- [x] 7.1 扩展 `attempt_reservation` 增列 `cancel_requested`/`cancel_confirmed`/`state_revision`/`ended_at`，或新增只读 `fetch_attempt_lifecycle`；验证不新增事件/表，空库与存量库迁移均可读
- [x] 7.2 `TracingPublishMiddleware` 发布前调用 `guard_runtime_call`、`thread_deletion_exists` 和运行/尝试状态查询；验证权限撤销、墓碑、cancelled/failed/timed_out、归属不一致和台账不可用均抑制发布
- [x] 7.3 验证 `running` 可发布当前验证版本、`succeeded` 可迟到但不撤销、取消意图不等于已取消；验证测试注入 `lifecycle_probe`，生产默认走真实 store
- [ ] 7.4 验证 stop/delete/恢复：停止后不发布过期结果，删除优先于迟到写入和画布恢复，恢复不复活旧许可/旧报告
- [x] 7.5 验证未新增公共事件类型；现有 `resource.deleted`、`run.cancel_requested`、`run.state_observed` 足以支撑发布前事实

## 8. G0/G1/G2 验收与最终替换

- [x] 8.1 G0：在 0.7.28 上跑 tracing 纯函数/fixture/fake model、并行/失败/修订、六项画像、门禁/画布/回读；验证测试全绿，且 fixture 结果不替代真实服务
- [ ] 8.2 G1 身份/业务：用真实 IAM 验证业务许可、未授权、跨企业、撤权、IAM 故障的正/反例；验证 401/403/409/503 语义，拒绝时新增模型/MCP 请求为 0
- [ ] 8.3 G1 MCP：用真实只读连接验证站点目录、目标站/邻站、网格、历史气象、NWP 的成功/拒绝/超时/撤权；验证业务白名单与 `approved_query_tools` 交集、轨迹/PSCF 外呼为 0
- [ ] 8.4 G1 模型：用真实配置模型跑一次 PM2.5 完整链路；验证报告数值/证据/限制可复核，模型自由文本不进入正文
- [ ] 8.5 G1 六项：六项污染物各跑一次真实数据闭环；验证 O3 8h/跨日、CO 单位、PM2.5 24h 补充、PM10/NO2/SO2 缺失/边界；无数据项记未执行
- [x] 8.6 G1 持久化/迁移：真实 Postgres 保存报告、回读、删除和 checkpoint 迁移；验证同一摘要、scope 隔离、`dirty=false` 和回滚限制
- [ ] 8.7 G2 浏览器：真实页面验证补问、冻结、PM2.5 和至少一项其他污染物报告、画布、同线程改半径、切企业/线程、停止/删除、重开；保留键盘/窄屏证据
- [ ] 8.8 汇总析因/成本/失败：记录 A0/A1/B0/B1 fixture 与真实调查差异、Token/工具/重试/时延、未执行项；验证不以 2/3 通过代替确定性正确性
- [ ] 8.9 证据与资源：把本轮测试、真实服务、容器、浏览器、迁移证据写入新目录，扫描秘密/大文件；验证只回收本任务资源，不覆盖旧报告/共享卷
- [ ] 8.10 最终替换：G1/G2 通过后把集成分支合入 dev，`intelligent-tracing` 直接指向新版；验证 dev 工作区干净、三图注册、0.7.28 锁/镜像一致、旧图未保留，并记录回滚 commit

## 9. 交付与归档条件

- [x] 9.1 更新 `AGENTS.md`、README/运行说明和 tracing SKILL/限制说明，明确三图、0.7.28、六项污染物与轨迹/PSCF 不可用；验证文档命令与 `langgraph.json`/Dockerfile 一致
- [ ] 9.2 分开记录升级提交、tracing 集成提交和最终 merge；验证提交中不含凭据、缓存、构建物或未授权的外部仓库改动
- [x] 9.3 输出交付说明：改了什么、为何、0.7.5/0.7.28 基线、真实/未执行证据、G1/G2 状态、回滚方式和剩余风险；验证未把 fixture 或计划写成上线通过
- [ ] 9.4 只有全部必需项完成或用户明确接受延期后才申请归档/同步主规格；验证未完成项有记录，不提前把 change 标为完成

## Workflow follow-up

- 归档前把 `runtime-dependency-baseline`、`pollution-tracing-analysis`、`tracing-multiagent-orchestration` 的 delta 同步到主规格，并更新 `agent-service-connections`。
- 归档后复核 `intelligent-tracing` 的正式运行证据和旧图回滚点；不要在未完成 G1/G2 时归档。
