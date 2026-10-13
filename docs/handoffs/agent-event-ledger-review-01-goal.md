/goal 持续完成 Air Agent 的 add-agent-event-ledger 首次独立验收定向修复，解决协调者已复现的 R01–R07 与交付缺口，并提交可独立复验的结果。本轮仍为匿名02；这是首次独立验收后的修复，首次用时1小时52分保留。

先读 AGENTS.md、涉及目录约定、docs/reviews/flash-blind-02-add-agent-event-ledger.md、docs/reviews/evidence/flash-02-independent/README.md、该目录全部JSON反例和探针，以及本change的proposal/design/specs/tasks与事件v1契约。作者自己的“全部闭合”报告不是独立验收结论；当前38项任务中相关缺陷项已重新打开。执行OpenSpec apply，只做本change的修复，不改评分、不覆盖首交输入和独立证据。保留change1真实MCP和完整矩阵的生产门禁。

先把独立反例变成有效回归，确认现实现失败，再逐项修复。不是只让原44/30套件再次变绿：

1. R01：同一预留/相同submission_key现在能创建多个平台run。原生和BFF受控入口原子裁决启动权，已启动/受理未知不得盲重发；并发同内容复用结果或明确待确认，不重复业务操作。校验预留的真实graph/规范内容/操作与关联，防换graph、换input、metadata与config冲突。权威request/attempt注入lifecycle所消费的config；metadata-only形态或安全规范化后完整闭环，或明确拒绝，不能接受后平台success而台账仍admitting。保留已验证的镜像run_id非幂等事实，不能假装上游支持不存在的能力。
2. R02：所有Agent侧事实、event、outbox同一连接/事务提交，覆盖状态、回答、恢复、停止及Trace，不只是admin侧。outbox失败/进程退出时不留半提交事实；对旧孤立记录提供有界修复。同事件重放补偿不丢投递，类型/作用域/关键关联变化应冲突，不能只比较payload而忽略信封。
3. R03：submission稳定关联自己的attempt；initial→regen→原键重放仍回原attempt；确认只影响该操作。retry/regen来源同用户/企业/线程且记录实际来源。人工resume保留原request/attempt，追加幂等恢复映射，不新建逻辑提问；实际创建/恢复入口接上helper。无来源回退initial时规范摘要、操作kind和重放行为一致。
4. R04：事件request/attempt/answer/run/thread关联由可信作用域核验，数据库相应复合约束拒绝跨租户/用户拼接；不限于RLS约束事件行本身。0007已经执行，新增纠正迁移0008及后续必要revision，不改写已执行0007；验证空库迁移与0007旧库升级/冲突预检。
5. R05：所有删除入口验证本人归属或明确独立管理权限；真实native/BFF删除流程接入持久删除标记。标记后立即禁读/禁新写，后台/本人/原文/关联回读一致，完成后仍遵守删除；保留期到期拒绝原文。最小权限worker能实际清理本租户对应事件/内容，RLS下更新0行不能假done，不以owner绕过。恢复验证用“先备份→后来删除→恢复旧备份→重施外部删除记录与当前权限→允许读取”，保留新写，不只备份前预先标deleted。仅新建专用演练库，拒绝固定共享库的危险DROP。
6. R06：receipt幂等裁决在副作用前，与投影同事务；同事件重复处理只应用一次。有效租约及代次涵盖处理和确认，过期旧持有者不能先提交副作用再假确认成功，有界关停。投影重建从保留event读取，包括已done事实，真实使用consumer_version、游标、租户窗口、删除规则和运维审计；版本2不是响应里的装饰字段。重建不调模型/MCP。
7. R07：修正Next Route额外导出confirmRunWithMarker的TS2344，helper放普通模块；必须隔离目录干净生产构建，不能依赖已有.next类型或增量缓存。“tsc通过”不等于生产构建通过。遵守frontend/AGENTS并读当前Next文档。
8. 交付缺口：补偿能按可信metadata定位“受理成功但run_id未确认”的已有run，受限只读服务校准在原成员已撤销时仍可工作，具备有界调度和真实故障/重启验证，不恢复用户业务权限。缺失配置必须如实保留未完成，不把no_platform_run_id跳过当作闭合。持久保存授权原问题快照或不可漂移的消息/版本引用，并按request精确读取，保留期/删除一致，为后续需求分析提供实际来源；不只保存摘要和可变thread指针。修正ownership文档/API/OpenAPI与真实路由，报告重写成最终状态，不保留互相矛盾的“未完成/全完成”拼接。

验证用现有依赖和全新专用PG/API/RLS/worker环境；不重置业务卷，不清理历史用户对话。首次独立证据里的探针仅操作本轮合成线程，运行暂停在首节点前避免真实业务工具，清理只按自己创建ID。真实API、模型/MCP替身、数据库故障注入分别标记。真实MCP仍安全禁用，不需为此恢复全局凭据。

迭代先做受影响回归，通过后做共享契约所需全套。当前独立基线：admin141 passed；台账44 passed；根303 passed/46 skipped/5既有失败；集成20 passed/7 skipped/1既有500k失败；前端30契约；tsc通过；clean build新增失败待修。旧HMAC治理套件不改成假成功、不删除/skip掩盖新回归。保持正文确认、完整资料计算、现有SSE及stopPending体验。

每阶段记录实际开始/结束、修复用时和环境等待，避免重复无意义重启和全量；不把测试耗时冒充应用性能，不猜Token。首交1小时52分、作者此前自记23处内部修复均保留，独立验收后的修复另计。

验证后再勾任务。交付docs/reviews/agent-event-ledger-repair-01.md，列R01–R07逐项根因/改动/先失败后通过的可复跑证据、剩余缺口、迁移影响、总用时来源，状态“待独立复验”。不改协调者评分，不自行宣布验收/冻结，不commit/push/merge/archive，不开始第3/5等其他change。目标是实质闭环，不是只补报告或函数定义。
