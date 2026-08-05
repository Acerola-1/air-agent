## ADDED Requirements

### Requirement: CompositeBackend 路由配置
所有业务 graph（basic-qa、intelligent-analysis、data-analysis、deep-research、intelligent-report、intelligent-tracing）SHALL 使用 `CompositeBackend` 作为 backend，配置为 `default=StateBackend()`，`routes={"/skills/": FilesystemBackend(root_dir=str(Path(__file__).parent), virtual_mode=True)}`。

#### Scenario: basic-qa graph 使用 CompositeBackend
- **WHEN** basic-qa graph 初始化
- **THEN** backend 参数为 `CompositeBackend(default=StateBackend(), routes={"/skills/": FilesystemBackend(root_dir=str(Path(__file__).parent), virtual_mode=True)})`

#### Scenario: data-analysis graph 使用 CompositeBackend
- **WHEN** data-analysis graph 初始化
- **THEN** backend 参数为 `CompositeBackend(default=StateBackend(), routes={"/skills/": FilesystemBackend(root_dir=str(Path(__file__).parent), virtual_mode=True)})`

### Requirement: Skill 文件从磁盘读取
`/skills/` 路径下的文件操作 SHALL 路由到 `FilesystemBackend`，保证 Skill 文件（SKILL.md、references/fast.md、references/expert.md）从磁盘正确读取。

#### Scenario: 读取 Skill SKILL.md 文件
- **WHEN** agent 通过 `read_file` 工具读取 `/skills/base/air-quality-realtime/SKILL.md`
- **THEN** CompositeBackend 将请求路由到 FilesystemBackend，文件从 `src/<graph_name>/base/air-quality-realtime/SKILL.md` 正确读取

#### Scenario: SkillsMiddleware 加载 Skill 列表
- **WHEN** SkillsMiddleware 初始化并扫描 `/skills/` 目录
- **THEN** 所有 Skill 的 name 和 description 正确出现在 system prompt 的 Skills 列表中

### Requirement: 非 Skill 路径写入不落盘
非 `/skills/` 路径的文件写入操作 SHALL 路由到 `StateBackend`，数据存在 LangGraph state 中，不在磁盘上创建文件。

#### Scenario: agent 创建分析脚本不落盘
- **WHEN** agent 通过 `write_file` 工具写入 `/analyze_data.py`
- **THEN** 文件内容存储在 LangGraph state 中，磁盘上不创建 `src/<graph_name>/analyze_data.py` 文件

#### Scenario: 内部 offload 数据不落盘
- **WHEN** DeepAgents 将大工具结果 offload 到 `/large_tool_results/call_xxx`
- **THEN** 数据存储在 LangGraph state 中，磁盘上不创建对应文件

### Requirement: 清理已存在的 agent 生成文件
`src/basic_qa/` 目录下已存在的 agent 生成的 .py 文件 SHALL 被删除，这些文件不属于项目源码。

#### Scenario: 清理 basic_qa 目录下的 agent 生成文件
- **WHEN** 执行清理操作
- **THEN** `src/basic_qa/` 目录下仅保留 `graph.py`、`__init__.py`、`skill_discovery.py` 和 `skills/` 目录等源码文件，所有 agent 生成的 .py 文件被删除
