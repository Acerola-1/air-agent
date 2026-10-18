# I2 评审者补修与独立复验

复验：2026-10-17；核对、资源收尾与报告更新：2026-10-18。

**GLM 5.3 flash 首次交付未通过，42/100，用户提供实施用时143分钟。评审者已补修核心链路，正式remote六图24格已通过；完整L2仍未关闭。** 本报告的新增实现、测试、截图均属评审者，不回填模型成绩；模型独立返工0轮。

## 输入与归属

- Agent `dev` 基线/HEAD：`319dab499585e640d0d216699cd767a3a1c567e1`；IAM冻结 `8da8ba1127b7ea7aa9c0a9f7289d90cbcc96a368`。按规划、契约、实际diff和冻结首交审查，不采信作者全勾选。
- 首交37文件、SHA-256、diff、作者证据及首次独立反例保留；[首次评分报告](flash-blind-i2-glm-5-3-flash-first-delivery.md)不改写。旧scorecard内容只追加，旧阶段报告另存 `takeover-before-final-wrap.md`。
- 本轮只改Agent消费者；未改IAM或RAG产品、未切共享服务。作者先前自述的共享角色密码/账号变更缺少可核实前态，没有猜测回滚。
- 本文证据均位于 `evidence/run/i2-review-20261017/`；未获授权commit/push/merge/archive，当前交付仍是可审查diff。

## 五维成绩、稳定性和实施效率

| 首交维度 | GLM得分 | 首交依据 |
|---|---:|---|
| 契约正确性与业务可用 | 13/30 | 正常签票/native认证/执行引用接线断链。 |
| 权限和数据边界 | 10/25 | 同步仍查本地身份库、平台GUC残留、Origin依赖未挂载、新表RLS缺失。 |
| 测试与验证可信度 | 6/20 | 单测不能覆盖正常入口；local矩阵代替remote、未验项勾完成。 |
| 可维护性与项目契合 | 10/15 | 有分层框架，但异步阻塞、重复客户端、绑定SQL与协议不完整。 |
| 范围控制与交付完整 | 3/10 | 交付声明超过证据，且自述动过共享测试环境。 |
| 总分 | **42/100，未通过** | 首交成绩固定；后续评审者工作不重新计为GLM成绩。 |

首交稳定性不足，多项关键路径缺陷；没有模型返工样本。143分钟仅来自用户，实施/自测/外部等待未分段采集；Token、成本、工具次数和评审者有效实施耗时未知。无法给出模型达完整验收总时间。L3尚未在本轮独立验收，不据本次跨任务分数/耗时重排GLM与DeepSeek。

## 评审者主要补修

1. 正常签票门面登记共享摘要绑定，修正native导入与可信执行引用传递。主体、UUID、issuer/audience、四修订、时间及返回关联严格校验。local/remote显式选择，故障不回local。
2. sync/async真实外呼逐次查IAM；总500ms预算包含排队、连接和读取，晚到成功丢弃。异步不阻塞事件循环，客户端按配置指纹复用并关闭连接。
3. 每请求取得IAM当前权限和本人企业；同UUID投影只供业务FK，平台GUC复位。新增0018对五张桥接/投影表FORCE RLS；0014–0017原文件不改。库存清单分页完整，冲突拒绝apply。
4. 登出撤销IAM会话和当前应用handle，企业CAS/旧声明校验、Origin依赖及remote身份CRUD边界接通。httpx纳入运行依赖。
5. MCP独立复验发现“initialize后撤权、tools/call仍发1次”。增加实际HTTP发送认证守卫，每次初始化/通知/调用/重试重查IAM和绑定；缓存工具只保留绑定，不捕获第一位用户身份。补修后同屏障新增发送0，正常和真实500重试仍可用。
6. 删除墓碑优先于native正文/状态/恢复；原生搜索出口剔除已删除行，避免values/extracted泄漏。**精确计数尚有功能缺口：存在本人删除墓碑时 `/threads/count` 返回503；搜索当前页可能短于limit。没有将降级改写成验收要求。**
7. 双前端使用各自cookie/state、同源SSO发起与顶层跳转。修复管理代理抢先、聊天cookie识别、企业切换和迟到回调；会话epoch防ABA/退出后旧响应覆盖。401清上下文回登录，503显示可重试错误。真实浏览器发现logout BFF将503当成功，已补为只接受204/401；失败保留界面和重试，且仅清当前前端cookie。管理390px侧栏挤压已修复。

## 独立复跑结果与证据层级

