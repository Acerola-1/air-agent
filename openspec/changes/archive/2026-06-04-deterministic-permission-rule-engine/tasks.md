## 1. 准备与基线确认

- [x] 1.1 确认当前权限链路入口：`src/common/middleware/permission_classify_middleware.py`
- [x] 1.2 确认当前状态字段：`permission_need`、`permission_result`、`permission_query_overrides`、`user_id`
- [x] 1.3 确认两个 MCP 工具在 Python 侧的工具名：`get_user_profile`、`resolve_region_scope`
- [ ] 1.4 确认 `get_user_profile` 返回结构包含 `found`、`bound_region`、`city`、`province`、`frequent_regions`
- [ ] 1.5 确认 `resolve_region_scope` 返回结构包含 `found`、`ambiguous`、`region`、`city`、`province`、`candidates`
- [ ] 1.6 梳理当前 `permission_region.py` 中行政区逻辑和时间逻辑边界，标记可复用函数
- [ ] 1.7 建立本次变更的灰度配置名：`USE_DETERMINISTIC_PERMISSION_ENGINE`
- [ ] 1.8 建立本次变更的安全降级配置名：`PERMISSION_FAIL_CLOSED`

## 2. 新增 slots 抽取模型

- [ ] 2.1 新建 `src/common/permission_slots.py`
- [ ] 2.2 定义 `PermissionExtractedSlots` Pydantic 模型
- [x] 2.3 字段包含：`region_text`、`target_level`、`time_granularity`、`original_time_span`、`data_type`、`metric_names`
- [ ] 2.4 为 `time_granularity` 设置默认值 `other`
- [ ] 2.5 为 `data_type` 设置默认值 `region`
- [x] 2.6 增加 `normalize_time_granularity(value: str | None) -> str`
- [x] 2.7 增加 `normalize_target_level(value: str | None) -> str | None`
- [ ] 2.8 增加模型校验：`original_time_span` 只能为空或两个元素
- [x] 2.9 增加模型校验：禁止额外字段污染权限判定，可设置 `extra="ignore"` 或显式忽略 PermissionResult 字段
- [x] 2.10 编写 `PERMISSION_SLOT_EXTRACTION_PROMPT`，明确要求 LLM 不得做权限判定

## 3. 新增 PermissionFacts 模型

- [ ] 3.1 新建 `src/common/permission_facts.py`
- [ ] 3.2 定义 `TimeGranularity` Literal：`hourly/daily_count/daily/week/month/month_count/year/year_count/other`
- [ ] 3.3 定义 `RegionLevel` Literal：`province/city/district`
- [ ] 3.4 定义 `PermissionFacts` Pydantic 模型
- [x] 3.5 字段包含：`question`、`user_id`、`beijing_time`、`user_profile`、`requested_region`、`original_time_span`、`time_granularity`、`data_type`
- [x] 3.6 增加可选字段：`extraction_confidence`、`raw_slots`、`raw_context`
- [x] 3.7 增加 `model_config`，允许 MCP 原始 dict 输入但禁止无意义字段参与判定
- [x] 3.8 增加 `build_permission_facts(...)` 辅助函数，统一从 slots、MCP 结果和问题构造 facts
- [x] 3.9 编写 `tests/unit_tests/test_permission_facts.py`
- [x] 3.10 测试默认粒度、缺失行政区、非法时间范围、原始 MCP dict 透传

## 4. 拆分或整理时间规则函数

- [ ] 4.1 新建 `src/common/permission_time.py`，或明确保留在 `permission_region.py` 中并补充 TODO
- [x] 4.2 迁移/复用 `calculate_exemption_window()`
- [x] 4.3 迁移/复用 `calculate_time_intersection()`
- [x] 4.4 迁移/复用 `truncate_time_span()`
- [x] 4.5 迁移/复用 `is_scope_excluded()`
- [x] 4.6 迁移/复用 `_parse_date_str()`，改名为公开或内部函数时保持类型注解
- [ ] 4.7 确认 `hourly` 和 `daily_count` 的窗口格式精确到秒
- [ ] 4.8 确认 `daily/week/other` 的窗口格式精确到天
- [ ] 4.9 确认 `month/month_count` 为当前月和上一个自然月
- [ ] 4.10 确认 `year/year_count` 为当前年和上一自然年
- [x] 4.11 编写或迁移 `tests/unit_tests/test_permission_time.py`
- [x] 4.12 覆盖 full_window、partial、none、非法时间、跨格式日期

