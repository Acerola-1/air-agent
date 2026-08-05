# Permission Check - 预编译 Agent 方案 Spec

## Purpose

权限审查通过 `PermissionClassifyMiddleware` 完成。中间件在 `__init__` 中通过 `create_agent` 预编译权限审查 agent，`abefore_agent` 中根据 `permission_need` 按需调用，将 `PermissionResult` 写入 AgentState 并注入系统提示词。

## Architecture

```
PermissionClassifyMiddleware.__init__
  └─ create_agent → self._permission_agent (预编译)

PermissionClassifyMiddleware.abefore_agent
  ├─ user_id (from configurable)
  ├─ classify_permission_need → permission_need
  ├─ no_check → 快速返回
  └─ need_check/uncertain →
       await self._permission_agent.ainvoke(task_desc, config)
       → structured_response (PermissionResult)
       → state.permission_result

PermissionClassifyMiddleware.awrap_model_call
  └─ <permission_context> 注入系统提示词
```

## Pre-compiled Agent

| Parameter | Value |
|-----------|-------|
| factory | `langchain.agents.create_agent` |
| model | `ModelRegistry.mimo_v2_5_pro` |
| tools | `[get_beijing_time, permission_region_context_tool]` + dynamic MCP tools |
| system_prompt | 权限审查提示词（含完整规则表和刚性约束） |
| response_format | `PermissionResult` |
| middleware | `[CodeInterpreterMiddleware(ptc=[])]` |

### Agent 内部执行流程（由提示词引导）

1. 调用 `get_beijing_time` 获取当前北京时间
2. 从任务描述中提取 userId，调用 `get_user_profile(userId)` 获取用户权限画像
3. 调用 `resolve_region_scope(query, contextProvinceCode, contextCityCode)` 解析行政区
4. 调用 `permission_region_context_tool` 获取行政区上下文
5. 推断时间粒度和时间范围，用 CodeInterpreter 计算豁免窗口
6. 判断适用范围排除项
7. 判断行政区权限和修正策略
8. 生成 correction_text（按文案模板）
9. 返回 PermissionResult

## Main Model Guidance

系统提示词引导主模型消费 `PermissionResult`：

- `fix_strategy = "truncate_time"`：使用 `legal_time_span` 作为查询时间范围
- `fix_strategy = "replace_region"`：使用 `allowed_region` 作为查询行政区
- `fix_strategy = ""`：不修正，按原问题继续
- `correction_text` 非空时，最终回复必须包含该修正说明

## Required Tools

| Tool | Status | Source | Called By |
|------|--------|--------|----------|
| `get_beijing_time` | ✅ 已实现 | Python | 预编译 agent |
| `permission_region_context_tool` | ✅ 已实现 | Python | 预编译 agent |
| `get_user_profile` | ✅ 已接入 | MCP datacenter | 预编译 agent |
| `resolve_region_scope` | ✅ 已接入 | MCP datacenter | 预编译 agent |
| `CodeInterpreter` | ✅ 已实现 | Middleware | 预编译 agent |

待接入工具（站点/网格）：

| Tool | Status | Source |
|------|--------|--------|
| `resolve_station_scope` | 待接入 | MCP datacenter |
| `resolve_grid_scope` | 待接入 | MCP datacenter |

## File Location

中间件：`src/common/middleware/permission_classify_middleware.py`
提示词：`src/common/middleware/permission_check_prompt.py`
确定性计算：`src/common/permission_region.py`
结果模型：`src/common/permission_result.py`

## Registration

在 `src/basic_qa/graph.py` 的 `_build_middleware()` 中注册 `PermissionClassifyMiddleware`，位于 `SkillToolRegistryMiddleware` 之前。不需要 `subagents` 参数。