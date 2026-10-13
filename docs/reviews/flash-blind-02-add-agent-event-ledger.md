# 匿名模型02：事件台账独立验收

日期：2026-10-13。结论：**未通过；不能冻结v1契约，暂不开第3/5项并行。** 本轮仅验收与增补反例，不接管修改产品代码、不提交/推送/归档。作者验收报告保留原样，本文另记独立结论。

用户报告首次用时 **1小时52分**，原样记入效率。作者自记三轮内部修复23处、容器重启11次，是作者来源，不等于Codex已要求过三轮返工。首次向本对话送审，尚未达到独立验收；不要把1小时52分称为“达验收总时间”。与身份change复杂度不同，不直接按总分/小时宣布模型排名。

## 1. 输入、范围和已复跑结果

HEAD仍为规划提交7b82c04；以change1接管后的393文件指纹分离新增范围。接管前输入冻结于`evidence/flash-02-independent/anonymous-input.json`（361个非秘密源码/测试/规格文件）。OCR全工作树清单及本轮重点审阅/跳过理由见`review-inventory.json`，OCR列208个reviewable文件，本轮审阅26个重点路径文件、明确跳过182个（含旧身份/旧证据与协调者新增附件）；重点是本项新受理、原生授权、写账、worker与查询；不宣称所有旧身份差异重新审阅。

独立环境使用**三套新的空PG库从0001迁移到0007**；调度角色为随机新角色，只有这套验收库的0007同等列权限/RLS，没有业务库权限。不依赖业务库的人工测试记录。

| 检查 | 独立结果 | 说明 |
|---|---|---|
| 管理端全套 | **141 passed** | 清洁API/RLS双库，53.94秒；作者111为其自述，不能直接当全套计数 |
| 台账专项 | **44 passed** | 契约、lifecycle及真实PG/worker，1.40秒 |
| 根单元 | **303 passed / 46 skipped / 5 failed** | 29.42秒；5项均为此前checkpoint close一项+knowledge route四项，逐项保留。作者302/6口径不同，不冒写成当前实测 |
| 集成，启用live和PG | **20 passed / 7 skipped / 1 failed** | 16.97秒；500k旧ModelRegistry monkeypatch失败保留；六图准入正反测试通过 |
| 前端契约 | **30 passed** | 12.73秒，一轮独立复跑，不把作者连跑3次当本轮独立3次 |
| 前端tsc | 通过 | 当前树的独立检查；不能替代干净Next生产构建 |
| 干净生产构建 | **失败，新增类型错误** | 隔离无.env副本、webpack；网络权限补齐后字体获取/编译成功，随后Route类型校验失败。不是字体网络故障 |
| OpenSpec strict / diff-check | 通过 | CLI实际任务数38，作者“31/31”统计错误；勾选不代表验收 |

实际应用延迟/吞吐/Token成本基准未测；作者列的测试/构建阶段用时不等于应用运行性能。独立浏览器矩阵本轮未重做，已有阻断足以拒绝契约冻结。

## 2. 阻断发现与复现

### R01（P1）：同一预留可多次创建，且准入与lifecycle不一致

位置：`src/common/identity/langgraph_auth.py:398`、`src/common/event_ledger/store.py:639`、`frontend/app/api/[..._path]/route.ts:148`。

`attempt_reservation`只是查询存在和非终态，没有一次性消费/受理锁、与实际输入/graph的摘要比对。BFF忽略`admitted.reused`，重复提交仍继续调用平台创建。

**真实原生API**：同一attempt在首节点前暂停的安全输入创建3个不同run，全部200；第三次换成intelligent-analysis并换正文，台账仍只有1个basic-qa attempt。**真实BFF**：相同submission_key、相同正文连续两次都是200，平台run_id不同。没有执行真实业务工具，均为本轮合成线程，最后原生删除204。证据`live-admission.json`。

另一个原生正向反例：合法预留只放metadata（hook明确接受这一形态），真实basic-qa问答平台**success**，台账却仍**admitting**、没有platform_run_id、answer_count=0。hook从metadata验证但没有将权威关联注入运行config，lifecycle只读configurable。证据`native-lifecycle.json`。因此“原生入口不是旁路”尚未闭合：拒绝无预留不等于每个放行运行都可靠落账。

