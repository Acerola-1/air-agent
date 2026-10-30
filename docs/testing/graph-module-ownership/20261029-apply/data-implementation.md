# 数据分析图结构归位记录

本次仅整理现有封存数据分析图的代码归属；保留公开入口、菜单驱动的 Skill、工具循环及终答逻辑，不恢复该图的专项开发。`prompt_builder.py` 改为 `prompts.py`，原公共数据分析提示组装按原顺序迁入；原先未参与组装的 `DATA_ANALYSIS_OUTPUT_GUARD` 未被启用。目录菜单匹配保持原有大小写敏感精确匹配；运行节点与受理显式选择沿用 `match_menu_skill` 的大小写归一，映射故障不得被吞掉。

数据分析自己的 `context/` 负责历史投影、按轮摘要、水位、当前轮工具结果治理、请求组装、一次超窗恢复及状态合并。历史消息仍完整保留，摘要成功才写覆盖水位，模型超窗只重试失败的模型调用一次；初始 3→2→1 收缩行为不因归属迁移调整。本图 `tool_policy.py` 负责当前轮工具重试预算并向公共执行层传入已耗尽集合；实际外呼仍经过公共身份和业务授权。本图发送策略没有添加 Basic QA 的能力激活限制。

定向测试位于 `tests/unit_tests/data_analysis/`，涵盖原有投影、摘要、失败及取消、数据治理、有界恢复契约、目录和菜单冲突，以及用受控模型/工具替身编译图的菜单、工具后循环和终答；跨图禁止导入测试和控制器替换隔离测试检查独立性。受控模型/工具不代表真实模型、MCP 服务或原业务系统已验收，逐发送实际核权、跨版本 checkpoint 和真实系统消费链仍需主任务对应验收证据。无服务启动、部署、提交或归档。

本次定向执行：`.venv/bin/python -m pytest tests/unit_tests/data_analysis -q` 为 45 passed，耗时 6.26 秒；有一条受控图测试中的 LangGraph 配置参数类型警告。`.venv/bin/ruff check src/data_analysis tests/unit_tests/data_analysis` 和 `.venv/bin/ruff format --check src/data_analysis tests/unit_tests/data_analysis` 通过。这些结果不代替上述尚未执行的跨进程持久化、实际外呼安全及真实业务系统验收。

提示组装核对：公共函数删除前，对同一输入直接比较旧 `common.prompts.with_data_analysis_output_guard` 与新 `data_analysis.prompts.with_data_analysis_output_guard`，字符串相等；使用占位环境变量，仅导入比较，无模型或工具外呼。

2026-10-30 主代理整合：两条新增编译图用例使用本图真实模型节点、ToolNode 和真实本地合成工具，注入工具返回后超窗；仅对模型提供商、准备期 MCP 发现、终答发布和台账收尾使用明确替身。模型最多3次（初次计划＋失败＋一次恢复）、工具恰1次、摘要1次、原历史ID全部保留；二次超窗停止。结合 BQA 编译图、图装配的定向共44项通过。实际官方SDK新旧同载荷对照各为模型39／工具8（两图合计），封存图实际允许的预报MCP发生调用，成功水位与暂停样本保留；扩展跨版本恢复的未完成项仍见主报告。
