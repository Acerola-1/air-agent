# Agent 开发指南

本文件约束整个仓库的 AI/自动化开发。面向使用者的介绍与启动步骤放在 [README.md](README.md)，交互规范放在 [UX-CONTRACT.md](UX-CONTRACT.md) 和 [DESIGN.md](DESIGN.md)；子目录有更具体指南时同时读取。

## 开始工作

- 先检查分支、HEAD、工作区 diff、相关 OpenSpec 和实际服务配置，区分已实现、规划和未验证事实。不要覆盖、重置或顺手删除其他任务的未提交改动。
- 存在 `.codegraph/` 时，理解/定位代码先用 CodeGraph（MCP `codegraph_explore` 或 `codegraph explore`）；不存在时跳过，不自行建索引。文本与路径搜索优先 `rg` / `rg --files`。
- 涉及 Next.js 时读取 `frontend/AGENTS.md` 和当前安装版本 `node_modules/next/dist/docs/` 的相关指南。
- IAM 与知识库是独立项目。未经该次任务授权，不改外部仓库，不把规划中的服务切换视为已经上线。

## 仓库边界与入口

| 区域 | 当前职责 |
|---|---|
| `langgraph.json` | 六个公开 Graph ID 与 custom auth/custom HTTP 声明 |
| `src/basic_qa/`、`src/intelligent_analysis/` | 显式业务节点流和语义 Skill 路由 |
| `src/data_analysis/` | 菜单驱动的确定性分析；不能被显式技能绕过菜单约束 |
| `src/intelligent_report/`、`src/deep_research/`、`src/intelligent_tracing/` | DeepAgents；涉及执行边界需覆盖主任务和真实子任务 |
| `src/common/identity/`、`capabilities.py`、`mcp_scoped.py` | 可信身份、能力选择及作用域工具发现/执行 |
| `src/common/event_ledger/`、`observability/` | 运行事实台账和模型/工具/Skill观测 |
| `frontend/` | Next.js聊天、反馈、能力选择、画布和同源BFF |
| `admin/api/` | FastAPI身份/企业/业务API、迁移、种子、恢复与管理测试 |
| `admin/web/` | 当前Next.js管理页面；独立IAM的Vue计划不改变此事实 |
| `docs/contracts/agent-events-v1/` | 事件契约源文件与接口快照 |
| `openspec/specs/` | 主规格；`changes/`存活动计划，`changes/archive/`存已关闭阶段 |

所有公开图的实际路径以 `langgraph.json` 为准。Skill目录采用 `SKILL.md` 及可选 `references/fast.md` / `expert.md`；空目录不能算已有业务技能。空气质量日期与等级的历史约定已移至 [领域参考](docs/domain/air-quality-level-reference.md)，涉及计算/规则时必须核对该文档、具体Skill、实现与测试，不能猜补法规边界。

## 不可回退的边界

- **身份与租户**：浏览器声明不是授权。每请求及模型/工具外呼前实时复查身份、企业成员、修订与绑定；撤销即时生效。身份/授权后端不可用时失败关闭，不回退匿名、旧HMAC或本地JSON账号。
- **数据库**：请求走非owner、非BYPASSRLS运行角色；事务本地 `air_agent.*` GUC、FORCE RLS和复合关联共同隔离。owner连接仅用于明确授权的迁移/引导/恢复，不进入请求热路径。
- **受理与执行**：台账受理和幂等先于平台启动；不确定结果不盲目重发。request/submission/attempt/platform run是不同对象，不能互换ID。选择快照由服务端冻结并回读，不能从前端草稿拼造。
- **MCP/Skill**：选择只能缩小当前许可。目录、实际dispatch、重试、恢复、DeepAgents子任务与规则文件读取都要执行同一约束；只隐藏schema或提示模型拒绝不算保护。导入图不能做模型/Embedding/MCP网络预热。
- **事实与统计**：停止意图不等于已取消，选中不等于loaded/executed，工具HTTP200不等于业务成功。usage/价格未知不记零，逻辑调用与重试尝试分开计数。
- **隐私与删除**：凭据、会话、原文和内部推理不进入普通事件或日志；受控正文按独立权限/保留期访问。删除优先于晚到写入、回读、恢复和重放，恢复备份后重施墓碑及最小权限。
- **异步UI**：按用户、企业、context_version、图和epoch丢弃旧结果；A→B→A同样隔离。运行及stopPending时锁住相关操作，未知停止状态不能假装成功。

## 依赖与开发命令

