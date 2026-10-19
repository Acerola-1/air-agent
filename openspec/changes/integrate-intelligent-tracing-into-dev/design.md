# Design

## Context

当前 `dev` 为 `155214a`，`langgraph.json` 只有三图：`basic-qa`、`data-analysis`、`intelligent-tracing`；权限已收敛为 IAM 业务权限 + `BusinessGrant` + 业务注册表 + `approved_query_tools` 双白名单。`feature/intelligent-tracing-pm25` 为 `3cc7bdc`，基于 `6ff95dd`，实现了 PM2.5 G0/fixture 闭环，但依赖 0.7.28，且默认入口使用 `UnavailableDataSource`、空站点目录和旧 `capability_*` 字段。

实际合并检查结果：两分支无同名改动文件，`git merge-tree` 无文本冲突；把 feature 合到 dev 后，0.7.28 下 tracing + 图配置/导入 89 passed，全量 5 failed / 549 passed / 73 skipped（既存失败）；但用 dev 当前 0.7.5 跑 tracing 为 8 failed / 74 passed，典型错误是 `build_freeze_request_tool ... missing 1 required positional argument: 'runtime'`。因此这是运行时和授权语义的集成问题，不是 Git 冲突。

本设计只规划，不修改业务代码。实施者从本 change 开始，在独立集成分支完成后再合入 dev。

## Goals / Non-Goals

**Goals:**

- 让 dev 运行时升级到 0.7.28 目标锁，并在 `155214a` 基线上重新建立测试/容器/迁移证据。
- 把 feature 的 tracing 内核、四专员、证据门禁和报告发布重构到 dev 的真实业务授权与 MCP 执行层。
- 接真实站点目录、目标站/邻站序列、网格、历史气象和 NWP，六项污染物分别验收。
- 直接用新版替换 `intelligent-tracing`，不保留旧通用图或并行公开 Graph ID。
- 发布前接现有 event ledger 的取消/墓碑/运行状态事实，聊天、画布、持久化和回读使用同一验证版本。

**Non-Goals:**

- 不在本 change 内新增公开 Graph ID、公共事件类型或外部 IAM/RAG 仓库改动。
- 不恢复六图、旧 MCP 绑定或旧 local 鉴权。
- 不把 `include_tools`、`fork` 或任意自由代码执行纳入主链。
- 不在本 planning change 内实施代码、迁移或部署。

## Decisions

### D1. 用集成分支保留 merge 语义，最终直接替换旧图

从当前 `dev` 新建 `feature/intelligent-tracing-dev-merge`，把 `feature/intelligent-tracing-pm25` 以 `--no-ff` merge 进来，在集成分支上做适配；验收通过后把集成分支合回 dev。`langgraph.json` 的 `intelligent-tracing` 路径不变，最终 `graph.py` 直接是新版；不保留旧通用图，也不加第二公开 Graph ID。

旧图不是“空图”，它是带全量 MCP 工具和通用提示的自由 DeepAgents；但不符合受控溯源目标。直接替换的前提是新版已接真实数据/业务授权并通过 G1，而不是把 G0 空壳切上去。

### D2. 先升级依赖，再集成 tracing

Phase 0 单独提交升级：

- `pyproject.toml` 收紧 `deepagents>=0.7.28,<0.8`、`langchain-quickjs>=0.3.9,<1.0`，并把 `langchain>=1.4.6`、`langchain-core>=1.6.11`、`langgraph>=1.2.16` 的直写下限对齐。
- 用 Python 3.13 重新生成 `uv.lock`（目标：langchain 1.4.6、core 1.6.12、langgraph 1.2.16、checkpoint 4.3.0、langsmith 0.14.11、quickjs 0.3.9）。
- 固定 Dockerfile 基础镜像 digest，明确容器 tag；同步 `/api/constraints.txt` 与镜像内实际版本。
- 在 `155214a` 上分别跑 0.7.5 与 0.7.28，按失败集合比较；旧 `6ff95dd` 计数不能复用。

不维护 0.7.5 双基线。若升级未完成，tracing 集成不得宣称可合并。

### D3. 授权字段以 dev 业务模型为准

`TracingRequest`、`RuntimeScope` 和 `EvidenceRecord` 绑定：

- `business_code`
- `business_policy_version`
- `business_permission`
- 实际 `graph_id`
- tenant/user/thread/context_version/epoch

可信值来自 `identity_from_config` 和 `BusinessGrant.pinned_config()` 写入的 `ledger_business_code`/`ledger_business_policy_version`/`ledger_business_permission`/`ledger_graph_id`。`capability_snapshot_ref` 只保留为技能/工具选择引用，不再承担业务授权语义；不得从 `RuntimeScope` 合成身份。

