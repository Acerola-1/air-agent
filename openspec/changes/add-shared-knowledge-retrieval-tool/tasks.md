# Tasks

核心实现与独立补修已完成，完整验收仍有未完成项。以下复合任务包含实施与验收条件，checkbox 未勾选不表示尚无代码；严格区分代码完成、受控测试、真实服务、真实配置模型、浏览器与正式切换，[验收矩阵](acceptance.md)未通过的项不能勾成实测完成。

> **实施进展（2026-10-20）**：三仓核心链路（IAM 委托 → RAG 受限消费 → Agent `knowledge_search` → 引用存储/原文代理/前端）已实现并完成真实 IAM/RAG/Agent PG 与桌面浏览器联调；完整 A01—A25/K01—K23、合成 F-A…F-L 资料、12 格、性能与删除/恢复门禁仍未执行。详见[实施与验收记录](../../../docs/reviews/shared-knowledge/20261020-implementation/REPORT.md)。以下 checkbox 保持未勾选，避免把部分验证写成全部完成。

## 1. 契约、基线与隔离资料

- [ ] 1.1 冻结三仓HEAD/脏源码摘要、实际进程/镜像/锁来源与已知失败身份，建立自有IAM/Agent PG、Redis、RAG资料/端口/角色清单；验证实际实例不是正式15432/19132或共享RAG，保留所有权和回收记录。
- [ ] 1.2 按contracts.md建立IAM签发/委托内省、RAG受限操作与批量来源校验、Agent工具/引用DTO及合法非法样本；三方契约测试验证知识专用权限投影、字段/schema/operation/修订/错误码、RFC8785来源摘要向量和剩余deadline一致。
- [ ] 1.3 在自有RAG创建F-A/F-W/F-B/F-I/F-L合成资料与角色矩阵，保存原文/hash/入库与真实document/chunk映射；验证同chunk_id跨workspace不覆盖，正向查询能检索预期片段。
- [ ] 1.4 把K01—K23、三图12格、预算/模型/provider/检索profile固定为可执行数据和驱动入口，验证每题/每格独立输出结果，失败/中止也生成新目录报告，不以静态文件存在替代接线。
- [ ] 1.5 用官方镜像对on_thread_read/create_run/custom HTTP及只读校准入口做路径探针，冻结state/history、runs各stream模式/replay能否拦截的证据和缺口；任何缺口须在6.5建立保护代理并关闭原绕过入口后才能发布知识功能。

## 2. IAM受限用户委托

- [ ] 2.1 新增签发DTO/路由与knowledge-delegation:issue内部client scope；实现ref归属、runtime受众、当前企业、四修订、PG/Redis源会话与有效知识权限检查，覆盖A07/A08/A10的正反例及API文档。
- [ ] 2.2 实现256位opaque只读单operation令牌、摘要存储、TTL≤60秒及源期限上界；验证无明文落库/日志、不可登录/生成/签票/切企业/再委托，A09通过。
- [ ] 2.3 在现introspect新增独立版本委托分支与knowledge consumer校验，每次重读源状态和权限；测试token未到期撤销/改密/停用/权限回收/上下文切换及IAM故障，A12/A13通过。
- [ ] 2.4 如需新增IAM存储，按该仓当前head追加迁移/TTL清理和最小DB权限；验证空库/存量升级、并发签发、过期和恢复后失效，不修改已执行revision。
- [ ] 2.5 回归原human/service_account内省、SSO、runtime签票和执行ref契约，验证旧audience规则未放宽、原接口兼容、无新长期派生登录会话。
- [ ] 2.6 更新IAM受限接口与部署配置模板、内部client授权说明，私有测试scope通过正常管理面配置；提交脱敏配置交接，不改正式客户端或root，验证示例无秘密且可在自有环境复跑。

## 3. RAG委托消费与纯检索保护

