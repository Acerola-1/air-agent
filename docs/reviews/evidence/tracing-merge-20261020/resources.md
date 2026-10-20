# 临时资源清单（2026-10-20）

所有权：本轮实现者自建，仅用于 `integrate-intelligent-tracing-into-dev` 的 G0/G1 验证。
共享正式资源只读，不在本清单回收范围内。

## 自建容器

| 名称 | 镜像 | 端口（宿主） | 用途 | 回收方式 |
|---|---|---|---|---|
| `tracing-merge-pg` | `postgres:17-alpine` | 127.0.0.1:25432 | 空库/存量迁移演练，DB `tracing_migration`、`tracing_upgrade` | `container stop/rm tracing-merge-pg` |
| `tracing-merge-redis` | `redis:7-alpine` | 127.0.0.1:26379 | 隔离 API 栈的迁移锁/队列 | `container stop/rm tracing-merge-redis` |
| `tracing-merge-api` | `air-agent/langgraph-api:tracing-merge-20261020` | 127.0.0.1:22024 | 镜像 `/ok`、三图注册、401 验收（空库） | `container stop/rm tracing-merge-api` |
| `tracing-upgrade-new` | 同上 | 127.0.0.1:22025 | 存量库接管验证 | `container stop/rm tracing-upgrade-new` |
| `tracing-upgrade-old` / `tracing-upgrade-old2` | `air-agent/langgraph-api:latest`（历史镜像） | 127.0.0.1:22025 | 生成旧版本存量库 | 已停止，待删除 |

凭据：数据库密码随机生成，仅写入 `/tmp/tracing_merge_pg.env` 与 `/tmp/tracing_merge_api.env`（0600），
不进仓库；API 许可证键只在运行时从私有 `.env.langgraph` 读取，不落证据。

## 禁止事项

- 不 TRUNCATE / downgrade / seed 共享 15432 `air_agent_business`。
- 不 `container-compose down` 共享栈；不停止 `air-agent-postgres`、`air-agent-redis`、
  `air-agent-langgraph-api`、`iam-*`、`lightrag-*`。
- 只回收上表自有容器；共享卷与镜像保留。

## 回收状态

- 2026-10-20 12:00：`tracing-upgrade-old`、`tracing-upgrade-old2` 已停止，待本轮结束删除。
- `tracing-merge-pg`、`tracing-merge-redis` 保留到 G1 持久化/迁移任务（8.6）完成后回收。
- 新镜像 `air-agent/langgraph-api:tracing-merge-20261020` 保留（最终替换使用）。
