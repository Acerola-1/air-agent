# 临时资源与回收

- 新建独立临时 worktree：`/tmp/air-agent-tracing-g0`（从 `6ff95dd`，detached），仅用于 0.7.28 G0；未替换主 `.venv`。
- 未创建/使用正式库、未 seed/truncate/downgrade、未 compose down 共享栈、未调用真实 MCP/轨迹/PSCF。
- 未创建容器、卷、端口或外部凭据；测试全部使用进程内 fixture 与内存 store。
- `/tmp/air-agent-upgrade` 原 worktree 及其 `pyproject.toml`/`uv.lock` diff 保留，未覆盖；其旧六图/旧证据作为历史。
- 待最终交付后可按需删除 `/tmp/air-agent-tracing-g0`；当前保留以便 Lead 复核。
