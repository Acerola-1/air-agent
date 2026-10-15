# 匿名运行05第二轮修复独立验收

日期：2026-10-15（Asia/Shanghai）。Change：`add-skill-tool-selection`。**结论：仍未通过，出现新的正常路径、迁移及安全开关回归；不能归档或放行Change6。** 本轮只审计，没有替作者补修产品。

## 时间、输入与评分

| 项目 | 记录 |
|---|---|
| 首交 | 用户实测131分钟，46/100，保留 |
| 首修 | 用户实测83分钟，63/100，保留 |
| 二修 | **用户本轮提供60分钟** |
| 模型累计 | **274分钟／4小时34分钟，仍未独立完成** |
| 外部等待／Token | 用户与作者报告外部等待0.0s；Token未采集，不推测 |
| 当前诊断分 | **58/100**：契约16/30、权限与隔离13/25、验证10/20、可维护性11/15、范围与交接8/10 |
| 效率 | **2/5**；用时缩短，但正常路径和共享安全边界出现回归，尚未交付闭环 |
| 分支／HEAD | `codex/change-04-skill-tool`／`8620cf78b22cb7234fe650fa5c9f032359e0d70d` |
| 起始跟踪diff SHA-256 | `85b1056525f0ff4e3ad5f713bc257518f19330debeae47fcc256010ca4704e75` |

冻结102份作者输入；相对首修冻结清单13份已有文件改变，另有新文件，详见 [anonymous-input.json](evidence/flash-05-repair-02-independent/anonymous-input.json)。作者原始报告、原始日志、两轮独立审计均保留，不覆盖首次或首修成绩。低于首修的原因是本轮引入回归，不是因为60分钟本身。

## 有效修复，保留成果

1. 同键从显式改为明确Auto现返回409；不存在工具、仅另一服务有同名工具、未知协议分别422/422/403。正常Auto受理、native hook、关闭选择开关及首次恢复继承仍可用。
2. `glob(pattern,path)`和异步glob已兼容当前SDK；根ls只显示S1；原S2下载/搜索与路径逃逸反例仍被拦截。
3. 同一submission的Skill适配器回调已传递selection_id/digest/submission_id，loaded→executed保留manual。此为真实适配器回调证据，不扩大为完整三图使用事实落库验收。
4. 前端发送时固化草稿，延迟确认不再被后来S2草稿污染。但固化浏览器意图仍不等于服务端已受理快照，见G04。
5. 新迁移的9项Ruff行长问题已消除；本轮抽查8份修复文件Ruff通过，正常错误率也已改为实际计数。作者保存的`ruff_check.txt`仍含较早56项错误；不把旧日志当当前新迁移仍有9项错误，当前判断以独立重跑为准。
6. BFF至管理端已接入menu_name字段，当前上下文头/默认JSON/目录渲染的首修成果保留。

## 阻断项与真实反例

### G01：动态注入导致三张DeepAgents正常运行崩溃（P0，新增）

`src/common/middleware/tenant_runtime_guard_middleware.py:49`对`getattr(t,'metadata',{})`直接`.get()`，而真实本地BaseTool的metadata合法值就是None，默认值不会覆盖None。首次模型调用在handler之前抛出`AttributeError: 'NoneType' object has no attribute 'get'`。

独立测试实际导入并`ainvoke`三张生产图：deep-research、intelligent-report、intelligent-tracing，全部同样失败。使用受控模型，身份guard单独隔离为放行，其他生产图和中间件不替换，无供应商外呼。**正向对照：只替换为原HEAD的guard实现并重新编译同三图，三个都返回controlled success。** 因此不是模型凭据或身份库环境阻塞，更不能用GraphLoad代替运行验收。

证据：[production-sdk-graphs.json](evidence/flash-05-repair-02-independent/production-sdk-graphs.json)、对应脚本和日志。还需在修复后验证真正生产动态工具及子任务dispatch，不是仅修这个None错误。

### G02：格式修复删掉列，新空库和旧head升级失败（P1，新增）

`admin/api/migrations/versions/0013s_capability_selection.py:55`后不再声明`selection_source`列，但第71行还创建引用该列的CHECK，ORM和运行代码也仍要求它。

