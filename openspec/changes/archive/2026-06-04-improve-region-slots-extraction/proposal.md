## Why

当前 `PermissionExtractedSlots` 只提取 `region_text`（如"金水区"），导致调用 `resolve_region_scope` 消歧时传入的 `city`/`province` 来自用户画像（如"商丘市"），而非用户问题中提到的城市（如"郑州市"）。这会造成跨市查询时消歧错误，返回用户绑定城市的行政区而非用户实际想查的行政区。

## What Changes

- **BREAKING**: 重构 `PermissionExtractedSlots`，用 `province`/`city`/`district` 三级字段替代 `region_text`
- 修改 LLM 提示词，要求分别提取省、市、区县三级行政区名称
- 修改 `_resolve_requested_region`，直接使用 LLM 提取的 `city`/`province` 作为消歧参数
- 修改 `build_permission_facts` 适配新字段
- 更新测试用例

## Capabilities

### New Capabilities
- `region-slots-three-level`: 三级行政区（province/city/district）分别提取的 slots 规范

### Modified Capabilities
- `permission-classify-middleware`: 中间件中 `_resolve_requested_region` 的调用逻辑，从使用用户画像消歧改为使用 LLM 提取的三级字段消歧

## Impact

- `src/common/permission/slots.py`: `PermissionExtractedSlots` 模型重构
- `src/common/middleware/permission_classify_middleware.py`: `_resolve_requested_region` 方法修改
- `src/common/permission/facts.py`: `build_permission_facts` 适配新字段
- 测试用例更新
