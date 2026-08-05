## Why

当前权限审查链路中，`get_user_profile` 和 `resolve_region_scope` 两个 MCP 工具返回的 `code` 字段（如 `41b6dc401`）不是标准行政区划编码，在 Python 侧没有任何实际用途，反而导致 `context_city_code` 传参错误（传成了用户关联城市的 code，而非查询行政区的父级城市 code），引发"商丘市 + 金水区"这种错误组合查询不到结果的问题。同时，`resolve_region_scope` 的消歧参数设计不合理，应直接使用中文名而非内部 code 进行消歧。

## What Changes

- **BREAKING**: 删除 `get_user_profile` 返回中的 `code` 字段，仅保留 `name` 和 `level`
- **BREAKING**: 删除 `resolve_region_scope` 入参中的 `context_city_code` 和 `context_province_code`，替换为 `city` 和 `province`（中文名）
- **BREAKING**: 删除 `resolve_region_scope` 返回中的 `code` 字段，仅保留 `name` 和 `level`
- 修改 Python 侧 `region.py` 中所有依赖 `code` 的逻辑，改为使用 `name` 进行行政区比较和消歧
- 修改 Python 侧 `engine.py` 中所有依赖 `code` 的逻辑
- 修改 Python 侧 `mcp_tools.py` 中 `resolve_region` 函数的参数签名
- 修改 Python 侧 `permission_classify_middleware.py` 中的调用逻辑
- 更新 `docs/权限P0工具需求.md` 文档，反映新的接口设计

## Capabilities

### New Capabilities

- `region-name-based-resolution`: 基于中文名称的行政区解析和消歧能力，替代基于 code 的解析方式

### Modified Capabilities

- `permission-check-subagent`: 权限审查链路中行政区解析和比较的逻辑，从基于 code 改为基于 name
- `permission-classify-middleware`: 中间件中调用 `resolve_region_scope` 的参数构造逻辑

## Impact

- **服务端（Java）**: `PermissionVaildTools.java` 和 `PermissionMcpToolServiceImpl.java` 需要修改 `get_user_profile` 和 `resolve_region_scope` 的实现
- **Python 侧**: `src/common/permission/` 目录下所有文件需要修改
- **文档**: `docs/权限P0工具需求.md` 需要更新
- **测试**: 所有涉及 `code` 字段的测试用例需要更新
