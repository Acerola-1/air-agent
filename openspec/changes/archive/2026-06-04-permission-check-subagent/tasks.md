## 1. 基础模型与状态定义

- [x] 1.1 创建 `src/common/permission_result.py`，定义 `AllowedRegion` 和 `PermissionResult` Pydantic BaseModel（含 data_type/accessible_station_count/accessible_grid_count 字段以支持站点/网格）
- [x] 1.2 扩展 AgentState，在 `src/common/skill_discovery.py` 的 `SkillDiscoveryState` 中新增 `permission_need: NotRequired[str]` 字段（取值 no_check/need_check/uncertain）
- [x] 1.3 在 `SkillDiscoveryState` 中新增 `permission_result: NotRequired[dict]` 字段，用于中间件写入权限审查结果，供主模型和 Skill 读取
- [x] 1.4 在 `SkillDiscoveryState` 中新增 `user_id: NotRequired[str]` 字段，中间件从 configurable 获取后写入 state
- [x] 1.5 在 `SkillDiscoveryState` 中新增 `permission_query_overrides: NotRequired[dict]` 字段，用于传递可修正越权场景的强制查询覆盖参数

## 2. 权限审查预编译 Agent 定义

中间件按 MCP 工具签名懒创建并复用一个轻量权限审查 agent，用于 LLM 语义提取（时间粒度推断、行政区名称识别、适用范围排除项判断）。

- [x] 2.1 在 `src/common/middleware/permission_classify_middleware.py` 中通过 `_ensure_permission_agent()` 懒创建权限审查 agent：
  - `model`: `ModelRegistry.mimo_v2_5_pro`（与主模型一致，规则遵循可靠）
  - `tools`: 动态注入 MCP 工具（`get_user_profile`、`resolve_region_scope`，如果可用）；`get_beijing_time` 和 `permission_region_context_tool` 已下沉到中间件
  - `system_prompt`: 权限审查提示词（引导 agent 按步骤获取用户画像、解析行政区、推断时间粒度、计算豁免窗口、判定权限、生成修正策略和文案）
  - `response_format`: `PermissionResult`（Pydantic BaseModel，保证结构化输出）
  - `middleware`: `[CodeInterpreterMiddleware(ptc=[])]`（日期计算辅助）
  - 编译结果存为 `self._permission_agent`，工具签名变化时重建
- [x] 2.2 MCP 工具动态注入：`_ensure_permission_agent()` 中从 `mcp_client.get_profile_mcp_tools()` 和 `mcp_client.get_region_mcp_tools()` 获取可用工具，有则加入 `tools` 列表，无则跳过

## 3. PermissionClassifyMiddleware 重写

中间件在 `abefore_agent` 中完成规则预分类，按需调用预编译 agent，将 PermissionResult 写入 state 并注入 system prompt。

- [x] 3.1 重写 `src/common/middleware/permission_classify_middleware.py`，移除旧逻辑（`_safe_user_profile`、`_permission_context` 中的 SubAgent 触发引导）
- [x] 3.2 `abefore_agent` 从 `config["configurable"]` 中提取 `user_id`，写入 `state["user_id"]`
- [x] 3.3 `abefore_agent` 调用 `classify_permission_need` 做规则预分类，写入 `state["permission_need"]`
- [x] 3.4 当 `permission_need` 为 `no_check` 时，`abefore_agent` 快速返回，不调用权限审查 agent
- [x] 3.5 当 `permission_need` 为 `need_check` 或 `uncertain` 时，`abefore_agent` 预取确定性上下文、构造任务描述并调用权限审查 agent：
  - 构造 `HumanMessage(content=任务描述)`，包含：用户原始问题、user_id
  - 注入 `<prefetched_context>`，包含北京时间、用户权限画像、行政区解析结果和行政区权限上下文
  - 调用 `await self._permission_agent.ainvoke({"messages": [human_msg]}, config)`，config 从 `runtime` / `config` 获取，确保 `configurable` 传递
  - 从 agent 返回结果中提取 `structured_response`（即 PermissionResult）或 AIMessage 中的结构化输出
  - 序列化为 dict 写入 `state["permission_result"]`
  - `fix_strategy=replace_region/truncate_time` 时，写入 `state["permission_query_overrides"]`
