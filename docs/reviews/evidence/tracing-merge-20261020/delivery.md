# 交付说明：智能溯源集成到 dev（2026-10-20）

本文件记录 `openspec/changes/integrate-intelligent-tracing-into-dev` 当前实际完成范围。
本文件已按 2026-10-20 的 G1 PM2.5 实跑结果更新；**G2、其余五种污染物、
授权 MCP 拒绝/撤销、IAM 503 仍未执行**，不能据此宣称完整验收通过。

## 1. 分支与提交

| 内容 | 提交 |
|---|---|
| 从 `dev` 新建集成分支并 `--no-ff` 合并 feature | `ad50180`（`feature/intelligent-tracing-dev-merge`） |
| Phase 0：依赖/锁/镜像/迁移基线 | `fd545a9`（可单独回滚） |
| tracing 业务授权、真实 MCP 数据源、报告持久化、发布门禁 | `215d6cb` |
| 文档与交付说明 | `b0e2a95` |
| G1 真实运行修复 + PM2.5 证据 | `acc57d2` |
| 合并到 `dev` | `a1184c6`（G2 经用户明确接受延期） |

`dev` 已合入集成分支：merge commit `a1184c6`；合并前 `dev` 为 `baa1b24`。
G2 未执行，用户于 2026-10-20 明确接受延期后才合并；change 保持活动、未归档。

## 2. 改了什么、为什么

- **运行时基线**：`deepagents 0.7.28`、`langchain 1.4.6`、`core 1.6.12`、`langgraph 1.2.16`、
  `checkpoint 4.3.0`、`quickjs 0.3.9`、`langsmith 0.14.11`；`pyproject.toml` 下限收紧，
  `uv.lock` 由 Python 3.13 重生成；基础镜像固定 digest，应用镜像 tag 改为
  `air-agent/langgraph-api:tracing-merge-20261020`。原因：feature 依赖 0.7.28 的中间件/
  子图签名，dev 0.7.5 下 tracing 有 8 个失败。
- **授权契约**：`TracingRequest`、`RuntimeScope`、`EvidenceRecord` 绑定
  `business_code`/`business_policy_version`/`business_permission`/实际 `graph_id`；可信值只来自
  平台注入的 `ledger_business_*`。`capability_snapshot_ref` 降为技能/工具选择引用，缺失时
  不再充当授权证明；缺少可信业务快照时冻结、取数、报告、发布全部失败关闭。
- **真实数据接入**：新增 `StationCatalogProvider` 与 `BusinessMCPDataSource`，站点目录、
  目标站/邻站小时序列、历史气象（NWP）、网格值/几何全部经
  `execute_mcp_tool` → `ScopedMCPToolFactory`；每个产品独立参数构造与归一化，不使用
  万能 `{station,start,end,radius_km}`。请求级缓存 key 增加业务码/策略版本，命中前复查身份。
- **工具边界**：业务注册表为 `intelligent-tracing` 增加窄子集，排除
  `mcp_station_analysis_trajectory`、`mcp_station_analysis_single_pscf`、
  `mcp_station_analysis_history_pscf`；lead 仍使用
  `TenantRuntimeGuardMiddleware(inject_mcp_tools=False)` + 无文件/执行工具。
  tracing Skill 从在线可选目录移除，仅留内部参考。
- **六项污染物**：生产默认 `analysis-policy-v2`（phase=2，六项启用），`analysis-policy-v1`
  保持仅 PM2.5，不静默扩大。
- **报告与发布**：`ValidatedReport` 绑定 `run_id` 与作用域摘要；生产改用
  `PersistentReportStore`（归档表、scope/摘要/过期/删除校验），替代默认
  `InMemoryReportStore`；发布前新增 `fetch_attempt_lifecycle` 只读查询（复用既有列，
  不新增事件/表），结合 `guard_runtime_call`、`thread_deletion_exists` 抑制取消/失败/
  墓碑/归属不一致/台账不可用的发布。

## 3. 基线与验证

