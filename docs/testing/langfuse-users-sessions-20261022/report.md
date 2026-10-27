# Langfuse Users / Sessions 完善与验收

2026-10-22，分支 `dev`，起始 HEAD `0b3c4bf13b26ec26b8aaa93fb06c7bb5ffb8059d`。在用户已有的七个文件未提交改动上继续修复，保留其三图执行期接线和官方 SDK 回调。未 commit、push、归档或升级依赖，未修改 IAM / RAG 仓库。

## 本次修复

1. 原流式包装器在 `yield` 两侧持有 OTel 属性上下文。跨任务或跨线程关闭时无法恢复原上下文，后续无关观测继承旧用户/会话。现改为每次读取和关闭时进入、退出官方 `propagate_attributes`，先恢复调用者再返回数据块。异步普通流、事件流和 protocol 流共用一套执行逻辑。原生工厂仍使用运行任务的上下文管理入口，支持并发与嵌套；作用域使用单个 ContextVar，避免每轮创建新 ContextVar 而在长生命周期任务内积累键。
2. 原 remote 身份的账号为空，观测从 `metadata.user_account` 兜底，而授权边界账号为空时未清除客户端值。现不再从 metadata 取身份标签。原生票据绑定读取对应用户的 IAM 同步引用，事务内设置用户 GUC，保留已有 FORCE RLS；只读取该用户的 account，不新增表或迁移，也不增加 IAM 网络请求。账号缺失则省略标签，不影响稳定用户 UUID；绑定缺失、过期或存储故障继续按原规则拒绝。
3. 原生受理边界覆盖三处 metadata，账号缺失时清除同名字段，并移除会被 LangChain 自动镜像进 metadata 的 configurable 别名。BFF 同时覆盖顶层与 config.metadata，防止不同优先级的字段保留浏览器声明。

保留原样官方 `CallbackHandler`，不重写 SDK 父链、exporter 或 mask。用户与会话属性经官方公开接口传递至所有子观测；进程内传播不启用 HTTP baggage。Users 以用户 UUID 聚合，Sessions 以 thread ID 聚合，attempt 决定独立 trace。可读账号是 metadata，不是 Users 主键，也不作为授权依据。

## 失败复现与回归

[修复前反例](before-fix.txt)：6 failed、1 passed，直接复现三处账号字段未清除、异步跨任务关闭及同步跨线程关闭的旧标签残留。保留失败记录，没有删除断言或跳过场景。

- [最终 Python 回归](python-regression-final.txt)：224 passed，25.20 秒。覆盖三图接线、官方回调、完整输入输出、独立 attempt、同步/异步流、跨任务/线程关闭、取消、事件/protocol 流、并发用户、作用域复用、本地模型观测关联、授权与冷导入。
- [前端契约](frontend-contracts.txt)：71 passed，包含新增的顶层与 config.metadata 账号防伪正向控制；[类型检查](frontend-typecheck.txt)退出码 0。
- [前端隔离生产构建](frontend-build.txt)：Next.js 16.3.0 Webpack 构建成功，包含类型检查与路由产物。构建使用独占临时目录，没有在运行中的 `.next` 上写入。
- Ruff、格式和 diff 空白检查通过；[OpenSpec strict](openspec-validation-final.json)只证明文档结构。交付文件的现有凭据值扫描未发现匹配。

Python 保留原有两条节点 config 类型提示；前端构建保留既有 middleware 命名弃用和依赖中的 Edge Runtime 提示。本次没有通过屏蔽或放宽检查消除这些提示。

## 真实 RLS 与镜像验证

[自有空库 RLS 探针](isolated-reference-rls.json)使用独占数据库和独占非 owner、非 BYPASSRLS 角色。绑定 A/B 分别读取自己的账号；A 读取 B 返回 0 行，并有 A 正常读取的正向控制。缺失引用返回空标签，缺失或过期绑定拒绝。探针数据库与角色已回收，未查询或改写正式业务数据。

