# 图实现归位实施记录


> 以下保留结构迁移阶段的实现与运行证据；之后用户授权移除加载记录的执行门槛，当前行为见[后续清理记录](../20261030-remove-load-execution-gate.md)。历史激活状态拒绝预期不代表当前要求。
变更：`refactor-graph-module-ownership`。实施基线为 `dev` 的 `98ce4ff` 加迁移开始时全部未提交／未跟踪改动；精确旧源码及差异保存在本任务被忽略的 `.local/graph-module-ownership/20261029-apply/preimplementation/`。本记录更新于 2026-10-30，实施、验收、Git 和部署分别报告。

## 最终代码归属

- `src/basic_qa/` 拥有 builder、nodes、state、prompts、capabilities、tool_policy、tool_runtime、tool_recovery 和独立 context。旧通用构图分支、意图车道、未使用的提示组装与旧 find_skill 流程退休；`resolve_skill` 仍注册为空兼容节点，当前路由不会进入它。公开 Graph ID、节点和 checkpoint 字段保留。
- `src/data_analysis/` 独立拥有原菜单、提示、节点、工具重试和完整上下文流程；保持当前封存业务，不回滚旧版本、不新增功能。两图不互相导入，不通过共享生命周期控制器间接联动。
- `common` 保留材料读取、运行模式、Token／容量计算、纯文本归纳、通用工具、存储、协议、身份、台账和观测。共享提示片段按功能通用性保留，不按消费者数量机械搬动。127 项公共模块／资产的最终去向见 [归属审计](ownership-audit.md)。
- `application` 固定映射并按需装配图策略，公共层不反向导入图／装配实现。管理 API 与原生 auth／HTTP 入口接同一策略源；Dockerfile、compose、langgraph.json 引用同步。数据分析菜单策略的加载、匹配、属性和类型异常失败关闭，移除旧吞错放行。
- DeepAgents 内部图和 SDK 摘要结构保留，仅更新公共引用和必要发送策略登记。九个 BQA Skill 的 SHA-256 与实施前完全一致。

`common/tool_execution/results.py` 没有保留：其中重试、预算和大结果治理编排分别归两图的 `tool_runtime.py`；真正公共的归档／计算存储继续使用原公共模块。公共发送、schema 摘要及协议配对在 `common/tool_execution/`。这是目标路径细化，不是恢复共享控制器。

## 两层检查与真实发送

BQA 包装器入口仍检查当前能力和披露；helper、低层 execute 及工厂最终发送必须经过可信登记策略。策略／state／模型调用／披露缺失或故障均拒绝。

工厂从运行角色回读已受理选择，关联 tenant、user、membership、thread、attempt、submission、graph 和选择摘要；metadata 不能扩大选择。每次发送在身份／材料等异步复验后取得本图的同步最终校验器。实际 HTTP 发送前再次读台账（包括删除墓碑），随即同步检查能力、披露、模型调用和真实参数快照，再交给传输层；SDK 重连和重试也走该守卫。公共层不解析 BQA 私有 state。通用可选 schema 声明键改为 `mcp_disclosed_schema`，它不代替必选策略。

证据见 [发送专项](send-policy-tests.md)。测试计数分开发现、受控 HTTP 503 和成功业务调用，不把拒绝返回值当作零外呼证据。

## 已完成验证

所有数据库、身份与业务内容均为本任务自建的合成资源。正式业务库、正式 IAM 和原数据分析系统未用于验收。

- 根单测 `unit-final-04.txt`：**1218 passed、79 skipped、6 failed**，103.36 秒。六个失败均在精确迁移前源码中复现，身份列于下文；没有全量绿测声明。该次收集后新增的两条数据分析编译图恢复用例另行验证。
- `compiled-final-05.txt`：**44 passed**。包含两图真实模型节点／编译图中的有界超窗恢复、工具只执行一次、完整历史保留；数据分析终答发布替身与身份／MCP 替身的范围由测试明确标注。
- `send-final-07.txt`：**36 passed**，23.69 秒；真实回环 MCP 和合成 IAM，覆盖直接绕过 ToolNode、未披露、休眠、参数／schema 不符、空选择、策略缺失／异常、重试撤权、最后异步窗口及变异敏感性。此专项的台账读取是替身。
- `send-pg-03`：**3 passed**，4.53 秒。真实非 owner、非 BYPASSRLS 运行角色与恢复的空业务库，计数 MCP 正控 1 次；同用户换成员、策略等待中删除会话后新增调用 0。另用真实 DeepAgents SDK 文件工具把成功／失败加载、执行阶段写入 PG，校验材料版本及正文不进入事件。
- 两图公开工厂的新旧拓扑 JSON 完全相同，默认 checkpointer 均为 None；新进程阻断并计数网络连接后导入／编译成功。架构检查覆盖普通、相对、局部、TYPE_CHECKING 和可识别动态引用，三图按需装配及双向包隔离。
- `new-native-hooks-19`：当前管理 API／官方 Agent 和实际 SDK 恢复旧 BQA 摘要、v2 能力、8 条工具结果及暂停线程，resume 新增模型 4／工具 1；数据分析菜单冲突 422，合法续轮完成。三图有效入口成功；tracing 问候计划恰 1 次模型、0 次业务工具，不把它误称零模型任务。伪造实际图 409、内部状态普通／流式 403、缺策略 503，拒绝时模型和业务工具增量均为 0；恢复策略后同一预留可完成流式运行，未误消费启动权。
- `real-model-native-02`：当前配置的 **cn:deepseek-v4.1-flash**，官方服务＋实际 SDK＋独立 PG，7 轮连续问答完成。模型请求依次 2／2／2／3／3／3／3，业务工具 0；第 4 轮起有成功摘要水位，第 7 轮可结合最早问题和合成历史回答。使用测试容量配置（96k、摘要触发比 0.05）加速触发，不更改项目配置，不代表生产窗口／质量专项已验收。该样本生成于最后删除不可达旧分支之前；删除后的实际路径由同拓扑、编译图及原生恢复回归覆盖。
- `current-native-21` 与精确旧实现的 `native-baseline-data-11` 使用相同冻结载荷、受控模型和 schema：**均为模型 39 次、业务工具 8 次**。当前两图完整新会话／追问／菜单链通过，数据分析使用合成页面播报和实际允许的预报 MCP（不误称 broadcastHour 外呼），并生成成功摘要水位、已有工具结果和 call_model 暂停样本；所有自建资源回收。
- `reference-final-02`：离线材料与题库算术 **253 PASS**，网络被阻断。旧 expected_intent 保留为历史元数据，当前检查输入边界，不将此报告包装成 v1 正则路由准确率。
- 最终受影响组合 `affected-final-06.txt`：**165 passed**，47.57 秒，覆盖架构、真实计数发送、两图编译流程和新增恢复用例。Ruff check／format --check（核心64文件）、维护文档链接、OpenSpec strict 与 diff --check 均通过；23个既有无关差异片段与开始时完全相同。
- Ruff、格式、旧引用及部署声明检查以本目录最终汇总为准；原始完整日志、fixture dump 和合成凭据只留在被忽略的本任务目录。

