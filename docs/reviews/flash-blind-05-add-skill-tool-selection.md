# 匿名运行05：Change4首次独立验收

日期：2026-10-15，Asia/Shanghai。结论：**未通过，需要修复；本轮只审计，协调者未补修产品**。

用户提供首交用时**2小时11分钟/131分钟**；修复0轮，达验收总用时未知。未知模型身份，不猜供应商。首次诊断评分**46/100**，效率**2/5**（当前交付未闭环）；应用端到端性能不评分，Token未采集。不按跨change耗时排名。

## 输入与保存

基线8620cf78b22cb7234fe650fa5c9f032359e0d70d，分支codex/change-04-skill-tool。57个Git可见交付文件，另有9份被Git忽略但确实存在的原始日志，均已核对并保留指纹；tracked diff SHA256=10e2796fe6ae8b4598c02dd615bb0b12190ff589d6c72f0ee6242f6b54f4d9d7，与作者一致。anonymous-input.json已冻结全部输入文件指纹；报告/证据和本次修复提示词是协调者新增，不计模型成果。作者tasks和报告保持原样，不回写其勾选。

OCR delegate仅用于文件筛选/规则，审阅由当前协调者完成，无外部模型代审。47个OCR文件逐项登记，10个默认排除项（含新增测试/文档）另审；文件覆盖不是运行场景覆盖。见evidence/flash-05-independent/review-coverage.json。

## 复跑结果及正向成果

| 检查 | 独立结果 | 解释 |
|---|---|---|
| 作者核心专项 | 28 passed | 包括受控MCP实际请求、目录、直接hook与局部Skill节点；不是六图native/BFF完整运行 |
| 作者管理专项 | 11 passed | 部分持久化/事务/来源规则有效，测试缺少受理→正常启动正例 |
| 前端契约 | 46 passed | 代码可编译，但样本与真实Python/BFF目录契约未贯通 |
| TypeScript | tsc通过 | 类型断言不能保证真实JSON字段存在 |
| 本change OpenSpec strict | 通过 | 只证明文档结构，不证明29项任务完成 |
| UI静态审计 | errors=0 | 真实浏览器仍失败，静态扫描不替代运行 |
| 修改/新增Python Ruff | 9项E501 | 均在新迁移，作者“全部修改文件零错误”口径未复现 |
| 当前根单元 | 361 passed /75 skipped /6 failed | 同环境原HEAD也是完全相同6项；额外artifact失败由本轮显式runtime URI环境共同触发，不记模型新增回归 |
| 台账+OpenAPI受影响回归 | 候选28 passed /8 failed，maxfail=8停止 | 同环境原HEAD38 passed /1 failed；至少7项新增，主要是启动摘要不兼容。共同collection_disabled失败不计新增 |
| 原基线空库迁移 | 成功到0012o_delivery | 真实独立空库正向控制 |
| 当前空库迁移 | UndefinedTable失败 | 旧0012o_integrity过早操作新选择表 |
| 已有旧head→新head升级 | 成功到0013s_capability_selection | 说明增量可升级，不代表空库可初始化 |

首次沙箱阻止本机PG连接，已在允许本地网络环境复跑成功；不把沙箱错误计模型失败。原全仓9份历史spec未修改。未重复耗费真实模型调用、未声称真实企业MCP通过；本轮不以完整生产构建/24格模型运行通过自居。作者9份原始.log实际存在，因Git忽略规则没有出现在rg/普通diff清单；本轮已核对内容和原始指纹，并以author-*.txt保留字节副本。不将日志误记为缺失或据此扣分。构建日志显示成功，本轮真实浏览器使用该现有构建产物，未再运行一次生产构建。

## 阻断与缺口（稳定编号）

### R01 / P0：所有新受理与原生启动摘要不兼容

admin新受理写v2，runtime claim_start及管理端启动裁决仍算v1。真实登录→/agent/requests=200→同正文native hook=409 run_request_mismatch/content。开关关闭时auto仍复现。既有台账启动测试新增失败，与外部模型、MCP、票据过期无关。证据live-probes.json和admin-ledger-regression.txt；源码admin/api/app/services/ledger.py:1040及src/common/event_ledger/store.py:1049附近。

### R02 / P1：前端三处真实契约冲突

目录fetch没有BFF必需的x-tenant-id/x-tenant-context-version，真实浏览器登录后弹窗显示“页面企业上下文缺失，请刷新后重试”。query tenant_id不能替代头。默认toPayload多传selection_source，真实Admin API=422 extra_forbidden；修复必须让source继续由服务端裁决。目录返回required_tool_ids，UI读取required_tools.length，实际React+真实目录项渲染TypeError；title/tool_name/services也与返回字段不一致。证据client-probes.json、live-probes.json、react-catalog-shape.json、browser-catalog-error.png/txt。

### R03 / P1：从空库无法初始化

enterprise_permissions.permission_statements默认把新表加入旧revision运行列表。新表尚未创建就ALTER TABLE，UndefinedTable；原HEAD同条件空库成功，已有旧head升级成功。不能只在已经升级好的库跑测试，再称从零/存量都通过。证据empty-migration.txt、baseline-empty-migration.txt、upgrade-existing-migration.txt。

### R04 / P1：受理没有真正验证目录与工具

