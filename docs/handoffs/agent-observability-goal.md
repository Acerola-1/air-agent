/goal 持续完成 Air Agent OpenSpec change complete-agent-observability 的实现、测试和真实本地联调，交付六图逐调用观测、实际Token、Skill/MCP归因、Langfuse映射及版本化成本。本轮匿名运行编号04。

先进入协调者为Change 5准备的独立工作区，核对分支codex/change-05-observability与启动记录；主目录/反馈worktree只读。实际路径和起始提交见主工作区docs/handoffs/parallel-launch.json。工具不支持/goal则按本持续目标推进，直到已授权任务完成或确实外部阻塞，不能只给计划。

完整读取本工作区AGENTS.md、docs/handoffs/parallel-03-05.md、docs/contracts/agent-events-v1/freeze.json、docs/flash-blind-evaluation.md、docs/reviews/agent-event-ledger-takeover-acceptance.md，以及openspec/changes/complete-agent-observability/中的proposal、design、全部specs、tasks、handoff和contract-extension；按当前工具的OpenSpec apply实施本项。验证源码冻结9a1781e3f6d74ad99e5f325e14a69642f6474927是起点祖先、机器哈希和初始diff，不覆盖现有代码或误将已存在提示词适配算新成果。

先完成一个真实主模型的可信预留→原生run→实际usage observation→PG事件/outbox→Langfuse真实写/flush/回读→授权明细/聚合，再扩辅助模型、六图、Skill/MCP、重试恢复与价格。CallbackHandler绑定、HTTP200或Langfuse有历史trace均不证明本次采集成功。主/normalizer/summarizer/subagent等每次实际尝试都独立关联，逻辑调用/尝试分开，重复流块和结束回调不重复计费。缓存/推理是usage子集，缺usage/价格保留null和覆盖率；Decimal计费、价格生效时间/币种/修订清楚，多Skill关联不复制整笔费用。模型TTFT不等于浏览器首正文或工具进度。

使用独立PG应用/原生库、Redis及8105/3105/2029端口；开始检查占用，不杀其他服务。只操作本项专用库/独立Apple Container项目，不默认compose重启主服务或清共享卷。现有企业MCP材料缺失保持禁用，受控MCP标明替身；真实Langfuse不可达则保留相应任务未完成，继续其他独立任务，不假造落库。当前锁定SDK/OpenTelemetry以冻结代码和实测镜像为准，AGENTS旧文字不授权降级；不擅自改模型或升级框架。真实秘密只放未跟踪环境，不能打印、写报告或上传Trace。

本项独占runtime观测新模块和模型/辅助/Skill/MCP/六图最小采集接线；聊天UI/store只读。迁移0012_observability从0010分支，后续0012o_*；不依赖反馈迁移、不改0001–0010、不合并另一分支。管理入口/配置/权限及runtime五事件可按责任表做最小兼容扩展，browser三种预留保持拒绝。公共validator现有token子串规则会拒绝input_tokens：仅对runtime.model_usage的顶层登记数字计数作非负整数窄例外，src/admin两个Python副本和TS一致，字符串/bool/负数/嵌套secret/browser来源仍拒绝，不能删除token规则。每个公共diff列入报告由协调者最终合并。

可信身份只来自平台注入RuntimeIdentity，关联与父观测必须同租户；原身份/准入/终态/rs-v1投影及每次外呼授权保持。不得用with_listeners破坏官方图导出类型。观测关闭/故障不影响已有答案，但授权仍失败关闭。队列/flush/重试有界并报告丢弃；默认无提示词、工具参数/结果、内部推理/凭据，旧Langfuse自动callback也必须受隐私约束。共享Langfuse项目不向企业用户提供绕租户权限的控制台或密钥。

按任务逐项验证：真实PG RLS/连接池、伪关联/跨企业、六图stream/wait与native/BFF、适用图真实恢复、主辅助usage、10+10+5重试、Skill候选未执行、HTTP200无结果、工具多层去重、删除晚到/外部清理、价格边界/重算/币种/缺失。真实Langfuse回读检验敏感marker不泄漏。至少30次同条件比较采集关闭/开启的本地P50/P95开销，外部等待分列；新增P95>50ms需修复或明确未过验收，测试执行时间不是应用性能。

既有基线管理160、台账84、前端31；根314/75/5既有失败，集成31/7/1既有500k失败；干净构建通过。迭代先受影响测试，公共接线后复跑必要安全/台账、真实集成、双语言契约、类型/必要干净构建、Ruff与strict OpenSpec。不要删/skip/弱化安全断言，合法启用类型可以将预留集合改成精确集合并保留旧非法来源场景。

交付docs/reviews/agent-observability-acceptance.md及flash-04安全证据，逐spec映射真实/替身/未知，列调用点覆盖、公共diff、实际落库、价格/usage来源、开销条件、迁移/配置/权限/consumer、未完成项与实际用时/返工/环境等待；Token无计量写未采集。只使用匿名运行04。验证后勾任务、保留diff，状态待独立验收，不自动commit/push/合并/归档。另一分支未完成不阻塞本分支交付，组合迁移与反馈+usage/Trace由协调者负责。