修复应在可信创建入口原子裁决启动权、禁止同一提交盲重发（含响应丢失与并发），校验/注入真实图、内容摘要和request/attempt/run关联。恢复需独立的受控操作许可，不能复用任意非终态预留。

### R02（P1）：Agent事实、事件、outbox并非同事务

位置：`src/common/event_ledger/store.py:171`、`:231`、`:285`、`:361`。

每次`_run`分别开启并提交一个事务。write_event先提交event，再分别写outbox；observe_state/record_answer等又先提交事实，再写event。

**实际PG+故障注入**：仅令outbox写入失败，数据库已保存事件1条、outbox0条；恢复写入后用同event_id重试返回duplicate，outbox仍不会补建。这是已确认事实永久无法投递的路径，不是“回调失败开放”的合理例外。证据`pg-boundaries.json`。Admin侧同事务实现不能证明Agent侧也满足。

必须覆盖所有运行写账分支同一连接/事务，故障回滚零可见；对已经形成的孤立记录有有界补偿。重放冲突摘要还必须涵盖类型及关键关联，不只比较payload。

### R03（P1）：提交与尝试关联漂移，人工恢复新增提问

位置：`admin/api/app/services/ledger.py:314`、`:765`、迁移/模型的AgentSubmission定义。

submission只关联request，重放时取该request最新attempt。**真实API**：先initial，再regenerate，最后重放原initial键，返回regenerate的attempt而非原attempt（`live-admission.json`）。多次操作确认还按request批量改所有submission状态。

**真实PG+管理路由**：在已有线程提交operation_kind=resume，返回200且创建新的request（`deletion-api.json.resume_admission`）。记录恢复的helper存在，但受控创建入口没有接到这条链路；“定义了函数”不是恢复不增提问的完成证据。新恢复平台run_id行为已由探针核验，不要求平台支持不存在的能力，应保留原逻辑request/attempt并追加恢复映射。

要求submission→attempt稳定且唯一，初始重放/多次regen/失败retry/人工resume都使用各自幂等操作关系。来源尝试须同用户、同企业、同线程；无来源降级时规范摘要和操作kind必须一致，避免自身重放409。

### R04（P1）：事件可以拼接另一租户的尝试

位置：`src/common/event_ledger/store.py:174`、`admin/api/migrations/versions/0007_agent_event_ledger.py:305`。

**非owner运行角色+真实PG**：A作用域写trace.linked，attempt_id指向本轮创建的B租户尝试，返回written=true/reason=ok。事件表没有对request/attempt/answer/run关联做相应复合约束，写入层也未复核。证据`association.json`。RLS约束事件行的tenant，不会自动保证引用对象的tenant。

这是已证明的跨租户关联污染；本轮没有证明跨租户正文泄露，不扩张结论。修复时服务端验证与数据库约束都要覆盖关联；已执行0007后新增纠正迁移，不改历史revision。

### R05（P1）：删除未接线、无归属检查、禁读/清理/保留期失效

位置：`admin/api/app/services/ledger.py:1011`、`:1057`、`:1617`、`src/common/event_ledger/worker.py:174`、`frontend/lib/agent-ledger.ts:126`。

**实际管理路由+PG**：仅同企业运营元数据权限的另一成员提交本人之外线程的deletion，返回200。随后被删除线程的request和原文回读仍200、仍返回内容；retention_until调整为过去后仍返回内容。证据`deletion-api.json`。`is_thread_deletion_pending`从未被真实读取路径调用；BFF真正的线程删除没有调用`requestThreadDeletion`，新增函数没有接线。

**实际最小权限worker**：deletion_cleanup只设置tenant，没有user/受控清理作用域；事件子查询被RLS隐藏，更新0行，返回成功，outbox却done。删除标记仍requested、原文deleted_at仍NULL（`pg-boundaries.json`）。当前测试“删除立即禁读”只断言插入标记和outbox，没有真的请求回读。

修复应先校验删除主体与归属、真实接入native/BFF删除路径，再统一禁读/禁新写/保留期与受限清理授权，清理无效不能done。恢复必须用“备份后删除、再恢复旧备份、重施外部删除台账后开放”的反例；当前恢复脚本只是备份前预先deleted_at，不能证明旧备份重施删除，脚本统计/打印亦不能替代断言。

