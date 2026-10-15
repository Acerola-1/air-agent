# 匿名运行05首轮修复独立验收

2026-10-15，Asia/Shanghai。Change：`add-skill-tool-selection`。结论：**未通过，仍需修复；有实质进步，不可归档。** 本轮协调者只审计、运行探针和记录，没有补修产品代码。

## 用时、输入与评分

| 项目 | 记录 |
|---|---|
| 首次交付 | 用户实测131分钟，首评分46/100，保留不变 |
| 首轮修复 | **用户实测83分钟（1小时23分）**；不用作者报告“约45分钟”替代 |
| 累计模型投入 | **214分钟（3小时34分），尚未达到独立验收** |
| 当前诊断分 | **63/100**：契约18/30、权限与隔离16/25、验证10/20、可维护性11/15、范围与交接8/10 |
| 实施效率 | 3/5；较首交改善，不能据不同Change的耗时直接排模型速度 |
| 应用性能 / Token | 完整保护路径性能尚未独立通过；Token未采集 |
| 源码 | `codex/change-04-skill-tool`，HEAD `8620cf78b22cb7234fe650fa5c9f032359e0d70d` |
| 跟踪文件diff SHA-256 | `7ba0604aed879e7ed556081de4f5353c33f73102c7f1614894ae65d1fa83e5db` |

输入冻结85份作者文件（包括磁盘上实际存在的日志）；相较首交冻结清单17份已有文件改变。独立证据在 [flash-05-repair-01-independent](evidence/flash-05-repair-01-independent/)。审计结束前已核对作者输入、上一轮审计文件全部未被本轮审计改写；评分表只在随后追加本轮结果。

## 已确认有效的修复

- R01的原始全新运行阻断已修复：真实管理API受理200，非owner运行角色领取启动权`claimed`；另用新预留调用真实native hook成功，重复消费拒绝；选择开关关闭的Auto路径正常。没有把先领取再调用hook产生的预期409误算成缺陷。
- R02已修复：严格默认JSON载荷受理200；带受控上下文的实际TS编译客户端/BFF探针200；重新生产构建前端后，真实浏览器经管理端签票、实际`artifact_http`目录路由和PG身份复核，展示7个Skill、可选择，Escape关闭并恢复触发按钮焦点。React真实字段渲染通过。这里未启动官方平台运行服务，会话列表的预期不可用不计产品回归。
- R03已修复：独立临时空库从0001到原head成功，再升级新head成功；选择表`ENABLE/FORCE RLS`均为true。临时库随后删除，没有修改旧revision。
- R05部分修复：原来的S2下载、根搜索泄漏及`../outside`逃逸反例均被堵住。仍有SDK兼容问题，见下文。
- R06部分修复：普通`controlled-one/controlled-two`同名工具生成不同代理，选择第一服务能保留对应工具；歧义短名不再落到最后服务。特殊别名碰撞和动态接线仍未完成。
- R07原字典访问及继承catalog问题已修复：真实受理继承快照200，按submission读取，合法MCP恢复的native hook成功。不将手动设置waiting状态的hook探针当成真实SDK interrupt→resume完成。
- R08企业ID变化时清草稿已修复；R09原5工具逐页反例得到tool0至tool4且游标结束，大小截断后游标重算、超时收集已完成任务的逻辑已修正。完整账号/上下文代次与权威回读仍未闭合。

## 仍阻断验收的发现

### F01 / R04：格式校验冒充可信目录校验（P1）

`admin/api/app/services/ledger.py:1004`仅接受符合正则的catalog字符串，没有与当前授权目录/材料/修订计算结果核对。`_validate_mcp_tool_authorization`在工厂目录为空或导入/发现异常时放行；目录非空时又允许“任意其他服务上存在同名短名”替代精确稳定ID。协议检查仅拒绝字面值`unverified`。

独立真实API+PG反例全部返回**200**：

1. `cat_000000000000000000000000` + 从未发现的`does_not_exist`，工厂目录为空。
2. `controlled-one:exists_elsewhere`，该工具只注册在`other-service`。
3. 绑定协议改为未注册的`future-unknown-protocol`，仍可受理。

这证明可冻结不真实的运行计划；**本轮没有据此宣称已实现越权外呼**，执行层可能另行拒绝。另，BFF调用`admitRequest`和其输入类型没有传递`menu_name`，增加管理端字段不能保证正常BFF菜单冲突在受理前422。修复必须验证真实当前目录、精确服务工具、已核验协议、菜单与材料，失败关闭；不得特判`v1`或只拒绝测试字符串。

证据：`live-probes.json`、`live-extended.txt`及对应探针。

### F02 / R01补充：同键改为Auto绕过选择冲突（P1）

`ledger.py:735`只在新请求被归类为`user_explicit`时比较选择摘要；Auto则直接拿旧摘要算新内容。独立API先提交allowlist，再用同键同正文明确提交Auto，得到**200、reused=true，返回旧allowlist**，应为409。必须区分旧客户端省略与显式提交Auto，覆盖双向选择变化，并保留旧v1兼容。

证据：`live-probes.json`的`same_key_explicit_changed_to_explicit_auto`。

### F03 / R06：代理碰撞与真实图动态接线仍缺失（P1）

`src/common/mcp_scoped.py:450`仍将连字符替换为下划线，`service-a`和`service_a`各自的query生成**两个完全相同的`mcp_service_a__query`代理名**。索引标记歧义不等于模型/SDK实际名称唯一。

