# Change4最终独立验收与协调者补修

日期：2026-10-15。变更：`add-skill-tool-selection`。分支：`codex/change-04-skill-tool`。起点与独立验收时HEAD：`8620cf78b22cb7234fe650fa5c9f032359e0d70d`；全部成果仍在未提交工作区。

## 结论与归属

收到的接管稿不能直接认定完成。它修复了前两轮部分问题，但仍有目录校验、操作继承、权威回读、三张DeepAgents规则实际加载、观测关联、恢复权限和浏览器交互缺陷。协调者已直接补修，完成代码与本地集成验收。**28/29项任务完成，5.4只保留系统中文输入法候选窗的物理选词抽查**；尚未获得豁免，不伪报实机通过，不归档。

Gemini3.8 Flash（用户提供的名称）运行05仍为首次46、首修63、次修58分；模型实测投入131+83+60=274分钟，最终未独立完成。用户粘贴的另位接管Agent身份和耗时未提供，不能并入Gemini成绩。本报告新增修复与验证属于协调者，不给匿名模型补发完成分。评分表已追加真实归属。

证据根目录为 `docs/reviews/evidence/takeover-05-final/`，下文文件均相对此目录。原交付、两轮修复、接管报告及历史证据保留。`incoming.json`记录开始时文件指纹与tracked diff SHA256；`final-source-fingerprint.json`记录最终产品/测试来源，供下一次接管核对。

## 实际补修

1. **受理与授权**：MCP-only及显式空集合也校验目录版本；工具存在性使用当前成员实时授权发现的目录，不能使用另一进程或另一成员填充的全局缓存。真实分离进程中合法目录可受理，伪造目录409。
2. **幂等与继承**：在查提交幂等前归一化retry/resume继承，修正省略选择首次成功、重放却冲突的问题；retry/resume不得改变原计划，regenerate显式Auto可以切回自动。已确认的同提交映射不能被迟到unknown降级，恢复提交与原提交分别确认。
3. **权威回读**：本人线程的指定平台run或最近已确认提交，返回数据库实际选择UUID与摘要。BFF补映射核对request、attempt、submission三项；前端不再从当前草稿拼造UUID/hash，流结束后的同代迟到确认仍可生效，旧代结果不能更新新视图。刷新打开历史会话同样回读。
4. **真实执行**：三个业务图取消请求时匿名MCP发现；六图按可信身份刷新与过滤动态代理。DeepAgents显式S1在第一次模型请求前确定性加载完整规则，校验材料指纹并保持包含工具定义的完整请求预算，不能等待模型自愿find_skill。主任务和实际SDK子任务均落实工具及规则边界。
5. **观测事实**：业务规则、Deep文件读取与executed保留manual；loaded缓存按tenant、attempt、submission精确消费，不借同attempt的另一提交回调补造执行事实。真实S1材料、loaded正文指纹、选择摘要和提交关联逐项对齐。
6. **恢复与删除**：恢复无ACL备份后重施选择表运行角色权限及FORCE RLS，随后重施删除墓碑。旧选择与观测不可读，晚到选择INSERT与观测写入均被RLS拒绝。
7. **前端完整流程**：取消弹窗不提交草稿，Complete才应用；真正的radio键盘导航、分页/epoch/version保护、stopPending锁定、当前用户/企业/context变化清理、“最近已受理”独立显示、中文技能名称与可恢复服务故障提示。
8. **输入与停止**：Composer增加composition生命周期、native isComposing与Safari229保护。实际停止复验又发现断流早于首条metadata时，本次运行已interrupted却无法找回；增加BFF覆盖的本次提交键关联，不能误认上一轮或其他提交。空线程尚未创建完成时停止，不再等待不存在的运行，迟到创建也不能继续发起模型。停止/删除透传真实Origin供管理端校验，移除固定localhost:3000；停止确认成功清除旧错误。

## 运行证据与真实性