## 5. 实现确定性规则引擎

- [ ] 5.1 新建 `src/common/permission_engine.py`
- [x] 5.2 定义常量 `RULE_VERSION = "V2.3"`
- [x] 5.3 定义配置常量 `PERMISSION_FAIL_CLOSED = True`
- [x] 5.4 实现公开函数 `check_permission(facts: PermissionFacts) -> PermissionResult`
- [x] 5.5 实现 `_parse_beijing_time(value: str) -> datetime`
- [x] 5.6 实现 `_extract_requested_region(requested_region: dict | None) -> dict | None`
- [x] 5.7 处理 `requested_region.found=false` 场景
- [x] 5.8 处理 `requested_region.ambiguous=true` 场景
- [x] 5.9 实现 `_allow(...) -> PermissionResult`
- [x] 5.10 实现 `_reject(...) -> PermissionResult`
- [x] 5.11 实现 `_build_time_truncation_result(...) -> PermissionResult`
- [x] 5.12 实现 `_build_region_replacement_result(...) -> PermissionResult`
- [x] 5.13 实现 `_build_time_correction_text(original, legal) -> str`
- [x] 5.14 实现 `_build_region_correction_text(requested, replacement) -> str`
- [x] 5.15 在 `check_permission()` 中先校验用户画像可用性
- [x] 5.16 在 `check_permission()` 中判断适用范围排除项
- [x] 5.17 在 `check_permission()` 中计算豁免窗口
- [x] 5.18 在 `check_permission()` 中判断时间交集
- [ ] 5.19 `full_window` 时返回 `permitted=True`、`exemption=full_window`
- [ ] 5.20 `partial` 时返回 `permitted=False`、`fix_strategy=truncate_time`、`legal_time_span`
- [ ] 5.21 `none` 时调用 `build_permission_region_context()`
- [ ] 5.22 行政区权限通过时返回 `permitted=True`、`exemption=normal`
- [ ] 5.23 行政区权限不通过且有 replacement 时返回 `fix_strategy=replace_region`
- [ ] 5.24 行政区权限不通过且无 replacement 时返回硬拒绝
- [ ] 5.25 所有结果写入 `original_time_span`、`time_granularity`、`data_type`、`rule_version`
- [ ] 5.26 所有分支写入明确 `reason`
- [ ] 5.27 所有修正分支写入用户可读 `correction_text`

## 6. 改造 PermissionClassifyMiddleware 主流程

- [ ] 6.1 保留 `_latest_human_content()`、`_permission_context()`、`_rejection_message()`、`_build_query_overrides()`
- [ ] 6.2 保留 `classify_permission_need()` 预分类
- [x] 6.3 将 `_ensure_permission_agent()` 改造为 `_ensure_slot_extractor()`，或新增并灰度保留旧方法
- [ ] 6.4 slot extractor 的 `response_format` 改为 `PermissionExtractedSlots`
- [ ] 6.5 slot extractor 的 system prompt 改为 `PERMISSION_SLOT_EXTRACTION_PROMPT`
- [ ] 6.6 移除 slot extractor 的 `CodeInterpreterMiddleware` 依赖，除非仍需要模型做日期归一化
- [ ] 6.7 `_prefetch_deterministic_context()` 中保留 `get_beijing_time` 和 `get_user_profile`
- [ ] 6.8 调整 `_prefetch_deterministic_context()`：不要在 slots 抽取前直接用完整问题调用 `resolve_region_scope`
- [ ] 6.9 新增 `_resolve_requested_region(slots, user_profile, latest_question)`
- [ ] 6.10 `_resolve_requested_region()` 优先使用 `slots.region_text`
- [ ] 6.11 `_resolve_requested_region()` 传入 `contextCityCode` 和 `contextProvinceCode`
- [ ] 6.12 `_resolve_requested_region()` 传入 `targetLevel=slots.target_level`
- [ ] 6.13 slots 缺少 `region_text` 时，按配置决定是否用完整问题兜底调用 `resolve_region_scope`
- [ ] 6.14 在 `abefore_agent` 中构造 `PermissionFacts`
- [ ] 6.15 在 `abefore_agent` 中调用 `check_permission(facts)` 获取 `PermissionResult`
- [ ] 6.16 将规则引擎结果序列化写入 `update["permission_result"]`
- [ ] 6.17 调用 `_build_query_overrides()` 写入 `permission_query_overrides`
- [x] 6.18 日志记录 `permitted`、`fix_strategy`、`exemption`、`rule_version`
- [x] 6.19 保留旧权限 Agent 路径作为灰度回滚分支
- [x] 6.20 新增配置判断：`USE_DETERMINISTIC_PERMISSION_ENGINE` 为 false 时走旧路径

