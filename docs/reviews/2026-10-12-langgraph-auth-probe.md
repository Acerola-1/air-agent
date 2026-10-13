# 原生 Auth 探针验证（任务 1.2）

日期：2026-10-12。环境：Apple Container 1.5.0（macOS 27 beta），隔离网络 `air-agent-probe-net`，专用 PG `probe-pg`（库 `air_agent_authcheck`，独立于业务卷）+ 专用 `probe-redis`。

## 被测对象

- 镜像：`air-agent/langgraph-api:latest`（基于官方 `langchain/langgraph-api:3.13`）
- 镜像内版本：`langgraph_api_version=0.15.1`，`api_variant=licensed`，`api_revision=276fc17`
- 镜像 digest：`sha256:1aac4e4e72b4262f62dfd75c519d9d0dbca4401c36fcec46147435d41652a3e3`
- 探针文件：`tests/auth_probe/probe_auth.py`（静态 token 的 authenticate + threads create/read/search handler）、`tests/auth_probe/probe_graph.py`（最小图）、`tests/auth_probe/probe_http.py`（自定义 FastAPI app）
- 启动 env：`LANGGRAPH_AUTH_TYPE=custom`、`LANGGRAPH_AUTH={"path":"/probe/probe_auth.py:auth","disable_studio_auth":true}`、`LANGGRAPH_HTTP={"app":"/probe/probe_http.py:app"}`

## 实测结果（正向/伪造请求）

| 场景 | 结果 | 结论 |
|---|---|---|
| `POST /assistants/search` 无凭据 | 401 | 默认拒绝，noop 可退出 ✅ |
| `Authorization: Bearer probe-valid-token` | 200 | authenticate 生效 ✅ |
| 伪造 token（Bearer attacker） | 401 | 签名/token 校验真实执行 ✅ |
| 仅 `x-api-key` 头（无 Bearer） | 401 | 平台 API key 不绕过成员认证 ✅ |
| `threads` 创建带伪造 metadata（handler 注入 trusted 字段） | 创建成功；`threads/$TID` 读取被 handler 403（"not probe thread"） | create/read hook 被调用 ✅ |
| `threads/$TID/runs/stream`（线程不属于探针） | SSE `event: error` `403: not probe thread` | run 创建走资源 hook，父线程校验有效 ✅ |
| `threads/search` handler 返回 `{"meta": {"tenant_id": value}}` | 500：filter key 必须是 `$eq` 或 `$contains` | 本平台版本 filter 语法为 `{"meta": {"tenant_id": {"$eq": ...}}}`，正式实现按此写法 |
| 未注册 handler 的 `PATCH /threads/{id}` 改 metadata | 200：`tenant_id: EVIL, user_id: SPOOF` 直接写入 | **资源 hook 缺失路径 fail-open**。正式实现必须全局 `@auth.on` default-deny + 逐 (resource, action) 覆盖，且 threads.update 禁止改可信归属字段 |
| 自定义 HTTP app `/probe-archive`（无凭据） | 200，`request.state.user=None` | **自定义 app 路由不受平台 authenticate 保护**。归档接口必须自带独立认证校验（scope 签名/可信票据），不能依赖内置 hook ✅ 证据 |

## 记录的不兼容/风险点

1. 自定义 `http.app` 路由与认证中间件**互不相通**（见上表）：本 change 的归档接口与未来自定义入口都要单独验证可信上下文（设计 §4 已预判，实测确认）。
2. 未注册 handler 的 (resource, action) 默认放行（启动日志有 warning 提示）：正式 auth 模块必须用全局 default-deny。
3. 镜像为 licensed variant，启动需 LangSmith license 凭据（`LANGCHAIN_API_KEY` 提供）；隔离实例沿用根 `.env` 中已有凭据，无新增暴露。
4. search filter 语法 `$eq/$contains`（0.15.1 实测）。
5. 网络隔离：Apple Container 自定义网络与 default 网络跨网直连不通；宿主机 bridge 端口映射（192.168.64.1:15432）在容器内被宿主机 TUN 代理 fake-ip 干扰不可靠——隔离实例应使用同网专用 PG，不复用业务实例。

## 结论

官方自托管镜像（0.15.1）**支持**原生 `authenticate`、资源级 hooks（create/read/search/run create）与运行认证注入；`disable_studio_auth` 可关 Studio 旁路。不兼容点（自定义 app 无认证、fail-open 路径、filter 语法）已在设计中预留对策，正式实现（任务 5.1–5.5）按上述实测语法与边界落地。

复跑方式：见 `tests/auth_probe/` 与本文启动 env 清单；探针实例名 `probe-lg`（宿主端口 2025）。
