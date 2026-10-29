# 本机分离服务启动

一键启动（推荐）：

```bash
./run-local.sh            # 等同 up：容器 → Langfuse → IAM/RAG/Agent 全部拉起
./run-local.sh status     # 只读核对：端口、HTTP 200、容器状态与网络归属
```

等价的显式形式：

```bash
.venv/bin/python deployment/run_separated_local.py up
.venv/bin/python deployment/run_separated_local.py start   # 只拉起宿主进程，不动容器
.venv/bin/python deployment/run_separated_local.py status
```

`up` 的顺序固定为：Apple container 系统 → 既有依赖容器（只 `start` 已存在的，缺容器直接报名字，不重建）→ Langfuse 栈（`LANGFUSE_ENABLED=true` 时）→ 宿主进程（IAM → LangGraph + 私有桥 → admin API/RAG/前端）。每一步都已就绪时会跳过，因此重复执行是安全的。

## 地址模型（为什么以前每次启动都要手改）

Apple container 的 `default` 网络属于 vmnet 桥，桥重建后整个网段会换一个 /24（`192.168.64.0` → `192.168.65.0` → …），容器访问宿主机只能用**当前网段**的网关。以前这些地址写死在配置里，网段一漂移就会出现两个现象：

1. 启动器直接报"必须指向本机私有桥"，只能手改 `.env.langgraph`；
2. 更隐蔽的情况是容器仍在跑但连不到宿主——宿主机 Clash TUN 对不可达的 `192.168.x.1` 会返回**假连接**（连一个没人监听的端口也"连接成功"，随后的 HTTP 请求才超时），所以服务看着都起来了，实际全在空转。

现在的处理：

- **应用侧地址运行时渲染。** `.env.langgraph` 里的 `IAM_BASE_URL`、`MAIN_LLM_BASE_URL`、`SUMMARY_LLM_BASE_URL`、`LANGFUSE_BASE_URL`、`KNOWLEDGE_API_URL` 只校验"私有地址 + 19110 /v1"等形态，启动器按 `container network inspect` 的实际网关重写进 `.local/i4-live/native.env`；`bridge.json` 的 `bind_host` 与来源 IP 也自动跟随并重启私有桥。旧网段写在源文件里不会再导致启动失败，也不需要手改。
- **Langfuse 栈固定网段。** 六个 Langfuse 容器固定在 `air-agent-langfuse` 网络（`10.99.0.0/24`，网关恒 `10.99.0.1`），`.env.langfuse` 里的内部地址是常量，不再随 `default` 漂移。该网络由 `up` 创建；直接用 `container-compose` 前必须先存在。
- 依赖容器（PostgreSQL/Redis）的 IPv4 依旧每次从 `container inspect` 读取后写入 native env。
- **Langfuse 只"启动"不"重建"。** 容器已存在只是停止时，`up` 用 `container start` 拉起（秒级）；只有容器缺失或不在固定网络时才退回 `container-compose up`。

## 为什么不要随手跑 container-compose

`container-compose up` 在这个版本（1.1.0）里对 Langfuse 栈是"全量重建"语义：即使配置没变，它也会停掉全部容器重新创建，并且每次都重新解析镜像 tag。实测一次冷镜像重建要十几分钟（`cgr.dev/chainguard/minio:latest` 漂移时会真正下载 ~117 MB），期间 Langfuse 不可用。所以：

- 日常启动用 `./run-local.sh`（`up`），它只在必要时才调用 Compose。
- 需要更新 Langfuse 镜像/配置时才手工 `container-compose up -d --profile observability -f docker-compose.yaml langfuse-postgres langfuse-clickhouse langfuse-minio langfuse-redis langfuse-web langfuse-worker`；执行前先确认 `air-agent-langfuse` 网络存在。
- `container-compose down` 会移除容器（卷保留），本栈不需要它；不要用它做"重启"。

修改 `.env.langfuse` 或 Compose 中的 `networks` 会让 Compose 重建 Langfuse 容器；重建可能需要重新拉取镜像（网络慢时数分钟），期间 Langfuse 暂不可用。

## 校验与环境

