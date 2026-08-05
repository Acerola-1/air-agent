## 1. 模型重构

- [x] 1.1 修改 `PermissionExtractedSlots`：删除 `region_text`，新增 `province`/`city`/`district` 三个可选字段
- [x] 1.2 修改 `normalize_target_level` 或相关字段校验逻辑

## 2. 提示词更新

- [x] 2.1 修改 `PERMISSION_SLOT_EXTRACTION_PROMPT`，要求 LLM 分别输出三级行政区
- [x] 2.2 更新示例 JSON，展示 `province`/`city`/`district` 的提取方式
- [x] 2.3 增加规则说明：只提取用户问题中明确提到的行政区

## 3. 中间件修改

- [x] 3.1 修改 `_resolve_requested_region`：从 slots 提取 `province`/`city`/`district`
- [x] 3.2 修改 `_resolve_requested_region`：优先使用 LLM 提取的 `city`/`province` 作为消歧参数
- [x] 3.3 修改 `_resolve_requested_region`：LLM 未提取时回退到用户画像

## 4. Facts 构建修改

- [x] 4.1 修改 `build_permission_facts`：适配新的 slots 字段

## 5. 测试更新

- [x] 5.1 更新测试用例：使用新的 `province`/`city`/`district` 字段
- [ ] 5.2 新增跨市查询测试用例
- [x] 5.3 运行 `pytest tests/unit_tests/test_permission_*.py` 验证所有测试通过

## 6. Lint 检查

- [x] 6.1 运行 `ruff check src/common/permission/` 确认无 lint 错误
