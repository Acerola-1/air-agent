## 1. 修复无行政区查询的短路逻辑

- [x] 1.1 修改 `_resolve_requested_region` 方法：当 `llm_province`、`llm_city`、`llm_district` 均为 null 时，直接返回 `None`，不调用 `resolve_region_scope`
- [x] 1.2 在 `abefore_agent` 中，当 `requested_region` 为 `None` 时，确保规则引擎使用 `default_allowed_region(user_profile)`
- [x] 1.3 测试：验证"查一下昨天的空气质量"（无行政区）不再被错误拒绝

## 2. 增强权限上下文向主流程的暴露

- [x] 2.1 修改 `_permission_context` 方法：从 `permission_result` 中提取 `matched_rules`、`debug_context`、`auto_filled` 并注入 context
- [x] 2.2 确保修正文案（`correction_text`）在自动补全场景下正确生成
- [x] 2.3 测试：验证主流程能收到完整的权限上下文信息

## 3. 验证与回归测试

- [x] 3.1 运行现有权限相关测试，确保无回归
- [x] 3.2 运行 ruff 和 pyright 检查
- [x] 3.3 验证边界场景：有行政区、无行政区、用户无 bound_region
