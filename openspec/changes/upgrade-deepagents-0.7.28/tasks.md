## 历史基线说明（2026-10-19 复核）

以下 1.x–3.x 是 2026-10-18 在旧 worktree `/tmp/air-agent-upgrade`（HEAD `8e172de`）和旧六图清单上的实测记录。它们保留原始勾选、旧端口、旧服务和事故信息，只用于溯源；当前 `dev` 已收敛为三图（basic-qa、data-analysis、intelligent-tracing），不得把旧六图结果当作当前基线验收，也不得为满足旧描述恢复已删除图。

2026-10-19 复核还确认：升级 worktree 的 `pyproject.toml` 仍声明 `deepagents>=0.7.5,<1.0`，只是 `uv.lock` 解析到 0.7.28。该声明缺口必须在 G1 前修正并单独报告，不能描述为“声明已固定 0.7.28”。

## 1. 依赖升级（历史六图基线）

- [x] 1.1 在独立 worktree `/tmp/air-agent-upgrade`（HEAD `8e172de`）执行 `uv lock --upgrade-package deepagents --python 3.13`
- [x] 1.2 将 `langchain-quickjs` 下限提到 `>=0.3.9,<1.0` 并重新锁定
- [x] 1.3 使用 `uv sync --frozen --all-extras --python 3.13` 安装并确认关键版本：deepagents 0.7.28、langchain 1.4.6、langchain-core 1.6.12、langgraph 1.2.16、langgraph-checkpoint 4.3.0、langchain-quickjs 0.3.9、langsmith 0.14.11
- [x] 1.4 六个图全部导入并编译为 `CompiledStateGraph`
- [x] 1.5 全量单测对比：升级 30 failed / 414 passed / 51 skipped；基线 30 failed / 413 passed / 51 skipped；失败集合一致
- [ ] 1.6 将 `Dockerfile` 的 `FROM langchain/langgraph-api:3.13` 固定到 digest

## 2. 容器级验收

- [x] 2.1 构建镜像 `air-agent/langgraph-api:deepagents-0.7.28`（manifest `18cccdd5feeb`）
- [x] 2.2 使用隔离 Postgres（25432）、Redis、API（22024）与独立卷启动；`/ok` 200，启动 15.9s
- [x] 2.3 启动日志包含六个 `graph_id`，含 `intelligent-tracing`
- [x] 2.4 空库 `schema_migrations` 1–13 全部应用，`dirty=false`，checkpoint/store/thread/run 表存在
- [x] 2.5 未认证与伪造 Bearer 访问 `/assistants` 均 401；有效票据 `/assistants/search` 200，返回 6 个 assistant
- [x] 2.6 显式 thread_id 创建 200 → `claim-confirmation` 返回 `confirmed:true` → 回读 200
- [x] 2.7 `admit_request` 生成 request/submission/attempt 与能力选择快照；带预留创建运行 200
- [ ] 2.8 真实模型运行：当前阻塞于宿主 `192.168.64.1:8787` 无监听（`openai.APIConnectionError`）
- [ ] 2.9 QuickJS `execute` 运行时冒烟
- [ ] 2.10 开启 tracing 验证 langsmith 0.14.11 + `httpx2` 实际追踪
- [ ] 2.11 在存量库副本演练 checkpoint 4.1.1 → 4.3.0 升级

## 3. 风险与善后

- [x] 3.1 清理隔离容器、卷与临时 env 副本；共享栈恢复并确认 `/ok` 200
- [ ] 3.2 共享库 `seed.py` 误执行事故由 owner 复核（platform `role_permissions` 重插、tenant_admin 模板补齐）
- [ ] 3.3 `httpx2`/`httpcore2` 供应链与出网策略审查
- [ ] 3.4 回滚演练：恢复 0.7.5 锁并确认六图可启动
- [ ] 3.5 决定合并方式（独立分支/PR），不与溯源改造混提

## 4. 实施备注

- 日期：2026-10-18（Asia/Shanghai）
- 升级工作区：`/tmp/air-agent-upgrade`；变更仅 `pyproject.toml` 与 `uv.lock`
- 证据目录：`/tmp/upgrade-evidence/`（`api-startup.log`、`assistants.json`、`thread-*.json`、`tracing-run.json`、`summary.md`）
- 事故 1：`container-compose down` 连带停止共享 API，已用 `container-compose up -d --profile prod` 恢复
- 事故 2：误执行 `admin/api/scripts/seed.py` 写入共享库；影响与复核项见 design.md「实施事故与善后」


## 5. 当前基线补验计划（2026-10-19，三图）

本节是当前基线的待执行计划，不继承 1.x–3.x 的旧勾选。历史六图实测与事故记录仍按原文保留。

- [ ] 5.1 在升级 change 内把 `deepagents` 声明从 `>=0.7.5,<1.0` 收紧到目标范围（至少 `>=0.7.28,<0.8`），重新生成锁并用 `uv sync --frozen --all-extras --python 3.13` 复核实际版本；声明与锁分开报告
- [ ] 5.2 以当前 `dev` HEAD（复核时为 `6ff95dd`）新建独立临时 worktree，不把业务/三图代码硬写进旧六图 worktree；保留原 `/tmp/air-agent-upgrade` 的 diff 与证据
- [ ] 5.3 在新 worktree 和 0.7.28 锁上导入/编译当前三图，记录解释器、包版本、锁摘要和 HEAD；不得用旧六图结果替代
- [ ] 5.4 在当前三图基线上运行定向/全量单测，按失败集合与 0.7.5 基线比较；既存 flaky 单列
- [ ] 5.5 用当前锁构建新镜像并在隔离栈启动，确认 `/ok` 200、启动日志仅/至少当前三图注册、`intelligent-tracing` 未被跳过
- [ ] 5.6 在隔离空库上复核 checkpoint 迁移 1–13（或当前 head）与 `dirty=false`；在库副本上演练存量 4.1.1 → 4.3.0，记录回滚限制
- [ ] 5.7 复核鉴权失败关闭、线程归属确认、台账受理后创建运行；真实票据与临时资源均不得来自历史报告
- [ ] 5.8 在可用真实模型下完成一次当前图运行；记录模型配置、结果、失败；不可达记未执行
- [ ] 5.9 开启 tracing 验证 langsmith 0.14.11 + `httpx2` 的实际链路和出网策略；未执行不得写成通过
- [ ] 5.10 更新交付说明：明确三图当前结论、旧六图历史结论、声明缺口、未执行项与回滚证据
