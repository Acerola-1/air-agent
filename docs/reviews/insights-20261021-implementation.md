# Change 7 需求洞察实施报告（2026-10-21）

## 1. 基线与边界

- 基线：`dev@2a66945`（工作区开始时干净），迁移 head `0027_console_paging_index`。
- 实施分支：`codex/change-07-demand-insights`（未 commit/push/merge/归档）。
- 工作区：主仓库独立分支；未修改 `src/intelligent_tracing/**`、图、MCP、根
  `pyproject.toml`/`uv.lock`、Dockerfile 或共享 RAG/Qdrant。
- 共享接线：`app/main.py` 增加一行 router 注册；`models.py` 增加本域 ORM 注册；
  `permission_catalog.py` 增加 `agent:insights:manage` 展示目录项；其余为本域新文件。
- 正式库/正式 IAM/共享 Langfuse/知识库未被访问；测试使用自建
  `air-agent-insights-pg`（127.0.0.1:15433）与自建卷。

## 2. 实现内容

### 2.1 持久化与迁移

新增迁移 `admin/api/migrations/versions/0028_demand_insights.py`：

- `agent_insight_profiles` / `agent_insight_profile_state`
- `agent_insight_jobs` / `agent_insight_delegations`
- `agent_question_sources` / `agent_question_source_gaps`
- `agent_question_intents` / `agent_question_intent_contents`
- `agent_question_topics` / `agent_question_clusters` / `agent_question_cluster_vectors`
- `agent_question_assignments` / `agent_insight_operations`

复合 FK 同企业、FORCE RLS、运行角色最小读写、独立
`air_agent_insights_worker` 跨企业领取/委托读取；原文/向量与可聚合元数据分表，
metrics 不能通过普通查询取得 intent 原文或簇原型。迁移不提供 downgrade
（删除/到期事实不能因回退丢失）。

空库从 0001 升到 0028 成功；关键策略/授权已在真实 PG 检查（32 条本域策略）。
`tests/demand_insights/test_db_integration.py` 验证 A/B 企业隔离、metrics 不能读
intent_contents、runtime 不能读 delegation、worker lease/fence、late
confirmation、隐私清理与统计去重。

### 2.2 标准化、模型与归类

- 确定性脱敏（手机/邮箱/账号/坐标）、证件/银行卡/显式口令令牌拒绝、注入前缀
  不进入控制流；规则拆分一条两意图并保留城市/日期/污染物 slots。
- 动作检测修正：`查…明天` 仍是 query；`趋势/近7天` 在无显式趋势词时按 query
  处理；显式预测词才进 forecast。
- 有界 cosine 候选（≤2000 簇、top10）、动作/对象/否定/时间约束、容量转
  `capacity_review`、临界 LLM 复核、无匹配 candidate、低置信 unclassified。
- Embedding/LLM 适配器：受保护 endpoint、维度/有限值/范数校验、3 次退避、
  usage 缺失不记 0、未配置不外发。

### 2.3 作业、委托与 worker

- `python -m app.demand_insights.worker` 显式启动，默认 off；独立 worker 角色；
  每批/外呼前/提交前重新内省委托；撤权/切企业/过期暂停，IAM 故障暂停。
- 持久 job、唯一来源版本、租约/fence、CAS 进度、断点/anti-join、缺口重试、
  晚确认/晚补内容回看、预算暂停、持续增量后继作业。
- 委托只存应用会话 handle SHA-256，job 不存 Bearer/cookie；最长 12h 且不超过
  会话有效期。

### 2.4 统计、维护与隐私

- 标准问题/主题榜，原子意图/去重请求/独立用户/占比分母；主题直接去重不累加
  子簇；未评价不等于未解决；负反馈为请求级证据。
- 增长同长度窗口；前期覆盖不足不可比；真实零基线 `new=true` 且
  `growth_rate=null`；`today` 两边界各减 1 业务日。
- 改名/指派/合并/拆分、CAS 与 operation_id 幂等、合并重定向环检测、拆分预览
  token 绑定企业/profile/来源集合/修订/过期。
- 删除/到期清理文本、向量、归属、原型；恢复脚本按企业重施墓碑并暂停过期委托
  作业。

### 2.5 后台页面

新增 `/operations/insights`、`[clusterId]`、`jobs`、`unclassified`，复用 6 的
`QueryToolbar`/`DataTable`/`TrendBars`/`useOperationQuery`；侧边栏增加“需求洞察”。
错误/空态/覆盖不足、数据截止、权限分区、窄屏与焦点均已实测（见证据）。

