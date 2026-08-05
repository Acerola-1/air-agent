## Why

当前权限 slots 同时包含单值 `province/city/district/target_level` 和多区域 `region_entities`，多区域场景下会出现“真实请求是多个区域，但兼容字段只保留第一个城市”的语义歧义。权限结果也需要把每个请求区域的解析、校验、替换、拒绝和最终查询区域逐项保留下来，避免主流程只拿到聚合列表而缺少逐项解释依据。

## What Changes

- **BREAKING**：将权限抽取结果中的行政区表达统一升级为区域请求列表，废弃以单值 `province/city/district/target_level` 表达主语义的方式。
- 每个区域请求项 SHALL 带有独立的文本、层级提示、消歧上下文和来源，例如显式请求、自动补全、集合父级、集合展开子项或权限替换。
- 单区域、多区域、无区域自动补全和集合/下钻查询 SHALL 共用同一套列表模型，区别只由 `region_mode` 和区域项来源表达。
- 中间件 SHALL 按区域请求项逐个调用 `resolve_region_scope`，并为每个标准区域独立构造 `PermissionFacts` 和调用 `check_permission()`。
- 权限结果 SHALL 新增逐项 `region_results`，记录每个请求区域的原始文本、解析结果、权限状态、实际查询区域、订正原因和可选合法时间范围。
- 主流程 SHALL 使用 `permission_query_overrides.regions` 执行查询，同时使用 `permission_result.region_results` 生成逐项解释；不得依赖旧单值字段或只取第一个区域。
- 兼容期可保留旧字段只读输出或适配层，但其值不得作为多区域或集合区域的权威输入。

## Capabilities

### New Capabilities

- `permission-region-list-slots`: 定义权限抽取阶段统一的区域请求列表结构，覆盖单区域、多区域、自动补全和集合/下钻行政区语义。

### Modified Capabilities

- `permission-classify-middleware`: 权限中间件从统一区域列表逐项解析、逐项校验并聚合查询覆盖参数。
- `permission-result-schema`: 权限结果结构增加逐项区域结果，主流程可按区域分别处理允许、替换、拒绝和时间截断。

## Impact

- `src/common/permission/slots.py`：重构 `PermissionExtractedSlots` 行政区字段和抽取提示词。
- `src/common/middleware/permission_classify_middleware.py`：使用区域请求列表驱动请求计划、解析、集合展开、权限校验和上下文注入。
- `src/common/permission/result.py`：新增 `RegionPermissionResult` 或等价结构，表达逐项区域权限结果。
- `src/common/permission/facts.py`：继续保持单个 `PermissionFacts` 一次校验一个标准区域，由中间件循环构造。
- `src/basic_qa/graph.py` 和主流程提示词：明确读取 `regions` 与 `region_results`，不再依赖旧单值行政区字段。
- `tests/unit_tests/test_permission_slot_extraction.py`、`tests/unit_tests/test_permission_classify_middleware.py`、`tests/unit_tests/test_permission_result.py`：更新并补充统一列表模型、混合层级、多区域逐项结果和兼容回归测试。
- 该变更会影响依赖 `province/city/district/target_level` 的旧测试、提示词和调用方，需要同步迁移或提供明确的兼容适配层。