| 项 | 结果 |
|---|---|
| `155214a` + 0.7.5 全量单测 | 7 failed / 465 passed / 73 skipped |
| `155214a` + 0.7.28 全量单测 | 7 failed / 465 passed / 73 skipped（失败集合相同） |
| 集成分支 + 0.7.28 全量单测 | 8 failed / 557 passed / 73 skipped（7 既存 + 已知 flaky `test_total_deadline_discards_late_success`，单跑 3 次通过） |
| tracing 单测（G1 修复后） | 97 passed |
| 全量单测（G1 修复后） | 7 failed / 564 passed / 73 skipped（失败集合与基线相同；`test_guard_middleware_framework_compat` 单跑通过） |
| 三图冷导入 | 成功，网络尝试 0 |
| 新镜像 | 构建成功；`/ok` 200；三图注册；未认证/伪造 Bearer 401 |
| 空库迁移 | 62 条迁移（max 63），`dirty=false`，checkpoint/store/thread/run 表存在 |
| 存量迁移 | 旧运行时先迁移的自建库由新镜像接管，`/ok` 200、`dirty=false`、新增迁移 0 |
| 报告持久化（自建 Postgres） | 保存/回读/摘要/run 绑定/篡改/过期/删除/跨 scope 隔离 5 passed |
| MCP 目录 | 109 个工具真实 `tools/list`；站点目录/序列、历史气象、网格目录/序列/几何均取得真实最小样本 |
| G1 PM2.5 真实链路 | 真实 IAM `root`/`alpha-env`、真实 MCP、真实 `hy3`；报告 `rep_618e1b4b1c6d460b9e5f5019dfa61176`，台账 `succeeded`；站点覆盖 23/23、24h 滑动均值 16.0 µg/m³；报告为确定性 fallback 并明确气象/空间限制 |
| 真实身份负例 | 401 未登录、403 `context_missing`、409 `context_changed` 实测通过 |

证据根目录：`docs/reviews/evidence/tracing-merge-20261020/`
（`baseline.md`、`upgrade.md`、`mcp-catalog-probe.md`、各 pytest/迁移/启动日志）。

## 4. G1/G2 执行状态

已完成：

- **授权企业 MCP 成功路径（8.3 部分）**：真实 IAM 业务许可下，MCP 目录、
  站点小时序列、历史气象/网格请求均经 `execute_mcp_tool` 发出；PM2.5 运行取得
  站点 23/23 小时数据。
- **真实 IAM 正例/负例（8.2 部分）**：401 无会话、403 缺企业上下文、
  409 企业上下文不一致实测；`root` 正常签发 runtime ticket 并完成真实运行。
- **真实模型 PM2.5（8.4）**：真实 `hy3` 完成 freeze、并行子任务和报告尝试；
  最终正文由确定性 fallback 渲染并过 gate 发布，模型自由文本不可见。

仍未执行：

- **IAM 503 / 撤权（8.2）**：未短暂停止 IAM 制造 503，也未做真实撤权演练；
  发布前台账探针仍是受控探针矩阵。
- **MCP 拒绝/超时/撤权（8.3）**：未在真实业务许可撤销后验证“新增 MCP 请求为 0”。
- **六项污染物（8.5）**：只完成 PM2.5；PM10 首次实跑在工具阶段超过 3 分钟未收敛，
  已主动取消；O3/NO2/SO2/CO 未执行。
- **真实浏览器（8.7）**：未登录正式页面，未执行补问、画布、停止/删除、窄屏/键盘证据。
- **成本/析因（8.8）**：未采集 Token/费用/重试的完整汇总；本次仅记录单次
  报告与台账事实。
- **正式库迁移/回滚（8.6 的正式环境部分）**：只在自建 Postgres 执行；未对共享正式库
  downgrade。旧 `:latest` 镜像内置代码需要已退役环境键，不能直接作为回滚镜像，
  回滚须按旧锁重建。
- **真实 MCP 边界工具**：`mcp_station_base_range_site_list`、`mcp_station_base_lng_lat_near_...`、
  `mcp_grid_base_grid_by_station_proximity` 与 `gridHashType` 参数在真实服务端失败，
  已排除出映射；网格 `row/col` 由经纬度量化得到，只用于确定性排序，不代表官方拓扑。
- **Token/成本**：本轮未采集模型 Token 与成本。

## 5. 回滚方式

1. 代码：在 `dev` 上 `git revert -m 1 a1184c6`；需要完全回到升级前时，再按
   `fd545a9` 之前的锁与镜像重建。
2. 依赖：恢复 `pyproject.toml`/`uv.lock` 到 0.7.5 并 `uv sync --frozen`；重建镜像。
3. 数据库：迁移序列未改变（62/63，`dirty=false`），无破坏性迁移；回滚运行时不需要
   downgrade 正式库。
4. 运行态：正式栈当前运行 `air-agent/langgraph-api:tracing-merge-20261020`，
   `/ok=200`、三图注册；旧 `:latest` 不能直接回滚启动，须按旧锁重建。

## 6. 当前风险

- 网格/邻站的数据口径与官方拓扑不等价；报告只输出观测现象，不输出源距离/贡献率。
- 新镜像基础层升级到 API 0.16.2；生产 `DB_MIGRATION_BY_CORE_API=false` 是当前镜像布局下
  可用路径（Go core 迁移目录在镜像内缺失）。
- G2 未执行，浏览器端的补问、画布、停止/删除/重开和窄屏/键盘行为尚无实测证据；
  PM2.5 单次成功不能外推为六项污染物或全部企业场景通过。
- `tests/unit_tests/test_graph_import_no_network.py` 单独运行偶发挂起（全量套件通过），
  属于既存测试抖动，本轮以受控超时脚本留证。
