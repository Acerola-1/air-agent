# 企业化规划前的工作区基线

日期：2026-10-12。用户授权先清理工作区并提交已有成果，再编写总体路线图和首个 OpenSpec change。

## 保留与清理

- 保留尚未提交的消息流、模型预算、历史摘要、完整数据计算、归档授权实现，以及对应测试、设计文档、OpenSpec 和浏览器证据。
- 删除 39 项可再生成的 `__pycache__`、pytest/Ruff 缓存与 `.DS_Store`；不清理依赖环境、正在使用的 Next.js 构建目录、业务数据或容器卷。
- 删除 `tests/unit_tests/test_vanna_visualization.py`。它只测试已下线的 `common.config.vanna_sql_adapter`，源模块已经不存在；此前完整测试一直因此无法收集。历史报告保留其原始记录。
- 不删除仍有业务含义的失败测试，不用跳过或修改断言掩盖基线问题。
- 待提交文件的凭据特征扫描与 JSON 字段检查未发现新增实际凭据；真实环境配置不纳入提交。

## 本次验证

| 检查 | 实际结果 |
|---|---|
| 治理相关单元测试及 ToolNode 集成测试 | 58 passed |
| `npm --prefix frontend run test:contracts` | 7 passed |
| `frontend` TypeScript `tsc --noEmit` | 通过 |
| 37 个改动/新增 Python 文件 Ruff 与格式检查 | 通过 |
| 完整 `make test`（删除孤立退役测试后） | 196 passed、26 skipped、10 failed |

完整测试执行使用仓库 `.venv`，关闭测试时的外部追踪。10 项失败如下，范围与 2026-10-11 验收报告一致，不能将整套测试描述为全绿：

1. `test_checkpointing.py::test_reset_checkpointer_cache_closes_previous_connection`：连接关闭预期未满足，1 项。
2. `test_knowledge_route.py`：引用已移除的 `clear_knowledge_route_cache`，4 项。
3. `test_mcp_client.py`：引用已下线的 `datacenter_mcp_client`，5 项。

本轮未重新执行真实模型/MCP/PG 验收或浏览器业务验收；2026-10-11 的证据属于历史验证。本轮服务探针显示主前端登录页可访问，而默认 LangGraph API 2024 和 Langfuse 3001 端口拒绝连接，不能据此证明其他地址或实例不可用。

## 规划审计更正

仓库已经包含 `admin/api`（FastAPI、SQLAlchemy、Alembic、RBAC、关注城市、审计）和 `admin/web`（Next.js 后台）。前轮对话只检查了 `frontend` 与 Agent，遗漏这一既有后台。后续路线图以复用这两部分为前提。

当前主聊天端仍从本地 JSON 用户库认证，后台从 PostgreSQL `users` 认证；cookie 格式兼容并不等于身份源一致。首个 change 应统一身份源、添加企业成员关系、收敛权限并贯穿 MCP 授权，不能重新创建第三套用户系统。

本报告只记录清理与验证事实。已有完成 change 未自动归档，已有未完成 change 未删除、未标记完成。