## 3. 验证结果

### 3.1 自动化

```bash
cd admin/api
PYTHONPATH=../../src INTERNAL_API_TOKEN=test-token \
INSIGHTS_TEST_OWNER_URI=... INSIGHTS_TEST_WORKER_URI=... ADMIN_RUNTIME_DATABASE_URI=... \
.venv/bin/python -m pytest tests/demand_insights -q
# 43 passed
```

- `tests/demand_insights/test_{normalization,matching,scope,calibration_preview}.py`
- `tests/demand_insights/test_{db_integration,api_contract,openapi_contract}.py`
- 前端：`npm --prefix admin/web run lint`（tsc）通过；
  `npm --prefix admin/web run build` 通过（28 个静态页，新增 4 条 insights 路由）。

### 3.2 真实 Embedding 校准

`docs/reviews/evidence/run/insights-20261021/calibration-real.json`

- 供应商：SiliconFlow `Qwen/Qwen3-Embedding-8B`，4096 维，真实 HTTP；
- 160 条合成样本：60 校准（留一法调阈值）/100 冻结留出；
- 选定阈值：similarity `0.72`、margin `0.10`、review_low `0.62`；
- 留出：100 个清晰意图，自动归类 100，正确 100，precision `1.0`，
  coverage `1.0`，关键误合并 0，敏感泄漏 0；
- 用量：Embedding 1417 tokens（其他模型未调用；Token 总账未采集）；
- 限制：合成模板样本，不代表生产分布；辅助 LLM 未配置，本次只验证确定性路径+
  Embedding 阈值，LLM 临界复核未做真实质量门禁。

### 3.3 真实浏览器

登录 `http://localhost:3101`（本地测试 API 8101 + 自建 PG，local 身份）后实测：

- 总览：4 合格/4 已处理/覆盖 100%、标准问题表、主题示例、分析版本/归属修订；
- 详情：标准问题、统计、每日趋势、约束、脱敏来源、原问题授权链接；
- 改名：真实 POST 200，标题与修订 0→1 更新；
- 指派（新建簇）：真实 POST 200，来源意图移动，旧簇统计归零；
- 作业页：创建增量作业在 local 模式返回 `remote_identity_required`，页面正确显示
  安全错误；
- 未归类页：空态、缺源队列、权限分区；
- 窄屏 390×844：`documentElement.scrollWidth=375 < innerWidth=390`，无页面级
  横向溢出；表格自身横向滚动；
- 证据：`browser-overview.png`、`browser-overview-narrow-390.png`、
  `browser-detail-rename-form.png`、`browser-jobs-page.png`、`browser-login*.png`。

## 4. 验收矩阵（Q01–Q20）

| ID | 状态 | 证据/限制 |
|---|---|---|
| Q01 | 部分通过 | 真实 PG A/B RLS 测试；未做 owner/非 owner 全表正反对照 |
| Q02 | 部分通过 | metrics 只返回聚合，sources/unclassified 403；root 旁路未实测 |
| Q03 | **阻塞** | 真实 IAM 未运行；`agent:insights:manage` 未登记；委托内省无真机正对照 |
| Q04 | 通过 | 同来源重复处理 active assignment 唯一；多 attempt/resume 未单独造数 |
| Q05 | 通过 | 晚确认 gap 重查；`test_late_confirmation_gap_rediscovered` |
| Q06 | 通过 | 多意图/追问/敏感/注入纯函数测试 |
| Q07 | 通过 | 真实留出 query/cause/forecast/advice/trend 关键误合并 0 |
| Q08 | 部分通过 | 维度/容量/非法向量单测；未造 2001 簇真实表加载 |
| Q09 | 通过（Embedding） | real calibration 报告；辅助 LLM 未验证 |
| Q10 | 部分通过 | lease/fence/旧结果拒绝测试；双 worker 并发建簇未压测 |
| Q11 | 部分通过 | 适配器重试/usage 代码；无真实 503/预算故障注入 |
| Q12 | 通过 | 原子/请求/用户三计数 DB 测试；业务日窗口单测 |
| Q13 | 通过 | 真实零基线 new=true/growth null；覆盖不足不可比 |
| Q14 | 通过 | 请求级负反馈 DB 测试；技术失败/停止实际事件未造数 |
| Q15 | 通过 | 改名/合并/拆分 CAS/幂等/来源覆盖 DB 测试 |
| Q16 | 部分通过 | profile 版本/重建/激活代码与校准写入脚本；半途失败未演练 |
| Q17 | 部分通过 | 删除/到期清理与晚到写保护代码 + 清理测试；模型运行中删除竞态未实测 |
| Q18 | **部分/阻塞** | `insights_restore_guard.py` 与恢复顺序文档；未做真实备份恢复演练 |
| Q19 | 部分通过 | 真实浏览器总览/详情/改名/指派/错误态/窄屏；启用→新提问→删除全链因无 IAM/聊天栈未跑 |
| Q20 | **阻塞/后置** | 7.4 性能按用户要求后置；未做 10 万请求/1000 簇基准与三图最终组合 |

