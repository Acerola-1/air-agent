> 历史实施提示词：对应change已于2026-10-15归档，勿重新执行。下一轮使用skill-tool-selection-goal.md。

/goal 持续完成 Air Agent OpenSpec change add-chat-feedback-interactions 的实现、测试和真实本地联调，交付可独立验收的回答反馈、复制和浏览器体验指标。本轮匿名运行编号03。

先进入协调者为Change 3准备的独立工作区，核对当前分支codex/change-03-feedback与启动记录；主目录/另一个worktree只读。工作区路径和起始提交见主工作区docs/handoffs/parallel-launch.json。若当前工具不支持/goal命令，将本提示作为持续目标执行，直到已授权任务完成或确实外部阻塞；不要只给计划。

完整读取本工作区AGENTS.md、docs/handoffs/parallel-03-05.md、docs/contracts/agent-events-v1/freeze.json、docs/flash-blind-evaluation.md、docs/reviews/agent-event-ledger-takeover-acceptance.md，以及openspec/changes/archive/2026-10-15-add-chat-feedback-interactions/中的proposal、design、全部specs、tasks、handoff和contract-extension。用当前工具的OpenSpec apply实施本项。源码冻结9a1781e3f6d74ad99e5f325e14a69642f6474927，先验证其是本工作区起点的祖先、机器契约哈希和初始diff；若有漂移记录差异，不reset/clean或覆盖他人工作。

仅做本项：已确认回答版本的点赞/点踩/原因/意见/撤销、成功复制采集、单页单调计时、停止阶段、本人回读与授权统计。先完成真实提问→answer映射→评价保存→PG事件/outbox→刷新回读的最小完整链，再扩其余任务。现有复制按钮在clipboard成功前显示成功，需要实际修正。UI消息缺answer ID，必须从服务端权威映射，不按数组位置或最近attempt猜；重生成两版独立评价且只算1个问题。评价操作ID幂等优先、CAS防迟到覆盖，事实/历史/事件/outbox同事务，原文意见独立授权且不进事件/Trace。

使用独立PG应用/原生库、Redis及8103/3103/2027等交接分配端口；开始检查占用，不杀其他服务。可以创建专用测试库或独立Apple Container项目，不调用默认主项目compose、不重启主服务、不清理共享卷。依赖使用各自uv.lock/npm锁及自己的venv/node_modules；不要擅自升级框架、修改模型或启用真实企业MCP。真实秘密只在未跟踪本地环境中安全读取/配置，不打印或写报告。

并行边界已明确：本项独占聊天UI/store和反馈新模块，只按责任表做browser分派、路由登记和本项三种browser事件的兼容接线/生成副本；runtime五种预留仍拒绝。迁移0011_feedback从0010分支，后续0011f_*；不依赖观测分支迁移，不改0001–0010，不合并另一分支。身份、台账准入/终态/store/reconcile/rs-v1投影规则保持。公共diff逐项写报告供协调者集成；本项不是只能交patch的伪实现，要在自己的工作区完整启动真实服务。

逐任务验证再勾选：真实PG非owner RLS、A/B及同用户多企业、并发/重复/响应丢失/事务故障、删除晚到/恢复、混合批次、clipboard拒绝、进度不算首正文、刷新不跨时钟、旧终态不解除stopPending。实际浏览器验证提交/两版回答/正负撤销/复制/刷新/企业切换。满意率与覆盖率fixture分别2/3、3/4，复制3次但distinct=1；历史未采集不伪零。浏览器、真实API、真实PG和替身/fixture分别标明，HTTP200或静态测试不替代业务行为。

基线：管理160、台账84、前端31；根314/75/5既有失败，集成31/7/1既有500k失败；干净构建通过。迭代先受影响测试，公共接线后做必要基础身份/台账与完整管理/集成回归、双语言契约、类型/干净构建和strict OpenSpec。合法启用事件后预留集合断言可按明确新集合更新，安全负例必须保留；不删/skip/弱化原测试掩盖新回归。真实企业MCP与完整长运行生产门禁依然保留。

最终交付docs/reviews/chat-feedback-interactions-acceptance.md及flash-03安全证据，逐spec场景对应实际证明，列公共文件、迁移/开关/启用时间、未完成项，记录实际用时、返工、环境等待；工具不提供Token则写未采集。匿名编号03，不透露或猜模型身份。保留分支diff，不自动commit/push/合并/归档；状态待独立验收。另一分支未完成不妨碍本项交付，组合验收由协调者负责。