- [ ] 3.1 增加严格委托内省解析与只读operation路由限制，原用户/源session/tenant/修订映射稳定auth_binding；以新token续签读旧record及错session反例验证A09/A14，保留既有登录/机器路径测试。
- [ ] 3.2 对query/record/source/document保留实时知识权限、entitlement、映射及快照核验；默认all与显式混合非法库覆盖A10/A11/A12，真实越权检索增量0且正向控制有效。
- [ ] 3.3 实现受限批量引用validate接口与至多50项限界，验证未知/错owner/跨企业/旧digest/删除/过期状态；无权项不返digest，无墓碑/保留期证据返回unknown，不将token到期当record过期，补接口文档与样本。
- [ ] 3.4 对query增加总deadline与取消传播，PG按剩余时间设置timeout并仅取消本任务；故意慢查询验证HTTP结束后PG活动/连接回收及不影响其他用户，K19/A22通过。
- [ ] 3.5 对委托原文text增加最多8000字符分页，PDF安全流式/Range并保留文件白名单/hash/owner；测试任意URL/重定向/路径/跨doc拒绝以及合法text/PDF正对照。
- [ ] 3.6 更新RAG协议与remote部署文档，验证纯PG不调用额外生成/Embedding/rerank、记录保留支持原文；增加有效空查询empty_discovery原因，区别其他missing并保留retrieved/partial原状态，不改检索算法/索引/正式数据。

## 4. Agent共享工具与客户端

- [ ] 4.1 实现common/knowledge的工具契约/惰性工厂及受管HTTP客户端，在runtime_tools注册稳定knowledge_search；导入测试证明无外部I/O、不会把private配置或资料库目录透传schema。
- [ ] 4.2 实现可信runtime ref到短委托的调用内申请/核对与library_codes缩小，不能由模型传tenant/workspace/地址/凭据；逐发送/重试/恢复复查，A08/A11/A12与秘密扫描通过。
- [ ] 4.3 实现上游响应的严格规范化、状态/完整性/verified/复合证据身份、缺页码、20秒/1MiB/片段/字符上限及无自动重发；K06/K07/K10/K16—K19与受控取消测试通过。
- [ ] 4.4 复用现有工具逻辑/尝试/阶段观测与可信运行关联，记录knowledge-api本地HTTP标签及实际覆盖；A21守恒、失败分类/未知用量/不重复计数通过，必要契约扩展同步源登记和各生成副本。
- [ ] 4.5 编写客户端/工具使用与故障定位文档及可复跑受控测试，区分无权/无命中/来源失效/服务失败，保证没有共享账号或local身份兜底。

## 5. 三图默认可见与执行接线

- [ ] 5.1 QA修改所有意图/显式Skill的模型可见列表、ToolNode和通用工具过滤；覆盖knowledge/data_query/chitchat与MCP空集合，验证schema有且纯问候真实调用0，记录旧零工具断言的产品变化。
- [ ] 5.2 分析图同步ToolNode和allowed-tools空/非空分支，允许无菜单知识检索但保留菜单驱动数据分析和显式Skill冲突；A03/A04成功及拒绝路径都有真实执行计数。
- [ ] 5.3 tracing lead显式加入一个共享知识工具，保持原4工具与inject_mcp_tools=False；测试主schema、实际知识调用和四专员工具/确定性职责未扩大，A05通过。
- [ ] 5.4 公共执行包装器、模型工具预算、MCP动态刷新与原生/BFF接线统一复核，工具不能被静默删去或重复注册；执行三图12格受控矩阵并更新三图开发文档。

## 6. 强制依据、引用存储与回读

- [ ] 6.1 实现KnowledgeAnswerPolicy的请求及最终断言/原文支持检查，receipt不得由模型/浏览器伪造；所有未判定正文/画布先服务端缓冲，K03/K11/K12及新增K21/K22跨chunk与真实来源错数值/单位/年份/范围/否定词反例通过，不能仅靠引用ID或ui_visible放行，合法非拒答控制有效。
- [ ] 6.2 新增引用/receipt/回答映射及受控摘录存储，当前Agent迁移head追加并启用非owner/FORCE RLS/复合关联/保留期；验证空库/存量升级、跨企业/no-context拒绝与并发幂等，登记失败不公开来源。
- [ ] 6.3 新增受保护Agent引用text/pdf API与同源BFF，可信映射record/evidence并每次重新委托；A16/A17含错owner/session/context、URL/路径攻击及合法分页/PDF，补DTO/OpenAPI与客户端契约测试。
- [ ] 6.4 实现工具长输出/摘要/画布/引用归档的来源身份保留；伪造页码/链接、旧revision及恶意原文K08/K09/K10/K16通过，普通观测不得含原文。
- [ ] 6.5 按1.5探针在native state/history/replay、BFF历史、工具归档、报告回读/恢复及模型上下文复用前核验派生依赖闭包，必要时建保护代理并关闭绕过入口；K13/K14/K23/A20含摘要去ID、原始消息压缩移除、51项末项失效与迟到写入，分批全量验证；缺依赖或无法安全剥离时拒绝受保护结果。
- [ ] 6.6 接删除墓碑、受控正文清理和备份恢复规则，验证恢复后仍不可读被删资料、旧checkpoint不能重放；与Change6负责人串行合入生命周期/正文接缝，并更新隐私/保留/回读文档。

