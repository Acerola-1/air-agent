## Why

当前权限链路把“用户未指定行政区”误处理为行政区越权后的替换修正，导致主流程拿到的语义不稳定：后续查询可能不知道应使用用户默认关联区域，也无法可靠地向用户说明系统做了自动补全。需要重新定义无行政区查询的权限协议，使 `bound_region` 成为可信默认查询区域，并把补全结果、修正文案和权限上下文稳定传递给主流程。

## What Changes

- 将“用户未指定行政区”定义为自动补全场景：使用 `get_user_profile.bound_region` 构造默认查询区域，而不是把 `requested_region=None` 留给规则引擎兜底替换。
- 自动补全场景必须生成面向用户的修正文案，说明“已自动补全查询区域”以及补全后的区域名称。
- 主流程必须通过 `permission_context` 和 `permission_query_overrides` 获得完整权限上下文，包括补全后的区域、原始槽位、匹配规则、debug 上下文、自动补全标记和修正文案。
- 修正 `PermissionResult` 语义：无行政区自动补全不是越权替换，不应默认表现为 `fix_strategy=replace_region`；只有真实越权行政区替换才使用 `replace_region`。
- 更新单元测试和 OpenSpec 断言，移除把无行政区默认区域固化为 `replace_region` 的旧期望。

## Capabilities

### New Capabilities
- `permission-autofill-correction-context`: 定义无行政区查询时使用用户默认关联区域、生成自动补全文案、并向主流程传递完整权限上下文的行为。

### Modified Capabilities
- `permission-classify-middleware`: 修改权限中间件在 slots 无行政区时的补全、状态注入和主流程 prompt 协议。
- `permission-result-schema`: 调整 `PermissionResult` 字段语义约束，明确 `auto_filled` 与 `fix_strategy=replace_region` 的关系。

## Impact

- `src/common/middleware/permission_classify_middleware.py`: 行政区补全、slots 清洗、`permission_query_overrides` 构造、`permission_context` 注入。
- `src/common/permission/engine.py`: 无 requested region 场景的规则结果语义，避免把默认补全当作越权替换。
- `src/common/permission/facts.py`、`src/common/permission/region.py`: 如有需要，补充默认区域构造和 raw slots 上下文。
- `tests/unit_tests/test_permission_engine.py`、`tests/unit_tests/test_permission_classify_middleware.py`、`tests/unit_tests/test_permission_region.py`: 更新无行政区、自动补全、上下文注入相关断言。
- 不修改 MCP 服务端合约；继续以 `get_user_profile.bound_region` 和 `resolve_region_scope` 作为可信事实来源。
