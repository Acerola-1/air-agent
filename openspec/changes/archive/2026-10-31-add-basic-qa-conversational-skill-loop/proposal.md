# 基础问答多轮 Skill 循环提案

## Why

当前 `basic-qa` 虽然保存会话历史并有模型/工具循环，但入口分类和 Skill embedding 只读取最新一句，自动加载 top1 后又禁止模型调整 Skill；“那杭州呢？”等追问因此无法在能力选择阶段利用历史。需要让模型根据会话理解任务，按需发现和加载 Skill，同时让规则加载、工具启用及真实执行保持可核验。

## What Changes

- 将 Basic QA 的必经“最新一句分类 → embedding top1”改为上下文驱动的模型/工具循环，保留输入限制、可信受理、身份复查、上下文治理和最终正文发布链路。
- 首次规划提供本图 Skill 简介；新增可选 `search_skills(query)`，复用 embedding 返回候选元数据，不自动选定或加载。模型可以直接加载已知 Skill，不要求每轮搜索。
- 改造并接通 `load_skill`：校验稳定 ID、材料版本、模式及已受理范围，加载完整规则，提交本轮活动 Skill 状态，再在下一次模型请求中披露对应可用工具。加载本身不执行业务查询，也不授予 MCP 人员权限。
- **BREAKING（仅 Basic QA 执行策略）**：领域数据工具执行前必须有本轮有效活动 Skill；自动模式不再允许无 Skill 直接执行领域 MCP。通用时间、知识检索及既有受控结果处理工具按明确分类保留。其他图不继承此新增前置条件。
- 区分线程材料缓存与本轮活动规则；新业务轮可直接 `load_skill` 复用已知材料而不搜索。支持自动模式顺序切换 Skill，首版同一时刻只激活一个 Skill。
- 增加实际工具入口校验、每次发送前规则重建、有界循环及既有终答发布；用固定多轮用例分别验收受控模型、真实配置模型、原生持久化和浏览器行为。

## Capabilities

### New Capabilities

- `basic-qa-conversation-loop`：上下文驱动决策、可选检索、可信加载、逐轮工具范围、结束条件及多轮验收。

### Modified Capabilities

- `agent-request-capability-selection`：限定 Basic QA 无 Skill 直接执行业务 MCP 的旧场景，保留显式选择、其他图及业务权限契约。
- `skill-three-layer-system`：明确 Basic QA 的元数据目录、模式材料加载与当前目录来源，消除对其必须使用 SkillsMiddleware/read_file 的旧假定。
- `skill-content-basic`：为 Basic QA 明确通过受控加载器读取总纲与 references 的模式选择要求，保留领域规则。
- `agent-capability-observability`：区分可选搜索、直接加载、缓存加载、真实执行及未执行，不用规则字符串存在代表已执行。
- `agent-service-connections`：区分无需人员绑定与 Basic QA 领域工具的 Skill 执行前置条件。
- `composite-backend-routing`：移除仅针对 Basic QA 的通用文件后端强制要求，改用受控 Skill 材料加载。

## Impact

- 预计涉及 `src/basic_qa/graph.py`、`src/common/business_graph/`、Skill 目录/路由/材料版本辅助层、`capabilities.py`、工具包装及 Skill 观测；优先复用现有 `context_preparation.py` 与受理/运行守卫。
- 更新九个现有 Basic QA Skill 的工具名称映射和模式依赖一致性；不增加未经验证的因果分析能力，不自动启动溯源图，不修改 IAM 项目。
- `data-analysis` 继续封存，`intelligent-tracing` 不迁移；共享模块修改须有针对性兼容回归。无需迁移为 Deep Agents，也不为此升级框架依赖或新增文件系统/shell/subagent 工具。
- 与活动变更 `simplify-model-window-compaction-trigger`、`unify-user-visible-answer-streaming` 的既有实现保持兼容；不继承其未完成验收为已通过，不自动归档历史变更。
- 当前仅创建计划文件，不修改运行代码、部署配置或现有主规格，不提交或推送。实现阶段按 `tasks.md` 提供可审查 diff 与独立验收证据。

## 范围纠正（2026-10-28）

按用户明确要求，撤销实现过程中额外加入的回答字段校验。系统不要求接口回显城市、时间或指标，不增加结构化回答类别/证据引用声明，不据返回结构拦截终答。保留本次原定的会话理解、可选搜索、受控加载、工具范围与既有流式输出。
