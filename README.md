# Air Agent

面向空气质量业务的多租户 Agent 应用，提供问答、分析、报告与研究能力。Python 服务使用 LangGraph API，聊天前端使用 Next.js；当前管理 API 提供统一身份、企业权限及 Agent 业务数据接口，管理页面位于 `admin/web/`。

本仓库正在分阶段建设企业运营后台。已经落地的运行台账、反馈、观测和能力选择，是后续运营分析的数据基础；热门问题分析、质量工作台和完整运营看板仍按 [企业化路线图](docs/enterprise-agent-roadmap.md)推进。

## 当前能力

- **六张业务图**：基础问答、智能分析、菜单驱动的数据分析，以及基于 DeepAgents 的报告、深度研究和污染溯源。
- **企业与访问控制**：账号、企业、部门、成员、角色、权限和审计；服务端会话可撤销，企业切换带上下文版本校验。
- **可信运行台账**：关联逻辑提问、提交、尝试、平台运行和回答；支持幂等、重新生成、恢复及独立的停止意图。
- **反馈与体验采集**：按回答版本点赞/点踩、意见、成功复制事件及可见正文计时。采集由部署开关控制。
- **能力选择**：按当前成员授权提供 Skill/MCP 目录，支持 Skill 自动/单选、MCP 自动/子集/禁用；已受理计划约束主任务、子任务、重试与恢复。
- **运行观测**：模型 usage、延迟、价格版本与成本、Skill 各阶段和工具结果分类，可关联 Langfuse Trace；未知数据保留缺失状态。
- **资料与画布**：归档大工具结果、按会话回读报告与可视化资料，支持删除与恢复后的墓碑重施。

能力目录不会授予额外权限。真实企业 MCP 仍须完成平台协议和企业身份绑定核验；受控测试 MCP 通过不代表生产接入完成。

## 架构与身份链路

```text
聊天页 frontend/                 管理页 admin/web/
       │ 同源BFF                         │ 同源代理
       ├────────── admin/api/ ──────────┘
       │           会话、企业权限、业务台账
       │           内部签发短期运行票据
       ▼
LangGraph API：custom auth + 资源归属 + 实时身份复查
       ├── 六张业务图 → 模型 / 按企业身份授权的 MCP
       ├── PostgreSQL：线程、归档、业务台账与观测
       ├── Redis：官方运行时所需的协调与队列
       └── Langfuse：AI 链路观测与评测
```

浏览器使用 HttpOnly 不透明会话，Next 服务端换取运行票据后调用 Agent。授权同时在服务端与数据库落实，历史搜索的 metadata 过滤不构成安全边界。当前身份源仍在本仓库的 `admin/api/`，并未切换到独立 Java IAM。

独立 IAM 项目计划使用 **Spring Boot + Sa-Token + Vue 管理后台**，消费者通过 HTTP introspection 接入。该项目另行实施；本仓库两个前端目前仍为 Next.js，详见 [IAM 抽取方案](openspec/changes/extract-shared-iam-service/proposal.md)。

## 目录导航

| 路径 | 职责 |
|---|---|
| `src/basic_qa/`、`src/intelligent_analysis/` | 显式业务节点流与语义 Skill 路由 |
| `src/data_analysis/` | 菜单映射驱动的确定性分析 |
| `src/intelligent_report/`、`src/deep_research/`、`src/intelligent_tracing/` | DeepAgents 图；本地 Skill 材料按目录实际内容提供 |
| `src/common/` | 身份、能力、模型、MCP、台账、观测、中间件与归档 |
| `frontend/` | 聊天、能力选择、反馈、画布及 Agent BFF |
| `admin/api/` | FastAPI 管理与业务 API、Alembic 迁移、引导和恢复脚本 |
| `admin/web/` | Next.js 企业/成员/权限等管理页面 |
| `docs/contracts/agent-events-v1/` | 事件登记、样本与接口契约 |
| `openspec/specs/` | 已同步主规格；活动计划与归档分别在 `changes/`、`changes/archive/` |
| `docs/reviews/` | 独立验收、失败基线、原始证据与模型评价 |

公开 Graph ID 以 [`langgraph.json`](langgraph.json) 为准：`basic-qa`、`intelligent-analysis`、`data-analysis`、`intelligent-report`、`deep-research`、`intelligent-tracing`。

## 本地准备

