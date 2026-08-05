## ADDED Requirements

### Requirement: 空气质量数值预报 Skill 内容边界
涉及空气质量数值预报的 Skill SHALL 在自身 detailed references 中声明该预报数据的业务适用性、调用条件、禁止条件、使用边界和缺失降级口径。该约束 MUST 落实在 Skill 内容文件中，不得仅依赖系统提示词、工具描述或运行时中间件。

#### Scenario: Skill 内容包含预报边界
- **WHEN** 某个 Skill 的 `allowed-tools` 包含 `mcp_city_common_get_air_quality_forecast`
- **THEN** 该 Skill 对应的 `references/fast.md` 或 `references/expert.md` SHALL 明确说明空气质量数值预报何时可用、何时不得使用、如何与监测数据区分
- **AND** 不得只写“独立调用空气质量数值预报”而缺少业务触发条件

#### Scenario: 系统提示词不是主要约束来源
- **WHEN** 实施空气质量数值预报规则收敛
- **THEN** 修改 SHALL 优先落到具体 Skill 的 `SKILL.md` 和 `references/*.md`
- **AND** 公共系统提示词 SHALL NOT 作为控制空气质量数值预报调用条件的主要实现方式

### Requirement: Skill 工具白名单与详细规则一致
Skill 的 `allowed-tools` SHALL 与 detailed references 中的业务流程保持一致。若 detailed references 不允许或不需要主动使用空气质量数值预报，则对应 Skill SHOULD 移除 `mcp_city_common_get_air_quality_forecast`；若保留该工具，则 MUST 写明可选触发条件。

#### Scenario: 非预测 Skill 不保留无边界预报工具
- **WHEN** 某个 Skill 被判定为非预测类
- **THEN** 该 Skill SHALL 移除 `mcp_city_common_get_air_quality_forecast` 或在 detailed references 中声明“默认不调用，仅用户明确询问未来空气质量风险时使用”
- **AND** 最终回答结构 SHALL 不主动增加未来预测板块

#### Scenario: 预测 Skill 保留工具并写明边界
- **WHEN** 某个 Skill 被判定为必需预测类、条件预测类或可选预测类
- **THEN** 该 Skill 可以保留 `mcp_city_common_get_air_quality_forecast`
- **AND** detailed references MUST 写明预报数据不得覆盖已监测事实，且缺失时如何降级输出
