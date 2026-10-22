# DeepAgents / Langfuse trace 接线修复验收

日期：2026-10-22。分支 `dev`，开始时 HEAD `b6a885d5d18c6515d3aa7256b5cf6912a58f6f94`。保留原有未提交改动；本次未 commit、push、归档或升级依赖，未修改 IAM/RAG 仓库。

## 已确认的现象与修复

读取截图对应 17:48–17:52 全窗口的 Langfuse `events_core FINAL`，共 78 跨度、20 个 trace；其中 10 次 ChatOpenAI、4 次 task 及冻结/报告工具分别成为根节点。这个窗口与转述调查中的 46/16 过滤口径不完全相同，不能混用计数。

当前锁与运行镜像均为 Langfuse 4.15.6、DeepAgents 0.7.28、langchain-core 1.6.12、LangGraph 1.2.16；正式原生 API 为 0.16.2，本地开发环境 API 为 0.15.1。直接调用及固定样例在同一镜像中可以正确继承静态回调，因此不能把“静态 handler 必然导致拆分”或旧 issue 本身当成已证明的唯一根因。本次未在旧正式 worker 中逐回调记录 parent_run_id。

修复把 `intelligent_tracing.graph:graph` 改为原生支持的图工厂：编译结构复用，回调在 worker 恢复可信配置后每次执行创建。企业与受理 attempt 决定固定 trace ID，同 thread 的不同 attempt 分开，恢复 submission 复用原 attempt 的 trace。schema/state 读取不挂回调，也不把 Python 对象塞入受理 JSON。

主图及专员继承同一个 handler，回调在当前异步任务内执行；平台自己的列表/manager 回调由框架合并，避免重复投递。观测中间件获取 ambient config 时不再因缺业务身份而丢弃已有 tracing callbacks；业务观测与授权仍各自要求可信身份。执行期 handler 的弱引用注册表清理失效项，初始化失败不阻断业务图。

隐私出口继续过滤正文，新增保留 SDK 的 `as_root` / `is_app_root` 布尔标记、环境、固定图名和 UUID 用户/线程标识。可信工厂绑定覆盖客户端 metadata 的同名 trace 属性。

## 验证结果

- [完整定向回归](pytest-final.txt)：158 passed，20.41 秒；包含完整四专员、同步/流式、并行 attempt、同线程不同轮、恢复 trace ID、平台回调只投递一次、隐私与失败降级。
- [冷进程导入检查](import-proof.txt)：三图启用 Langfuse 时无任何网络尝试；图工厂的只读调用仍返回编译图。测试证据改用自有临时目录，不覆盖 2026-10-19 历史文件。
- Ruff 检查、格式检查和 diff 空白检查通过；[OpenSpec strict 结果](openspec-validation.json)只证明规格结构。
- [真实 Langfuse 探针](live-v2/live-probe.json)使用固定模型 `ScriptedChatModel`、内存 `FixtureDataSource` 与内存报告库；探针禁止业务数据库连接，不调用真实模型、MCP、IAM 或知识库。
- [真实入库计数](live-v2/ingestion-verification.json)：同步和流式各 65 跨度、1 应用根、8 模型调用、4 task、1 冻结工具；子节点缺父为 0，关键模型/工具无父为 0，正文标记泄漏为 0。两轮 trace ID 不同。验证环境为 `trace-wiring-validation-20261022`。

SDK 指定 trace ID 时，应用根的 OTel parent 是 SDK 生成的虚拟父节点；验收根依据真实入库的 `is_app_root`，子节点则必须引用同一执行内真实跨度。没有把全部节点硬挂到根、虚造业务父节点或仅按同 trace ID 判定完整。

第一次真实导出暴露了隐私过滤遗漏 `is_app_root`：两轮虽各为单 trace，但根标记缺失。该版本的 [探针记录](live-probe.json)保留，随后补修根标记并用新目录 `live-v2` 重验，没有把失败改写为通过。

## 运行态与限制

重载前只读确认原生平台没有 pending/running 任务；正式启动入口预检通过后，仅停止原生应用，再通过 `deployment/run_separated_local.py start` 恢复。数据库、Redis、Langfuse 和其他服务未重建；启动器更新了私有桥的受限来源 IP。应用启动完成且三仓服务健康。

本次真实 Langfuse 是实际服务入库，模型、数据和授权依赖是受控实现；没有重放截图中的真实业务问题，也未宣称真实 MCP/模型或整个 OpenSpec 3.7 的撤销、缓存、摘要等验收完成。消息未提供原问题全文，旧历史 trace 不会被追溯合并。本次两个成功验证 trace 保留以供核对。Token 未采集。

原生图导出改为工厂，直接调用者使用 `graph(config)` 取得编译图；已适配仓库中的接管基准脚本。该基准自身未运行，本次没有启动额外负载或容量测试。

官方依据：[DeepAgents 集成](https://langfuse.com/integrations/frameworks/langchain-deepagents)、[LangGraph 集成](https://langfuse.com/integrations/frameworks/langgraph)、[issue 10721](https://github.com/langfuse/langfuse/issues/10721)。该 issue 报告 SDK 3.10.1，不能直接作为当前 4.15.6 的升级结论。
