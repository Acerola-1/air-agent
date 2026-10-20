# 交付说明：智能溯源集成到 dev（2026-10-20）

本文件记录 `openspec/changes/integrate-intelligent-tracing-into-dev` 当前实际完成范围。
**没有把 G1/G2、真实模型、授权企业 MCP 或浏览器验收写成已通过。**

## 1. 分支与提交

| 内容 | 提交 |
|---|---|
| 从 `dev` 新建集成分支并 `--no-ff` 合并 feature | `ad50180`（`feature/intelligent-tracing-dev-merge`） |
| Phase 0：依赖/锁/镜像/迁移基线 | `fd545a9`（可单独回滚） |
| tracing 业务授权、真实 MCP 数据源、报告持久化、发布门禁 | `215d6cb` |
| 文档与交付说明 | 本轮最后一个文档提交 |

`dev` 分支本身**尚未**合入集成分支（按 change 的 D12/8.10，需 G1/G2 先通过）。

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
| tracing 单测 | 91 passed |
| 三图冷导入 | 成功，网络尝试 0 |
| 新镜像 | 构建成功；`/ok` 200；三图注册；未认证/伪造 Bearer 401 |
| 空库迁移 | 62 条迁移（max 63），`dirty=false`，checkpoint/store/thread/run 表存在 |
| 存量迁移 | 旧运行时先迁移的自建库由新镜像接管，`/ok` 200、`dirty=false`、新增迁移 0 |
| 报告持久化（自建 Postgres） | 保存/回读/摘要/run 绑定/篡改/过期/删除/跨 scope 隔离 5 passed |
| MCP 目录 | 109 个工具真实 `tools/list`；站点目录/序列、历史气象、网格目录/序列/几何均取得真实最小样本 |

证据根目录：`docs/reviews/evidence/tracing-merge-20261020/`
（`baseline.md`、`upgrade.md`、`mcp-catalog-probe.md`、各 pytest/迁移/启动日志）。

## 4. 未执行（G1/G2 与真实环境限制）

- **授权测试企业 MCP**：没有可用的测试企业票据/凭据，未执行业务许可下的真实调用；
  目录样本为匿名只读探测，不能替代 8.3。
- **真实 IAM 正/反例（8.2）**：未取得授权测试企业/账号，未执行 401/403/409/503 矩阵。
  发布前台账探针用受控探针与存储函数矩阵验证，未做真实跨企业撤权演练。
- **真实模型闭环（8.4/8.5）**：未确认可用模型网关与授权企业，未执行 PM2.5/六项真实链路。
- **真实浏览器（8.7）**：未登录正式页面，未执行补问、画布、停止/删除、窄屏/键盘证据。
- **正式库迁移/回滚（8.6 的正式环境部分）**：只在自建 Postgres 执行；未对共享正式库
  downgrade。旧 `:latest` 镜像内置代码需要已退役环境键，不能直接作为回滚镜像，
  回滚须按旧锁重建。
- **真实 MCP 边界工具**：`mcp_station_base_range_site_list`、`mcp_station_base_lng_lat_near_...`、
  `mcp_grid_base_grid_by_station_proximity` 与 `gridHashType` 参数在真实服务端失败，
  已排除出映射；网格 `row/col` 由经纬度量化得到，只用于确定性排序，不代表官方拓扑。
- **Token/成本**：本轮未采集模型 Token 与成本。

## 5. 回滚方式

1. 代码：`git revert 215d6cb` 后用 `fd545a9` 之前的锁与镜像；集成分支未合入 `dev`。
2. 依赖：恢复 `pyproject.toml`/`uv.lock` 到 0.7.5 并 `uv sync --frozen`；重建镜像。
3. 数据库：迁移序列未改变（62/63，`dirty=false`），无破坏性迁移；回滚运行时不需要
   downgrade 正式库。
4. 运行态：正式栈当前仍运行旧镜像（`air-agent/langgraph-api:latest`）；下一次
   `deployment/run_separated_local.py start` 会按 compose 使用新 tag，需确认新镜像已
   构建并验证。G1/G2 未通过前不要把集成分支合入 `dev`。

## 6. 当前风险

- 网格/邻站的数据口径与官方拓扑不等价；报告只输出观测现象，不输出源距离/贡献率。
- 新镜像基础层升级到 API 0.16.2；生产 `DB_MIGRATION_BY_CORE_API=false` 是当前镜像布局下
  可用路径（Go core 迁移目录在镜像内缺失）。
- 正式栈若在 G1 前重启，会加载集成分支源码但仍是 0.7.5 依赖镜像；tracing 运行时可能
  不可用，basic-qa/data-analysis 不受影响。
- `tests/unit_tests/test_graph_import_no_network.py` 单独运行偶发挂起（全量套件通过），
  属于既存测试抖动，本轮以受控超时脚本留证。