| 范围 | 实测结果与精确边界 | 证据文件 |
|---|---|---|
| 正式remote六图 | **24/24通过**：六公开图×native/真实Next BFF×wait/stream；官方LangGraph API 0.15.1容器、PG持久化、真实IAM、用户新网关原模型hy3。检查真实run_id、成功终态和最终AI回答。显式空MCP白名单，不代表实配企业MCP联调。usage 12格reported、12格unreported，后者不填0。 | graph-matrix24-final-results.json |
| SDK子任务/摘要/辅助/重试 | **8/8通过**；实际DeepAgents/LangGraph/LangChain SDK+受控HTTP+真实IAM。父任务后撤权子任务收包0；摘要/同步辅助撤权0；HTTP500重试前撤权后新增0。此项在宿主SDK进程，不冒称六图容器子任务矩阵。 | sdk-results.json |
| 超60秒已受理执行 | **62.139秒通过**：旧native ticket再入站401；实际宿主SDK图使用可信引用，前后两次ChatOpenAI发送成功；登出后下一发送0。不是官方容器62秒持久化运行。 | long-sdk-results.json |
| MCP真实边界 | 真实ToolNode+MCP SDK有效调用、凭据/主体覆盖、真实500重试通过；initialize后登出，tools/call HTTP尝试0/接收0。保留补修前1次泄漏反例。 | mcp-results.json、mcp-midflight{,-before}-results.json |
| mt/rs窄能力 | **9/9通过**：mt只更新匹配迁移元数据；禁止读/启动/错线程。rs仅窄投影，禁止原始状态/运行/新运行，其他企业404。 | narrow-results.json |
| 停止/恢复/反馈/删除 | **20项通过**：真实BFF建线程→native运行/检查点→取消interrupted→rs校准→BFF resume成功→回答映射/反馈200→删除→读404/恢复403→登出后签票401，rs不能绕删除。 | lifecycle-results.json |
| 已删除搜索 | 原生search200，返回41行，2个墓碑ID泄漏0；状态404。count降级仍单独保留缺口。 | search-deleted-results.json |
| IAM/Redis/PG真实故障 | 分别暂停自有IAM、停止自有Redis/PG：API503，下一模型收包0；恢复后正向成功。API耗时512.629/481.221/4.838ms是入口观测，不冒称HTTP总耗时必须≤500ms。 | actual-dependency-fault-results.json |
| 身份/数据库/迁移 | 真实IAM正常入口、跨进程签票/native、跨登记client拒绝；非owner PG、平台GUC复位、五表FORCE RLS无作用域可见0；CAS200/409、旧声明409、Origin403；新库升级与0018降17再升head。 | live-results.json、rls-results.json、reference-negative-results.json、boundaries-results.json、migration-scope-roundtrip.txt |
| 双端SSO与cookie | 两个Next生产进程实际HTTP start200→IAM303→callback303→me200；重放/错state400。管理logout204仅清管理cookie，独立聊天会话me200。此项非中央IAM Vue的完整浏览器SSO操作。 | sso-results.json、cookie-isolation-results.json |
| 浏览器 | 双端账号登录/选企业/A→B→A/刷新；聊天换账号后旧列表不再出现；390×844双端、聊天弹窗Tab/Escape/焦点恢复；真实IAM故障退出显示“退出尚未完成，请重试”，恢复重试回登录。完整受控迟到响应矩阵和中央SSO浏览器仍未通过验收。 | chat-final-narrow.png、admin-final-narrow2.png、chat-after-account-switch.png、chat-logout-fault-final.png |
| Python根单测 | 最新候选**414通过/75跳过/6失败**，同环境冻结基线**365/75/同6失败**。五项为checkpoint关闭与knowledge_route×4；另一项framework async假响应耗尽，基线同现，单独重跑3通过。明确记录基线不稳定，不称全绿。 | root-final-latest-summary.txt、baseline-unit-latest-comparison-summary.txt、guard-rerun-summary.txt |
| 管理API与前端 | 管理**191通过/5跳过**；聊天契约**63通过/0失败**；最新聊天生产build通过。管理生产build/tsc先前通过，之后未改管理源代码；remote专项先前46通过。没有删断言或扩skip掩盖失败。 | admin-final-latest-summary.txt、frontend-contracts-latest-summary.txt、chat-build-latest-summary.txt、原阶段证据 |

初期评审驱动自身存在catalog参数、重复登录限流、停止时机等错误，调正后才记录上述结果，不把驱动错误归责模型；作者local矩阵从未算作remote通过。

## A01–A14当前状态

