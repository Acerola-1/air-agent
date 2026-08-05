## Why

当前所有 6 个业务 graph 使用 `FilesystemBackend(root_dir=str(Path(__file__).parent), virtual_mode=True)` 作为 backend。这导致两个问题：

1. **Agent 在源码目录创建 .py 脚本**：DeepAgents 的 `FilesystemMiddleware` 为 agent 提供 `write_file` 工具，agent 运行时会在 `root_dir`（即模块源码目录）下创建分析脚本（如 `analyze_air_quality.py`、`run_analysis.py` 等），污染源码目录。
2. **内部数据落盘**：DeepAgents 自动将大工具结果 offload 到 `/large_tool_results/`、对话历史到 `/conversation_history/`，这些内部数据也写入 `root_dir`，混入项目文件。

官方文档明确建议：将 `FilesystemBackend` 包裹在 `CompositeBackend` 中，default 使用 `StateBackend()`（临时存储，不落盘），仅将需要磁盘访问的路径路由到 `FilesystemBackend`。

## What Changes

- 将所有 6 个 graph.py 的 `backend` 参数从 `FilesystemBackend(root_dir=..., virtual_mode=True)` 改为 `CompositeBackend(default=StateBackend(), routes={"/skills/": FilesystemBackend(root_dir=..., virtual_mode=True)})`
- `/skills/` 路径路由到 `FilesystemBackend`，保证 Skill 文件（`SKILL.md`、`references/fast.md` 等）可从磁盘读取
- 其余路径（包括 `/large_tool_results/`、`/conversation_history/` 及 agent 自行创建的文件）路由到 `StateBackend`，数据存在 LangGraph state 中，不写磁盘
- 清理 `src/basic_qa/` 目录下已存在的 agent 生成的 .py 文件

## Capabilities

### New Capabilities
- `composite-backend-routing`: 使用 CompositeBackend 将 Skill 文件读取路由到 FilesystemBackend，其余文件操作路由到 StateBackend，防止源码目录被污染

### Modified Capabilities
- `skill-content-basic`: Skill 文件读取路径从直接 FilesystemBackend 改为通过 CompositeBackend 路由，读取行为不变但 backend 架构变更

## Impact

- **6 个 graph.py 文件**：`src/basic_qa/graph.py`、`src/intelligent_analysis/graph.py`、`src/data_analysis/graph.py`、`src/deep_research/graph.py`、`src/intelligent_report/graph.py`、`src/intelligent_tracing/graph.py`
- **新增 import**：`CompositeBackend`、`StateBackend`
- **依赖**：`deepagents.backends.composite.CompositeBackend`（已存在于当前 deepagents 版本中）
- **向后兼容**：Skill 文件读取行为不变，agent 写文件行为从落盘改为存 state，对用户透明
- **需清理**：`src/basic_qa/` 下约 25 个 agent 生成的 .py 文件