需要 Python 3.13、uv、Node.js/npm，以及 macOS 上的 [Apple Container](https://github.com/apple/container) 和 [container-compose](https://github.com/Mcrich23/Container-Compose)。项目不使用 Docker Desktop。当前 compose 是本地开发栈，部分端口映射未限制为回环；对外部署应单独配置网络、密钥和暴露面。

根项目依赖由 `pyproject.toml` / `uv.lock` 管理；管理 API 有自己的依赖声明。一个环境联调两个 Python 项目时：

```bash
uv sync --all-extras
uv pip install --python .venv/bin/python -e './admin/api[dev]'
npm --prefix frontend ci
npm --prefix admin/web ci
```

管理 API 安装步骤用于本地联调，不能用根目录 `uv sync` 代替；再次同步根环境后如管理端依赖被清除，需重新安装。修改依赖应更新对应项目的声明和锁文件。

复制 `.env.example` 为私有 `.env` 并填写部署配置；不要提交密钥。首次启用本地 Langfuse 还需复制 `.env.langfuse.example` 为 `.env.langfuse` 并填写密钥。关键配置分工如下：

| 配置 | 用途 |
|---|---|
| `DATABASE_URI` | 管理迁移/引导使用的 owner 连接；官方 Agent 平台存储连接在 compose 中配置 |
| `ADMIN_RUNTIME_DATABASE_URI` | 宿主机管理 API 的受限运行角色，必须非 owner、非 BYPASSRLS |
| `.env.langgraph` 中的 `RUNTIME_DATABASE_URI` | 容器可达的同一受限角色连接；不能直接照抄宿主 localhost 地址 |
| `RUNTIME_TICKET_KEYS`、`INTERNAL_API_TOKEN` | 管理 API、聊天 BFF 与运行时之间的服务端身份契约 |
| `ARCHIVE_SCOPE_KEYS` | 聊天 BFF 与运行时一致配置的归档签名密钥 |
| `MAIN_LLM_*`、Embedding/检索配置 | 模型网关与能力所需服务；地址须能从实际运行环境访问 |
| MCP 地址与企业绑定 | 服务端连接配置加企业/成员授权身份，不能只填一个全局地址就视为企业授权完成 |

`run-local.sh` 会用shell读取根环境文件；JSON值应整体用单引号包住，确保读取后仍为合法JSON。

`AUTH_SECRET` 仅保留旧会话迁移用途；当前登录不再使用旧 HMAC cookie 或 `data/users.json`。Next 服务端的 `ADMIN_API_URL`、`LANGGRAPH_API_URL`、内部令牌和归档密钥需要在进程环境或私有 `.env.local` 中配置；`admin/web` 的 `ADMIN_API_URL` 默认指向本机8100。

## 启动顺序

先启动数据库，再引导角色和迁移，随后运行管理 API，最后启动 Agent 与聊天前端。以下以默认开发端口为例；已有数据环境先确认库、角色与迁移状态。

```bash
container system start
container-compose up -d postgres redis
```

将与运行角色 URI 一致的 `TENANT_RUNTIME_DB_PASSWORD` 导出到当前终端，配置非默认 `ADMIN_BOOTSTRAP_ACCOUNT` / `ADMIN_BOOTSTRAP_PASSWORD`，然后在管理 API 目录执行：

```bash
cd admin/api
../../.venv/bin/python scripts/bootstrap_tenant_db.py
../../.venv/bin/alembic upgrade head
../../.venv/bin/python scripts/seed.py
# 需要行政区划/关注城市时首次导入：
../../.venv/bin/python scripts/import_regions.py
../../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8100
```

引导脚本直接读取进程环境中的角色密码；应用配置会读取私有 `.env`。平台账号创建后，按管理权限建立企业、成员和角色；平台角色不会自动获得所有企业的成员权限。

另开终端，回到仓库根目录：

```bash
# 首次、依赖变化或镜像缺失时构建：
container build -t air-agent/langgraph-api:latest .
# run-local.sh 要求 .env、.env.langgraph 和 .env.langfuse 已配置。
NEXT_HOST=127.0.0.1 ./run-local.sh
# 可选：管理页面
npm --prefix admin/web run dev -- --hostname 127.0.0.1
```

`run-local.sh` 启动 prod/observability profile 和聊天前端，**不会启动管理 API、执行业务迁移或初始化账号**。其最终提示不能代替逐服务检查。

| 默认入口 | 地址 |
|---|---|
| 聊天页 | `http://localhost:3000` |
| 管理页 | `http://localhost:3100` |
| 管理 API 文档 | `http://127.0.0.1:8100/docs` |
| LangGraph API | `http://localhost:2024`，容器内8000；需要认证 |
| Langfuse | `http://localhost:3001`，访问权限独立管理 |

自选端口要同时调整代理地址、CSRF/CORS允许来源及实际容器端口映射。脚本的 `LANGGRAPH_PORT` 变量不会自动改写 compose 映射。

改 `src/` 后通过受管 compose 流程重新加载；改依赖或镜像构建配置需重建镜像。管理 API 与两个前端分别管理进程。

```bash
container logs -f air-agent-langgraph-api
container-compose down
```

`down` 不负责停止宿主机前端/API，需分别停止自己的进程；不要用全局清理命令删除共享数据。容器、命名卷和测试环境的操作约束见 [开发指南](AGENTS.md)。

## 检查与验收

```bash
.venv/bin/python -m pytest tests/unit_tests -q
.venv/bin/python -m pytest tests/integration_tests -q
npm --prefix frontend run test:contracts
npm --prefix frontend run build
npm --prefix admin/web run lint
```

管理端数据库测试需先配置**专用测试库与非 owner 角色**，在 `admin/api/` 使用上述联调环境执行 `python -m pytest tests/`；不要指向共享业务库。UI 的真实浏览器检查、实际模型/MCP联调、恢复演练与单元测试分别提供证据。

[Change4最终验收](docs/reviews/skill-tool-selection-final-acceptance.md)记录了六图运行、权限边界、性能和逐项基线。根/集成套件仍有已识别的历史失败；本次归档接受保留系统中文输入法候选窗抽查，未将其写成实测通过。正式生产的企业 MCP 和负载验证仍需实际环境。

## 继续开发

- 修改前先读 [AGENTS.md](AGENTS.md)、相关主规格及活动 change。
- UI 行为与设计约定见 [UX-CONTRACT.md](UX-CONTRACT.md)、[DESIGN.md](DESIGN.md)。
- 后台后续范围和依赖见 [企业化路线图](docs/enterprise-agent-roadmap.md)；归档表示该阶段验收关闭，不表示整个企业后台已完成。