- Python 3.13，优先 `python3`、`.venv/bin/python` 或 `uv run`，不假设系统有 `python`。
- 根 `pyproject.toml` / `uv.lock` 是Agent依赖声明与锁；管理API另有自己的项目/锁。日常 `uv sync --all-extras`，运行环境 `uv sync --frozen --no-dev`；依赖变更用 `uv add/remove`，不绕开声明私装后声称锁已覆盖。
- 管理API与运行时联合开发的环境安装方式见README；不要假设只装其中一个项目即可联调。
- 不恢复废弃的 `requirements.lock.txt`。Dockerfile从锁导出依赖；OpenTelemetry约束以当前pyproject及官方镜像兼容性为准，不凭旧文档改版本。
- Python遵循Ruff、snake_case模块/函数、PascalCase类；Skill目录用kebab-case。只为需要解释的接口/逻辑添加清楚注释或Google风格docstring。

常用定向检查：

```bash
.venv/bin/ruff check <受影响文件>
.venv/bin/ruff format --check <受影响文件>
.venv/bin/python -m pytest <相关测试路径> -q
npm --prefix frontend run test:contracts
npm --prefix frontend run build
npm --prefix admin/web run lint
```

需要类型检查时优先basedpyright，pyright可回退。已有Makefile依赖当前环境中的python；使用它前确认解释器。不要为文档或低影响改动机械重复长测试；共享契约、持久化、鉴权或图接线变动应执行相应回归和实际联调。

## OpenSpec与验收

- 先核实相关change状态、主规格和依赖，按proposal/design/specs/tasks推进；strict通过只证明文档结构，不证明产品实现。
- 正文、要求、场景、设计和任务用中文；保留CLI需要的英文结构标记，如 `## ADDED Requirements`、`### Requirement:`、`#### Scenario:`、WHEN/THEN及checkbox格式。
- 新迁移接当前head；验证空库与存量升级，不能改已执行revision来消除漂移。契约扩展同步源登记、src/admin/TS validator、生成副本、样本与OpenAPI快照。
- 测试放在相关域：根 `tests/unit_tests/`、`tests/integration_tests/`、`admin/api/tests/`、`frontend/tests/`。安全反例必须有有效正向控制；MCP越权看真实请求数0，不能只看mock或页面。
- 区分单测、受控模型/服务、真实配置模型、实际SDK与真实浏览器；图加载成功或HTTP200不能代替运行完成。UI需要实际页面/键盘/窄屏证据，事件模拟不代替系统输入法/剪贴板物理验证。
- 对照具体基线失败身份，不删断言、弱化条件或全局跳过来过关。原报告/证据不可覆盖：复跑用新目录，保留时间、来源和局限。
- 耗时用实测，模型实施、验证及外部等待分开；Token不可得记未采集。模型评分保留原始成绩，协调者补修不归实施模型。
- 归档同步主规格并保留验收报告。未完成项或用户接受的豁免要显式记录，不能改写成实测通过。

## 容器与测试资源

本机使用Apple `container`及 `container-compose`，没有Docker Engine socket。编排支持up/down/build/version；日志、查看和执行用原生CLI：

```bash
container ls
container image ls
container logs -n 100 <自有容器>
container exec <自有容器> <命令> <参数>
container-compose up -d --profile prod --profile observability
```

- `container exec`在容器名后**不加 `--`**；非交互不用`-it`。`container-compose`不支持logs/ps/exec/restart；避免用手动stop/start打断受管栈网络。
- 先核实当前CLI版本及配置。PostgreSQL挂载卷内的PGDATA应使用子目录；官方API内部监听8000，需要Redis，即使关闭某些队列开关也不能假设不需要Redis。
- 图运行使用官方容器和PostgreSQL；`langgraph dev`内存模式不代表当前持久化/认证行为。src已挂载时重新加载即可，改依赖/镜像配置才重建镜像。
- 长测试/迁移/RLS/恢复演练先建立自有库、端口、容器和凭据作用域；记录所有权。共享15432、Langfuse、知识库及其他Agent测试库不可TRUNCATE、downgrade或全局prune。
- 只清理本任务自建资源；不删除共享卷或镜像。端口问题先查实际地址、映射、TCC与服务健康，不把LISTEN直接当数据连通。
- 私有 `.env`、`.env.langgraph`、`.env.langfuse`、`.env.local`、dump和会话不得提交；模板只记录配置形状。非必要不暴露服务，测试服务优先绑定回环。

## 提交与交付

- 提交使用中文前缀：`[新增]`、`[修复]`、`[优化]`、`[文档]`等；提交前检查diff、秘密、误收集缓存/构建物和证据大小。
- commit/push/合并/归档按用户当前授权执行；没有授权时保留可审查diff。已有明确授权不重复询问。
- 交付说明写清改了什么、为何、验证结果、原有失败及真实限制；附相关文档/证据链接。不要把计划、替身或豁免描述成已实测上线。
