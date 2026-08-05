## 1. 文档更新

- [x] 1.1 更新 `docs/权限P0工具需求.md`：删除 `get_user_profile` 返回中的 `code` 字段
- [x] 1.2 更新 `docs/权限P0工具需求.md`：修改 `resolve_region_scope` 入参，删除 `context_city_code`/`context_province_code`，新增 `city`/`province`（中文名）
- [x] 1.3 更新 `docs/权限P0工具需求.md`：删除 `resolve_region_scope` 返回中的 `code` 字段

## 2. 服务端（Java）修改

- [x] 2.1 修改 `PermissionVaildTools.java`：`get_user_profile` 返回删除 `code` 字段
- [x] 2.2 修改 `PermissionMcpToolServiceImpl.java`：`resolve_region_scope` 入参删除 `context_city_code`/`context_province_code`，新增 `city`/`province`（中文名）
- [x] 2.3 修改 `PermissionMcpToolServiceImpl.java`：`resolve_region_scope` 返回删除 `code` 字段
- [x] 2.4 修改 `PermissionMcpToolServiceImpl.java`：`resolve_region_scope` 消歧逻辑改为基于中文名匹配
- [x] 2.5 服务端测试验证：测试"郑州市金水区"解析、"朝阳区"按省份消歧

## 3. Python 侧 MCP 工具封装修改

- [x] 3.1 修改 `src/common/permission/mcp_tools.py`：`resolve_region` 函数删除 `context_province_code`/`context_city_code` 参数
- [x] 3.2 修改 `src/common/permission/mcp_tools.py`：`resolve_region` 函数新增 `city`/`province`（可选，中文名）参数
- [x] 3.3 修改 `src/common/permission/mcp_tools.py`：更新 `resolve_region` 调用 MCP 工具的参数构造

## 4. Python 侧行政区逻辑修改

- [x] 4.1 修改 `src/common/permission/region.py`：删除 `infer_region_level_from_code` 函数
- [x] 4.2 修改 `src/common/permission/region.py`：删除 `infer_parent_codes` 函数
- [x] 4.3 修改 `src/common/permission/region.py`：`_normalize_profile` 删除 `code` 相关逻辑
- [x] 4.4 修改 `src/common/permission/region.py`：`_region_from_request` 删除 `code` 相关逻辑
- [x] 4.5 修改 `src/common/permission/region.py`：`_same_province` 改为基于 `province_name` 比较
- [x] 4.6 修改 `src/common/permission/region.py`：`_same_city` 改为基于 `city_name` 比较
- [x] 4.7 修改 `src/common/permission/region.py`：`get_default_district_for_city` 删除 `code` 字段
- [x] 4.8 修改 `src/common/permission/region.py`：`_frequent_or_default_district` 删除 `code` 字段
- [x] 4.9 修改 `src/common/permission/region.py`：`get_replacement_region` 删除 `code` 相关逻辑

## 5. Python 侧规则引擎修改

- [x] 5.1 修改 `src/common/permission/engine.py`：删除 `_build_user_region_dict` 中的 `code`
- [x] 5.2 修改 `src/common/permission/engine.py`：删除 `_build_requested_region_dict` 中的 `code`
- [x] 5.3 修改 `src/common/permission/engine.py`：更新 `_extract_requested_region` 兼容新返回格式

## 6. Python 侧中间件修改

- [x] 6.1 修改 `src/common/middleware/permission_classify_middleware.py`：`_resolve_requested_region` 删除 `province_code`/`city_code` 提取
- [x] 6.2 修改 `src/common/middleware/permission_classify_middleware.py`：`_resolve_requested_region` 提取 `province_name`/`city_name`
- [x] 6.3 修改 `src/common/middleware/permission_classify_middleware.py`：`_resolve_requested_region` 调用 `resolve_region` 时传入 `city`/`province` 中文名
- [x] 6.4 修改 `src/common/middleware/permission_classify_middleware.py`：删除 `_extract_user_bound_region_name` 中 `code` 相关逻辑（如有）

## 7. 测试更新

- [x] 7.1 更新 `tests/unit_tests/test_permission_region.py`：删除 `code` 相关测试用例
- [x] 7.2 更新 `tests/unit_tests/test_permission_region.py`：新增基于 `name` 的比较测试
- [x] 7.3 更新 `tests/unit_tests/test_permission_engine.py`：删除 `code` 相关测试用例
- [x] 7.4 更新 `tests/unit_tests/test_permission_engine.py`：适配新返回格式
- [x] 7.5 运行 `pytest tests/unit_tests/test_permission_*.py` 验证所有测试通过

## 8. 回归验证

- [x] 8.1 验证"郑州市金水区"能正确解析为金水区
- [x] 8.2 验证"商丘市用户查询金水区"能正确返回郑州市金水区（消歧）
- [x] 8.3 验证"朝阳区"按省份消歧（北京 vs 长春）
- [x] 8.4 验证权限规则引擎的正确性（放行/拒绝/替换）
- [x] 8.5 运行 `ruff check src/common/permission/` 确认无 lint 错误