使用官方LangGraph API **0.15.1**、真实PostgreSQL及原配置的 **hy3** 模型。最初`.env`网关宿主桥接地址192.168.64.1已失效；验证宿主8787同一网关/模型健康后，仅在本轮私有测试环境改为192.168.66.1，没有更换模型、凭据或供应商，没有修改项目`.env`。第一次fallback回答保留为诊断，**不算成功格子**。

- **24条Auto**：六图×native/BFF×stream/wait，全部真实回答完成。
- **12条显式请求**：六图×native/BFF wait，全部真实回答完成。原Deep目录只有.gitkeep，所以这些Deep请求的Skill为Auto、MCP为显式空集合，不能冒充S1加载。
- **另6条Deep显式S1**：临时加入受控本地S1材料，三Deep图×native/BFF真实调用原模型。与前三业务图六条S1一起，共**12条S1真实loaded/executed**，材料版本、正文SHA、manual与选择/提交关联均核实。三份临时材料已定点移除，内容保存在`deep-fixture-materials.json`，不作为产品技能交付。
- **12条真实暂停恢复**：六图×native/BFF。该镜像static interrupt_before会返回run status success，但checkpoint.next仍有下一节点；验证脚本读取实际next后记录waiting_input再resume，没有用run success冒充整图结束。恢复创建新平台run、沿用attempt及不可变计划，实际模型完成。
- **6张真实SDK图的受控MCP**：模型输出受控，独立HTTP fixture实际收包。业务图使用现有Skill白名单内的helper_get_latest_time；Deep主/子任务允许T1实际调用各1次、直接要求T2被拒且无额外网络；S1主/子实际读取成功，S2读取失败。这里证明控制边界，**不等于真实企业平台MCP协议已验证**。

主要索引：`api-matrix-combined.json`、`api-matrix-reconfirmed.json`、`matrix-ledger-observation-proof.json`（原36条均confirmed/succeeded、回答与成功模型观测齐全）；`deep-explicit-fixture.json`、`skill-stage-real-proof.json`（12条S1）；`interrupt-resume-combined.json`；`sdk-dispatch.json`、`business-dispatch.json`、`business-disclosure-verification.json`；`process-boundary.json`。

BFF wait/resume的迟到unknown确曾降级映射，原失败结果保留；修复后对既有真实run重放安全确认并查数据库收敛，未为凑矩阵重新创建相同业务运行。最初BFF harness错误地继续使用调用方thread UUID，已改为使用代理实际返回的thread_id，旧诊断输出保留。不得把探针错误或旧失败JSON当成最终结果。

## A01–A24证据映射

P=真实PG/HTTP，S=实际SDK+受控模型/独立HTTP MCP，U=单元/契约，B=真实浏览器。通过仅限这些层次；不把U或S称为生产平台实测。