- [x] 3.6 agent 调用失败时的异常处理：
  - `ainvoke` 超时或抛异常：降级为 `permission_need=no_check`（宽松降级，不阻断用户查询）
  - `structured_response` 为空或格式错误：降级为 `permission_need=no_check`
  - 记录异常日志，后续迭代可改为严格降级（`permitted=False`）
- [x] 3.7 `wrap_model_call` / `awrap_model_call` 注入权限上下文到 system prompt：
  - `permission_need=no_check`：不注入任何权限上下文
  - `permission_need=need_check/uncertain` 且有 `permission_result`：注入 `<permission_context>` 包含 PermissionResult 摘要和 `permission_query_overrides`，引导主模型按强制覆盖参数调整后续查询参数
  - `permitted=false` 且 `fix_strategy=reject` 或无可用修正策略时，短路主模型调用并直接返回拒绝说明
- [x] 3.8 更新 `src/common/middleware/__init__.py` 导出（如有变更）

## 4. 时间豁免窗口确定性计算

虽然权限审查 agent 使用 LLM 推理时间粒度和时间范围，但确定性 Python 函数提供精确的豁免窗口计算和日期逻辑，中间件预取 `get_beijing_time` 结果，agent 提示词引导其使用 CodeInterpreter 做日期计算，确定性函数作为验证基准和单测对照。

- [x] 4.1 在 `src/common/permission_region.py` 中新增豁免窗口计算函数 `calculate_exemption_window(granularity, beijing_time) -> list[str]`
- [x] 4.2 在 `src/common/permission_region.py` 中新增时间交集判断函数 `calculate_time_intersection(original_span, exemption_window, granularity) -> str`（返回 `full_window` / `partial` / `none`）
- [x] 4.3 在 `src/common/permission_region.py` 中新增时间截断函数 `truncate_time_span(original_span, exemption_window) -> list[str]`
- [x] 4.4 在 `src/common/permission_region.py` 中新增适用范围排除项判断函数 `is_scope_excluded(time_span, granularity, beijing_time) -> bool`
- [x] 4.5 编写单测覆盖各类粒度的豁免窗口计算、时间交集判断、截断和排除项

## 5. 权限审查提示词更新

原 `src/basic_qa/subagents/permission_check_prompt.py` 的提示词需适配中间件调用方式：agent 从任务描述中获取 user_id 和 `<prefetched_context>`，优先消费中间件预取结果。

- [x] 5.1 将提示词从 `src/basic_qa/subagents/permission_check_prompt.py` 移至 `src/common/middleware/permission_check_prompt.py`（与中间件同属 common 层）
- [x] 5.2 更新提示词执行流程：
  - 步骤 1：读取 `<prefetched_context>` 中的 `beijing_time` 获取当前北京时间
  - 步骤 2：读取 `<prefetched_context>` 中的 `user_profile` 获取用户权限画像
  - 步骤 3：读取 `<prefetched_context>` 中的 `requested_region` 获取行政区解析结果
  - 步骤 4：读取 `<prefetched_context>` 中的 `permission_region_context` 获取层级白名单、父级关系、同省/同市判断、默认安全行政区和修正目标建议
  - 步骤 5-11：推断时间粒度、解析时间范围、计算豁免窗口（必须用 CodeInterpreter）、判定权限、确定修正策略、生成文案、返回 PermissionResult
- [x] 5.3 提示词刚性约束保留：
  - 所有日期计算必须用 CodeInterpreter 编写 JavaScript 执行
  - 行政区层级白名单必须严格遵循规则表
  - 行政区父子关系必须优先读取 `<prefetched_context>` 中的 `permission_region_context`
  - 命中适用范围排除项时不得享受时间豁免
  - 修正策略优先级：时间截断 > 行政区修正
  - 必须返回 PermissionResult 结构