| 条目 | 状态 | 保留边界 |
|---|---|---|
| A01–A06 | 已有本轮正反实证 | 见身份/协议/签票/RLS/故障证据；非生产身份切换。 |
| A07 | 部分通过 | >60s宿主实际SDK与登出撤权通过；没有补齐本轮账号停用/改密的全部实测。 |
| A08 | 已有定向实际SDK正反实证 | 主/辅助/摘要/子任务/MCP/重试均有控制；宿主SDK与正式图矩阵分层。 |
| A09 | 部分通过 | mt/rs/RLS/绑定通过；删除安全读取已修复，精确count兼容仍未完成。 |
| A10 | 自有迁移/投影验证通过 | 正式历史同UUID导入是另列切换前置，未执行。 |
| A11–A12 | 部分通过 | 真浏览器主要流程及HTTP SSO/Origin/CAS/业务生命周期通过；完整双端受控迟到响应及中央SSO浏览器仍缺。 |
| A13 | 24格通过 | 当前配置模型均成功；实配企业MCP凭据联调未采集，不冒称有。 |
| A14 | 检查和基线对照已记录 | 见最终静态/strict/秘密扫描、负载JSON；完整应用资源和生产性能未知。 |

## 性能独立记录

首交/补修各900次核验，暖连接，并发1/10/30各100×3，0错误、每操作1次核验。并发30 P95首交三轮136.294/129.022/129.337ms，补修106.885/118.381/93.724ms。连续N=1/5/20各10样本：N20总耗时P50首交128.952ms→补修136.086ms，P95 140.408→152.737ms，均0错误。两种采样方向不同，不下“整体更快”的结论。

同机自有IAM配额10000/min，未压计费模型；样本与原始P50/P95/P99见first/repaired-performance.json及first/repaired-sequential-performance.json。该数据仅是身份核验开销，非应用全路径、CPU/内存或生产负载门禁。未知数据不猜。

## 连接方案与资源收尾

最初通用代理被自动审批拒绝且未执行；用户随后“允许”受限方案。实际只监听Apple私有桥50310/50313，只允许专用Graph来源IP；IAM仅允许固定introspect的execution_context复查，模型仅允许hy3 chat/completions及合成提示。没有任意目标或请求头透传。限制4项负例通过，见bridge-limit-results.json；不是持续审批阻塞。

2026-10-18核对宿主50300/50301/50303/50304/50310/50313均无监听，未对旧PID盲发信号。浏览器验收专用标签已不存在。仅停止匹配 `audit.run=i2-independent-20261017` 的 `i2-review-langgraph`、`i2-review-redis7`、`i2-review-pg17`，均确认stopped；其他容器状态未改变，未删除数据卷，见cleanup-results.json。

自有数据库/冻结源码/私有脚本留在专用资源和 `/tmp/i2-independent-20261017/` 便于复核；凭据文件0600不复制进交付。原作者资源不因名称相似就停止或删库；共享15432/IAM/RAG未在本轮清理中改动。三份旧测试自动改写的历史证据已恢复HEAD；其余已有未提交内容保留。非秘密证据目录被ignore，不强制入Git。manifest.json记录证据摘要，final-source-hashes.json记录当前交付文件摘要；私有环境与原始请求日志未复制。

## 当前结论及下一步缺口

**核心remote身份与运行链路已修复并获得实证，完整L2仍未通过；不归档、不提交。** 尚需修复精确会话计数兼容，并补齐双端受控迟到响应/中央SSO浏览器与A07账号状态负例。正式切换所需权限目录发布、历史同UUID导入证明、唯一写者维护窗口、生产回退和现有用户切换保持另列，不算本轮代码完成条件，也没有宣称已上线。

![真实故障退出后的可重试提示](evidence/run/i2-review-20261017/chat-logout-fault-final.png)

最终交付检查：29个改动Python文件Ruff（无缓存）通过，diff空白检查和OpenSpec strict通过；真实秘密精确比对26项、182份文本文件零命中，见final-checks.json与secret-scan.json（覆盖新增清单后的最终数量以JSON为准）。前一次Ruff缓存写入被沙箱限制，改为无缓存执行后通过，未降低规则。

## 2026-10-18 提交前整理（用户新授权）

用户随后授权清理并提交。清理两端构建产物、类型增量文件、测试/静态检查缓存及源码树字节码；两份退役的作者验收环境配置移至私有/tmp目录，未丢弃。6份长期回归测试、原始首交/评分/验收证据保留；依赖、数据库、持久化运行状态和其他仓库未动。清理数量与体积见housekeeping-20261018.json。一个测试文件仅统一格式，AST保持相同。

本次交付保存到dev；精确count和剩余验收尚未关闭，main保持不合入本次L2。上文“不提交”为前一阶段授权记录，本段取代其当前交付策略；不push、不归档，不改变GLM原成绩。