独立临时库0001→0012o_delivery成功，随后→0013s失败：`psycopg.errors.UndefinedColumn: column "selection_source" does not exist`，新选择表未创建。**同样路径在首修独立验收成功。** 作者已有专用库执行过先前含该列的0013s，迁移版本已经在head，重复检查该库不能检测当前迁移源码的问题。

证据：`empty-to-old-head.txt`、[old-head-to-new-head.txt](evidence/flash-05-repair-02-independent/old-head-to-new-head.txt)。临时库已删除，未改变作者库schema或共享库。

### G03：目录版本仍可伪造；合法MCP在分离服务中无法受理（P1，未闭合并有新回归）

`ledger.py:1015`仍仅用正则校验catalog，未比对当前目录。独立真实API选存在的`air-quality-basic-query`、MCP空集，加`cat_000000000000000000000000`，仍**200**。原未知工具先被422拒绝，不能证明目录版本校验正确。

另一方面，`ledger.py:367`依赖管理进程自己的`get_factory().catalog_entries()`，既不读取运行时目录快照，也没有在当前身份下发现。**两个独立HTTP进程**的正向联调：真实登录/内部签票→运行时custom app目录**200**，真实HTTP MCP发现5个已授权工具→选其中的query_air和返回的真实catalog版本→管理API **422“能力目录为空或工具尚未发现”**。运行时发现结果不会自动出现在另一个进程的Python单例中。作者同进程注册工厂后受理的绿测试掩盖了这个正常部署边界。

另，工厂导入或获取抛异常时`factory=None`仍跳过工具存在性校验，未完全失败关闭。目录/授权/材料/修订应来自可信当前scope契约，而不是目录字符串格式或跨进程不可共享的缓存。

证据：`live-probes.json`的`forged_catalog_with_valid_skill_and_no_mcp`；[process-boundary.json](evidence/flash-05-repair-02-independent/process-boundary.json)。后者不是官方平台运行矩阵，只验证真实目录与管理服务进程边界；无实际模型请求，MCP业务调用0。所有自建HTTP/fixture进程已关闭。

### G04：客户端自行生成“已受理ID/摘要”，仍无服务端回读（P1，未闭合）

`frontend/lib/chat-store.ts:383`从`run.submittedSelection`重新算摘要，并派生UUID，再标为admitted；`confirmRunAdmission`仍只返回boolean。服务端selection_id实际上由数据库生成随机UUID，显式Skill摘要还包含真实材料指纹，客户端此处却使用默认materialVersion=auto。

独立编译实际TS：发送真实Skill及MCP空集，延迟确认并改草稿，S1确实不被污染；但把结果与真实管理API的同一规范Skill/工具选择比对，**selection_id与selection_digest均不匹配**。报告将“原始浏览器意图的固定副本”称为权威回读不成立。新增digest.ts与chat-store内又有一份重复SHA实现，不能代替服务端选择事实。

证据：[accepted-vs-server.json](evidence/flash-05-repair-02-independent/accepted-vs-server.json)。这是实际TS+受控确认时序和真实受理DTO对照，不冒充完整浏览器运行。必须接服务端返回的选择ID/摘要/版本、历史回读与继承计划，不能在浏览器自行铸造。

### G05：关闭测试协议仍被环境猜测重新打开（P1，新增安全承诺回退）

`src/common/mcp_authorization.py:65`将原先唯一显式开关扩为：加载pytest、URI含test或selection05、存在PYTEST_CURRENT_TEST，任一即启用。新增v1同样被作为fixture协议开放。

独立非pytest进程将`TENANT_MCP_FIXTURE_MODE=false`，仅将不会连接的URI设为`postgresql://unused:unused@invalid/production_test_records`，两个fixture协议仍可解析通过。URI中账号、口令、库名含test也可能触发；不应以库名/测试框架推测授权启用。此反例不需要外部数据库或真实凭据。

证据：`new-regressions.json`。恢复显式开关语义；测试应显式配置，不在生产授权函数加入通过测试的旁路。不据此声称已观察到未授权生产外呼。

### G06：名称编码仍不唯一，别名没有接入实际MCP识别（P1，部分修复）

新`server_clean_key`虽区分service-a/service_a，但将`svc_a`和`svc--a`都编码为`svc__a`，仍生成两个完全相同的`mcp_svc__a__query`。这两个server_key均符合现有标识格式，不能称单射。