三张DeepAgents图仍在模块导入时固定`_mcp_tools`；生产`TenantRuntimeGuardMiddleware`只对已有`request.tools`做过滤，没有注入后发现工具。实际导入三图后再登记`late_query`，工厂已有代理，而三图固定列表仍全空。此为真实生产对象装配证据，未冒充实际模型完成。Auto过滤又直接返回全局代理，尚未证明按当前授权scope隔离披露。

证据：`modules-extended.json`、`dynamic-graph-objects.json`；作者的test_task_4_4仍只直接测试一个basic-qa文件后端，没有运行任何子任务。

### F04 / R05：覆盖方法与真实SDK签名不兼容（P2）

`src/common/capabilities.py:775`的glob/aglob缺少SDK的`path`参数。真实调用`glob('*.md', path='/s1')`直接TypeError；调用自己的`aglob('s1/*.md')`也因基类再次调用`self.glob(pattern,path)`而TypeError。正向同步`glob('s1/*.md')`可读，证明确为覆盖签名错误。根ls仍列出S2目录，需按已约定的显式范围处理；本轮未观察到新的S2正文泄漏。

证据：`modules-extended.json`。应验证实际DeepAgents文件工具及子Agent，而非只增加未使用的方法名。

### F05 / R08：从草稿伪造“已受理快照”（P1）

`frontend/lib/chat-store.ts:274`在确认成功后重新读取**当前draft**，将run_id当selection_id、digest写空，再填入acceptedSelection，完全没有从服务端回读快照。

编译实际TS的时序探针：发送S1，延后确认，将草稿变成S2，再放行确认；“已受理”被填为**S2、id=platform-run-id、digest空**。即便通常UI在飞锁定，也不能把此拼接作为权威事实，尤其retry/regenerate继承与刷新历史。store/effect仍不按user与ctx_version建作用域。企业ID切换修好不代表这些部分完成。

证据：`accepted-readback.json`、`probe-accepted.cjs`。

### F06 / R10：实际使用与已受理计划仍不能完整关联（P1）

`src/common/observability/skill_adapter.py`没有将可信selection_id/digest/submission_id传给新扩展的record_span参数；loaded缓存只存版本，executed再次固定`source='loaded-rules'`。对真实适配器排队回调的独立探针得到loaded来源manual，executed来源loaded-rules，两个都缺选择关联参数。新增store参数本身不构成调用链实现。

证据：`modules-extended.json`的`skill_span_calls`。A19、任务6.2目前不能认定完成。

### F07 / R10：证据结论仍超出数据（P1），静态检查未全绿

- 作者撤回任务5.4和7.1的完成勾选，值得保留。但1.3、4.4、3.5、6.2等仍勾选；A16的六图显式/真实恢复、A18备份恢复重施墓碑、A19关联没有本轮足够证据。A15未完成不自动涵盖这些独立验收项。
- `skill-tool-selection-repair-01.md:129`写受理Auto P95为7.120/6.980ms，JSON实际为**73.490/102.342ms**；显式P95写9.660/9.420，JSON为**76.034/26.989ms**。第二轮净增写2.440，JSON为**-75.353ms**。报告称故障20/20，脚本和JSON为**5次**。
- `benchmark_admission_overhead.py:273`的正常错误率仍写常量0、错误数也写0；只累加成功请求，不能证明错误率。脚本新增的是ASGI+真实PG受理采样，并没有覆盖完整native启动/真实模型运行，不应统称全部保护性能完成。未据上述不一致推断作者主观意图。
- 本轮显式检查新迁移仍有**9个E501**；作者没有修该文件。生产构建与strict通过不能替代此项。

## 本轮独立验证结果与限制

| 验证 | 结果 |
|---|---|
| 作者核心4份测试 | **28通过**；初次受沙箱loopback限制，允许本地网络后重跑通过，环境失败不扣分 |
| 管理台账与选择专项 | **44通过/1失败**；唯一collection_disabled断言与原基线相同；原至少7个新增台账回归消失 |
| 前端契约 | **46通过** |
| 根单元 | **361通过/75跳过/6失败**；失败身份与上一轮同环境的原HEAD、候选一致，无新增 |
| 新空库与旧head升级 | 成功，新选择表FORCE RLS开启；本轮没有完整复做跨租户/备份恢复矩阵 |
| 新生产构建 | 成功，包含TypeScript检查；第一次沙箱构建停滞，终止自有进程后在允许环境成功 |
| OpenSpec本Change strict | 通过；未改9份历史主规格格式失败 |
| 新迁移及核心模块Ruff | 新迁移9个E501，未通过 |
| 浏览器 | 新构建+真实目录、7 Skill、单选及Escape成功；非官方平台运行，未完成390px/IME/24格/真实商业模型矩阵 |

OCR预览72项：53项作者文件审查（33项与首交同指纹沿用首次审查，20项本轮修复重点审查），19项上一轮协调者自有审计产物不重复当作者代码评审，仅核对保留。全部有状态，详见`review-coverage.json`。**文件覆盖不等于行为验收覆盖。** 原始日志没有因Git忽略而被记为不存在。

## 下一步

继续同一Change，按 [第二轮定向修复目标](../handoffs/skill-tool-selection-review-02-goal.md) 收敛，不进入下一Change、不归档。优先真实目录与幂等、三图动态工具及唯一别名、权威回读与观测关联，最后补SDK/浏览器/恢复矩阵。不能仅再跑原来的绿测试。

模型目前**未独立完成**，协调者本轮也**未接管补修**；不提前写“由Codex完成”。不commit/push/合并/清理作者容器。仅停止本轮自建8104/3104/29999服务、关闭自建浏览器标签页、删除自建迁移临时库；作者PG/Redis及其他IAM/知识库资源保留。审计前测试库备份仅置于权限600的临时目录，未入库。
