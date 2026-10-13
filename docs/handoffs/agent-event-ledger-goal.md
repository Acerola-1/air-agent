/goal 持续完成当前 Air Agent 工作区 OpenSpec change add-agent-event-ledger 的实现、验证和本地联调，交付可独立验收的企业事件台账及供后续并行开发使用的 v1 契约。本轮匿名编号02。

你是新的实施者。协调者已经直接补修第一项，并放行第二项的本地实施；不要再次等待上一模型修复或把第一项已勾选任务当作完整生产验收。先读取 AGENTS.md、涉及目录约定、docs/enterprise-agent-roadmap.md、docs/enterprise-agent-parallel-work.md、docs/flash-blind-evaluation.md、docs/reviews/flash-blind-01-repair-02-review.md、docs/reviews/evidence/flash-01-repair-02/change2-source-baseline.json，以及本 change 的 proposal.md、design.md、全部 specs、tasks.md、handoff.md。

第一步核对当前 HEAD 与上述非秘密工作树文件哈希。HEAD7b82c04是规划提交，当前实现未提交，以协调者记录的代码指纹为起点。若关键身份文件与指纹不符，先查看差异、记录实际起点和风险，不覆盖他人改动。保留既有工作树，不 reset/clean，不删除前轮证据或为开始工作自动提交。使用当前工具支持的 OpenSpec apply 流程；只实施本 change。

依赖接口和边界：统一身份是 admin/api；可信 RuntimeIdentity 来自 configurable.langgraph_auth_user，保留 user/tenant/membership/session 与四种修订号。非 owner RLS和事务局部上下文必须继续生效。运行票据 rt-v1；迁移票据 mt-v1只授权指定线程metadata窄更新。BFF新线程由服务端生成UUID，平台创建2xx后用原会话票据确认认领，409不可当成功。归档要求有效会话和已确认归属；外呼逐次核验授权，不能重新启用SDK隐式重试。前端stopPending表示“停止未确认”，断流和旧已完成run不可充当本次终态。第一项迁移链当前0006，本项新迁移从0007起，不改已执行revision。

复用已有PG/Alembic/身份体系，完成可信提问、submission_key幂等、运行尝试/人工恢复/回答版本/Trace映射、六图生命周期、停止意图与终态校准、事件v1/outbox/消费去重/重放、租户分级读取及基础指标口径。保留正文确认、归档完整计算和停止体验。本项不实施点赞/复制按钮、完整span/Token/成本采集、选择器、问题聚类或后台大屏。

先核验实际官方LangGraph镜像/SDK的运行受理、ID/metadata、恢复和幂等能力，再做一条从可信受理到真实run与台账回读的最小完整链路，随后按任务扩展。上游已受理但响应丢失时先校准，不盲目重复执行；不能只埋点聊天代理而漏掉native入口。未覆盖入口应拒绝开放。不要重写整个聊天架构或身份体系。

验证A/B及同用户多企业、非owner RLS/连接池交错、六图真实运行API、浏览器停止/导航/回读、重复/乱序/崩溃/补偿、双worker/租约/死信/重放、删除与恢复，以及确定fixture的指标分母/窗口/缺失标签。第一项真实企业MCP仍因外部材料缺失安全禁用，完整长运行及两前端矩阵仍待补；可用明确标记的模型/MCP替身验证台账，不得声称这些生产门禁已通过。继续独立可做任务，仅阻塞必要部分。

既有验证基线：管理端117 passed；根单元279 passed/26 skipped/5项既有失败（checkpoint close一项、knowledge route四项）；前端19项契约；两端类型/生产构建通过。真实联调具体数值和跳过项见独立报告与integration-codex.txt，既有500k旧ModelRegistry monkeypatch失败保留。不得删除、跳过或弱化断言掩盖新回归。迭代优先受影响测试，触及共享契约后再做必要全量；不无意义反复跑同一轮全量。记录每个阶段实际用时、修复次数、环境等待；工具无法提供Token则写未采集，不猜。

交付docs/contracts/agent-events-v1/机器可读schema、来源/类型登记、合法非法样本、状态/指标字典及第3/5文件责任表。共享接口只有一个负责人，先给冻结候选，协调者独立验收后才冻结与开启第3/5并行。本次不启动其他change。

完成后写docs/reviews/agent-event-ledger-acceptance.md：实现/配置/迁移影响、可复跑命令、spec场景证据映射、未完成项、性能实测或未知、耗时来源。只使用匿名编号02，不透露模型/供应商。验证后再勾任务，交付状态“待独立验收”，保留diff；不自动commit、push、合并或归档。目标持续推进到已授权工作完成，遇到真正外部阻塞保留说明，不能只写计划或测试清单即宣布完成。