## 7. MCP 工具调用策略调整

- [x] 7.1 确认 profile 工具选择名仍为 `get_user_profile`
- [x] 7.2 确认 region 工具选择名仍为 `resolve_region_scope`
- [ ] 7.3 将文档中的 `get_permission_user_profile` 统一说明为历史名称，不作为 Python 侧查找名
- [ ] 7.4 `get_user_profile` 调用失败时记录 warning，并按 fail-closed/fail-open 配置处理
- [x] 7.5 `resolve_region_scope` 调用失败时记录 warning，并构造 `requested_region=None`
- [ ] 7.6 `resolve_region_scope` 返回 ambiguous 时保留 candidates 进入 facts
- [x] 7.7 不再把两个 MCP 工具注入权限判定 Agent 供其自主决策
- [x] 7.8 中间件主动调用 MCP 工具的结果应进入日志调试上下文，但避免输出敏感信息给用户

## 8. PermissionResult 协议兼容

- [ ] 8.1 保持 `PermissionResult` 现有必需字段不变
- [ ] 8.2 确认 `fix_strategy` 支持空字符串、`truncate_time`、`replace_region`、`reject`
- [x] 8.3 确认 `_build_query_overrides()` 对 `truncate_time` 和 `replace_region` 仍兼容
- [x] 8.4 可选新增 `matched_rules: list[str]` 字段，默认空列表
- [x] 8.5 可选新增 `debug_context: dict | None` 字段，默认 None
- [ ] 8.6 如新增字段，更新 `tests/unit_tests/test_permission_result.py`

## 9. 单元测试：规则引擎

- [ ] 9.1 新建 `tests/unit_tests/test_permission_engine.py`
- [x] 9.2 构造省级用户画像 fixture：广东省
- [x] 9.3 构造地市用户画像 fixture：宁波市
- [x] 9.4 构造区县用户画像 fixture：杭州市西湖区
- [x] 9.5 构造省级 requested_region fixture
- [x] 9.6 构造地市 requested_region fixture
- [x] 9.7 构造区县 requested_region fixture
- [ ] 9.8 测试最近 7 天 full_window 放行
- [ ] 9.9 测试近 30 天 partial 截断
- [ ] 9.10 测试 2024 年 none 进入行政区权限
- [x] 9.11 测试省级用户查本省区县替换为所属地市
- [x] 9.12 测试省级用户查外省区县替换为所属省份
- [x] 9.13 测试地市用户查本市区县放行
- [x] 9.14 测试地市用户查本省其他市区县替换为本市默认区县
- [x] 9.15 测试地市用户查外省城市替换为本市
- [x] 9.16 测试区县用户查省级替换为自身区县
- [x] 9.17 测试区县用户查同市其他区县放行
- [x] 9.18 测试区县用户查外市替换为自身区县
- [ ] 9.19 测试 month/month_count 近两月豁免
- [ ] 9.20 测试 year/year_count 近两年豁免
- [x] 9.21 测试排除项命中后不得享受豁免
- [ ] 9.22 测试 ambiguous 行政区处理
- [x] 9.23 测试 user_profile not found 处理

