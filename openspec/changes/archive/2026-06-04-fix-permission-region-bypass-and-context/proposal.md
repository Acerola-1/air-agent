## Why

当前权限中间件在处理用户未指定行政区的查询时存在两个关键问题：(1) 当 `PermissionExtractedSlots` 中所有城市字段均为 null 时，仍无意义地调用 `resolve_region_scope` MCP 工具，导致返回 `found=false` 后触发硬拒绝；(2) 主流程无法获知权限校验的详细结果（如匹配规则、debug 上下文、自动补全标记），导致后续 skill 执行缺乏必要的上下文信息。

## What Changes

- **修复无行政区查询的短路逻辑**：当 `province`/`city`/`district` 均为 null 时，跳过 `resolve_region_scope` 调用，直接使用用户 `bound_region` 作为默认查询区域
- **增强权限上下文向主流程的暴露**：将 `matched_rules`、`debug_context`、`auto_filled` 等结构化信息注入主模型的 `permission_context`
- **修正自动补全场景下的修正文案生成**：确保当行政区被自动补全时，主流程能收到清晰的修正说明

## Capabilities

### New Capabilities
- `permission-region-bypass`: 无行政区查询时直接使用用户默认区域，避免无意义 MCP 调用
- `permission-context-enrichment`: 向主流程暴露完整的权限校验结果和 debug 信息

### Modified Capabilities
- `permission-check`: 修改 `PermissionClassifyMiddleware` 的 `_resolve_requested_region` 和 `_permission_context` 方法的行为

## Impact

- `src/common/middleware/permission_classify_middleware.py`: `_resolve_requested_region` 和 `_permission_context` 方法
- `src/common/permission/engine.py`: `_extract_requested_region` 日志增强（可选）
- 权限审查流程的整体行为：无行政区查询不再被错误拒绝