## 5. 配置/权限/迁移/回退/启用清单

### 配置

- `INSIGHTS_ENABLED`、`INSIGHTS_WORKER_ENABLED`（默认均 false）；
- `INSIGHTS_WORKER_DATABASE_URI`（独立 worker 角色）；
- `INSIGHTS_EMBEDDING_*`（真实校准必须 4096 维 Qwen3-8B 或另行校准）；
- 可选 `INSIGHTS_LLM_*`；
- `INTERNAL_API_TOKEN`（游标/预览签名）。

### 权限

- 新增 `agent:insights:manage`，写操作必须同时具备
  `agent:operations:read` + `agent:content:read`；
- 本域不加入 `agent:business:*:use`，不恢复工具/Skill 逐人授权；
- 真实 IAM 登记/授予/撤权**未完成**（无运行中的自有 IAM 测试实例）。

### 迁移

- `0028_demand_insights` 接 `0027_console_paging_index`；
- 先 `bootstrap_tenant_db.py`（runtime + insights worker 密码），再 `alembic upgrade head`；
- 空库已验证；存量库升级只新增本域表，不改 0024–0027 事实。

### 回退

- 先关 worker 与页面入口，保留旧 active profile；不 downgrade 0028；
- 删除/到期 worker 继续运行；恢复备份先重施墓碑/权限/委托有效期。

### 启用

- 管理员在正常管理面授权 `agent:insights:manage`；
- 配置受保护模型端点并应用通过的校准报告；
- 选择起始业务日创建增量作业；首次不默认分析全部历史；
- 逐项复核 Q03/Q18/Q19/Q20 门禁后再宣布上线。

## 6. 未完成门禁与阻塞

1. **真实 IAM**：本地 19100 未监听；无自有 IAM 测试库/管理面可用，未登记
   `agent:insights:manage`，未完成授权/撤权/切企业实机验证。不得以本地目录或
   mock 令牌替代。
2. **辅助 LLM**：未配置受保护 LLM 端点，临界复核/多意图模型拆分未做真实质量
   验证；确定性路径与真实 Embedding 已通过。
3. **Worker 全链**：真实委托依赖 IAM 会话，未跑“启用→新提问→增量归类→删除→
   刷新”完整链；分类/lease/RLS/清理已分别以 worker 角色实测。
4. **性能与最终三图组合**：按 tasks.md 2026-10-21 用户说明后置；未做 10 万请求/
   1000 簇基准，未在最终 0.7.28 锁/溯源新版上回归。
5. **恢复演练**：只有脚本与顺序说明，未做真实备份恢复演练。
6. **共享 OpenAPI 冻结**：`test_ledger_openapi_freeze` 在 Change 6 后已因
   `/agent/console/*` 新增路由失败（本 change 又加 `/agent/insights/*`）；按并行
   契约未覆盖他方生成副本，待集成负责人统一重新生成。

## 7. 资源与复跑

- 已使用并**回收**的自有资源：
  - 容器 `air-agent-insights-pg`（postgres:17-alpine，127.0.0.1:15433）；
  - 卷 `air-agent-insights-pgdata`；
  - 数据库 `air_agent_insights_test`，角色 `air_agent_runtime`、
    `air_agent_insights_worker`（测试密码仅本机，未写入仓库）；
  - 前端测试进程 admin API 8101、admin web 3101（已停止）。
- 证据目录：`docs/reviews/evidence/run/insights-20261021/`（该 run 目录被
  `.gitignore` 忽略，保留在本机供协调者复核；可提交的小型汇总为
  `docs/reviews/insights-calibration-summary-20261021.json`）。
- 本报告不包含正式用户原文、凭据或共享服务数据。
