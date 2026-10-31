# Spec Delta

## Purpose

定义 Agent 运行时在目标依赖版本上的可复现安装、三图加载、镜像启动和持久化迁移基线，确保智能溯源集成不以未升级的旧运行时冒充验收。

## ADDED Requirements

### Requirement: 目标依赖声明与锁一致

系统 SHALL 将 `pyproject.toml` 的 deepagents 声明收紧到 `>=0.7.28,<0.8`，并将 langchain、langchain-core、langgraph、langchain-quickjs 的直写下限与目标锁对齐；`uv.lock` MUST 由 Python 3.13 重新生成，不得手改锁或保留可解析到 0.7.5 的声明。

#### Scenario: 按锁安装
- **WHEN** 执行 `uv sync --frozen --all-extras --python 3.13`
- **THEN** deepagents 实际版本为 0.7.28，langchain 1.4.6、langchain-core 1.6.12、langgraph 1.2.16、checkpoint 4.3.0、quickjs 0.3.9 与锁一致
- **AND** 声明范围不再允许 0.7.5

#### Scenario: 声明与锁漂移
- **WHEN** 锁解析到 0.7.28 但 `pyproject.toml` 仍允许 0.7.5
- **THEN** 验收失败并记录为未完成，不得宣称已固定目标版本

### Requirement: 当前三图可在目标基线加载

系统 SHALL 在 0.7.28 目标锁上导入并编译当前 `langgraph.json` 声明的三个图：`basic-qa`、`data-analysis`、`intelligent-tracing`；导入期间 MUST NOT 发起模型、Embedding 或 MCP 网络请求。

#### Scenario: 三图冷导入
- **WHEN** 在目标虚拟环境导入三个 `graph.py`
- **THEN** 每个模块返回可调用图对象，无 ImportError、Pydantic 校验错误或中间件签名错误
- **AND** 网络尝试计数为 0

#### Scenario: 公开图清单
- **WHEN** 读取 `langgraph.json` 与容器启动日志
- **THEN** 只注册 basic-qa、data-analysis、intelligent-tracing 三图
- **AND** 不因历史六图文档恢复已删除图

### Requirement: 目标镜像与容器可启动

系统 SHALL 从目标锁构建 langgraph-api 镜像，固定基础镜像 digest，并在隔离 Postgres/Redis/API 栈中完成启动与三图注册；容器 tag MUST 指向本次构建，不得使用未固定的 latest。

#### Scenario: 镜像启动
- **WHEN** 构建并启动隔离栈，访问 `/ok`
- **THEN** 返回 HTTP 200，启动日志包含三个 `graph_id`
- **AND** `intelligent-tracing` 未被跳过

#### Scenario: 未认证访问
- **WHEN** 未携带票据或伪造 Bearer 访问 `/assistants`
- **THEN** 返回 401，不泄露 assistant 或图清单

### Requirement: Checkpoint 迁移与回滚可复现

系统 SHALL 在空库和存量库副本上验证 checkpoint 4.1.1 → 4.3.0 迁移；迁移后 `schema_migrations` MUST `dirty=false`，checkpoint/store/thread/run 表可用；回滚演练 MUST 记录限制，不得在正式库直接 downgrade。

#### Scenario: 空库迁移
- **WHEN** API 首次连接空 Postgres
- **THEN** 迁移全部应用且 `dirty=false`
- **AND** checkpoint、store、thread、run 表存在

#### Scenario: 存量库副本
- **WHEN** 在自建副本上执行存量迁移
- **THEN** 记录迁移前后版本、耗时、失败与回滚结果
- **AND** 不在共享正式库执行 downgrade
