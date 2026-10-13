# 匿名运行04：观测实施交接

只实施本change。读取AGENTS.md、docs/handoffs/parallel-03-05.md、freeze.json，再读proposal/design/全部specs/tasks/contract-extension及本文件。源码基线9a1781e；从协调者创建的观测工作区开始，记录启动SHA/初始diff，主工作区和反馈工作区只读，不覆盖现有Langfuse提示词适配或替另一个change开发。

## 最小完整链路与完成条件

先在本项隔离服务完成一个真实主模型调用：可信预留→合法原生run→一条model observation实际usage→事件/outbox→真实Langfuse写入/回读→授权明细/聚合。随后扩辅助模型、六图、Skill/MCP、重试/恢复与价格。不能将CallbackHandler成功构造或HTTP200视为完整真实落库。

继续完成独立可做任务。真实企业MCP因材料缺失仍禁用，受控MCP可证明本域采集和隔离但需标注；Langfuse真实落库未验证对应任务不能勾选。模型/providerusage缺失则如实unknown，不更换模型规避测试。用户授权本域隔离环境和可逆修复，不授权主服务重启、共享卷清空、未验证凭据启用、push/合并/归档。

## 验收矩阵

| 规格 | 正向 | 必须反例 | 证据 |
|---|---|---|---|
| 模型关联 | 六图、主/辅助、流/wait、原生/BFF、恢复 | 伪identity、无上下文、旧run/parent跨企业 | 真实API+非owner PG，实际镜像/SDK版本 |
| usage/时间 | 实际usage、100+20=120、缓存/推理子集、单调耗时 | 重复流/结束、缺失/非法usage、工具规划TTFT不可测 | provider安全计数样本+fixture+真实回读 |
| 重试 | 1逻辑/2实际尝试、10+10+5=25 | 首失败缺usage、隐式SDK重试、撤销后外呼 | 受控失败测试及授权反例 |
| Skill/工具 | 语义/菜单/DeepAgents实际执行、MCP/local | 只候选未执行、HTTP200无结果、拒绝但外呼、双层重复 | 覆盖表+受控MCP，真实企业另留门禁 |
| Langfuse | 真实写/flush/回读、主/辅助映射 | 新/旧callback正文/票据marker泄漏、伪Trace、共享控制台绕权 | 支持API回读白名单、安全摘要，无密钥/原文 |
| 费用 | 时间T版本、Decimal、缺失小计/覆盖、币种分组 | 重叠价格、无usage/价伪零、重复计缓存/推理/多Skill、无配置权 | 确定性fixture+API/PG |
| 授权/删除 | 本人/运营/统计、RLS、隔离游标 | A/B、连接池无GUC、原文权限扩大、删除晚到/恢复/外部故障 | 真实PG/HTTP与安全删除证据 |
| 降级/性能 | 观测关闭/开启业务一致、30次同条件测量 | 图导入I/O/GraphLoadError、队列无界、flush卡答案 | 真实导出/故障+本地P50/P95开销，外部等待分列 |

使用已有uv.lock与Langfuse SDK版本，根`uv sync --all-extras`、管理目录`uv sync --extra dev`使用各自隔离venv。先受影响pytest，公共接线后复跑身份/台账负向、管理与真实集成。校验所有三份validator(src/admin/TS)和生成副本，token禁止键的计数窄例外必须有string/bool/negative/嵌套secret/browser来源反例；不能整体删除token规则。前端即使不改UI也需跑受影响TS契约，修改生成副本后必要类型/干净构建。严格`openspec validate complete-agent-observability --strict`。不得“修好”无关基线失败以显通过，也不得跳过本域安全断言。

图导出必须在当前官方镜像实测兼容，with_listeners改变对象类型曾造成GraphLoadError，不能沿用此模式而仅靠本地invoke。现有Opentelemetry在当前锁文件为1.42.1，与AGENTS旧文字1.37.0有出入；先核验实际镜像再记录，不能为了遵循旧文字擅自降级。native rs-v1仍只允许服务投影，不能为了查Trace把state/history授权打开。

交付`docs/reviews/agent-observability-acceptance.md`和flash-04安全证据，含逐spec场景矩阵、实际/替身/未知、真实落库、价格与usage来源、开销条件/样本、公共diff、配置/迁移/consumer、实际用时/修复/环境等待与工具Token或未采集。保留diff，最终“待独立验收”，不自动commit/push/合并/归档。本分支通过后仍由协调者与反馈分支组合，不能自称整个企业后台完成。