## 既有失败与未完成项

迁移前精确源码定向复跑 `baseline-unrelated-01.txt` 为 10 passed、6 failed，与根单测最后六个失败身份相同：

- `test_archive_http_live_auth.py::test_archive_scope_must_match_live_actor_and_confirmed_owner`：请求替身缺 method。
- `test_checkpointing.py::test_reset_checkpointer_cache_closes_previous_connection`：旧缓存关闭预期不成立。
- `test_knowledge_route.py` 四个用例：依赖已不存在的 `clear_knowledge_route_cache`。

没有为迁移绿测修改上述断言或恢复旧功能。最初两条 BQA 容量测试先因无可信能力夹具失败，现已显式提供空能力依赖和真实控制工具 schema，原调用上限／摘要／水位断言保留并通过；测试包化引入的一条裸导入也已修复。

管理观测测试中的退休 find_skill 引用已迁为真实 SDK read_file 阶段；matched 属于旧流程，不再伪造。原件保留于精确快照。它的旧本地登录夹具尚未重建成当前远程 IAM，**不声称整个 admin 管理测试套件通过**；有效的加载成功／失败、执行、版本和正文不入事件断言已在上述独立 PG＋合成远程 IAM 用例实际通过。

扩展的跨版本持久化矩阵仍未全部通过。修正测试 MCP 服务冷启动绑定后，`upgrade-data-22`（新源码）与 `old-data-resume-23`（精确旧源码）在相同扩展旧样本上复现了**完全相同**的数据分析恢复结果：两次模型、零次新增预报工具调用、相同可见 schema，线程完成且摘要／已有工具结果保留；均未满足夹具要求的新工具外呼断言。证据确认这条失败在迁移前已存在，未放宽断言，也未在结构迁移中扩张封存图功能。普通旧 BQA 暂停恢复已通过；`rollback-read-24` 让旧源码回读当前源码新写出的两图摘要、工具结果和暂停状态通过；`upgrade-read-25` 由最终新源码回读旧扩展样本的对应状态也通过。扩展恢复金标仍未全绿，任务 7.1 保持未完成，不能发布“全部 checkpoint 可升级／回退”的结论。

最终独立评审未完成：早期独立审计发现的问题已由主代理补修；后续评审曾受额度中断，2026-10-30 用户明确要求停止子代理，之后全部工作由主代理完成。主代理复核不能冒充独立评审，任务 7.4 保持未完成。原 BQA／压缩活动计划的开放项不因此关闭。

## 交付与复跑

代码改动已留在工作区，**未提交、推送、部署或归档**。没有改外部 IAM／RAG 仓库，没有升级依赖或新增迁移。正式环境仍需先解决上述兼容证据和独立验收前置条件，再单独决定发布；回退必须保留本轮精确旧源码与受控 dump，不能拿更早提交代替旧工作区。

主要可复跑入口位于 `scripts/graph_module_ownership/`。`baseline_native.py` 默认旧快照，`--current` 选择当前实现；`restore_probe.py` 接新证据目录、旧 fixture 目录、源码目录，`--new-host`／`--hooks`／`--read-only` 明确区分宿主、入口反例和纯回读。`send_postgres_probe.py` 使用独占 27641 端口恢复合成库后执行真实发送／观测验证；`real_model_native.py` 只从本机配置读取当前模型，向其发送合成会话。每次必须使用新证据目录，不能覆盖既有报告。

容器使用 `graph-ownership-20261029-apply-*` 和 `graph-ownership-send-20261029-pg`，仅删除本次创建成功的名字；各轮 result 的 cleanup 逐项记录。原始 dump、bootstrap、provider 密钥和 `.env` 不进入可提交报告。

最终清点：本任务命名前缀的容器剩余0；23个既有无关差异片段及全部九Skill材料与开始时一致，维护文档本地链接无失效。主代理已完成可执行的整合复核，OpenSpec strict／Ruff／格式／diff检查通过；未关闭7.1和7.4，提交／部署／归档仍未执行。