## 7. tracing知识依据与旧报告兼容

- [ ] 7.1 实现独立KnowledgeReferenceRegistry和ReportPlan引用ID选择，绑定scope/analysis/revision及检索receipt；伪造/跨范围/旧revision拒绝，知识不进入EvidenceRegistry和AllowedClaim。
- [ ] 7.2 扩展ValidatedReport的knowledge_basis与分版本digest，旧JSON按旧规则验证不补造来源；测试旧/新/空引用/损坏摘要及RLS存取映射兼容。
- [ ] 7.3 ReportGate独立校验并确定性渲染知识依据，保持分析正文归因门禁；K09/A18证明合法原文可引用但不能抬高污染结论或绕过扫描，非法自由正文拒绝。
- [ ] 7.4 正常/fallback/build_report/发布重建/_report_is_current/画布/持久化回读共用引用上下文，K15/A19/A20验证版本一致、撤权/删除/晚到结果以及原tracing回归；更新报告契约和迁移兼容说明。

## 8. 引用与原文界面

- [ ] 8.1 读取frontend/AGENTS、当前安装Next Route Handlers指南和前端设计技能，复用现有消息/原文阅读组件实现真实引用ID入口；验证模型任意URL不能伪装知识来源，无页码状态真实显示，更新DESIGN/UX-CONTRACT。
- [ ] 8.2 实现加载/分页/无权/过期/来源变化/503的中文交互、no-store及取消/epoch隔离；前端契约测试覆盖K20和每次点击/分页核权，不将Bearer/原文放URL或localStorage。
- [ ] 8.3 真实桌面/390×844与键盘验证三图引用、原文/PDF、错误重试和A→B→A，留浏览器证据；界面不显示内部delegation/受众/技术workspace等实现细节。

## 9. 三仓组合验收与交付

- [ ] 9.1 在最终三仓锁/官方平台/隔离PG与真实IAM/RAG组合执行A01—A25及K01—K23，受控模型、真实配置模型分层；每格有真实知识请求与来源打开，不以工具调用成功代替最终答案/报告完成。
- [ ] 9.2 执行并发1/5/10每档35样本×3轮客户端新增开销、真实检索、取消和资源恢复；A23报告P50/P95、成功/失败/覆盖、IAM/RAG等待及成本/未知，不宣称受控结果为生产容量。
- [ ] 9.3 三仓分别运行相关Ruff/pytest/Maven/前端契约与构建/迁移权限回归，按失败身份比较既存基线；本题库已知概念意图反例不能删题过关，不顺手改 unrelated 业务。
- [ ] 9.4 独立复核12格矩阵、委托越权真实请求0、原文跨会话/删除、流中间帧、旧digest与计数守恒，生成新目录验收报告；列原始成绩、补修、实测耗时、外部等待与不可得Token。
- [ ] 9.5 给出三仓交付diff、迁移/客户端scope/受保护配置/启动入口/运行切换与回滚清单，回收本轮自有资源并核对秘密/证据大小；正式环境未操作与仍未验证项逐条记录。

## Workflow follow-up

- 2026-10-20 用户授权保存三仓当前成果为本地 dev 阶段提交，作为 Change 6/7 开发起点；允许提交不代表完整验收通过或准许归档。提交依据和后续边界见 [阶段提交记录](../../../docs/reviews/shared-knowledge/20261020-dev-commit/REPORT.md)。性能评测不阻塞 Change 6/7 开发，知识库功能/安全未验收项继续跟踪。

- 2026-10-20 独立验收补修与证据见 [REPORT](../../../docs/reviews/shared-knowledge/20261020-independent-acceptance/REPORT.md)。局部 G0/G1/G3 已补，但三图 12 格、完整原生回放/删除恢复和性能门禁未通过；保持任务未完成，不以补修单测代替全矩阵验收。

- 实施按[handoff](handoff.md)进行，不能把本轮计划写作当业务代码实施授权。
- 独立验收后按用户后续明确授权进行正式配置、部署/运行切换、主规格同步、归档、commit/push或合并；不自动执行。
- 尚未通过的真实身份、原文、浏览器、恢复及性能门禁保留为未完成，不能把strict通过或checkbox改写成实测完成。
