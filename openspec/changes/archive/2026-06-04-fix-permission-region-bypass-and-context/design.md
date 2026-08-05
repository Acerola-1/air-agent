## Context

当前权限中间件 `PermissionClassifyMiddleware` 在 `_resolve_requested_region` 中，当用户未指定任何行政区（`province`/`city`/`district` 均为 null）时，会调用 `resolve_region_scope` MCP 工具解析用户 `bound_region`。这导致 MCP 返回 `found=false`，规则引擎判定为"无法解析行政区"而硬拒绝。

同时，`_permission_context` 注入主模型的信息有限，主流程无法获知权限校验的匹配规则、debug 上下文、自动补全标记等结构化信息。

## Goals / Non-Goals

**Goals:**
- 修复无行政区查询被错误拒绝的 bug
- 增强权限上下文向主流程的暴露，使主流程能获知权限校验的完整结果
- 保持向后兼容，不破坏现有权限审查流程

**Non-Goals:**
- 不修改规则引擎的核心判定逻辑（`check_permission`）
- 不修改 MCP 工具的服务端实现
- 不修改 `PermissionResult` 的数据模型（已有字段足够）

## Decisions

### Decision 1: 无行政区时直接返回 None，跳过 MCP 调用

**方案A（选择）**: 当 `llm_province`、`llm_city`、`llm_district` 均为 null 时，`_resolve_requested_region` 直接返回 `None`，不调用 `resolve_region_scope`。

**方案B**: 继续调用 MCP，但增强 MCP 返回处理。

**选择方案A的理由**:
- 用户未提行政区 → 应使用默认区域 → 不需要解析
- 减少无意义 MCP 调用，降低延迟
- 避免 MCP 返回异常导致的拒绝

### Decision 2: 在 `_permission_context` 中暴露完整的 `PermissionResult`

**方案A（选择）**: 将 `matched_rules`、`debug_context`、`auto_filled` 等字段从 `permission_result` 中提取并注入 `permission_context`。

**方案B**: 创建新的数据结构封装权限上下文。

**选择方案A的理由**:
- 复用现有 `PermissionResult` 字段，无需新增模型
- 主流程通过现有 `permission_result` 字段即可获取所需信息

### Decision 3: 自动补全时标记 `auto_filled` 并生成修正文案

当无行政区时自动使用 `bound_region`，在 `PermissionResult` 中设置 `auto_filled=true`，并生成修正文案告知用户。

## Risks / Trade-offs

- **[Risk]** 跳过 MCP 调用后，如果用户画像中没有 `bound_region`，`requested_region` 为 None，规则引擎会使用 `default_allowed_region` 返回用户默认区域。这可能导致查询区域与用户预期不符。
  - **Mitigation**: 在 `auto_filled` 场景中，修正文案明确告知用户使用了默认区域。

- **[Risk]** 增强的 `permission_context` 可能使 system prompt 过长。
  - **Mitigation**: 只注入关键字段（`matched_rules`、`auto_filled`、`debug_context`），并限制 `debug_context` 的大小。