| 项 | 最终结果与证据 |
|---|---|
| A01 | P/U：身份与目录鉴权、停用/故障失败关闭；`tenant-rls-pytest.txt`、`core-with-preload.txt`、目录端点测试 |
| A02 | P/S：作用域授权、无权零发现、双企业修订隔离；目录套件、`sdk-dispatch.json` |
| A03 | S：双服务同名与编译后动态分发正负控制；`core-delivery.txt`、`sdk-dispatch.json`、`business-disclosure-verification.json` |
| A04 | U/P：部分故障/超时/游标/分页与客户端版本变化；目录套件、`frontend-contracts-stop-final.txt` |
| A05 | P/S/U：S1、显式空集、格式/超限/目录伪造；`process-boundary.json`、`skill-stage-real-proof.json`、管理专项 |
| A06 | P/U：真实菜单一致及冲突/明确依赖反例；管理专项与业务执行套件 |
| A07 | P：同键并发、摘要冲突、event/outbox回滚；`admin-delivery.txt`管理台账及选择专项 |
| A08 | P/U：旧v1自动重放、历史not_collected、新Auto快照；同上 |
| A09 | P/U：三个config/metadata副本冲突在调用前拒绝；六图约束套件 |
| A10 | S：T1正向收包、T2直接请求失败且额外网络0；`sdk-dispatch.json`、`business-dispatch.json` |
| A11 | P/S/U：业务S1实际规则匹配与完整预算负例；`skill-stage-real-proof.json`、`explicit-preload-unit.txt` |
| A12 | S/P：三Deep主/实际子任务与规则读取边界，另有原模型S1真实运行；`sdk-dispatch.json`、`deep-explicit-fixture.json` |
| A13 | P/S/U：实时身份/绑定撤销、重试/子任务失败关闭；目录/六图套件及租户专项 |
| A14 | P：retry/resume省略与同键、regenerate显式Auto；管理专项、`interrupt-resume-combined.json` |
| A15 | P：24格实际成功，平台/台账/回答/模型usage关联；`matrix-ledger-observation-proof.json` |
| A16 | P/S：12显式+6补充S1，12恢复，S1各阶段及实际MCP分层验证；上述运行索引 |
| A17 | P：非owner A/B/同人双成员、复合FK与本人回读；`tenant-rls-pytest.txt`、管理专项 |
| A18 | P：真实dump/restore无ACL、重施权限与墓碑，晚到写入拒绝；`restore-final.txt`、`restore-deletion.json` |
| A19 | P/S/U：selected/loaded/executed分离、manual和同提交关联，禁止跨提交消费；`skill-stage-real-proof.json`、新Deep单元 |
| A20 | U：登记及三validator副本、旧事件与敏感反例；`core-delivery.txt`、管理/前端契约 |
| A21 | B/U：A→B→A/历史/在飞与迟到确认、epoch与失败JSON；`browser-evidence.json`、前端57项 |
| A22 | **部分完成**：B桌面/390/键盘/焦点/Escape/失败恢复/真实点赞/停止通过；U实际Composer组合事件通过。系统IME候选窗仍待物理抽查 |
| A23 | P/U：从零与旧head升级、新head单一、FORCE RLS、flag与回退；`migrations.txt`、`admin-delivery.txt`、六图约束 |
| A24 | S/P/B/U：完整保护开销与3/5受影响回归，详见下表；真实停止`browser-stop-final.json` |

## 性能与回归

性能使用真实完整DeepAgents图、真实PG身份守卫、作用域目录、显式规则加载、完整请求预算及独立HTTP MCP；模型输出受控，不包含商业模型等待。每种模式每轮35样本，共2轮、140次测量，错误0。不是早先只测helper的1.0996ms。

| 轮 | Auto P50/P95 ms | 显式Skill+MCP P50/P95 ms | P95差 ms |
|---|---|---|---|
| 1 | 158.675 / 195.409 | 161.187 / 206.242 | +10.833 |
| 2 | 176.397 / 202.995 | 176.801 / 193.221 | -9.774 |

两轮新增P95均低于50ms；负差属于样本波动，不能宣称零开销。原始样本见`full-protection-benchmark.json`，外部模型等待在此受控基准为0，真实模型矩阵耗时另存，不能把真实provider等待说成0。

| 检查 | 结果 | 原始输出 |
|---|---|---|
| 管理端全套 | 161通过 /35跳过 /0失败 | admin-delivery.txt |
| 租户/身份/RLS专项 | 另建空库30通过；已销毁，未对现有库TRUNCATE | tenant-rls-pytest.txt |
| 实际备份恢复专项 | 1通过 | restore-final.txt |
| 核心+新Deep回归 | 20通过；另7项确定性加载/框架兼容通过 | core-with-preload.txt、explicit-preload-unit.txt |
| 根单元 | 365通过 /75跳过 /6既有失败 | root-delivery-final.txt |
| 默认集成 | 7通过 /31跳过 /1既有失败 | integration-final.txt |
| 前端契约 | 57通过 /0失败，含实际Composer、提交键停止与Origin | frontend-contracts-stop-final.txt |
| TS/生产构建 | 最终来源通过；最初清缓存构建亦通过 | tsc-stop-final.txt、frontend-build-stop-final.txt、frontend-build.txt |
| Ruff | 38个受影响产品/测试文件通过 | ruff-final.txt |
| OpenSpec | 本change strict通过；不冒充历史9份主规格已修 | openspec-final.txt |

