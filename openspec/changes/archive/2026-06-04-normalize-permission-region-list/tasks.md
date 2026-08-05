## 1. Slots 区域列表模型

- [x] 1.1 在 `src/common/permission/slots.py` 中新增 `PermissionRegionRequest` 模型，包含 `text`、`level_hint`、`province_hint`、`city_hint`、`source`、`role`、`child_level` 等字段。
- [x] 1.2 将 `PermissionExtractedSlots` 的行政区主字段升级为 `region_mode` 和 `regions: list[PermissionRegionRequest]`。
- [x] 1.3 废弃或删除 `province/city/district/target_level/region_entities/region_collection_intent` 作为权威输入；如保留兼容字段，必须标记为 deprecated 且中间件不得优先读取。
- [x] 1.4 更新 `PERMISSION_SLOT_EXTRACTION_PROMPT`，要求单区域、多区域、无区域和集合/下钻查询统一输出 `regions` 列表。
- [x] 1.5 增加混合层级示例，覆盖 `郑州市和金水区PM2.5分别是多少？` 每个区域项独立携带层级和消歧上下文。

## 2. 区域请求计划与解析

- [x] 2.1 将当前 `build_region_request_plan` 改为以 slots `regions` 列表为权威输入，旧字段只做 fallback。
- [x] 2.2 实现区域项归一化 helper，补齐 `level_hint`、`source`、`role` 和缺失的 hints。
- [x] 2.3 对 `region_mode = "multi_explicit"` 的每个区域项逐个调用 `resolve_region_scope`。
- [x] 2.4 对混合层级区域逐项传递消歧上下文：省级不传用户城市，地市最多传省份，区县优先使用区域项或问题中的城市/省份上下文。
- [x] 2.5 对 `region_mode = "auto_fill"` 构造来源为 `auto_fill` 的区域项，并跳过 `resolve_region_scope`。
- [x] 2.6 对 `region_mode = "collection"` 解析父级区域项并展开目标 `child_level`，展开出的子项来源标记为 `collection_child`。
- [x] 2.7 集合展开失败时写入逐项拒绝原因，不得把父级区域当作最终查询区域。

## 3. 逐项权限校验与结果模型

- [x] 3.1 在 `src/common/permission/result.py` 中新增 `RegionPermissionResult` 或等价模型，表达 `requested`、`resolved`、`status`、`query_region`、`legal_time_span`、`correction_text` 和 `reason`。
- [x] 3.2 扩展 `PermissionResult`，新增 `region_results`，并保持 `allowed_regions/corrected_regions/rejected_regions` 聚合字段。
- [x] 3.3 中间件为每个解析后的标准区域独立构造 `PermissionFacts` 并调用 `check_permission()`。
- [x] 3.4 将单项规则结果转换为 `RegionPermissionResult`，区分 `allowed`、`replaced`、`rejected`、`time_truncated`、`auto_filled` 等状态。
- [x] 3.5 聚合逐项结果生成 `permission_query_overrides.regions`，只包含实际可执行查询区域。
- [x] 3.6 对部分拒绝场景继续保留允许区域，并在 `correction_text` 中说明未查询区域原因。
- [x] 3.7 对全部拒绝场景生成整体硬拒绝，不写入可执行 `regions`。
- [x] 3.8 对多区域时间截断结果进行聚合：一致时写入全局 `time_span`，不一致时仅保留逐项合法时间范围。

## 4. 主流程上下文与兼容迁移

- [x] 4.1 更新 `<permission_context>` 注入文本，明确 `permission_query_overrides.regions` 是最终查询区域列表。
- [x] 4.2 更新 `<permission_context>` 注入文本，明确 `permission_result.region_results` 用于逐项解释和审计。
- [x] 4.3 更新 `src/basic_qa/graph.py` 主流程提示词，禁止在存在 `regions` 时退回旧单值区域字段。
- [x] 4.4 梳理依赖旧 `province/city/district/target_level` 的测试、提示词或 helper，迁移到 `regions` 列表或显式兼容适配层。
- [x] 4.5 确认 `allowed_region` 和 `permission_query_overrides.region` 仅用于单区域兼容，不得覆盖 `regions`。

## 5. 测试覆盖

- [x] 5.1 更新 slots 模型测试，覆盖默认值、单区域列表、多区域列表、无区域自动补全和集合父级结构。
- [x] 5.2 增加多区域混合层级测试，覆盖 `郑州市和金水区PM2.5分别是多少？`。
- [x] 5.3 更新中间件多城市测试，确认循环调用 `resolve_region_scope` 且不读取单值 `city` 作为最终区域。
- [x] 5.4 更新集合区县测试，确认父级只用于展开，最终 `regions` 为下级区县列表。
- [x] 5.5 增加逐项权限结果测试，覆盖允许、替换、拒绝、自动补全和时间截断状态。
- [x] 5.6 增加主流程上下文注入测试，确认 `<permission_context>` 包含 `region_results` 和 `permission_query_overrides.regions` 指令。
- [x] 5.7 保留单区域和无区域自动补全回归测试，确认兼容路径仍能继续查询。

## 6. 文档与验证

- [x] 6.1 更新 `docs/权限P0工具需求.md`，补充 `regions` 列表和 `region_results` 的输入输出契约。
- [x] 6.2 更新 `docs/权限豁免测试用例-自然语言问题集.md`，补充混合层级、多区域逐项结果和兼容迁移手工用例。
- [x] 6.3 对受影响 Python 文件运行 `ruff format` 和 `ruff check`。
- [x] 6.4 运行权限 slots、result schema、permission middleware 相关单元测试。
- [x] 6.5 运行 `openspec validate normalize-permission-region-list --strict`，确认变更文档有效。
