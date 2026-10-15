# Change4收尾修复：通用接管目标

2026-10-15。用于另一位Agent接管，不再要求原匿名模型继续返工。当前只交付目标提示词，尚未发生协调者产品补修。将下方正文粘贴到支持`/goal`的工具；没有该模式时按普通持续任务执行相同正文。

```text
/goal 接管并完成 /Users/acerola/Dev/Python/air_agent 的 OpenSpec change add-skill-tool-selection，修复最新独立验收G01–G08并完成实际验收，交付可复验的收尾报告。

这是接管未完成实现，不是从零开发。当前分支codex/change-04-skill-tool，记录的HEAD为8620cf78b22cb7234fe650fa5c9f032359e0d70d，实施代码大量保留在未提交diff与untracked文件中。先检查实际HEAD/status并冻结全部输入。不得reset/clean/stash丢失变更；新建工作区时必须完整迁入该diff及新增文件并核对指纹，不能只检出HEAD后误以为已有实现。

先读：
1. 根/相关子目录AGENTS.md，openspec/changes/add-skill-tool-selection/的proposal、design、specs、tasks、contract-extension、acceptance、handoff。
2. docs/reviews/flash-blind-05-repair-02-review.md（本次主验收依据），及docs/reviews/evidence/flash-05-repair-02-independent/README.md、findings.json、anonymous-input.json、review-closeout.json与相关独立探针。
3. 首交与首修独立报告，docs/reviews/agent-interactions-observability-takeover-acceptance.md、docs/reviews/enterprise-closeout-2026-10-15.md，保留已经验收的3/5成果。
使用仓库现有OpenSpec apply工作流，但按真实证据重新检查完成项，不能把27/29任务已勾选当作完成证明。

按以下顺序收尾：
A. 先恢复可运行基线。修TenantRuntimeGuardMiddleware中metadata=None导致三张DeepAgents图首次模型调用崩溃；修0013s遗漏selection_source列导致新空库/旧head升级失败。恢复fixture协议只能由显式TENANT_MCP_FIXTURE_MODE=true启用，删掉按pytest/数据库URI/PYTEST_CURRENT_TEST推测开启的生产分支。
B. 统一可信目录与受理边界。管理服务与运行时是不同进程，不能假设Python全局MCP缓存共享。按当前可信身份、授权修订、精确server+tool、材料/菜单、真实catalog版本受理；故障失败关闭。合法目录→合法工具必须成功，格式合法的假目录必须被拒绝。禁止特判v1/expired或单靠工厂空列表制造全拒绝。
C. 修复幂等继承。区分客户端省略选择、明确Auto、retry/regenerate/resume继承后的有效选择。同键同有效选择可重放，不同选择409；修复省略选择的resume首次200、重放409。保持旧v1历史、同键并发一个启动权及恢复不新增提问/attempt的既有语义。
D. 接通真实SDK工具链。设计对全部合法server/tool名称无碰撞的模型名映射，覆盖service-a/service_a及svc_a/svc--a；统一原始短名/稳定ID/代理别名的披露、_is_mcp_tool识别、ToolNode调度、当前scope授权。三张生产DeepAgents图编译后发现、主/实际子任务都可执行T1且T2出站0，不用测试自建wrapper代替生产路径。Auto不能披露其他scope已发现的私有schema。
E. 接服务端权威选择回读。浏览器不可自行算selection UUID/digest冒充admitted；从实际受理/安全回读获取真实ID、摘要、材料和版本，贯通确认、历史、刷新、继承。保留已修好的草稿固化，延迟确认期间改草稿不得污染当前运行，账号/企业/图/ctx_version/epoch隔离完整。
F. 验证观测与完整矩阵。保留已修好的glob签名/文件边界、manual来源与同提交选择关联；复验真实使用、取消未用、恢复关联、删除与备份恢复重施墓碑。按acceptance.md补A12/A15/A16/A18/A19/A21/A22等缺口，不能把直接后端对象/人工waiting状态当真实子Agent/interrupt→resume。

验收方法：先跑既有独立失败探针，再让同一反例在修复后通过，保留正向控制。三张生产图SDK实际ainvoke、真实独立HTTP管理+运行时目录+MCP、fresh migration、同键恢复、实际TS对照服务端ID是最低门槛。随后完成官方LangGraph API六图×native/BFF×stream/wait的24格、显式/实际恢复与浏览器390×844/桌面/键盘/IME，证据带run/submission/selection/材料与服务网络计数。受控模型用于确定性边界，真实配置模型/Langfuse另证；先核实配置，不擅自换模型/网关，也不把缺凭据扩大为不能做受控SDK测试。

环境：macOS Apple container，不用Docker Desktop或全局prune。当前自有aa-sel05-pg:15434、aa-sel05-redis:16381可复用，先核对所有权/库名并备份。不得操作15432共享栈、知识库、IAM或共享卷。临时服务只绑定回环，只清理自己创建的资源。真实私有.env、密码、cookie/token、数据库dump不进证据或Git。

验证基线：核心专项28通过；管理台账+选择44通过/1既有collection_disabled断言失败；前端46通过；根361通过/75跳过/6同环境既有失败。逐项比对失败身份，不删除/弱化/全局跳过测试。本次迁移和三SDK图独立反例失败是新增真实缺陷，即使现有测试全绿也不能忽略。运行受影响测试、实际新空库/存量升级/RLS、前端契约、类型与生产构建、修复文件Ruff及Change strict；共享契约变动后复跑相应回归。既有9份主规格历史格式失败单列，不顺带改写。

报告写docs/reviews/skill-tool-selection-takeover-acceptance.md，原始证据另存docs/reviews/evidence/takeover-05/。关联G01–G08修复前后、A矩阵、实际样本与错误数、范围限制、最终diff指纹和资源收尾；性能区分helper、ASGI受理、native保护、模型/外部等待，不把错误数写常量。时间/Token取实测，不能推测。27/29勾选可回退，缺证据的任务保留未完成；完成后交付待独立复核，不自行宣称协调者已经验收。

原模型记录131+83+60=274分钟，46→63→58分，身份由用户揭晓为Gemini 3.8 Flash；原模型尚未独立完成，接管者的修复不回填给原模型、不修改其原始分数。本次只允许Change4收尾及必要接线，不实施6–10，不重写IAM/知识库，不复活Change3已豁免的物理剪贴板抽查。未获另行授权，不commit/push/合并/归档。目标是实际功能闭环，不能仅把专项测试重新跑绿。
```