根6项失败身份：artifact_db.test_pg_failure_never_falls_back（本测试环境设置了有效RUNTIME_URI，覆盖测试改写的DATABASE_URI）、checkpointing.test_reset_checkpointer_cache_closes_previous_connection、knowledge_route的test_policy_route_fast_path_skips_llm / test_knowledge_route_fast_path_skips_llm / test_route_cache_reuses_llm_result / test_ambiguous_route_uses_llm_fallback。与同环境起始独立基线相同；不是把新增失败混称基线。默认集成失败仍为500k旧monkeypatch依赖不存在的subagent_summarizer.ModelRegistry。

管理全套35跳过中包含身份隔离等受显式活体验证开关约束的项；相关30项已另建库真实执行通过。不是把全部跳过算成功，其他既有外部活体项亦不在本报告中补造。三张临时S1移除后不再声称该目录有产品技能。

## 浏览器、保留项与资源

真实浏览器步骤见`browser-evidence.json`及截图。点赞确实显示“已保存”；S1受理摘要、刷新打开历史回读、企业B清空旧视图、弹窗取消/键盘与失败重试均已验证。停止最终证据含cancel_requested=true、同提交平台映射confirmed、平台interrupted与页面解锁；不是用平台停止推断业务cancelled。更早在平台尚未确定是否创建时停止，会诚实保持unknown和锁定，不把“找不到活动运行”写成已取消；原诊断保留。

尚缺**系统中文输入法候选窗选词Enter**的物理抽查。已验证真实中文文本、换行，以及实际Composer的isComposing、Safari229、composition生命周期；这些不能替代OS候选窗。5.4保留未勾选。真实企业MCP协议、生产负载等既有门禁继续保留；本次恢复墓碑只验证本地数据与外部清理重排，不声称外部Langfuse删除已完成。工具Token用量未采集。

只清理本轮自建的aa05f-langgraph、8104/3104进程与临时S1，复位浏览器viewport并关闭自己的测试页。原有aa-sel05-pg/redis和合成数据保留，便于复现；共享15432、用户3107、IAM与知识库资源未触碰。清理实绩见`resource-cleanup.json`。独立SDK探针断言、网络和文件证据已完成，但部分父进程在SDK后台资源退出阶段等待，已精确终止并记录`sdk-harness-process-cleanup.json`；不能称这些脚本自行exit0，官方API矩阵请求本身正常完成。

本轮未commit、push、合并或归档。后续应先处理/明确接受5.4物理抽查，再决定归档；无需继续把代码修复返还原匿名模型。

## 用户验收关闭与归档（2026-10-15，后续状态）

用户在收到上述28/29项与IME保留项结论后，明确要求“归档并提交，更新README和Agent指南，push后告一段落”。本次按该指示关闭阶段：5.4物理候选窗仍未实测且保持未勾选，接受保留归档，不能理解为实机通过。上方“未commit/push/归档”是独立验收结束时的快照，已由本次交付指示替代。

三份主规格已同步并分别strict通过；旧9份主规格格式失败集合未变。变更归档到 `openspec/changes/archive/2026-10-15-add-skill-tool-selection/`，记录在 `evidence/takeover-05-final/archive-closure.json`。原3/5三份被复跑覆盖的输出已移存至本轮 `regression-reruns/`，历史路径恢复为验收提交字节，两组事实均保留。模型评分及成果归属不改变。
