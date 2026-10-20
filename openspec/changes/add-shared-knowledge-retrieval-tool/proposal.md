# 三图共享知识库检索工具

## Why

独立知识库已经提供受权检索与原文读取，但 Agent 当前没有可用的知识检索工具，知识问答依靠模型常识，另外两张图也无法获取可核验的知识依据。需要把现有知识服务封装为三个图默认具备的工具，并打通当前用户的安全委托和引用回读，避免“工具名可见但不能执行或不能展示来源”的半接入。

## What Changes

- 新增共用只读工具 `knowledge_search`，默认出现在 `basic-qa`、`data-analysis`、`intelligent-tracing` 主 Agent 的每轮工具列表中，包括纯问候；纯问候不主动调用，内部四专员不扩大工具职责。
- 默认检索当前用户、当前企业有权访问的全部资料；显式资料选择只能缩小范围。工具是受管本地 HTTP 适配器，不伪装 MCP，用户关闭 MCP 不关闭此工具。
- 具体法规、条款、标准限值类问题必须实际检索并提供有效来源；一般概念和数值计算可按需调用。无证据、权限拒绝或来源失效时不编造具体规定。
- IAM 新增短期、只读、目标固定为知识服务的用户委托；RAG 验证同一源用户及企业并保留资料权限。禁止把 Agent 运行引用直接当知识库 Bearer，禁止共享机器账号代替用户。
- 回答提供可点击原文引用，经 Agent 同源代理、当前会话和知识权限复查后读取；保留文档/片段/版本与真实位置，缺页码时不补造。
- 溯源报告新增独立、受服务端验证的“知识依据”结构，聊天、画布、存储和回读一致；知识原文不进入污染观测证据、claim 白名单或归因等级计算。
- 固定工具契约、三图与跨仓安全验收、性能及检索题库；更新已移除的旧知识路由主规格，保留既有失败与历史报告。

## Capabilities

### New Capabilities

- `shared-knowledge-retrieval`：共享工具契约、三图默认可见/可执行、授权资料范围、强制证据策略及有界调用。
- `knowledge-user-delegation`：Agent、IAM、RAG 间按原用户身份的短期只读委托、实时撤销及凭据不落运行态。
- `agent-knowledge-citations`：可信引用、原文保护代理、溯源独立知识区及跨正文/画布/历史的来源有效性。

### Modified Capabilities

- `agent-runtime-performance`：替换依赖已删除实现的“知识检索快速路由”，使用受保护部署固定的 Knowledge API 检索路线；默认 PG，不恢复旧 Milvus/Ollama 或 LLM 路由缓存。

## Impact

- Agent：`src/common/knowledge/**`、共享工具注册、三图工具和输出接线、受保护引用存储/迁移/API、前端引用与原文读取、测试与文档。现有台账/观测/选择语义尽量复用，不新增 Graph ID，不放宽数据分析菜单或溯源证据门禁。
- IAM：`/Users/acerola/Dev/Java/air_agent_iam`，新增受限委托签发/内省协议及必要迁移、客户端授权与负向测试；不放宽现有 audience 校验、不创建长期派生用户会话。
- RAG：`/Users/acerola/Dev/Python/RAG/env-rag-final`，支持受限委托身份与稳定源会话 owner 绑定，保留 query/record/source/document 的即时权限与来源复核；不改检索算法、不重建或重新入库。
- 三仓改动范围已由用户确认；本轮仅在 Agent 仓库保存计划。实际业务代码、数据库、正式凭据、部署、commit/push/归档均未执行；实施按 design 与 handoff 顺序进行，不能只完成 Agent 接口壳就宣称上线。
