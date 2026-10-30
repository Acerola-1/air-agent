# 已实现接口与生命周期（待整链验收）

Basic QA保留原生LangGraph。会话能力引用、当前attempt及当次实际模型披露分别保存在`bqa_capabilities`、`bqa_turn_key/bqa_turn_digest`及`bqa_disclosure`，不再以新用户消息清空能力。客户端仍只能提交消息或合法resume，不能写这些字段。

## 当前图流程

正常执行为：准备会话 → 准备模型上下文 → 模型决策 ⇄ 工具执行 → 流式最终回答 → 台账收尾。模型决策阶段可以按需搜索／加载 Skill 或独立工具，也可以直接决定资料已足够；搜索不是固定前置步骤。普通交流和复述可以不执行工具，但仍先经过模型决策，再进入最终回答。授权、容量等控制消息可提前结束并收尾。

入口实现为 `prepare_session`；图中仍使用 `classify_intent` 这个已写入旧检查点的标识，它不再进行意图分类。`prepare_model` 直接连接 `call_model`，没有依赖 `needs_planning` 的入口直达终答分支。`resolve_skill` 及其虚假入口边已删除，Skill 由模型调用 `load_skill` 按需加载。`answer_model` 从模型决策结束分支进入，不在工具循环中重复发布正文。旧 `needs_planning` 通道仅用于读取历史状态，不再参与路由。此次清理及旧源码跨进程恢复证据见 [拓扑清理记录](../graph-module-ownership/20261030-topology-cleanup.md)。

## 加载批次

`load_skill(skill_id, expected_version)`与`load_mcp_tool(tool_id, expected_schema_version)`均不执行业务查询。同版本已激活返回`already_active`，休眠重激活返回`activated`，新版本返回`loaded`；失败返回`unavailable`且保留其他能力。回执只含ID、版本和状态，不重复追加完整规则。

含加载的控制批次按模型调用顺序串行处理，后一个看到前一个累计状态，最终一次提交状态与逐call_id回执。多个独立业务查询仍由ToolNode并发。加载与尚未披露的新领域工具或结束声明混批返回配对错误，保持原能力，真实查询为0；这是异常路径，不计正常首次成功。已经在该次模型请求中披露、且当前仍合法的工具可以与新加载同批，不依赖新加载结果；执行仍验证原披露记录，不能因前一个load成功就放行本批猜测的新工具。

## 工具范围和版本

Basic QA业务登记从21项扩为冻结的109项已审核查询操作；仍与受保护连接的approved_query_tools、当前发现、受理选择及当次实际披露取交集。策略版本从`2026-10-19.1`变为`2026-10-28.1`，是全局变更，未来部署须排空所有受影响图的旧运行。本次未修改正式配置或主动发布。

工具稳定ID与provider别名分开。真实schema从当前企业连接的发现结果构建，避免用全局schema缓存替代当前连接定义。模型按激活集合按需收到工具定义；执行只核对当次实际提供的定义、真实调用、schema及当前授权，不以Skill归属、load_ref或激活状态追加门槛。共享工具按来源去重，退出某Skill不会撤掉仍有独立来源的工具。

## 搜索和索引

`search_mcp(query)`在合法池中按语义分数排序、稳定ID打破同分、最多返回Top 3。只有ID、简介、版本、可用状态和分数进入候选；不自动加载、查询或用全量schema降级。空白或超500字符为`invalid_query`；默认10秒超时为`search_timeout`；索引不可用为`index_unavailable`。合法池不足3项如实返回。

固定检索集揭示纯相似度会混淆城市／站点实时查询。当前排序增加明确对象词的本地元数据先验：公开工具命名空间区分城市、站点、网格和气象，同对象内按cosine排序，再以稳定ID打破同分。对象不匹配或无法分类的工具仍是合法候选；这不授予／取消权限、不拦直接加载或执行，也不新增重排模型或服务商。返回score为cosine加对象匹配先验，不能当成概率或人员许可。索引文本保留用途和受限参数摘要，剔除长返回字段表；全部定义仍留在程序侧。

迁移`0030_bqa_mcp_index`接`0029_insight_retention`，新增`bqa_mcp_index_documents`与`bqa_mcp_index_generations`，均FORCE RLS，运行角色按事务tenant GUC访问。文档按内容寻址，作用域含企业、业务策略与连接修订，模型／端点摘要独立作为model_key。未变向量可跨重启复用，不新增向量数据库或扩展。

首次真实搜索按需增量构建。数据库事务级advisory锁覆盖同作用域／模型的构建与完整代次发布；失败／取消回滚，发布前后复查当前范围。启动／图导入不进行网络预热。`PostgresIndexStore.prune(catalog, days=30)`只回收当前企业本实现过期缓存及孤立文档，不操作业务表；无权用户不能用索引缓存延长许可。

## 上下文与提示

规划和终答共用能力投影，完整Skill规则从当前版本恢复，走原有prepare_model_request、摘要水位与超窗恢复。仍超出容量时按最近使用次序休眠非当前attempt刚加载／已使用来源，保留引用并准确列出休眠状态。规则和相应schema同步退出；当前必要组合仍超限则明确容量不足，不截掉规则继续查询。

新版管理提示键为`air-agent/basic-qa/conversation-system-v2`，旧v1键不再读取，防止旧的逐轮加载／单Skill指令恢复。公共规则要求Skill优先、未覆盖部分工具补足、多个Skill可组合、专业范围外简短引导。没有新增结果回显字段、回答分类或证据声明校验。

能力事实扩展见[capability-facts.md](../../contracts/agent-events-v1/capability-facts.md)。普通事件只存ID、版本、来源和计数；受控Langfuse原有正文采集规则不改变。

## 2026-10-30 执行门槛清理

search／load管理模型上下文，不授予工具权限。执行层不再调用current_material，不读取Skill归属或bqa_capabilities判定是否可执行；只核对服务端当次提供的定义、模型消息／tool_call、schema和真实发送参数。公共身份／业务／连接／不可变选择／删除与逐发送核验继续生效。若已有调用之后仅激活状态改变，当前定义与授权仍有效则继续执行；后续模型请求仍按上下文状态提供定义。未在本次提供定义的猜测调用仍拒绝。实现与计数验证见 [本次清理记录](../graph-module-ownership/20261030-remove-load-execution-gate.md)。
