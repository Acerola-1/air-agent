## Background

### 业务背景

本项目是空气质量 AI 智能问数系统，用户通过自然语言查询空气质量和气象数据。系统服务于三级用户：省级用户（看汇总）、地市级用户（看本市明细+全省汇总）、区县级用户（看本市及以下明细）。不同级别的用户有不同的数据访问权限，严格按行政区权限管控时，高频短期查询会被频繁拒绝（如区县用户问"昨天全国PM2.5"），体验极差。

### 设计文档来源

设计文档来源：`docs/设计文档/AI智能问数权限方案设计-260521.md`（V2.3）描述了一套完整的权限控制方案，核心创新是**基于时间粒度的动态豁免机制**：近期数据跨行政区开放，远期数据严格管控。具体规则：
- 小时/日/周/自定义范围 → 最近7天窗口 → 窗口内无行政区限制
- 月/月累计 → 近两月窗口 → 同上
- 年/年累计 → 近两年窗口 → 同上
- 窗口外 → 严格按行政区权限 + 自动修正（时间截断优先，行政区修正其次）

### 旧方案情况

原有 `PermissionMiddleware`（`src/common/middleware/permission_middleware.py`）调用服务端 RBAC 接口（MCP `permission_vaild` 工具），是静态权限校验——后端已有权限判断，前端只是问一下后端。该中间件**已弃用且未挂载到任何业务图**，无法满足设计文档要求的语义分析权限控制（时间粒度推断、豁免窗口计算、行政区修正）。

### 架构差异

设计文档假设了 6 个独立 Agent 节点串联的架构（Intent → SlotFilling → Permission → Corrector → DataQuery → Response），但实际项目使用 DeepAgents 框架 + 中间件 + Skill 渐进披露架构。需要将设计文档的权限逻辑适配到现有架构中。

### 关键适配决策

1. 设计文档的 Permission Agent + Corrector Agent → 合并为 PermissionCheck SubAgent
2. 设计文档的 Intent Agent / SlotFilling Agent → 已由 Skill 路由 + 主模型推理覆盖
3. 设计文档的 20+ 伪代码函数 → 由 SubAgent LLM 推理 + quickjs 辅助计算替代
4. `week` 类型 → 设计文档归为"单一时间→转时间段"，我们简化为日级时间段与 `other` 共享逻辑

## Why

在 AI 智能问数场景下，用户频繁查询近期数据做跨区域对比（如"昨天全国PM2.5"、"上月杭州PM2.5"），严格行政区权限会导致这些合理需求被拒绝，用户体验极差。设计文档的豁免机制让近期数据查询不受行政区限制，远期数据仍严格管控，兼顾了安全与体验。旧方案是服务端 RBAC 静态校验，无法实现时间粒度动态豁免，需要全新实现语义分析权限控制。

## What Changes

- 新增 `PermissionClassifyMiddleware`：轻量中间件，在 `abefore_agent` 中执行规则预分类（复用 `permission_rules.py` 的 `classify_permission_need`），将分类结果写入 AgentState，不拦截不调 LLM
- 新增 `PermissionCheckSubAgent`：声明式 SubAgent，由主模型通过 `task` 工具触发，执行完整权限推理流程（时间粒度推断 → 豁免窗口计算 → 交集判定 → 行政区权限判定 → 修正策略 → 修正文案生成）
- 新增 `PermissionResult` Pydantic 模型：SubAgent 的结构化输出格式，包含是否放行、修正策略、修正后参数、修正说明文案
- 扩展 `AgentState`：新增 `permission_need` 字段，供主模型判断是否触发权限 SubAgent
- 在 `basic-qa` graph 中注册 PermissionCheckSubAgent 和 PermissionClassifyMiddleware
- `week` 类型按日级时间段处理，与 `other` 共享 7 天豁免窗口和截断逻辑，不作为独立"转时间段"分类

## Capabilities

### New Capabilities
- `permission-classify-middleware`: 轻免规则预分类中间件，在主循环前将 `permission_need`（no_check/need_check/uncertain）写入 AgentState
- `permission-check-subagent`: 权限审查 SubAgent，接收用户问题+身份信息，返回结构化 PermissionResult（含修正策略和修正后参数）
- `permission-result-schema`: PermissionResult Pydantic 模型定义，作为 SubAgent 的 response_format 和跨 Agent 传递的契约

### Modified Capabilities
- `subagent-architecture`: 新增权限审查 SubAgent 注册到 basic-qa graph，扩展 SubAgent 使用模式

## Impact

- **代码**：`src/common/middleware/` 新增中间件；`src/basic_qa/graph.py` 注册 SubAgent 和中间件；`src/common/` 新增 PermissionResult 模型
- **State**：AgentState 新增 `permission_need` 字段，需在 `SkillDiscoveryState` 或独立 State 类中扩展
- **依赖**：复用现有 `permission_rules.py`、`parse_region_tool`、`get_beijing_time`、`CodeInterpreterMiddleware(ptc=[])`；不引入新外部依赖
- **提示词**：主模型系统提示词需新增权限 SubAgent 触发引导；SubAgent 需独立的系统提示词（执行流程 + 修正文案模板）
- **设计文档对齐**：`week` 类型简化为日级时间段，不按设计文档的"单一时间→转时间段"分类