只检查catalog_version是否等于字面字符串expired，并只查server活跃绑定。真实API接受伪造目录not-a-real-catalog、不存在的does_not_exist工具和unverified协议=200。此处并未证明可越权读业务数据，最终出站仍有原授权守卫；但违反本change“未知/过期/不可用在受理前拒绝”承诺。菜单参数在受理validate_skill_selection未使用，冲突仅在执行节点抛ValueError，也不是启动前422。证据live-probes.json；ledger.py:924附近。

### R05 / P1：Skill边界不完整且路径fallback放宽

普通read拒绝S2，但download_files可返回完整S2内容、grep根目录可搜索S2，真实文件系统正向/反向控制已复现。新增router.resolve_skill_file('../outside')能跨出skills根目录。不是宣称可读任意.env，而是未选/跨目录SKILL.md边界确已突破。Gate0甚至用了测试内部另定义的BoundedSkillsBackend，不能替代生产类全部SDK入口和真实子任务。证据module-probes.json。

### R06 / P1：同名MCP没有接通模型披露/分发

两个普通服务query进入build_proxy_tools后仍是['query','query']，server_of('query')指向后注册controlled-two；选择controlled-one过滤后一个工具也不披露。别名service-a与service_a也碰撞。工厂直接传server_key成功，不能证明模型侧别名成功。DeepAgents三图的_mcp_tools仍是导入时静态列表；测试内自建dynamic wrapper与生产接线没有对应实现。授权作用域工具还并入全局schema目录，auto披露须重新核对当前scope，不能把其他成员发现的私有元信息当公共定义。证据module-probes.json及实际graph/mcp_scoped调用链。

### R07 / P1：继承与恢复路径失败

显式选择来源被继承，却未继承catalog_version，retry/resume不声明新选择均真实422。原生恢复MCP从dict取b.server_key导致AttributeError。resume新增snapshot而runtime按attempt fetchone，submission关联也须明确，避免同attempt多快照任取。证据live-probes.json。不能通过只测试“篡改应拒绝”证明合法恢复能完成。

### R08 / P1：前端上下文清理和已受理回读未接通

企业effect仅fetchCatalog，不清选择；只跟企业ID不跟user/ctx_version。独立编译TS从A切B后仍保留原显式Skill/tool_ids；A→B→A同ID上下文也需代次隔离。acceptedSelection在生产只有setter无调用，BFF按flat字段读Admin的嵌套响应，多数字段undefined，实际受理事实未被显示；不能把本地草稿当锁定快照。证据client-probes.json和源码引用。

### R09 / P2：工具分页重页，部分服务成功被总超时丢失

5个工具limit=1，结果tool0、tool1、tool1、tool1、tool1且next_cursor一直存在。大小裁剪后游标也未重算。gather总超时分支丢掉此前已成功结果，违反部分可用。直接模块探针验证分页，超时缺口由控制流审阅确认。证据module-probes.json。

### R10 / P1：任务/验收报告超过实际证据，观测尚未闭环

A15引用的test_task_4_6仅hasattr六模块；A16没有每图native/BFF真实run与submission，Deep边界测试没有运行子Agent。A18没有备份恢复过程。A19新增record_span参数但skill_adapter未传选择引用，executed仍标loaded-rules，manual来源丢失。性能脚本只比较list_graph_skills/validate_skill_selection，tool_filtering的DummyTool未识别为MCP；2s/5s/错误率由常量写入，不能证明请求保护及故障上界。全部29任务勾选和A01–A24全绿无法认可。原作者报告保留，用独立报告指出缺口，不推测其主观动机。

## 五维诊断评分

| 维度 | 得分 | 依据 |
|---|---:|---|
| 契约正确性/业务可用（30） | 9 | 目录/持久化初步具备；默认聊天、空库与恢复受阻 |
| 权限/数据边界（25） | 12 | 原身份/出站授权保留，受控调用负例可复现；选择受理、文件、scope/同名边界未闭合 |
| 测试/证据可信（20） | 8 | 专项39+前端46及9份原始日志确实可复核；真实矩阵/恢复/浏览器结论超出证据，既有回归未发现 |
| 可维护性/契合（15） | 10 | 复用现有域和共享组件；两套解析/DTO漂移、静态/动态接线、旧迁移helper耦合需修复 |
| 范围/交付完整（10） | 7 | 未改IAM/KB、未提交推送；任务过早关闭、真实运行证据不足 |
| 合计 | **46/100** | 首交诊断，不是主力模型选择结论 |

稳定性：首次关键路径未通过；仅一轮观察，尚不能断言该模型普遍能力。效率2/5反映131分钟后仍有基础阻断，不将不同change的相同时长直接排名。修复后另记分数/耗时，不覆盖首交。

## 下一步与环境

提供docs/handoffs/skill-tool-selection-review-01-goal.md定向修复提示词；先R01/R02/R03建立真实最小成功链，再修R04–R09和重做R10证据。仍是同一change，暂停归档及Change6推进，不再增加新功能范围。

协调者只新增审计证据/报告/评分与提示词，**未补修产品、未合并、未commit/push/归档**。源文件57项指纹最终复核；当前工作区就是源仓库切出的分支，不是独立worktree，后续并行工作必须另建工作区。匿名05原PG/Redis容器保留；审计前专用库dump只存/tmp、未入Git。审计临时8104/3104服务及两个新空库收尾另见review-closeout.json。真实模型/生产MCP/完整24格未因本次审计而获验收通过。
