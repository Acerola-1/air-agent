## 1. 槽位与工具合约

- [x] 1.1 扩展 `PermissionExtractedSlots`，新增 `data_scope`、`station_names`、`station_types` 字段；复用现有 `regions: list[PermissionRegionRequest]` 和 `region_mode`，并保持 LLM 不能输出权限判定字段的约束。
- [x] 1.2 更新权限抽取提示词，明确站点类型列表、站点名称抽取规则、多城市保留规则，以及“站点类型不是权限类型”。
- [x] 1.3 新增站点槽位清洗和校验逻辑，过滤空字符串、`None/null` 字符串、重复站点名称和重复站点类型；行政区去重继续交由现有区域计划和区域结果聚合逻辑处理。
- [x] 1.4 在 `common.permission.mcp_tools` 中封装站点归属解析工具调用，工具入参使用 `region`、`station_names`、`station_types` 自然语言字段。
- [x] 1.5 定义站点归属工具返回值归一化逻辑，将工具结果统一为 `found`、`stations[]`、`station_name`、`station_type`、`region.name/level/city/province` 结构。

## 2. 权限事实构造

- [x] 2.1 扩展 `PermissionFacts`，增加站点查询事实字段，用于承载候选站点、不可用站点、原始站点筛选条件、来源 `RegionRequestItem` 摘要和原始区域计划模式。
- [x] 2.2 在权限中间件中识别 `data_scope=station` 的查询，并在 `build_region_request_plan()`、行政区解析或自动补全完成后、构造 `PermissionFacts` 前执行站点归属解析。
- [x] 2.3 对单区域站点列表查询调用一次站点归属工具，支持 `station_names=[]` 且 `station_types` 非空的列表解析场景。
- [x] 2.4 对具体站点查询调用站点归属工具，传入用户抽取的站点名称和所属区域。
- [x] 2.5 对多区域站点查询复用 `RegionRequestPlan.items` 按区域拆分多次调用站点归属工具，并合并返回站点集合。
- [x] 2.6 当站点归属缺少 `region.name`、`region.level`、`region.city` 或 `region.province` 时，将该站点标记为不可用，不使用查询区域兜底。
- [x] 2.7 对 `collection` 区域计划先展开下辖子区域，再按展开后的子区域逐项调用站点归属工具；无法展开时不得退回父级汇总站点查询。
- [x] 2.8 对 `auto_fill` 区域计划使用用户绑定行政区作为可信默认区域调用站点归属工具，并保留自动补全结果语义。

## 3. 规则引擎

- [x] 3.1 在 `check_permission()` 中增加站点事实分支，保持用户画像校验、适用范围排除、豁免窗口计算和时间交集判断的现有顺序。
- [x] 3.2 当站点查询命中 `full_window` 时直接放行，并保留候选站点集合供后续查询使用。
- [x] 3.3 当站点查询命中 `partial` 时生成时间截断结果，并同时保留可执行站点集合。
- [x] 3.4 当站点查询无时间豁免时，将每个站点归属行政区转换为现有 `requested_region` 兼容结构，并复用 `build_permission_region_context()` 判断可访问性。
- [x] 3.5 实现多站点裁剪结果：全部可访问时放行，部分可访问时返回站点裁剪策略，全部不可访问时拒绝。
- [x] 3.6 实现站点归属缺失处理：单站点归属缺失返回“该数据暂不可用”，多站点中归属缺失站点从可执行集合剔除并写入 debug context。

## 4. 结果结构与上下文注入

- [x] 4.1 扩展 `PermissionResult`，增加可执行站点集合、候选站点总数、不可用站点数量或摘要字段。
- [x] 4.2 增加站点裁剪修正文案：“根据权限，仅展示您可访问的 {count} 个站点数据。如需查看其他区域站点，可查询最近7天数据。”
- [x] 4.3 扩展 `_build_query_overrides()`，将可执行站点集合写入 `permission_query_overrides`，并支持与 `time_span` 同时存在。
- [x] 4.4 更新权限上下文注入文案，明确后续 Skill 必须使用 `permission_query_overrides` 中的站点集合，不得按原始区域重新扩大查询范围。
- [x] 4.5 确保硬拒绝路径对站点不可用和全部站点不可访问返回用户可读文案。

## 5. 测试

- [x] 5.1 增加 `PermissionExtractedSlots` 单测，覆盖国控站列表查询、具体站点查询、多城市站点查询、站点类型过滤。
- [x] 5.2 增加站点归属工具封装单测，覆盖 found、not found、归属缺失、多站点返回、多区域合并和集合展开后多次调用。
- [x] 5.3 增加规则引擎单测，覆盖站点 full_window 放行、partial 时间截断、全部可访问、部分可访问、全部不可访问、归属缺失。
- [x] 5.4 增加权限中间件单测，验证多城市问题会复用 `RegionRequestPlan` 多次调用站点归属工具，集合模式会先展开子区域再解析站点，并将合并后的站点事实传给规则引擎。
- [x] 5.5 增加 `permission_query_overrides` 单测，验证站点集合、时间截断和修正文案正确注入主模型上下文。
- [x] 5.6 运行目标测试：`pytest tests/unit_tests/test_permission_slot_extraction.py tests/unit_tests/test_permission_facts.py tests/unit_tests/test_permission_engine.py tests/unit_tests/test_permission_classify_middleware.py tests/unit_tests/test_permission_result.py`。
