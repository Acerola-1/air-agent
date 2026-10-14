# 匿名运行04首次独立验收

日期2026-10-14，原始输入0f2cd02及42个文件指纹见evidence/flash-04-independent/anonymous-input.json。用户确认与运行03为同一第三匿名模型。首次实际耗时**1小时23分钟/83分钟**；作者约4.5小时估计保留在原报告，不作为正式用时。

结论：**未通过，55/100**。契约15/30、隔离授权13/25、验证7/20、可维护性12/15、范围纪律8/10。实施效率3/5，应用运行性能未获有效证明。原始33专项测试通过，但独立callback-probes.json复现下列问题。

1. O01：标准LLMResult的ChatGeneration.message usage没有提取，100+20=120被记usage_missing；负数输入被舍弃后仍measured。流片usage未保存、空token触发TTFT；实际模型、role/parent/retry覆盖不完整。
2. O02：旧CallbackHandler仍原样发送input/output/metadata，未实施白名单；SDK源码明确上报正文。Trace关联按字符串查询UUID键的_runs，且只在起始捕获，映射为空并非外部网关导致。
3. O03：403没有success:false、空data及空JSON均分类成功；tool_call_id非UUID直接传UUID列，物理重试序号/耗时和并发范围不稳定，菜单/辅助模型/Skill版本未完整接入。
4. O04：父观测/submission未建立所声称复合FK；trace写入缺删除guard；观测重投仍新造事件，校验失败可以观测行提交而无事件/outbox。新projection没有消费者闭环。
5. O05：计费函数重复两份，成本精度截到6位；independent推理重复收费，缺费率当免费；重算固定revision1、无有效修订，最多500项静默截断；汇总可能累计历史修订，未知类别缺失。
6. O06：MAX_QUEUE=0导致Python无界队列，flush漏算在途/超预算，重试只实际一次；完整API/删除/恢复/6图矩阵未验。
7. O07：benchmark缺trusted context，启用组入队0，未执行观测路径，作者近零P95结论不成立。独立性能待补测，不用测试耗时评分。

正向：兼容扩展保留禁止键反例，独立表/强制RLS/价格排他约束方向正确，实际失败尝试及Skill阶段落库证据、真实Langfuse独立写回读材料存在。外部网关503、企业MCP材料不足不等同代码失败，但不能解释标准结果usage缺失或未完成的确定性测试。

用户要求停止匿名模型返工，**由Codex接管两项收尾**。后续补修不回写本首次55分，不归匿名模型成果。未commit/push/归档，原作者树不修改。

O08补充：单运行接口缺少返回序列化字段导致实际500（独立PG探针），服务无条件开启operations而路由仅需登录，有潜在同企业越权；修复序列化时必须一并收紧本人范围。原500未向浏览器返回他人明细，不误称实测泄露。