## 10. 单元测试：中间件

- [ ] 10.1 更新 `tests/unit_tests/test_permission_classify_middleware.py`
- [x] 10.2 no_check 场景不调用 slot extractor、不调用规则引擎
- [x] 10.3 need_check 场景调用 get_user_profile、slot extractor、resolve_region_scope、check_permission
- [x] 10.4 slot extractor 返回 region_text 时，resolve_region_scope 使用 region_text 而非完整问题
- [x] 10.5 slot extractor 返回 target_level 时，resolve_region_scope 透传 targetLevel
- [x] 10.6 get_user_profile 返回 city/province 时，resolve_region_scope 透传上下文 code
- [ ] 10.7 check_permission 返回 truncate_time 时写入 `permission_query_overrides.time_span`
- [ ] 10.8 check_permission 返回 replace_region 时写入 `permission_query_overrides.region`
- [ ] 10.9 硬拒绝时 `awrap_model_call` 短路主模型调用
- [ ] 10.10 规则引擎异常时按 `PERMISSION_FAIL_CLOSED` 策略处理
- [x] 10.11 灰度配置关闭时走旧权限 Agent 路径

## 11. 单元测试：slots 抽取

- [ ] 11.1 新建 `tests/unit_tests/test_permission_slot_extraction.py`
- [ ] 11.2 测试 slots 模型忽略 `permitted` 字段
- [ ] 11.3 测试 slots 模型忽略 `fix_strategy` 字段
- [x] 11.4 测试非法粒度归一化为 `other`
- [x] 11.5 测试 target_level 只允许 province/city/district/None
- [x] 11.6 测试 original_time_span 长度非法时报错或置空

## 12. 回归测试矩阵落地

- [ ] 12.1 从 `docs/权限豁免测试用例-自然语言问题集.md` 选择 P0 核心用例转 facts 单测
- [ ] 12.2 覆盖用例 19/21/24/26：7 天完全豁免
- [ ] 12.3 覆盖用例 31/32/35/55：部分交集截断优先
- [ ] 12.4 覆盖用例 36/39/40：近两月完全豁免
- [ ] 12.5 覆盖用例 43/45/49/50：近两年完全豁免
- [ ] 12.6 覆盖用例 14/17/18：区县用户基础权限修正
- [ ] 12.7 覆盖用例 3/6/48：省级用户查区县修正
- [ ] 12.8 覆盖用例 11/12/13/63：地市用户修正
- [ ] 12.9 覆盖用例 52/53：排除项不得豁免
- [ ] 12.10 覆盖用例 64-67：知识问答 no_check

## 13. 日志与可解释性

- [ ] 13.1 规则引擎每次输出记录 rule_version
- [ ] 13.2 日志记录 permission_need
- [ ] 13.3 日志记录 time_granularity
- [ ] 13.4 日志记录 exemption/intersection/fix_strategy
- [ ] 13.5 日志记录是否命中 scope_excluded
- [ ] 13.6 日志记录 requested_region 的 found/ambiguous/level/code
- [ ] 13.7 日志避免打印完整用户隐私信息
- [ ] 13.8 可选将 matched_rules 写入 PermissionResult 便于测试断言

## 14. 清理旧权限判定 Agent

- [ ] 14.1 确认规则引擎主路径稳定后，删除旧 `_ensure_permission_agent()` 或改名标记 deprecated
- [ ] 14.2 删除 `PERMISSION_CHECK_SYSTEM_PROMPT` 中权限判定相关内容，或保留为历史文档
- [ ] 14.3 删除权限 Agent 专用 `CodeInterpreterMiddleware` 使用
- [ ] 14.4 确认两个 MCP 工具不再注入权限判定 Agent
- [ ] 14.5 更新 OpenSpec `permission-check-subagent` 变更状态或文档说明

## 15. 文档更新

