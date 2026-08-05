## Context

当前所有 6 个业务 graph（basic-qa、intelligent-analysis、data-analysis、deep-research、intelligent-report、intelligent-tracing）使用 `FilesystemBackend(root_dir=str(Path(__file__).parent), virtual_mode=True)` 作为 backend。这导致：

1. Agent 通过 `write_file` 工具在源码目录下创建 `.py` 分析脚本（如 `src/basic_qa/` 下已有 25+ 个此类文件）
2. DeepAgents 内部数据（`/large_tool_results/`、`/conversation_history/`）也被写入源码目录

DeepAgents 官方文档明确建议将 `FilesystemBackend` 包裹在 `CompositeBackend` 中，default 使用 `StateBackend()`，仅将需要磁盘访问的路径路由到 `FilesystemBackend`。

约束：
- 6 个 graph 共享相同模式，改动必须一致
- `skills=["/skills/"]` 参数要求 `SkillsMiddleware` 能从 backend 读取 Skill 文件
- Skill 文件存储在磁盘上，在 `src/<graph_name>/skills/` 目录下
- `FilesystemMiddleware` 是必需中间件，无法排除 `write_file` 工具

## Goals / Non-Goals

**Goals:**
- 将所有 6 个 graph 的 backend 改为 `CompositeBackend`，default 为 `StateBackend`，`/skills/` 路由到 `FilesystemBackend`
- 防止 agent 在源码目录创建文件
- 防止 DeepAgents 内部数据（offload、对话历史）落盘到源码目录
- 保证 Skill 文件读取行为不变
- 清理已存在的 agent 生成文件

**Non-Goals:**
- 不改变 Skill 文件的内容或结构
- 不修改 `FilesystemMiddleware` 或其他 DeepAgents 框架代码
- 不添加 `permissions` 规则（CompositeBackend 已从架构层面解决问题，permissions 可作为后续增强）
- 不改变 `data-analysis` 的 `unmatched_policy="strict"` 行为
- 不处理 `/tmp/` 目录下可能存在的 agent 创建文件（不在 `root_dir` 范围内）

## Decisions

### Decision 1: 使用 CompositeBackend 而非 permissions 或改 root_dir

**选择**: `CompositeBackend(default=StateBackend(), routes={"/skills/": FilesystemBackend(...)})`

**替代方案**:
- ❌ `permissions` 禁止写文件：agent 仍会"尝试"写然后被拒绝，浪费 token；且只拦截工具层，不防止内部 offload 数据落盘
- ❌ 改 `root_dir` 到临时目录：Skill 文件路径需调整，`SkillsMiddleware` 的路径解析可能不兼容
- ❌ 自定义只读 Backend：需要实现完整 `BackendProtocol`，维护成本高

**理由**: CompositeBackend 是官方推荐模式，从架构层面分离读写路径，不浪费 token，且不改变 Skill 读取行为。

### Decision 2: 路由前缀为 `/skills/`

**选择**: `routes={"/skills/": FilesystemBackend(root_dir=str(Path(__file__).parent), virtual_mode=True)}`

**理由**:
- `create_deep_agent` 的 `skills=["/skills/"]` 参数让 `SkillsMiddleware` 从 `/skills/` 路径加载 Skill 文件
- `CompositeBackend` 会将 `/skills/` 前缀的路径剥离后传给路由的 backend，如 `/skills/base/air-quality-realtime/SKILL.md` → FilesystemBackend 看到 `/base/air-quality-realtime/SKILL.md`
- 但 `FilesystemBackend(virtual_mode=True)` 会将非 `/` 开头的路径加上 `/` 前缀，最终解析为 `root_dir/base/air-quality-realtime/SKILL.md`，即 `src/<graph_name>/base/air-quality-realtime/SKILL.md`
- 这与 SkillsMiddleware 的实际加载路径一致

**验证**: CompositeBackend `_route_for_path` 剥离 `/skills/` 前缀后，FilesystemBackend 的 `_resolve_path` 在 `virtual_mode=True` 下会正确处理。

### Decision 3: 清理策略

**选择**: 手动删除已存在的 agent 生成文件，并添加 `.gitignore` 规则

**理由**: 已存在的 .py 文件是 agent 运行时产物，不属于项目源码。添加 `.gitignore` 可防止意外提交。

## Risks / Trade-offs

- **[Skill 路径解析兼容性]** → CompositeBackend 路由剥离前缀后，FilesystemBackend 的路径解析可能与 SkillsMiddleware 的预期不一致。**缓解**: 逐一验证 6 个 graph 的 Skill 加载。

- **[StateBackend 存储开销]** → agent 写的文件存在 LangGraph state 中，可能增加 checkpoint 大小。**缓解**: StateBackend 数据随会话结束消失，checkpoint 有 PostgreSQL 支持且已有 summarization middleware 处理溢出。

- **[data-analysis 特殊路径]** → data-analysis 使用 `create_data_analysis_find_skill_tool` 和 menu_skill_mapping，其 Skill 路径可能不完全在 `/skills/` 下。**缓解**: 需要单独验证 data-analysis 的 Skill 加载路径。