## 6. 主模型提示词更新

- [x] 6.1 更新 `src/basic_qa/graph.py` 的 `SYSTEM_PROMPT`：移除 SubAgent 触发引导（"先调用 task(permission_check)"），改为说明权限审查结果已在 `<permission_context>` 中注入
- [x] 6.2 更新 `SYSTEM_PROMPT` 中的 PermissionResult 使用引导：主模型按 `permission_query_overrides` 调整查询参数（`truncate_time` 用 `time_span`，`replace_region` 用 `region`），并在回复中包含 `correction_text`

## 7. 移除 SubAgent 注册

- [x] 7.1 从 `src/basic_qa/graph.py` 移除 `permission_check` SubAgent 定义和 `subagents` 参数
- [x] 7.2 从 `src/basic_qa/graph.py` 移除 `_get_permission_check_tools()` 函数
- [x] 7.3 从 `src/basic_qa/graph.py` 移除 `SubAgent` import 和 `PERMISSION_CHECK_SYSTEM_PROMPT` import（提示词已移至 common）
- [x] 7.4 清理 `src/basic_qa/subagents/permission_check_prompt.py`：移至 `src/common/middleware/permission_check_prompt.py` 后删除原文件
- [x] 7.5 确认 `src/basic_qa/subagents/__init__.py` 可清理或删除

## 8. 中间件注册

- [x] 8.1 在 `src/basic_qa/graph.py` 的 `_build_middleware()` 中注册 `PermissionClassifyMiddleware`，位于 `SkillToolRegistryMiddleware` 之前

## 9. 验证与测试

- [x] 9.1 编写 `tests/unit_tests/test_permission_result.py`，验证 PermissionResult Pydantic 模型的序列化/反序列化
- [x] 9.2 编写 `tests/unit_tests/test_permission_classify_middleware.py`，验证规则预分类逻辑和 State 写入
- [x] 9.3 重写 `tests/unit_tests/test_permission_classify_middleware.py`，覆盖新中间件流程：
  - 预分类为 no_check 时不调用预编译 agent
  - 预分类为 need_check/uncertain 时调用预编译 agent（mock `ainvoke`）
  - agent 返回 PermissionResult 后正确写入 state
  - agent 调用异常时降级为 no_check
  - `wrap_model_call` 注入权限上下文的条件判断
- [x] 9.4 编写 `tests/unit_tests/test_permission_region.py` 新增用例：豁免窗口计算、时间交集、截断、排除项
- [ ] 9.5 手动验证：启动 basic-qa graph，发送知识问答类问题（permission_need=no_check，不调用权限审查 agent）
- [ ] 9.6 手动验证：发送数据查询类问题（permission_need=need_check，中间件预取上下文并调用权限审查 agent，PermissionResult 写入 state）
- [x] 9.7 运行 `ruff check` 和 `basedpyright --level error` 确保代码质量

## 10. 设计文档对齐

- [x] 10.1 更新 `openspec/changes/permission-check-subagent/design.md`：D3 决策改为中间件预取确定性上下文 + `create_agent` 懒创建方式，说明 `abefore_agent` 中按需 `ainvoke`
- [x] 10.2 更新 `openspec/changes/permission-check-subagent/specs/permission-classify-middleware/spec.md`：描述中间件预取上下文、懒创建 agent 和调用流程

## 11. 后续工具依赖接入计划

- [x] 11.1 接入标准行政区信息工具 `resolve_region_scope`：MCP 工具已上线，注册到 `mcp_client.py` 的 `get_region_tools()`
- [x] 11.2 接入用户权限画像工具 `get_user_profile`：MCP 工具已上线，注册到 `mcp_client.py` 的 `get_profile_tools()`
- [ ] 11.3 接入站点归属工具 `resolve_station_scope`
- [ ] 11.4 接入网格归属工具 `resolve_grid_scope`
- [ ] 11.5 将 `permission_region_context_tool` 从本地静态 fallback 升级为聚合工具
- [ ] 11.6 补充站点/网格权限单测与集成验证
