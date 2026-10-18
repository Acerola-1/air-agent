# I2 首次交付独立验收：GLM 5.3 flash

日期：2026-10-17。身份由用户直接提供，本次不是身份隐藏的盲评。采用 `flash-blind-evaluation.md` 的同一五维标准，模型名称不改变评分方法。

**首次交付：未通过，42/100。模型独立返工 0 轮，用户要求由评审者直接补修。后续补修不改变本成绩。**

## 冻结输入与核对

- Change：`switch-agent-to-remote-iam`（用户称 L2），Agent `dev`，基线/首交 HEAD `319dab499585e640d0d216699cd767a3a1c567e1`；首交未提交。
- 冻结 IAM：`8da8ba1127b7ea7aa9c0a9f7289d90cbcc96a368`。读取 proposal/design/contract/spec/tasks/acceptance 和实际新增、修改文件后评审。
- 修改前冻结 37 个交付文件、SHA-256、tracked diff 和作者报告，见 `evidence/run/i2-review-20261017/input.json`、`first-delivery/`、`first-delivery.diff`。完整基线/候选在 `/tmp/i2-independent-20261017/{baseline,candidate}`；不把后续代码混入首交。
- `docs/reviews/anonymous-model-scorecard.md` 的原有未提交内容原样保留，本轮仅追加。
- 作者报告声明 tasks 15/15、A01–A12/A14 通过，同时承认 remote 正式容器、真实浏览器未验证，24 格实际使用 local 身份，六格受模型网关阻塞。上述缺口与勾选完成矛盾。

## 五维评分

| 维度 | 得分 | 首交证据与判断 |
|---|---:|---|
| 契约正确性与业务可用 | 13/30 | 有显式 remote 客户端、引用绑定和 SSO 框架；正常签票路由未接绑定，native 导入错误，引用提取丢字段，remote 正常运行无法闭合。 |
| 权限和数据边界 | 10/25 | 无 IAM 故障回退设计、摘要绑定及修订比较有价值；同步外呼仍读旧身份库，投影平台 GUC 未复位，remote 写路由缺 Origin 依赖，五张新表未启用所声称的 RLS。 |
| 测试与验证可信度 | 6/20 | 33 项作者单测独立复现通过，首交全单测 399 通过/75 跳过/5 个既有失败；但最关键正常入口未测，local 矩阵替代 remote、未跑浏览器仍勾全任务。 |
| 可维护性与项目契合 | 10/15 | 模块、迁移方向和技术投影基本合理；异步路径阻塞 HTTP、重复客户端/签票副本、绑定异步游标/清理 SQL 错误及错误状态映射尚需整合。 |
| 范围控制与交付完整 | 3/10 | 正式切换前置单列、未提交值得保留；首交完成声明过满。作者还自述更改共享运行角色密码、重置共享测试账号，不符合隔离输入，缺少可核实前态，未擅自回滚。 |
| **总分** | **42/100** | **未通过；评分用于解释本轮交付，不等于完成百分比。** |

## 独立发现与复现

1. **P1：native 认证 ImportError。** `langgraph_auth.authenticate` 从 `common.identity.remote` 导入实际定义在 `ticket_binding` 的 `resolve_binding_for_ticket`。冻结版本用有效签名票据执行真正认证函数，得到 ImportError，未触及 IAM。
2. **P1：签票未接入门面。** `/auth/runtime-ticket` 仍本地签票，不调用 facade/register；作者桥接脚本直接调用 helper，不能证明正常 BFF 入口可用。
3. **P1：可信引用丢失且同步边界未切换。** 两种正常提取器均丢失 `verification_ref`；remote async guard 拒绝合法运行，sync guard 仍查本地 PG。作者测试直接构造 RuntimeIdentity，绕过这条接线。
4. **P1：数据库平台作用域残留。** 原版投影设置 platform=on 后没有 finally 复位。真实非 owner PG 调用后 `current_setting` 仍为 on，后续请求内业务查询继承旁路。
5. **P1：remote 写接口没有 CSRF 依赖。** 冻结路由真实 ASGI 发起 `Origin: https://evil.example` 的 SSO start 返回303。文件尾的 `router_add_depends` 从未挂载。
6. **P1：双前端接线不完整。** 聊天 middleware 固定旧 cookie；管理端缺 SSO BFF 路由，原 rewrite 与新增路由命名不匹配；浏览器 fetch 跟随跨站授权不是顶层登录导航。
7. **P1/P2：权限/生命周期和协议不完整。** 本地注销应用 handle 不撤销已发运行引用；me/tenants 仍从本地投影/角色组装；recheck 接受与请求不一致的返回修订；异步调用阻塞事件循环，总 deadline 只是分阶段 timeout。
8. **P2：绑定与清单缺口。** async fetchone 未 await、DELETE LIMIT 非 PG 语法；inventory 未比对存量业务账号、冲突也未阻止 apply；未完整展开分页。

证据：`original-dynamic-results.txt` 是冻结源码上的真实执行；`first-counterexamples.txt` 含10个定向失败（其中部分为源码接线断言，不能称10个网络反例）。原始报告与输入保留，不以修复后的通过覆盖。

## 基线、稳定性、耗时与性能分开

- **实现稳定性：不足。** 单测绿与实际认证断链并存；首交存在多项 P1，不能独立交付。本轮没有模型修复样本。
- 根单测在同样许可环境、追踪关闭条件下：基线366通过/75跳过/5失败；首交399通过/75跳过/同5失败。五个 nodeid 是 checkpoint 关闭及 knowledge_route×4，未归罪本轮。
- 管理端以新建专用 API/RLS 库、原有反馈/能力前置开关运行：基线189通过/7跳过，补修191通过/5跳过；未删除断言或增加 skip。首交作者191/5另标作者报告，未冒称此项是冻结首交全管理端独立复跑。
- **模型实施耗时：143分钟/2小时23分，用户提供。** 实施/模型自测/外部等待未分段采集；Token、工具调用、成本未知。模型独立返工0轮。评审者的代码与时间不计入GLM。达完整验收总用时未知。
- **身份客户端性能：** 同机、自有 IAM、暖连接，并发1/10/30各100×3，首交900次与补修900次均0错误、每操作1次核验。并发30的首交P95三轮136.294/129.022/129.337ms，补修106.885/118.381/93.724ms。原始P50/P95/P99见对应JSON。只测身份复查，非模型或完整业务性能；自有 IAM 配额放宽至10000/min以避免默认600/min限流，不能外推生产配额下表现。连续N次与完整应用资源/生产门禁未采集。

## 通过门槛与比较结论

违反“重要正常路径可用”“权限边界”“未验证不得勾完成”的门槛，首交不能通过。L2比前两轮单个反馈/观测任务复杂；不能根据143分钟和42分直接重排GLM与DeepSeek。L3尚未在本次进行独立验收，不能先宣布哪个模型胜出。

后续状态见 [评审者补修记录](agent-remote-iam-takeover-validation.md)。GLM成绩固定保留。
