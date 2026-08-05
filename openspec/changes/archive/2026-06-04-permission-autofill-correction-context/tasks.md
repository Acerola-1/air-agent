## 1. 测试基线

- [x] 1.1 更新规则引擎无行政区测试，移除 `fix_strategy=replace_region` 的旧期望，断言自动补全不是越权替换
- [x] 1.2 新增中间件 slots 空行政区测试，覆盖 `None`、`"None"`、`"null"`、空字符串等清洗场景
- [x] 1.3 新增中间件自动补全测试，断言不调用 `resolve_region_scope` 且写入 `permission_query_overrides.region`
- [x] 1.4 新增权限上下文注入测试，断言 `<permission_context>` 包含 auto_filled、correction_text、matched_rules、debug_context 和 region override

## 2. 自动补全核心逻辑

- [x] 2.1 在权限中间件中抽取统一的行政区 slots 清洗 helper
- [x] 2.2 在权限中间件中实现从 `user_profile.bound_region` 构造标准自动补全区域对象
- [x] 2.3 调整 `_resolve_requested_region`，确保 slots 无行政区时跳过 `resolve_region_scope`
- [x] 2.4 调整 `abefore_agent` 权限事实构造，确保自动补全区域进入规则引擎或等价权限上下文

## 3. PermissionResult 与查询覆盖

- [x] 3.1 调整规则引擎或中间件后处理，使无行政区自动补全不默认产生 `fix_strategy=replace_region`
- [x] 3.2 自动补全场景设置 `auto_filled=true` 并生成包含区域名称的 `correction_text`
- [x] 3.3 确保自动补全区域写入 `PermissionResult.allowed_region` 或等价可执行区域字段
- [x] 3.4 更新 `_build_query_overrides`，支持 `auto_filled=true` 且 `fix_strategy=""` 时输出 `region`

## 4. 主流程上下文与拒绝策略

- [x] 4.1 更新 `_permission_context` 文案，明确存在 `permission_query_overrides.region` 时后续 skill 和工具调用必须使用该区域
- [x] 4.2 确保 `_rejection_message` 不会把自动补全场景当作硬拒绝短路
- [x] 4.3 限制 debug 上下文大小，只注入主流程需要的结构化字段

## 5. 验证

- [x] 5.1 运行 `pytest tests/unit_tests/test_permission_engine.py tests/unit_tests/test_permission_classify_middleware.py tests/unit_tests/test_permission_region.py`
- [x] 5.2 运行 `ruff check` 覆盖修改过的 Python 文件
- [x] 5.3 根据测试结果更新 OpenSpec 任务完成状态