IAM 与 RAG 默认使用 `../../Java/air_agent_iam` 和 `../RAG/env-rag-final`；仓库路径不同时用 `--iam-project`、`--rag-project` 指定。启动器读取各仓真实 dotenv，注入对应进程环境，不输出配置值、密钥或 SSO 参数。进程输出丢弃；`.local/i4-live/services` 仅保存脱敏生命周期日志和 PID，目录权限为 0700、文件为 0600。启动器没有 stop 子命令，也不会终止未知进程。

启动前会检查 IAM 使用 `iam_identity`、Agent 与 LangGraph 使用 `air_agent_business`，Agent 的 `ADMIN_RUNTIME_DATABASE_URI` 与 `RUNTIME_DATABASE_URI` 必须相同且均指向该业务库；Agent 身份和 RAG 必须启用 remote IAM。LangGraph 的 `.env.langgraph` 必须是无 shell 外层引号的 literal `key=value`。启动器要求既有 PostgreSQL 与 Redis 容器已运行，从 `container inspect` 读取它们的 IPv4，复制完整 env 到 `.local/i4-live/native.env`，只更新三项数据库 URI 的主机、`LANGGRAPH_STORE` JSON 对象中的 owner `uri` 和 `REDIS_URI`；其它 Store 字段保留。旧 native env 会先以唯一时间名备份。原秘密不重新生成。生成的 native env 与单应用 Compose 描述符权限为 0600，`.local` 为 0700。描述符只保留 canonical Compose 的 `langgraph-api`，移除依赖项并把 bind mount 改为绝对路径，因此不会重建 PostgreSQL/Redis，也不依赖容器服务名 DNS。IAM 与模型仍经 `192.168.64.1:19110` 私有桥，桥来源 IP 按 LangGraph 容器实际地址同步。RAG 使用仓库实际入口 `python -m env_rag_final.access`，记录目录独立为 `.local/remote-iam-live/records`。

这是已单独授权完成切库后的应用启动器：只启动或检查已切库应用，不执行数据库迁移、初始化或 root 凭据重置。端口已有服务且健康检查返回 200 时会跳过；LangGraph 的 2024 已监听时不会执行 Compose。否则用 `container-compose up -d --env-file .local/i4-live/native.env -f .local/i4-live/native-compose.yaml --profile prod langgraph-api` 仅启动应用；全局 env 文件阻止默认 host `.env` 覆盖插值值，服务环境中的数据库与 Redis URI 则完全来自 native env。LangGraph 须在 30 秒内返回 HTTP 200，桥接器以 TCP 监听作为健康判定，其余服务须在 20 秒内返回 HTTP 200。若既有 PostgreSQL/Redis 未运行，启动器会停止并提示先启动依赖，不会自动修复或重建。

Agent 两个 Next 项目的本机生产构建使用 Webpack，避开本机 Turbopack 绑定权限问题；IAM 使用 Vite 普通构建：

```bash
npm --prefix frontend run build -- --webpack
npm --prefix admin/web run build -- --webpack
npm --prefix ../../Java/air_agent_iam/web run build
```

IAM Java jar 需先由仓库既有构建流程生成；启动器只检查产物，不构建它。

## 启动期资源保护

性能测试见 [性能压力测试流程](../docs/performance/pressure-testing-workflow.md)。启动保护使用本轮实际部署的资源清单、预算和停止阈值，执行前完成监督校准。

恢复完整服务前先运行只读启动前检查；检查不通过时不得开始恢复：

```bash
.venv/bin/python -m scripts.product_chat_perf.startup_guard preflight --out .local/startup-preflight --seconds 30
```

需要按计划启动时，用同一守卫包裹现有启动命令。守卫不替代本启动器，只负责启动前拒绝、运行期停止线和自有组件收尾：

```bash
.venv/bin/python -m scripts.product_chat_perf.startup_guard plan \
  --out .local/guarded-start \
  --profile .local/guarded-start-service-profile.json \
  --plan .local/guarded-start-plan.json
```

超线或采集失败后立即终止当前启动命令并禁止后续步骤。收尾只优雅停止“启动前未运行、启动期间新出现”的容器和宿主服务；原本运行的 PG/Redis/应用不会被停止，不删除卷或数据，也不对服务使用 SIGKILL。

`--plan` 文件格式为 `{"steps": [{"name": "步骤名", "argv": ["命令", "参数..."]}]}`；每一步只在前一步成功且门禁未关闭时执行。
