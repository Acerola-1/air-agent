# 基线复核（2026-10-19 Asia/Shanghai）

## 仓库

- 分支：`dev`
- HEAD：`6ff95dd`（三图收敛已提交）
- 工作区既有未跟踪项：`openspec/changes/add-intelligent-tracing-multiagent/`、`openspec/changes/upgrade-deepagents-0.7.28/`、`tests/mcp_probe/probe_tracing_capability.py`；未覆盖或重置。
- `langgraph.json`：basic-qa、data-analysis、intelligent-tracing 三图；唯一启动入口 `deployment/run_separated_local.py start/status`。

## 环境

| 环境 | Python | deepagents | langchain | langgraph | checkpoint | quickjs | 用途 |
|---|---|---|---|---|---|---|---|
| 主 `.venv` | 3.13.3 | 0.7.5 | 1.3.x | 1.2.11 | 4.1.x | 0.3.5 | 未替换，仅基线 |
| `/tmp/air-agent-upgrade` | 3.13.3 | 0.7.28 | 1.4.6 | 1.2.16 | 4.3.0 | 0.3.9 | 原升级 worktree（旧 HEAD） |
| `/tmp/air-agent-tracing-g0` | 3.13.3 | 0.7.28 | 1.4.6 | 1.2.16 | 4.3.0 | 0.3.9 | 本次 G0，从 6ff95dd 新建 |

- G0 锁摘要：`a44c4eef27251220f5c04abc855e98f4f6055190263a249735846513204566d0`
- G0 pyproject 摘要：`a765c3936481bbc5ac8f5a317c7c935c44cc291e327c5be52bce7490194cbff0`
- G0 三图导入成功；cold import 无网络尝试（见 `pytest-import-guard.txt` 与 `three-graph-import-20261019/import-network-guard.json`）。

## 升级缺口（G1 前处理，本 change 不改）

- `/tmp/air-agent-upgrade` HEAD 仍是 `8e172de`，`langgraph.json` 仍六图；未把业务代码硬写进旧 worktree。
- 其 `pyproject.toml` 仍声明 `deepagents>=0.7.5,<1.0`，只有 `uv.lock` 解析到 0.7.28；不能称为“声明已固定 0.7.28”。
- 正式模型、真实 MCP、实际追踪/出网、存量 checkpoint 迁移/回滚、镜像 digest 未在本任务复验；均记未执行。
- 升级 change 已在自身范围内更正现行三图规格并追加第 5 节补验计划；旧六图/旧端口/事故记录保留，未改写为通过。

## 基线测试快照（非本次验收）

- 0.7.28 旧代码：5 failed / 432 passed / 73 skipped；主 0.7.5 旧代码：26 failed / 411 passed / 73 skipped。失败集合不同，不能只看数量。
- 本次改动后完整单测：6 failed / 513 passed / 73 skipped；其中 5 个与 0.7.28 旧基线相同，`test_total_deadline_discards_late_success` 为既存 flaky（复跑有 pass/fail 交替），见 `pytest-unit-full-g0.txt`、`pytest-flaky-check.txt`。