- [ ] 15.1 更新 `docs/设计文档/AI智能问数权限方案设计-260521.md` 或新增补充文档，说明实现采用代码规则引擎
- [ ] 15.2 更新 `docs/权限P0工具需求.md`，明确两个 MCP 工具仍保留且由中间件主动调用
- [ ] 15.3 更新 `docs/权限豁免测试用例-自然语言问题集.md`，标注哪些用例已自动化
- [ ] 15.4 更新 `openspec/changes/permission-check-subagent`，说明被本变更替代

## 16. 验证命令

- [x] 16.1 运行 `ruff check src/common tests/unit_tests/test_permission_*.py`
- [x] 16.2 运行 `ruff format src/common tests/unit_tests/test_permission_*.py --check`
- [ ] 16.3 运行 `basedpyright src/common/permission_engine.py src/common/permission_facts.py src/common/permission_slots.py src/common/middleware/permission_classify_middleware.py`
- [ ] 16.4 运行 `pytest tests/unit_tests/test_permission_engine.py`
- [ ] 16.5 运行 `pytest tests/unit_tests/test_permission_facts.py`
- [ ] 16.6 运行 `pytest tests/unit_tests/test_permission_classify_middleware.py`
- [ ] 16.7 运行 `pytest tests/unit_tests/test_permission_result.py tests/unit_tests/test_permission_region.py`
- [ ] 16.8 手动验证知识问答 no_check 路径
- [ ] 16.9 手动验证数据查询 full_window 路径
- [ ] 16.10 手动验证数据查询 truncate_time 路径
- [ ] 16.11 手动验证数据查询 replace_region 路径

## 17. 灰度与回滚

- [ ] 17.1 默认开启双跑日志模式，比较旧 Agent 和新规则引擎输出差异
- [ ] 17.2 收集至少 20 条真实查询差异样本
- [ ] 17.3 对差异样本补充 facts 单测
- [ ] 17.4 将主判定来源切换为规则引擎
- [ ] 17.5 保留旧 Agent 回滚开关一个版本周期
- [ ] 17.6 稳定后删除旧 Agent 判定路径

## 18. Review 修复补充

- [x] 18.1 修复规则引擎仅检查层级导致跨省/跨市越权放行的问题
- [x] 18.2 规则引擎行政区判断改为复用 `build_permission_region_context()`
- [x] 18.3 修复 `resolve_region_scope` 嵌套返回结构的解析兼容
- [x] 18.4 修复 `found=false` / `ambiguous=true` 时返回 `replace_region` 但缺少 `allowed_region` 的半失败状态
- [x] 18.5 行政区无法唯一解析时改为 `reject`，提示用户补充明确行政区
- [x] 18.6 未指定行政区且需要审查时，规则引擎返回用户默认安全区域覆盖参数
- [x] 18.7 slots 抽取任务注入当前北京时间，要求相对时间归一化为具体日期范围
- [x] 18.8 增加 `PERMISSION_REGION_FULL_QUESTION_FALLBACK` 配置，默认不把完整问题传给行政区解析工具
- [x] 18.9 为 `PermissionFacts.original_time_span` 增加长度校验
- [x] 18.10 修正 slots prompt 中“最近7天”示例，避免输出空时间范围
- [x] 18.11 避免权限中间件导入 `common.tools` 触发重型依赖，改为轻量北京时间实现
- [x] 18.12 将规则引擎单测改为真实 MCP 嵌套行政区返回结构
- [x] 18.13 补充地市用户查外省地市/区县、区县用户查外省区县等越权回归用例
- [x] 18.14 中间件单测 mock slots 抽取器，避免依赖真实 LLM
- [x] 18.15 运行 `pytest tests/unit_tests/test_permission_*.py -q`，确认权限相关测试通过
- [x] 18.16 运行 `ruff check`，确认本次权限相关代码和测试通过 lint
- [x] 18.17 权限审查开始前通过 stream writer 推送 `正在验证权限信息...` 进度消息
- [x] 18.18 兼容 MCP/LangSmith 工具返回的 content wrapper，正确解析 `output[0].text` 中的用户画像 JSON
