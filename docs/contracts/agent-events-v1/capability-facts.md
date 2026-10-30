# Basic QA 会话能力事实扩展

2026-10-28新增`runtime.capability`，保持v1信封和现有OpenAPI通用事件结构。事件只允许runtime来源；普通事件不接收问题、规则、参数、结果正文、地址或凭据。历史freeze.json保留当时摘要，不覆盖为此次扩展的验收结果。

能力类型为`skill`／`mcp_tool`，动作分别为`searched`、`selected`、`loaded`、`activated`、`reused`、`executed`、`load_failed`。记录已确认的稳定ID、材料／schema版本、来源、候选ID及已采集的embedding计数，不用零代替未知。观测ID按企业、attempt、动作、能力及实际调用引用去重。

- search只记录候选，不制造load或执行；直接load不制造matched。
- 同版本幂等命中记录reused；休眠重新激活记录activated；跨轮实际使用不制造新loaded。
- 一个工具有多个来源时只记录一条工具执行及真实来源，不复制工具成本；实际Skill来源各有阶段事实。独立工具不伪造所属Skill。
- 只有受保护工厂确认返回的业务调用才产生本扩展的executed事实。发送前拒绝不记executed；传输异常的物理尝试和不确定结果继续由原`runtime.tool_call`记录，不推断服务已成功执行。
- 加载后取消没有业务执行事实。模型规划正文、接口没有时间／城市回显或返回文本均不会受到本扩展的回答校验。

字段、动作与类型以event-registry.json的`capability_fact`为源，Agent、admin、TS读取同一规则；生成副本和合法／非法样本已同步。OpenAPI仍为原通用事件结构，没有新增API或正文SSE字段。共享样本、来源反例、台账API和OpenAPI快照分别验证；契约通过不能代替产品整链验收。
