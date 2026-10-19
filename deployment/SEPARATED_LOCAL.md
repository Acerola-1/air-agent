# 本机分离服务启动

使用 Agent 仓库的虚拟环境运行：

```bash
.venv/bin/python deployment/run_separated_local.py start
.venv/bin/python deployment/run_separated_local.py status
```

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
