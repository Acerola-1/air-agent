## Why

当前权限链路按“单个请求行政区”建模，无法可靠处理“郑州市和平顶山市分别是多少”这类多行政区查询，以及“郑州市下的区县分别是多少”这类集合/下钻查询。直接把问题压成一个 `province/city/district` 会丢失用户意图，可能只校验一个区域，或把“下辖区县明细”误当成“父级城市汇总”。

这类问题不应该要求用户手动拆分。系统应在权限前置步骤中自动拆分或展开行政区，逐个完成解析和权限校验，再把完整订正上下文交给主流程，由主流程按最终允许的区域列表继续查询并在回复中说明做了哪些订正。

## What Changes

- 将权限链路从“单请求区域”扩展为“请求区域集合”：
  - 显式多行政区：自动拆分为多个区域项，例如 `郑州市`、`平顶山市`。
  - 集合/下钻行政区：自动展开为下级区域项，例如 `郑州市下的区县` 展开为郑州市下辖区县列表。
- 对每个区域项独立调用行政区解析和确定性权限规则，生成逐项权限结果。
- 聚合逐项权限结果，产出可供主流程使用的完整权限上下文：
  - 原始请求区域列表
  - 实际允许查询区域列表
  - 被替换、上卷、截断或拒绝的区域项
  - 用户可见修正文案
- 主流程 SHALL 使用聚合后的查询覆盖参数继续查询，不要求用户拆分问题。
- 单行政区查询、无行政区自动补全、时间豁免、时间截断、行政区替换等既有行为保持兼容。
- 工具层：
  - 显式多行政区短期可复用现有 `resolve_region_scope` 多次调用。
  - 集合/下钻行政区需要可用的下级行政区展开能力；若现有本地行政区树可稳定使用，则先封装本地 helper；若生产必须通过 MCP，则补充 `list_region_children` 工具。

## Capabilities

### New Capabilities
- `permission-multi-region-guard`: 将多行政区与集合行政区查询自动拆分、展开、逐项校验并聚合订正上下文，避免单区域权限校验误放行。

### Modified Capabilities
- `permission-classify-middleware`: 在权限中间件中支持批量区域解析、批量权限校验、聚合权限结果和批量查询覆盖参数注入。

## Impact

- `src/common/permission/slots.py`: 扩展 slots 抽取能力，保留多个行政区实体和集合/下钻语义，避免压缩成单个字段。
- `src/common/middleware/permission_classify_middleware.py`: 增加区域请求规划、批量解析/展开、逐项规则校验和聚合结果写入。
- `src/common/permission/facts.py`: 支持从单个区域事实扩展到逐项构造 `PermissionFacts`，保持单区域兼容。
- `src/common/permission/result.py`: 增加批量权限上下文字段，表达多个请求区域、允许区域、拒绝区域和逐项订正。
- `src/common/permission/mcp_tools.py` 或本地行政区 helper：补充下级行政区展开能力；显式多区域可通过多次调用现有 `resolve_region_scope` 实现。
- `src/basic_qa/graph.py` 和主流程提示词：要求后续 skill 查找、参数抽取和工具调用使用 `permission_query_overrides.regions`。
- `tests/unit_tests/test_permission_classify_middleware.py`、`tests/unit_tests/test_permission_slot_extraction.py`、`tests/unit_tests/test_permission_result.py`: 增加多行政区、集合行政区、部分订正和单区域回归测试。
- `docs/权限豁免测试用例-自然语言问题集.md`: 补充多行政区与集合行政区手工测试用例。
