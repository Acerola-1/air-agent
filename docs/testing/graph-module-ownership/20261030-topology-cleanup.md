# Basic QA 无效流程清理（2026-10-30）

用户要求解决审查发现的不可达遗留节点、恒真条件分支与误导命名。本次在既有未提交重构基础上补修，不提交、部署或归档，不修改数据分析、DeepAgents、Skill 材料及真实授权策略。

## 结果与兼容边界

- 删除 `resolve_skill` 空方法、节点注册及入口中不会返回该结果的映射。迁移前精确工作区与清理前源码的 BQA v2 都绕过此节点；搜索和加载继续作为模型按需调用的工具，不恢复旧 Skill 路由。
- `prepare_model` 直接连接 `call_model`，删除 `_route_after_prepare` 和恒为真的 `needs_planning` 写入。旧字段继续保留为检查点通道；其值为 true、false 或不存在时，正常请求均先决策再回答。
- 入口方法从 `classify_intent` 改为 `prepare_session`，实际职责为输入检查和会话恢复。图内 `classify_intent` 标识已用于有效旧检查点，保留此持久化标识并在构图、状态及流程文档中明确解释。它不是意图分类器。
- 删除未生效的 `base_system_prompt` 构图／节点参数和旧默认提示词常量。实际使用的 `conversation-system-v2` 提示词及内容未修改。
- 删除最终回答末尾先抛异常再吞异常的不可执行 Skill 观测块及无消费者方法；真实执行观测仍在工具边界进行。清除孤立的工具过滤标题。
- `answer_model` 正常保留：由 `call_model` 判断资料足够后进入，独立进行无工具的流式终答。普通交流同样经过模型决策，不新增入口直达终答捷径。

实际流程为：准备会话 → 准备上下文 → 模型决策 ⇄ 工具执行 → 最终回答 → 台账收尾。输入超限在会话入口停止；授权、容量等控制路径可提前结束。

## 验证

工作基线为 `dev` / `98ce4ff33aecdcdcec6ef87a9c190ee1e14eefe1` 及开始本次补修时的未提交工作区。源码快照与新增证据位于 `.local/graph-module-ownership/20261030-topology-cleanup/`，不覆盖历史报告。

- 定向编译图／节点／输入／配置验证：77 passed，8.26 秒。包含普通回答的实际节点顺序、工具循环、输入超限不调用模型，以及旧 true／false／缺失标志不影响当前路由。
- 受影响回归：238 passed、24 skipped、1 warning，41.23 秒。覆盖 BQA 会话／能力／执行、公共 MCP 发送、工具重试、逐发送授权、上下文恢复及图归属。24 项跳过来自既有运行环境条件；警告来自既有公共台账收尾节点的 config 类型注解，未修改公共台账实现来消除警告。
- 持续维护的恢复用例：用补修前拓扑与实际节点生成 SQLite 检查点，关闭连接后以新图重开恢复，覆盖会话入口、模型准备、模型决策、工具执行、最终回答、台账收尾和业务查询结果已完成这 7 个有效暂停位置。每例总计 3 次模型决策、1 次业务查询、1 次最终回答。
- 精确源码跨进程复核：清理前源码进程生成 7 个真实序列化 SQLite 检查点，7 passed / 2.95 秒；当前源码在另一进程恢复同一批文件，7 passed / 3.24 秒。分别验证实际导入的源码路径，模型调用脚本按已消费次数续接；全链计数同上，工具和正文不重放。证据为 `exact-before-01.txt`、`exact-after-01.txt` 和 `exact-source-restore/` 下的逐例 JSON。模型与 MCP 为受控替身，LangGraph 与 SQLite checkpointer 为真实实现。
- 实际公开工厂拓扑对比：只移除 BQA 的 `resolve_skill` 节点及相关无效边，把 prepare 条件边改为直连；数据分析拓扑完全一致。两版导入网络请求均为 0。最终 11 项流程／恢复冒烟通过，4.07 秒；11 个受影响 Python 文件的 Ruff 与格式检查、文档链接及 diff 检查通过。

这不是实际 PostgreSQL／官方 Agent Server 的完整升级矩阵，也不声称覆盖早于迁移基线、实际暂停在旧 `resolve_skill` 的 v1 运行。未来部署仍需排空在途旧运行；原任务 7.1、7.4 保持未完成。此次仅使用自有临时 SQLite 文件，未启动／修改正式服务或数据库。

当前正式入口仍由官方 Agent Server 注入 PostgreSQL checkpointer；本次测试使用 SQLite 是独立验证序列化与路由恢复，不改变运行配置。

对应实现为 `src/basic_qa/{builder,nodes,prompts,state}.py`；新流程见[会话能力设计说明](../bqa-session-capabilities-v2/runtime-design.md)。Ruff、格式、OpenSpec strict、链接、diff 与变更范围检查结果随本次证据保存。历史迁移报告仍是当时状态，不被本次补修改写为完整验收通过。