主图继续使用 `TenantRuntimeGuardMiddleware(inject_mcp_tools=False)`，不向 lead 暴露 MCP 代理工具；其他图默认行为不变。

### D4. 站点目录和真实数据源都走统一授权执行层

新增 `StationCatalogProvider`：按当前可信 config 调 `execute_mcp_tool("ipp", "mcp_station_base_air_station_list", …)`，按企业/业务/区域做作用域缓存；`freeze_tracing_request` 从该 provider 取候选，而不是依赖空 `station_catalog` tuple。目录只用于消歧，不构成授权。

新增 `BusinessMCPDataSource` 替换 feature 的通用 executor 骨架：

- 每个 product 提供独立 `arg_builder` 和 `normalizer`（站点序列、邻站目录/序列、网格、历史气象、NWP 参数不同，不能共用 `{station,start,end,radius_km}`）。
- 默认执行器只调用 `execute_mcp_tool`，由 `ScopedMCPToolFactory`/`resolve_connection` 重新解析业务连接和 `approved_query_tools`。
- 保留 `CachedDataSource` 的请求级共享快照；缓存 key 增加 `business_code` 和业务策略版本，命中前仍复查身份/业务。
- 先做 `capability_mcp_mode=allowlist` + 已受理工具 ID 的零网络预检，真实执行再交给统一执行层。
- 不接受裸 HTTP、浏览器地址、自造凭据或通用 executor。

### D5. 真实 MCP 映射先发现后冻结

站点目录、目标站历史序列、历史气象、NWP 的候选已有较高置信度，但邻站序列、网格数据、网格邻接目前只有名字推断，`inputSchema`/返回体未验证。实施顺序必须是：

1. 在授权测试企业上做真实 `tools/list`，保存 inputSchema 和最小调用样本。
2. 确认每个 product 的实际参数、时间口径、返回字段、错误码和分页/上限。
3. 冻结 `product → tool` 映射、arg builder、normalizer 和 fixture 样本。
4. 再用真实 MCP 做成功/拒绝/撤权/超时用例。

在 schema 未确认前，不得把网格/邻站候选硬编码为事实，也不得以“工具目录存在”替代真实调用证据。

### D6. Tracing 专用业务工具白名单

`intelligent-tracing` 使用单独 `BusinessConnection` 子集，明确排除：

- `mcp_station_analysis_trajectory`
- `mcp_station_analysis_single_pscf`
- `mcp_station_analysis_history_pscf`

实际工具仍取“业务子集 ∩ 连接 `approved_query_tools` ∩ 本次已受理选择”。执行层再加轨迹/PSCF 名称拒绝作为第二道防线；其他业务白名单不受影响。

### D7. 四专员与状态通道保持现有受控设计

- `station-data`、`grid-data`：零模型确定性图，只回写 `station_findings`/`grid_findings`。
- `meteo-analyst`：隔离声明式专员，只调用受限气象/风场工具；数值由确定性中间件计算。
- `evidence-reviewer`：只输出 `EvidenceReview`，实际无数据/文件/执行工具，不决定最高许可。
- 原生委派只有 `description`/`subagent_type`；子图从父状态读取冻结请求。
- 每个子图只导出自己的字段；`structured_response` 与父 findings 分开更新；并行、反序完成、单侧失败、旧 revision 迟到均需测试。
- 0.7.28 的未变状态过滤只作为辅助，不能替代按字段归并和 revision 校验。

### D8. 六项污染物使用独立策略版本

阶段一 PM2.5 先通过，阶段二六项一起验收。六项启用时使用新的 `analysis-policy-v2`，而不是在 `analysis-policy-v1` 上悄悄扩大 `enabled_pollutants`；v2 明确 phase=2、六项启用、各污染物 QC/异常绝对下限/评价口径。

- CO 使用 `mg/m3`，其余 `ug/m3`。
- O3 小时序列用于过程/风场，日最大 8h 单独计算，处理跨日、缺测和有效性。
- PM2.5 24h 滑动只作补充统计。
- PM10/NO2/SO2 使用各自画像；没有独立评价口径时明确说明，不套用 PM2.5。
- 多污染物不得按不同单位/平均时段原始浓度直接排序；首要污染物只接受平台字段或共享标准化依据。

### D9. 报告门禁、发布与持久化统一

继续使用确定性 claim allowlist + 受控模板 + `ValidatedReport`：

- 模型只能提交 claim ID 计划，不能提交自由正文、参数或证据 ID。
- 允许有来源的完整率/同步率百分比和“无法计算贡献率”的拒绝说明；拦截没有禁词的越级归因。
- 聊天正文、画布、持久化和历史回读使用同一 `content_sha256` 和报告版本。
- 正式运行使用持久化报告存储；`InMemoryReportStore` 只限 G0 测试。
- 画布只接受已验证报告引用，服务端渲染；禁止任意 content 的 create_artifact 旁路。

