# basic QA 固定题库离线试检报告

执行时间：2026-10-20T05:55:49.553579+00:00；数据集：bqa-reference-202605-v1。

实际检查 253 项：通过 246，失败 7；40 题，9 个 Skill。

范围：实际意图规则、fast/expert 规则装载、题库结构及独立数值重算。网络被禁止，未连接业务库。不是模型实际回答/语义路由/并发性能验收。

## 失败项

| 题号 | 检查 | 预期 | 实际 |
| --- | --- | --- | --- |
| BQA-001 | intent | knowledge | data_query |
| BQA-002 | intent | knowledge | data_query |
| BQA-004 | intent | knowledge | data_query |
| BQA-005 | intent | knowledge | data_query |
| BQA-006 | intent | knowledge | data_query |
| BQA-007 | intent | knowledge | data_query |
| BQA-009 | intent | knowledge | data_query |

原始结果及源码摘要：[checks.json](checks.json)。

未执行：real-embedding-routing、real-model-answers、MCP-tool-execution、IAM/PG/BFF integration、browser、concurrent-performance。