`_is_mcp_tool`只比对原始短工具名集合，代理名为别名时实际返回false，即便工具metadata已标is_mcp。这会走本地handler/blocked代理而非授权MCP执行层。Auto过滤又原样返回全局代理，当前身份无绑定的schema披露隔离仍没实现。不能用factory.call的直接正例替代生产ToolNode的正确路由。

证据：`new-regressions.json`。需统一合法唯一名称、稳定ID、模型披露、ToolNode识别、授权dispatch与当前scope，做两种以上碰撞及编译后发现的实际主/子任务正反例。

### G07：首次恢复成功，同键重放却409（P1，新增于本轮幂等修复）

`admit_request`对existing提交直接比较normalize后的当前选择，发生在原选择继承之前。省略选择的resume第一次受理会继承源allowlist，重投完全相同的请求时却拿Auto与已继承allowlist比较。

真实API：同一合法来源、相同submission_key、同一补充内容，两次都省略新选择，**首次200，第二次409“同一提交键已用于不同选择”**。首次恢复继承已修好，但响应丢失后的安全重试仍被破坏。明确Auto与缺省继承必须区分，兼容旧v1不能只看initial省略成功。

证据：[live-replay.json](evidence/flash-05-repair-02-independent/live-replay.json)的`resume_same_key_omitted_choice_replay`。

### G08：证据范围与完成勾选仍不一致（P1，交付完整性）

5.4、7.1保留未完成是正确的；本轮未因外部商用凭据缺失扣“环境故障”分。但A12子任务、A16各图显式及实际interrupt→resume、A18备份恢复重施墓碑仍被写为通过，实际引用仍是直接后端/节点、人工waiting状态和删除单元测试。它们不是A15未完成可以代替声明的独立要求。

本轮三张生产SDK图受控执行和独立服务正常路径已经失败，说明无需商业模型凭据也能检出问题。两个性能JSON可来自不同采样，不凭不同数字判造假；但其脚本测的是helper和ASGI+PG受理（显式路径MCP为空且catalog=v1），不涵盖上述正常MCP选择、native启动、完整模型和子任务保护，不能据负P95差值宣称完整系统性能无退化。本轮没有重跑作者性能采样或认证它为有效全路径成绩。

## 独立验证汇总

| 验证 | 本轮结果与口径 |
|---|---|
| 核心专项 | **28通过**；仍不能覆盖实际三SDK图的新增崩溃 |
| 管理台账+选择 | **44通过/1既有collection_disabled失败**，失败身份不变 |
| 前端契约 | **46通过**；自造selection ID并不因这些测试通过而成为权威事实 |
| 根单元 | **361通过/75跳过/6同环境既有失败**；无新增测试失败，不等于无新增运行缺陷 |
| 三生产SDK图受控调用 | 候选**3/3失败**；原guard正向对照**3/3完成** |
| 跨进程目录→合法MCP选择 | 目录200、真实工具发现成功；管理受理**422失败** |
| 空库/旧head升级 | 原head成功；本轮新revision **UndefinedColumn失败**，新表无RLS可检 |
| 前端生产构建/类型 | **通过**；使用现有构建缓存，不冒称删缓存干净构建 |
| 8份修复文件Ruff | **通过**，包括新迁移；迁移语义仍错误 |
| 本Change OpenSpec strict | **通过**；未改历史主规格9项格式失败 |

OCR预览101项全部记账：60项作者文件（42项首修同指纹沿用，18项本轮改动/新增重点审查）；41项历史协调者证据只校验保留、不当作作者修复代码重复评价。另检查作者报告/tasks和测试输入。详见`review-coverage.json`。不把文件审查覆盖当完整产品验收覆盖。

## 收尾与下一步建议

**建议停止该匿名模型的继续返工，由协调者另行收尾；这是建议，不是已发生的接管。** 优先恢复三图正常运行、正确迁移和显式协议开关，再统一可信目录/幂等/快照回读/真实dispatch，最后补验收矩阵。无需再开启新Change来绕过本项未完成。

本轮协调者没有补修产品，没有commit/push/merge/archive。仅使用15434专用库和自建回环端口，未操作15432共享栈、IAM或知识库。自建进程与MCP fixture已关闭、自建迁移临时库已删除，作者aa-sel05-pg/redis保留；测试库审计前私有备份只在tmp，权限600，不入库。合成审计行保留供复验，不对整库做恢复/清理。最终源码与历史证据指纹核对见`review-closeout.json`。
