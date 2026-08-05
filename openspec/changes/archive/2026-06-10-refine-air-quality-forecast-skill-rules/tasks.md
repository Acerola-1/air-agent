## 1. 盘点与分类

- [x] 1.1 扫描 `src/basic_qa/skills`、`src/data_analysis/skills`、`src/intelligent_analysis/skills` 中所有包含 `mcp_city_common_get_air_quality_forecast` 的 `SKILL.md` 与 `references/*.md`。
- [x] 1.2 为每个命中 Skill 建立处理清单，记录模块、Skill 名称、当前 `allowed-tools`、当前 detailed references 是否主动要求调用空气质量数值预报。
- [x] 1.3 按必需预测类、条件预测类、可选预测类、非预测类对命中 Skill 逐个分类，并在实施记录中说明分类理由。
- [x] 1.4 明确每个 Skill 的处理动作：保留并补规则、保留但改为可选、移除工具白名单、或仅修改 detailed references。

## 2. 通用 Skill 文案规则

- [x] 2.1 编写可复用的 Skill 级"空气质量数值预报使用边界"文案，包含监测事实优先、只补未来、不得覆盖监测统计、缺失降级四项要求。
- [x] 2.2 编写小时级当日未覆盖全天场景专用规则，包含当日、最后有效小时早于 23 时、剩余小时缺失、历史日期禁止调用、全天完整禁止调用。
- [x] 2.3 编写非预测类 Skill 的默认规则，明确默认不调用空气质量数值预报，且不主动增加未来预测板块。

## 3. basic_qa Skill 修改

- [x] 3.1 修改 `air-quality-basic-query` 的 fast/expert detailed references，将空气质量数值预报改为当日实时且主数据未覆盖未来窗口时的条件补充，历史日期和完整当日数据不主动调用。
- [x] 3.2 修改 `trend-analysis` 的 fast/expert detailed references，保留未来趋势补充能力，但限制为用户请求包含当日/未来趋势或趋势区间延伸时使用，不覆盖历史序列统计。
- [x] 3.3 修改 `compliance-feasibility` 的 fast/expert detailed references，保留空气质量数值预报用于未来达标风险研判，并明确不得替代达标预测主工具和已监测累计事实。

## 4. data_analysis Skill 修改

- [x] 4.1 修改 `broadcast-hour` 的 expert detailed references，落实当日未覆盖全天才调用空气质量数值预报、预报只补剩余小时/未来 0-12 小时、不得覆盖已监测事实。
- [x] 4.2 修改 `broadcast-hour` 的 fast detailed references，默认不主动输出预测；仅用户明确追问后续风险且当日未覆盖全天时可补充空气质量数值预报。
- [x] 4.3 审查并修改目标控制/达标相关 Skill（如 `aqi-attainment-control`、`today-target-analysis`、`city-assessment-target-control`、`air-quality-calculator`），保留必要预测能力并补齐边界和缺失降级。
- [x] 4.4 审查并修改趋势/对比相关 Skill（如 `single-indicator-comparison`、`multi-indicator-comparison`、`rolling-24h-average-comparison`、`concentration-ratio`、`pollutant-correlation`），按业务需要改为条件或可选预测，不覆盖历史对比统计。
- [x] 4.5 审查并修改排名/日历/等级/空间/箱线图等非预测类 Skill，移除 `mcp_city_common_get_air_quality_forecast` 或明确默认不调用并不输出未来预测板块。
- [x] 4.6 确保每个被保留空气质量数值预报工具的 data_analysis Skill 在 fast/expert references 中都有分类一致的调用条件和输出降级口径。

## 5. intelligent_analysis Skill 修改

- [x] 5.1 修改 `broadcast-hour` 的 fast/expert detailed references，采用与 data_analysis 小时播报一致的当日未覆盖全天条件。
- [x] 5.2 修改 `integrated-index-ratio` 的 fast/expert detailed references，判断是否属于可选预测类；如保留工具，明确仅用户追问未来风险时使用，不影响综合指数占比事实分析。

## 6. 测试与验证

- [x] 6.1 新增或更新 Skill 内容静态测试，扫描所有包含 `mcp_city_common_get_air_quality_forecast` 的 Skill，并校验 detailed references 中存在调用条件、监测事实优先、缺失降级等规则。
- [x] 6.2 新增小时播报专项测试，验证 data_analysis 与 intelligent_analysis 的小时播报专家规则包含当日、未覆盖全天、不得覆盖已监测事实、历史日期禁止主动调用等约束。
- [x] 6.3 新增非预测类 Skill 测试或断言，验证被判定为非预测类的 Skill 不再主动要求独立调用空气质量数值预报。
- [x] 6.4 运行针对性测试：`pytest tests/unit_tests/test_skill_prompt_rules.py tests/unit_tests/test_data_analysis_menu_skill_discovery.py`，并根据新增测试文件补充运行对应测试。
- [x] 6.5 使用 `rg` 复查所有空气质量数值预报相关文案，确认没有遗留"无条件独立调用空气质量数值预报"的描述。