### D10. 发布前用台账事实，不用 config 标志

`TracingPublishMiddleware` 发布前：

1. `guard_runtime_call` 复查当前身份和业务快照。
2. 用 `require_ledger_context`/`ledger_scope_from_config` 取 thread/request/attempt。
3. 调 `thread_deletion_exists` 检查墓碑；墓碑存在则抑制。
4. 调 `attempt_reservation(allow_terminal=True)` 检查运行/尝试状态；需要 `cancel_requested`/`cancel_confirmed`/`state_revision` 时，给该 SELECT 增列或新增只读 `fetch_attempt_lifecycle`。
5. 状态为 cancelled/timed_out/failed、取消已确认、归属不一致或台账不可用时失败关闭。

不新增公共事件类型；`resource.deleted`、`run.cancel_requested`、`run.state_observed` 已足够。测试注入 `lifecycle_probe`，生产默认调真实 store。

### D11. Skill 只作参考，不进入可选中主链

feature 的 `pollution-tracing/SKILL.md` 保留为内部参考/模板；新图不调用 `find_skill`，且 `inject_mcp_tools=False` 会在显式技能加载分支前返回。实施时要么把 tracing Skill 从可选中目录隐藏，要么在模型调用前增加独立、受控的规则加载；不得让“选中 Skill”看起来改变了授权或取数范围。首阶段推荐前者，避免新增未验证路由。

### D12. 验收分 G0/G1/G2，最终才替换

- G0：本机 0.7.28、fixture/fake model、纯函数、证据/门禁、并行/失败/修订、六项画像单测。
- G1：真实 IAM 业务权限、真实只读 MCP、真实模型、正式 Postgres 报告持久化/回读、真实站点目录和六项数据。
- G2：真实浏览器补问、报告、画布、追问、停止/删除、重开；键盘/窄屏按现有 UI 契约验证。

只有 G1/G2 通过后，才把集成分支合入 dev 并把 `intelligent-tracing` 切到新版。集成期间集成分支不部署到正式运行态。

## Risks / Trade-offs

- [0.7.28 升级破坏 basic-qa/data-analysis] → 在 `155214a` 上先跑 0.7.5/0.7.28 对比、三图导入、容器启动和全量单测；升级单独提交、单独回滚。
- [真实 MCP 网格/邻站 schema 未确认] → 先做 `tools/list` 和最小样本门禁；未确认工具不进入映射，不用名字推断。
- [业务白名单仍含轨迹/PSCF] → tracing 专用子集 + 执行层名称拒绝 + 真实外呼零调用测试。
- [发布前台账查询缺失] → 增列/新增只读 `fetch_attempt_lifecycle`；不改事件契约，不做跨服务 admin 调用。
- [六项污染物数据覆盖不一致] → 每项独立 fixture、真实样本和缺失/边界用例；没有独立评价口径时明确说明。
- [直接替换导致中间态回退] → 集成分支不部署；G1/G2 通过后才合入 dev；保留旧版本 commit 作为回滚点。
- [计划文档与实际三图/AGENTS 六图不一致] → 以 `langgraph.json` 为准，实施时同步修正 AGENTS 过期描述。

## Migration Plan

1. Phase 0：升级 dev 依赖/锁/镜像，重跑 `155214a` 基线；失败集合与 0.7.5 对比，单独提交。
2. Phase 1：从升级后的 dev 新建集成分支，merge feature；把 tracing 代码适配到 dev 业务授权和状态 schema。
3. Phase 2：实现 `StationCatalogProvider`、`BusinessMCPDataSource`、tracing 业务子集和真实 MCP 映射；先 PM2.5 闭环。
4. Phase 3：六项污染物/`analysis-policy-v2`、报告持久化、发布台账守卫、六项 fixture/真实数据验收。
5. Phase 4：G1/G2 验收通过后合入 dev，直接切换 `intelligent-tracing`；不保留旧图。
6. 回滚：保留升级前锁/镜像和旧 trace 代码 commit；依赖回滚只恢复锁/镜像，不改写已执行迁移；报告/画布按删除与保留策略处理。

## Open Questions

- 真实 MCP 的邻站/网格工具 inputSchema 和返回结构，需在实施时通过 `tools/list` 与最小样本确认；这会决定映射，但不会改变本设计的授权/门禁边界。
- 正式环境是否具备可用真实模型和测试企业 MCP 连接；不可用时 G1 记未执行，不得用 fixture 代替。
