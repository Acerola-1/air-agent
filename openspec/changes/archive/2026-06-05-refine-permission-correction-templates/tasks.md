## 1. 文案模板与上下文

- [x] 1.1 梳理当前 `PermissionResult.correction_text` 生成点，确认时间截断、行政区替换、自动补全、站点裁剪和硬拒绝路径。
- [x] 1.2 新增或重构确定性修正文案 helper，输入用户画像、请求行政区、替代行政区、时间粒度和必要 debug context。
- [x] 1.3 实现行政区替换模板类型识别，覆盖省级用户区县上卷、省级用户外省上卷、地市用户本省其他区县替换、地市用户外省替换、区县用户省级汇总替换、区县用户外市替换。
- [x] 1.4 实现月/年粒度越权替换专门模板，覆盖 `month`、`month_count`、`year`、`year_count`。
- [x] 1.5 保留通用兜底模板，并在 debug context 或 matched rules 中记录模板回退原因。

## 2. 规则引擎接入

- [x] 2.1 调整 `_build_region_replacement_result()`，使用新的矩阵化行政区修正文案 helper。
- [x] 2.2 调整 `_build_time_truncation_result()`，生成“查询日期范围部分超出豁免窗口，已截断至合法时间范围”的文案。
- [x] 2.3 在 `build_permission_facts()` 或规则引擎输入中传递可选指标名称和请求区域摘要，供时间截断文案使用。
- [x] 2.4 确认自动补全文案保持现有“根据您的关联区域，已自动补全查询区域为...”语义，不误用越权替换模板。
- [x] 2.5 确认站点 `filter_stations` 和站点归属缺失文案保持专用模板，不被行政区模板覆盖。

## 3. 中间件聚合与注入

- [x] 3.1 确认 `_permission_context()` 原样注入规则引擎返回的细分 `correction_text`，不做降级覆盖。
- [x] 3.2 调整多区域 `_merge_batch_permission_results()` 聚合逻辑，保留并去重逐项细分修正文案。
- [x] 3.3 确保多区域部分替换、时间截断和部分拒绝时，`region_results[].correction_text` 保留单项文案。
- [x] 3.4 确认 `_rejection_message()` 优先返回细分 `correction_text`，仅在缺失时使用默认拒绝文案。

## 4. 测试

- [x] 4.1 增加行政区修正文案 helper 单测，覆盖文档矩阵中的 6 类核心行政区替换。
- [x] 4.2 增加月/年粒度越权替换文案单测。
- [x] 4.3 增加时间截断文案单测，验证包含“部分超出豁免窗口”和合法时间范围。
- [x] 4.4 增加规则引擎单测，验证 `PermissionResult.correction_text` 使用细分模板。
- [x] 4.5 增加中间件聚合单测，验证多区域逐项文案不会被泛化文案覆盖。
- [x] 4.6 增加站点文案回归单测，验证 `filter_stations` 和“该数据暂不可用”不变。

## 5. 验证

- [x] 5.1 运行目标 Ruff 检查：`ruff check src/common/permission src/common/middleware/permission_classify_middleware.py tests/unit_tests/test_permission*`。
- [x] 5.2 运行目标格式检查：`ruff format --check src/common/permission src/common/middleware/permission_classify_middleware.py tests/unit_tests/test_permission*`。
- [x] 5.3 运行目标测试：`.venv/bin/pytest tests/unit_tests/test_permission_engine.py tests/unit_tests/test_permission_region.py tests/unit_tests/test_permission_classify_middleware.py tests/unit_tests/test_permission_result.py tests/unit_tests/test_permission_facts.py`。
- [x] 5.4 使用 `openspec validate refine-permission-correction-templates --strict` 校验变更文档。