[最终原生镜像探针](native-final-probe.json)使用实际 langgraph-api 0.16.2、Langfuse 4.15.6、DeepAgents 0.7.28、langchain-core 1.6.12、LangGraph 1.2.16。原生同步、原生流式、直接跨任务流式三种路径均为 65 节点、1 应用根、0 子节点缺父，全部子观测的用户、会话与账号属性匹配。真实 `_generate_graph` 加载器接受包装器并保留官方回调。网络尝试 0，LangSmith 客户端创建/写入 0，业务数据库被禁止。

## 实际 Langfuse 聚合

[最终入库与完整接口回读](live-final/live-probe.json)使用环境 `users-sessions-validation-v3-20261022`、固定 `ScriptedChatModel` 与 `FixtureDataSource`：

- 用户 A 在线程 A 同步和流式各一轮：两个独立 trace，同一个 session，合计 130 个观测。
- 用户 A 新建线程 B：一个独立 session、65 个观测；Users 维度仍归入用户 A，合计 195 个观测、3 个 trace。
- 另一企业的用户 B 在线程 C 执行：独立用户与 session，65 个观测、1 个 trace。
- 四轮执行共 260 节点，每轮 65 节点、1 根、8 模型、4 task；全部节点的 user_id、session_id 和账号标签正确。模型直接挂 model，关键工具直接挂 tools，输入输出存在。
- 每轮之后创建一条不带身份的独立观测，实际回读均无用户/会话归属。Langfuse 将未设置字段表示为空字符串；首轮探针只接受 null，因而失败，输出保留在 `live-probe-log.txt`。随后按真实语义验证，没有修改产品断言；第二轮成功证据保留在 `live-v2`，最终版本再验于 `live-final`。

聚合通过官方 v2 observations 的 user_id / session_id 筛选回读，未使用 v4 已移除的旧 Users/Sessions 查询方式。业务数据库连接及 LangSmith 客户端被探针禁止。此处模型、数据与身份均为固定验收输入，未调用真实供应商模型、MCP、IAM 或知识库。

## 复验与交付状态

固定输入源为 `tests/unit_tests/tracing/fixtures.py`、`test_subagents_deepagents.py` 与 `test_langfuse_trace_tree.py`，故障与清理断言在新增的 `test_langfuse_context_lifecycle.py`，绑定账号查询断言在 `test_ticket_binding_account.py`。完整回归命令可按这些路径及日志中的测试范围重跑。

可重复验收脚本均已实际运行。每次在 `reruns/<独立标识>` 新建证据，不覆盖旧结果：

```bash
.venv/bin/python docs/testing/langfuse-users-sessions-20261022/verify_live.py
.venv/bin/python docs/testing/langfuse-users-sessions-20261022/verify_reference_rls.py
```

前者读取现有私有 Langfuse 配置、仅在独立验证环境写入固定观测，禁止业务数据库与 LangSmith；后者仅创建并回收自有空数据库和角色，不复用正式业务表。二者只输出非秘密标识、计数和校验结果。

正式入口预检和无 pending/running 检查后，加载已构建的前端产物并重载 Agent 应用。旧前端产物完整保留于 Git 忽略的本机目录；[切换记录](frontend-promotion.json)记录新 build ID。IAM、RAG、数据库、Redis 和 Langfuse 未重建。[最终启动](runtime-start-final.txt)、[服务健康](runtime-status-final.txt)与[实际加载配置](final-runtime-config.json)分别记录运行态；源码哈希与当前宿主一致。

[前端运行冒烟](frontend-runtime-smoke.json)确认登录页返回 200、未登录访问线程接口返回 401；未创建业务运行。[构建清理记录](frontend-cleanup.json)确认本次临时副本已删除，当前产物和旧版本备份保留。

该项实现和定向验收完成，工作区保留可审查 diff。未将其视为整个 OpenSpec 3.7 的身份撤销、缓存、摘要专项完成；未进行真实模型负载或真实浏览器登录验收，也不改写历史已上传 trace。LangSmith 观测保持关闭，原生服务的启动授权仍保留。
