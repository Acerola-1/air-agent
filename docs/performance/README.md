# 性能与并发测试

入口：[Agent 性能与并发压力测试流程](pressure-testing-workflow.md)。标准版本：`AGENT-CONCURRENCY-v1.0`；测试范围为本机真实模型的端到端持续并发。

1. 复制 [计划模板](templates/plan.template.json)，填写机器、模型、题库、SLO、预算、授权与资源保护。
2. 按 [执行器说明](executor-handoff.md) 核验工具及协议适配。
3. 执行正常问答检查与预热、阶梯升压、相邻边界复测。
4. 按 [报告模板](templates/report.template.md) 和 [结果结构](templates/results.template.json) 保存结果。

执行器状态：Locust 产品适配器待实现与验收。具体要求见执行器说明。

## 轮次档案

每轮目录使用 `YYYYMMDD-HHMMSS-模型-目的/`，时间统一 Asia/Shanghai，按目录时间倒序登记到 [index.json](index.json)。计划创建时间和实际执行起止分开记录。

每轮保存冻结标准、plan/conditions、逐请求/资源/阶段明细、results 和 report；凭据与受控正文存放在受保护的 `.local` 目录。目录独立，已登记结果保持原样。

比较性能时核对模型与配额、机器配置、入口、题库比例、SLO、计时及观测口径。分别记录最高观察档、确认下界和相邻失败支持的最大确认并发；分位数由对应轮次的原始样本计算。
