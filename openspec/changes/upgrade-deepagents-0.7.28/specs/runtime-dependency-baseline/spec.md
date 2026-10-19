## ADDED Requirements

### Requirement: 运行时依赖锁可复现

系统 SHALL 以 `pyproject.toml` 与 `uv.lock` 作为唯一依赖真相源，锁定 deepagents 0.7.28、langchain 1.4.6、langchain-core 1.6.12、langgraph 1.2.16、langgraph-checkpoint 4.3.0、langchain-quickjs 0.3.9；锁文件 MUST 使用 Python 3.13 生成，不能用未指定版本的 uv 解析结果替代。`pyproject.toml` 的声明范围 MUST 与锁中目标版本一致；仅锁解析到 0.7.28 而声明仍允许 0.7.5 时，不得声称“已固定 0.7.28”。

#### Scenario: 按锁安装
- **WHEN** 执行 `uv sync --frozen --all-extras --python 3.13`
- **THEN** 安装版本与 `uv.lock` 完全一致
- **AND** 不解析出新的 deepagents 或 langchain 版本

#### Scenario: Python 版本漂移
- **WHEN** 环境默认解释器为 Python 3.14 且未显式指定 3.13
- **THEN** 锁定或构建流程 MUST 拒绝生成与镜像不一致的锁
- **AND** 不得以“resolver 能通过”为由接受该锁

#### Scenario: 声明范围与锁一致
- **WHEN** 复核依赖基线
- **THEN** `pyproject.toml` 对 deepagents 的声明 MUST 排除 0.7.5 等非目标版本
- **AND** 若尚未修改，交付说明 MUST 将其列为未完成缺口，不得把锁解析结果当作声明已固定

### Requirement: 当前三个公开图在升级基线可加载

系统 SHALL 在升级后的依赖基线上成功导入并编译当前 `langgraph.json` 声明的三个图：basic-qa、data-analysis、intelligent-tracing。2026-10-18 的六图结果只作为历史证据保留，不是当前基线验收。

#### Scenario: 三图导入
- **WHEN** 使用升级后的虚拟环境导入三个 `graph.py`
- **THEN** 每个模块 MUST 返回可调用图对象
- **AND** 不得出现 ImportError、Pydantic 校验错误或中间件签名错误

#### Scenario: 仓库使用的 SDK 符号兼容
- **WHEN** 导入 `create_deep_agent`、`CompositeBackend`、`StateBackend`、`SummarizationMiddleware` 等当前仓库实际使用的符号
- **THEN** 所有符号 MUST 可导入且签名满足现有调用
- **AND** 不得用已删除图或历史符号清单替代当前代码检查

#### Scenario: 历史六图记录
- **WHEN** 阅读本 change 的旧报告、旧端口或事故记录
- **THEN** MUST 保留其原始事实与局限
- **AND** MUST NOT 将其改写为当前三图已通过，或恢复已删除图来满足旧描述

### Requirement: 官方镜像可构建并注册当前三图

系统 SHALL 能从升级后的锁构建 `langgraph-api` 镜像，并在容器内完成 API 启动与当前三图注册。

#### Scenario: 镜像构建
- **WHEN** 执行 `container build -t air-agent/langgraph-api:<tag> .`
- **THEN** 构建 MUST 成功
- **AND** 镜像内 deepagents、langchain、langgraph、quickjs 版本与锁一致

#### Scenario: API 启动与图注册
- **WHEN** 隔离栈启动 API 并访问 `/ok`
- **THEN** 返回 HTTP 200
- **AND** 启动日志 MUST 包含 basic-qa、data-analysis、intelligent-tracing 三个 `graph_id`
- **AND** `intelligent-tracing` 必须被注册，不能被静默跳过

### Requirement: 空库迁移与鉴权边界不被破坏

系统 SHALL 在空库上完成 checkpoint/store 迁移，并保持“未认证拒绝、有效票据放行、线程归属确认、台账受理后才能创建运行”的边界。

#### Scenario: 空库迁移
- **WHEN** API 首次连接空 Postgres
- **THEN** `schema_migrations` MUST 全部应用且 `dirty=false`
- **AND** checkpoint、store、thread、run 相关表 MUST 存在

#### Scenario: 未认证访问
- **WHEN** 请求未携带票据或携带伪造 Bearer 访问 `/assistants`
- **THEN** 必须返回 401
- **AND** 不得返回任何 assistant 或图清单

#### Scenario: 有效票据与线程归属
- **WHEN** 使用有效运行票据创建显式 thread_id，并调用归属确认端点
- **THEN** 创建与确认 MUST 成功
- **AND** 未确认的线程 MUST 对读取与运行保持不可访问

#### Scenario: 台账受理后才能创建运行
- **WHEN** 未提供 `ledger_attempt_id` 与 `ledger_submission_id` 创建运行
- **THEN** 必须被拒绝
- **WHEN** 提供真实存在且归属当前线程的预留
- **THEN** 运行创建 MUST 成功并返回平台 run_id

### Requirement: 升级回归以失败集合比较

系统 SHALL 以升级前后失败集合的比较结果判断是否引入回归，不得只看失败数量。

#### Scenario: 失败集合一致
- **WHEN** 同一套 `tests/unit_tests` 分别在 0.7.5 与 0.7.28 环境运行
- **THEN** 升级环境 MUST 不出现基线之外的失败用例

#### Scenario: 既存偶发失败
- **WHEN** 某用例在两个环境都以一定概率失败
- **THEN** 该用例 MUST 被标记为既存 flaky
- **AND** 不得计入升级新增失败

### Requirement: 回滚路径与未验证项显式记录

系统 SHALL 保留回到 0.7.5 依赖锁的回滚路径，并在交付文档中显式列出未验证项，不得以“启动成功”替代完整验证。

#### Scenario: 回滚到旧锁
- **WHEN** 升级验收失败
- **THEN** 必须能通过旧 `uv.lock` 与 `uv sync --frozen` 恢复 0.7.5 运行时
- **AND** 数据库回滚前必须在库副本验证 checkpoint 4.3.0 与 4.1.1 的兼容性

#### Scenario: 未验证项
- **WHEN** 真实模型调用、QuickJS 运行时或 LangSmith 追踪未执行
- **THEN** 交付说明 MUST 明确标记为未验证
- **AND** 不得描述为已通过

### Requirement: 新增 HTTP 客户端需完成出网与追踪审查

系统 SHALL 对 `langsmith 0.14.11` 引入的 `httpx2`/`httpcore2` 完成供应链与出网策略审查，并验证实际追踪链路。

#### Scenario: 出网守卫覆盖
- **WHEN** LangSmith 追踪通过 `httpx2` 发送请求
- **THEN** 现有 outbound guard MUST 明确覆盖或显式豁免该客户端
- **AND** 不得因客户端更换而绕过企业出网策略

#### Scenario: 追踪可用
- **WHEN** 开启 tracing 执行一次真实图运行
- **THEN** 追踪记录 MUST 可见
- **AND** 不得因 `httpx2` 缺失或版本不兼容静默丢 trace
