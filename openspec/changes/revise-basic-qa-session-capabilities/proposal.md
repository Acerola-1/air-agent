# Basic QA 会话级 Skill 与 MCP 工具能力管理

## Why

当前 Basic QA 的原生 LangGraph 实现每个新 attempt 都清空活动 Skill，并要求加载记录位于最新用户消息之后；正常的“湖州→那杭州呢→那金华呢”因此先遭遇工具拦截，再尝试补加载。需要从逐轮 Skill 前置条件改为会话级能力管理：优先使用预设 Skill，复用仍然有效的规则与工具，并允许模型为 Skill 未覆盖的需求独立发现和加载 MCP 工具。

## What Changes

- 保留原生 LangGraph 的模型／工具循环和现有受理、实时业务检查、上下文整理、停止、台账与最终正文流；不迁移 Basic QA 到 Deep Agents。
- 模型结合当前问题、历史、有效摘要和已激活能力决策。已有 Skill 能解决时优先使用；多个 Skill 可共同完成任务；仅对预设 Skill 未覆盖的需求或部分，独立发现和加载专业工具。纯复述和寒暄可无搜索、无加载，非专业话题简短说明业务范围并引导回来。
- **BREAKING（仅 Basic QA）**：移除“每轮清空 Skill”“只有最新用户消息之后的加载才有效”“所有领域工具必须从属于活动 Skill”三项策略，改为会话内有效加载引用与本次实际模型请求一致的工具披露。独立加载的工具不再因为没有所属 Skill 而被拒绝。
- 保存已加载与已激活的多个 Skill、独立工具引用及加载来源；跨轮复用，重复加载幂等，按模式、材料／schema 版本与当前范围复核。只有激活材料和工具进入实际模型请求，历史引用不无限扩大上下文。
- 新增 `search_mcp(query)` 与 `load_mcp_tool(tool_id, expected_schema_version)`：搜索每次最多返回 **Top 3** 合法候选简介，由模型决定使用零个、一个或多个；搜索不自动加载，加载不执行查询。首次搜索前优先考虑 Skill；已激活的适用工具可复用，无需反复检索。
- 将当前 Basic QA 的21项工具子集扩展为当前受保护部署连接下、代码已审核查询清单所覆盖的专业工具目录；当前公共登记109项是代码基线，不承诺全部在线。未登记新工具不自动纳入，其他图的运行入口及分析报告工作流不因目录扩展被启动。
- 在程序侧维护工具描述／schema摘要索引，按目录内容和 embedding 模型版本增量构建；不把全量工具定义交给模型，不在图导入时外呼，不要求每次启动全量重算。
- 继续使用现有企业业务资格、受保护连接、查询白名单及受理约束，不恢复人员→Skill/MCP授权，不新增地区权限或接口回显字段／回答证据校验。
- 重写多轮验收：正常连续追问以零加载前置错误为目标；首次成功、恢复成功与用户可见异常分别统计，不再将“先报错后成功”记为无异常通过。

## Capabilities

### New Capabilities

- `basic-qa-session-capabilities`：会话理解、Skill优先及工具补足、多个能力的跨轮生命周期、请求一致性、专业范围和自然结束。
- `basic-qa-mcp-tool-discovery`：专业查询工具目录扩展、程序侧增量索引、Top 3语义检索、独立幂等加载与动态执行。

### Modified Capabilities

- `agent-authorized-capability-catalog`：区分全量服务端检索目录、Top 3候选与实际模型工具集合，保留实时范围过滤。
- `agent-request-capability-selection`：区分模型会话能力状态与用户受理选择；Basic QA允许有效Skill或独立工具加载，不新增前端选择器。
- `agent-service-connections`：两种加载来源使用同一受保护业务连接和实际执行范围，不引入新的人员授权。
- `agent-capability-observability`：区分搜索、加载、激活、复用与真实执行，记录独立工具来源；分别报告首次成功和报错后恢复。
- `skill-three-layer-system`：Basic QA使用受控目录／材料加载与跨轮恢复，不要求使用Deep Agents中间件；保留模式文件边界。
- `skill-content-basic`：保持九个Skill的领域规则、fast/expert材料及非绘图必需／绘图可选的依赖要求。
- `composite-backend-routing`：澄清Basic QA通过受控加载器取材料，不要求文件后端或任意文件工具；其他图实际实现保持兼容。

## Impact

- 预计涉及`src/common/business_graph/{builder,nodes,state,basic_qa_runtime,basic_qa_integration,skill_materials,skill_search,tool_recovery,tool_wrappers}.py`，以及`capabilities.py`、`business_registry.py`、MCP目录发现、router索引复用、观测接线与对应测试。新增工具索引／加载模块，复用现有受保护MCP工厂，不重写连接或身份系统。
- 能力字段与控制工具新增；最终正文SSE及九Skill领域计算不借机重构。若观测字段变化，实施时须同步既有契约源、Python/admin/TS校验器和样本。正式Agent Server管理持久化，不另向正式库手工创建checkpointer表。
- 开发范围仅Basic QA；data-analysis继续封存，intelligent-tracing继续使用自身Deep Agents机制。共享模块只做必要兼容回归，不扩大外部IAM／RAG仓库范围。
- 本计划取代活动变更`add-basic-qa-conversational-skill-loop`作为后续Basic QA执行依据，见[设计中的替代关系](design.md)。旧计划4/31项、未提交实现和失败记录保留，不改为完成、不同时应用两套冲突规格。
- 三份历史主规格仍为早期delta格式；未来同步／归档前须做保留未涉及语义的格式规范化。本轮仅创建本change的计划文件，不修改主规格、代码、私有配置、正在运行的服务，不提交、推送、部署或归档。
- 已确认需求、实施设计及完整验收矩阵均在[design.md](design.md)；第三方评审可从[review-guide.md](review-guide.md)开始。