### R06（P1）：消费没有真正幂等短路，重建遗漏已完成事实

位置：`src/common/event_ledger/worker.py:174`、`:178`、`:356`。

实际worker处理同事件两次：处理器进入2次、receipt只有1条（`receipt.json`）。receipt在副作用之后INSERT ON CONFLICT，不检查是否已应用；唯一receipt数量不能证明副作用只发生一次。_handle也未验证当前有效租约，_finish才按owner过滤且不校验结果；过期旧持有者仍可先提交副作用。

**真实PG重建反例**：一个已消费done事件，选只包含该事件的时间窗口并请求consumer_version=2，would_replay=0（`rebuild.json`）。SQL只选非done任务，版本参数仅出现在返回值，消费receipt仍硬编码版本1。现工具是有限失败任务重试，不是规格要求的从保留事实重建投影。

要求receipt去重裁决与投影同事务且在副作用之前；租约/代次覆盖处理与确认、旧持有者不能提交；从保留event读取重建，真正使用消费版本/游标/租户窗口和审计，持续遵守所有删除标记。

### R07（P1）：干净生产构建失败

位置：`frontend/app/api/[..._path]/route.ts:305`。

Route文件额外export confirmRunWithMarker。隔离副本生产构建在字体编译成功后，Next生成的Route校验报TS2344：该额外导出不符合允许的Route出口。`tsc --noEmit`使用当前生成类型可以通过，但干净生产构建失败；已有增量通过或缓存结果不能覆盖该失败。证据`build.txt`。将helper移到普通服务模块或保持非导出，真实clean build通过。

## 3. 还需补齐的交付面（不能继续标完整完成）

- 受理响应丢失且platform_run_id缺失时，Reconciler只返回no_platform_run_id，没有按可信metadata查找既有运行；没有自动调度及撤销成员后的受限服务只读校准。保持unknown避免假成功是正确的，仍不等于补偿恢复已完成。
- 实际BFF不保存原问题快照，只有摘要/可变线程与message_id；没有不可漂移版本引用。content API还按整个线程取全部快照而非该request。热门问题分析的可授权稳定原问题来源不能留到后续隐式补齐。
- 契约ownership公开的`/agent-events/browser`、`/agent-requests/...`、`/agent-runs/...`与实际`/agent/events/browser`、`/agent/requests/...`、`/agent/runs/...`不一致。所谓“运行时写入端点”和原文权限/真实OpenAPI应给可调用版本，不只是逻辑表。
- 作者报告头部“未完成”与后面补完段相冲突，任务38项却写31/31；测试计数也需统一最终范围/环境，旧HMAC治理套件的确需迁移，不把它算本轮新缺陷，但也不能证明治理真实完整通过。

## 4. 评分、速度与下一步

| 维度 | 得分 | 证据 |
|---|---:|---|
| 契约正确性与业务可用 | 18/30 | 正向结构与六图接入存在，提交重放/恢复/落账关键边界不成立 |
| 权限和数据边界 | 13/25 | 基础RLS延续，但事件关联、删除归属/禁读及保留期失效 |
| 测试与验证可信度 | 10/20 | 44项专项/真实正向可复跑；测试未覆盖反例，生产构建及完成统计与声明不符 |
| 可维护性与项目契合 | 12/15 | 复用已有服务、独立包和契约生成；跨实现语义漂移、复杂服务需收敛 |
| 范围控制与交付完整 | 7/10 | 范围基本守住，预留类型未冒充采集；过早全部勾选与接口交接未闭合 |
| 总分 | **60/100，首次诊断性** | **未通过，待定向修复，尚未达到独立验收** |

效率暂记 **3/5**：首送审1小时52分，交付更快，但仍需实质修复；最终达验收总耗时未知。它是跨任务观察，不能和模型01身份工程直接排名。运行性能/Token仍未知。当前没有足够证据将任一匿名模型选为稳定主力。

下一步只给匿名02一次明确的定向修复；完整提示见`../handoffs/agent-event-ledger-review-01-goal.md`。修复后再独立复验，保留首次输入、评分和时长。第3/5仍暂停，v1不冻结，change2不归档；change1两项生产门禁继续保留。本轮没有代替匿名02修改产品代码。
