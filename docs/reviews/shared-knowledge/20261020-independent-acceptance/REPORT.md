# 共享知识检索：独立验收与补修报告

日期：2026-10-20。对象：三仓 `add-shared-knowledge-retrieval-tool` 未提交实现。结论：**已定位并补修多处真实缺陷；整项 change 尚不通过完整验收，不应归档或声称 A01—A25/K01—K23 全部完成。**

本报告独立于[实施者报告](../20261020-implementation/REPORT.md)。初始工作区、HEAD 和未提交文件摘要见 [baseline.json](baseline.json)；补修后摘要见 `final-source-manifest.json`。保留实施者改动，不 commit、push、归档，不改正式角色或重建知识索引。

## 用户截图中的两个现象

### “查不到适用范围”：已证实是 Agent 截断了已返回正文

真实 RAG 检索返回了 GB/T 18883—2002 资料；其中一个片段约 2253 字符，“本标准适用于住宅和办公建筑物，其它室内环境可参照本标准执行”在约第 799 字符。旧 Agent 仅向模型提供前 500 字符的短摘录，因此模型只看到了目录、前言等内容。

补修将“模型上下文片段”和“持久化/公开短摘录”分开：模型单片段最多 2000 字符，总内容最多 12000 字符，引用最多 8 个；存储摘录仍最多 500 字符。真实回放确认：旧 500 字符没有范围正文，新上下文包含范围正文。该结论只针对本次可观测记录，不代表所有检索问题都由截断造成，也不代表 2002 版是用户应采用的现行版本。

证据：[live-retrieval-source.json](live-retrieval-source.json)。本轮没有修改召回算法、重建正式数据或索引。

### “原文读取失败”：修复了协议缺陷，不能还原原截图的单次错误码

发现 PDF 委托类型不匹配：Agent 签发 `document:read`，RAG 同一个 source 处理入口却无条件要求 `source:read`，导致合法 PDF 被拒。现按 text/PDF 分别要求 `source:read`/`document:read`，Agent 和 BFF 保留 Range、206、Content-Range、no-store 与状态码。

真实联调结果：文本 200、4935 bytes；PDF 206、1024 bytes，范围 `bytes 0-1023/637662`；query 委托读 source 仍返回 403。这证明修复后的受保护协议可读，不能据此推断截图中**文本弹窗**失败就是 PDF 问题。

原截图没有保存失败请求的状态码。本轮使用新登录打开那条历史引用，返回 410；这是已有引用绑定旧源会话后的失效关闭，不能误报为 RAG 没资料。前端现明确提示“来源已失效或权限已变化，请重新检索”，分别处理无权、失效、503、超时。历史答案没有重写；截图背景仍是旧回答。

## 独立发现与修复

| 问题 | 补修与验证 | 层次/边界 |
| --- | --- | --- |
| 正文被 500 字符摘录截断 | 有界模型片段与存储摘录分离；真实范围条款进入模型上下文 | G0 + G1；尚未重新跑真实模型最终答案 |
| PDF 操作委托不匹配、206 丢失 | 分操作鉴权，透传 Range/206/416；保持每次读取重新授权 | G0 + G1；大 PDF 全链路流式限界仍未完成 |
| IAM 签发未复查 Redis 源会话 | 签发前复用 execution-ref 活性核验；Redis 会话缺失时不签发；修复 null operation | G0 + 自建 PG/Redis G1，正向及注销/Redis 缺失反例 |
| Agent 接受不完整委托响应 | 比较完整身份、四项整数修订及带时区 0..60 秒有效期 | G0；不把机器凭据当用户身份 |
| 历史片段、无效证据可被用作新依据 | 只用当前轮成功检索的已核验引用；partial/ambiguous/stale 等不向模型返回来源正文或登记引用 | G0；全平台发布链仍待探针 |
| 真实引用不能证明任意改写 | 具体法规答复确定性退回可核验原文引用，拦错数值/单位/年份/范围/否定词；不冒充语义蕴含模型 | G0；未实现自动补检索一次 |
| 模型草稿或画布绕过依据门禁 | 清除带 tool_calls 的预答正文；分析首轮草稿也检查；画布检查可见文本，未经核验的 HTML/SVG 拒绝，文本可退回来源引文 | G0；原生中间帧及工具参数投影未全验 |
| 历史/压缩后只看可见引用 ID | 持久化线程引用依赖；分批 50 条**全量**实时校验；原文、模型/工具热路径、原生读/运行钩子、归档读增加检查；拒绝用户注入 ToolMessage/state update/goto | G0；采用保守整线程关闭，细粒度派生闭包及原生 SDK 矩阵未通过完整验收 |
| tracing 可伪造知识区标题隐藏归因 | 仅豁免与受控 knowledge_basis 完全一致的渲染块；其它正文继续归因扫描 | G0，有合法含归因词原文正对照 |
| tracing fallback 丢知识依据、跨 run 引用 | 正常/重建/fallback 保留并复查 basis；必须同 thread、同可信 run；空 basis 保持旧摘要 | G0；真实模型到报告/画布/回读闭环未跑 |
| 20 秒超时只停 HTTP，PG 仍可能运行 | 传播单调时钟剩余预算，限制连接/语句超时；开启 aiohttp 客户端断开取消 | G1 自建空 PG：504 约 165.8ms（预算 150ms），断开约 10.9ms 后关闭连接，活动查询 0，独立连接可用 |
| source digest 仅排序 JSON，不是约定规范 | 使用 RFC 8785 库，覆盖中文、浮点、UTF-16 键序及非有限数反例；同步 RAG 锁文件 | G0；依赖 `rfc8785==0.1.4` |
| 知识工具观测被记作 unknown/本地空服务 | 按结构化业务结果区分成功/无命中/无权/超时/技术失败；独立 `knowledge-api` 服务键；tracing 增加单一观测记录入口 | G0；不把它标作 MCP；HTTP 子请求计数及费用全量对账仍未验 |
| 来源弹窗缺少分页/隔离/焦点恢复 | 复用 Dialog；8000 字符分页；请求取消、25 秒前端超时、作用域 epoch 丢弃旧结果；Esc、焦点恢复、中文错误与重试 | G0 静态/构建 + G3 失效路径/窄屏/键盘；成功分页与真实 A→B→A 未跑 |

知识检索工具属于受管 HTTP 工具，MCP 选择关闭不应移除它。现有 MCP 授权/执行路径保持独立；本轮不宣称已完成 MCP 并发、成本或所有重试计数测试。

## 本轮实测结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 最终 Agent 组合回归（知识、安全反例、观测、守卫、线程、tracing） | 131 passed，8.10s；外部 LangSmith 上传关闭 | [agent-final-combined.txt](agent-final-combined.txt) |
| 其它补修定向覆盖 | 先后有 151 passed、169 passed；测试集合不同，**不能相加**或当成全量单测 | [151 项](agent-final-focused-run02.txt)、[169 项](agent-targeted-final-run02.txt) |
| 观测/框架组合复跑 | 93 passed，7.45s；关闭外部追踪 | [agent-observability-final-run02.txt](agent-observability-final-run02.txt) |
| IAM 空库与真实 Redis 集成 | Flyway 26 个迁移；27 IT + 4 单测 = 31 passed，0 skipped | [iam-result.json](iam-result.json)、[完整执行](iam-integration-tests-run03.txt) |
| RAG 全量 | 732 passed、2 failed；两个失败是已有 LLM_BINDING_HOST/127.0.0.1:8787 环境断言 | [rag-full-final-run02.txt](rag-full-final-run02.txt) |
| 前端契约 | 63 passed | [frontend-contracts-final.txt](frontend-contracts-final.txt) |
| 前端最终隔离构建 | Next 构建及类型检查成功；最终构建已装入聊天前端 | [frontend-build-isolated-final.txt](frontend-build-isolated-final.txt) |
| UI 静态审计 | strict 0 findings；不是浏览器功能验收替代品 | [ui-static-audit-final.json](ui-static-audit-final.json) |
| Agent 定向 Ruff | 通过 | [ruff-agent-final.txt](ruff-agent-final.txt) |
| 真实检索/文本/PDF/错 operation | retrieved、200、206、403 | [live-retrieval-source.json](live-retrieval-source.json) |
| 实际 HTTP 超时/断开及 PG 清理 | 通过所述独立探针；**不是 P95 或容量数据** | [live-cancellation-final.json](live-cancellation-final.json) |
| 真实页面 | 旧会话引用明确失效、Tab 限定弹窗、Esc 关闭、焦点恢复；390×844 无越界 | [窄屏](source-expired-390x844.png)、[最终正式前端](source-expired-main-final.png) |

G0=受控/单测，G1=真实接口/存储，G2=真实配置模型完整运行，G3=实际浏览器。本轮没有新增 G2 三图完整结果。

### 原始失败与测试偏差均保留

- `counterexamples-before.txt` 保留修复前的反例失败，不覆盖成通过。
- 首轮 Agent 全量 `agent-full-unit.txt` 为 578 passed/73 skipped/9 failed/18 errors：包含沙箱 socket 限制、旧知识路由/检查点等失败，以及本次新增检查导致的测试依赖不完整和知识区换行问题。后两类已补测试依赖/修复；全量没有在最终源码上重新跑绿，不能沿用作者“所有失败均为既有基线”的笼统声明。
- 首轮新增观测组合 `agent-observability-final.txt` 为 92 passed/1 failed（框架 fake model 迭代器耗尽，同时外部追踪开启）；单独框架 3 passed；关闭外部追踪后的组合 93 passed，最终较大集合 131 passed。该顺序/追踪相关首轮失败尚未完全解释，不删日志，不声称所有运行都绿。
- IAM 最初两轮因本轮测试资料错误（全局角色不能承载企业知识权限、upsert 无更新时返回值假设）失败；修正自建测试资料后 31 passed。没有为此修改正式角色。
- `live-cancellation.json` 的首轮 500 来自探针代码自身括号错误；保留该记录，以 `live-cancellation-final.json` 的真实慢查询与断开结果为有效探针证据。
- 隔离前端构建的沙箱网络失败单独保留；允许宿主网络后完成构建。本轮曾在运行目录构建导致旧前端加载不到 chunk，随后恢复并改用隔离目录构建，最后只刷新聊天前端；未将此操作偏差算作实施者缺陷。
- 清理核对期间一次过宽的容器 JSON 工具输出包含环境敏感配置；这是验收操作偏差，未将原输出写入本目录。后续仅记录白名单字段；未自动修改或轮换凭据。

## A01—A25：完整判定仍未通过

下表状态针对**整行冻结验收标准**；已有局部证据不将整行改成 PASS。

| ID | 状态 | 已有证据与缺口 |
| --- | --- | --- |
| A01 | BLOCKED | 三图×BFF/原生×流式/非流式 12 格未执行；正常 air-reader 当前无 Agent 业务使用权限 |
| A02 | NOT_RUN | 默认工具/问候受控覆盖；未按固定题在真实 QA 全意图跑完 |
| A03 | NOT_RUN | 分析默认接线受控覆盖；菜单/无菜单真实知识闭环缺失 |
| A04 | NOT_RUN | 本地知识工具独立于 MCP；尚无最终真实授权 MCP 请求计数 0 全矩阵 |
| A05 | NOT_RUN | tracing 工具边界及子任务回归；未跑真实 lead/专员组合 |
| A06 | NOT_RUN | 静态注册及部分预算覆盖；完整冷启动/动态刷新/重试未验 |
| A07 | NOT_RUN | 真实合法委托/query 已通过局部；缺隔离合成库的完整消费链 |
| A08 | NOT_RUN | 部分错 operation/ref/源会话反例；跨 client/audience 等完整矩阵未验 |
| A09 | NOT_RUN | 真实 query→source 403、正确 text/PDF 成功；generate/login/再委托未逐项实测 |
| A10 | NOT_RUN | 空 IAM 独立权限资料及会话控制；五项知识条件逐一撤销/恢复未齐 |
| A11 | NOT_RUN | 范围归一化受控检查；air/water/dual/none 和混合非法真实资料矩阵缺失 |
| A12 | NOT_RUN | IAM 注销/Redis 缺失、旧引用 410 正反例；改密/角色/资料撤权/A→B→A 尚未齐 |
| A13 | NOT_RUN | DTO/源状态/Redis 失败关闭有反例；跨层后端故障完整状态传播未齐 |
| A14 | NOT_RUN | 同源来源接口成功、新登录旧引用 410；稳定 owner 续签全矩阵未齐 |
| A15 | NOT_RUN | 当前轮/具体问题/精确引文/画布反例通过；官方 SDK 全部中间帧尚未录制 |
| A16 | NOT_RUN | 线程/企业受控隔离、当前 run 引用校验；完整身份/修订及原生投影探针未齐 |
| A17 | NOT_RUN | text 200、PDF Range 206、query token 403；恶意路径/redirect/大文件全矩阵未齐 |
| A18 | NOT_RUN | 知识区不能绕归因扫描，受控正反例通过；真实知识报告未跑 |
| A19 | NOT_RUN | 正常/fallback/旧摘要回归通过；真实画布、持久化回读尚未齐 |
| A20 | NOT_RUN | 依赖闭包、51 项分批反例和读取钩子已加；删除/墓碑/备份恢复/原生 state/history/replay 不完整 |
| A21 | NOT_RUN | 结果分类/独立服务键/单入口受控通过；外呼次数、费用覆盖、全平台秘密扫描未齐 |
| A22 | NOT_RUN | 响应增量上限、deadline、PG 取消有证据；PDF 全程流式限界未完成 |
| A23 | NOT_RUN | 本轮不做容量/P95 验收；按用户先完成功能 change 再性能评测的顺序保留 |
| A24 | NOT_RUN | 实际失效路径/390×844/键盘通过；成功分页/PDF浏览/全部错误/真实 A→B→A 未齐 |
| A25 | NOT_RUN | 相关兼容回归通过；最终三仓全量及登录/SSO/人类机器路径等未全验 |

## 固定题 K01—K23 的证据状态

所有题目的**完整 G2/G3 固定流程**均未跑完，不能当成题库已验收；以下列出可复用的局部结果，原题不改动。

| 题目 | 本轮证据 | 完整状态 |
| --- | --- | --- |
| K01/K02 | 默认 schema、一般概念不强制检索的受控覆盖 | NOT_RUN |
| K03/K04/K05 | 合成空气/水资料用于 G0；未在隔离 RAG 实际导入并完成角色题库 | BLOCKED |
| K06/K07 | 空结果/候选完整性结构受控检查 | NOT_RUN |
| K08/K09 | 资料指令/污染归因边界受控检查 | NOT_RUN |
| K10 | 片段/摘录/总量有界检查；未导入真实 F-L | BLOCKED |
| K11/K12 | 不检索却输出具体标准、普通问题新增具体规定均有拦截反例 | NOT_RUN |
| K13/K14 | 来源变化/失效关闭及旧会话浏览器 410；缺真实资料撤权/更新/删除闭环 | NOT_RUN |
| K15 | 新旧 digest、fallback、跨 run 拒绝受控通过 | NOT_RUN |
| K16/K17 | 不编造位置；不发布不完整证据正文 | NOT_RUN |
| K18 | 无默认盲重试、结果分类受控；物理 HTTP 对账未齐 | NOT_RUN |
| K19 | 真实 HTTP 断开/超时→自有 PG 语句结束及独立连接可用 | NOT_RUN：尚非完整 Agent stop 链 |
| K20 | 弹窗作用域隔离实现；真实跨企业切换未跑 | NOT_RUN |
| K21/K22 | 分块重组及真实引用错误数值/单位/年份/范围/否定词反例通过 | NOT_RUN：缺原生流实证 |
| K23 | 51 项第二批拒绝、无可见 ID 仍读取依赖的受控反例通过 | NOT_RUN：真实失效/原生全矩阵未齐 |

## 运行态、资源及复跑入口

- 本轮自建 PG `25439`、Redis `25479`，空 IAM 测试库在这个自建 PG 中，不是正式 `19132`。临时只读 RAG `28099` 使用独立 record 根；临时前端 `3300` 使用独立构建目录。资源与回收结果见 [resources.json](resources.json)、[cleanup.json](cleanup.json)。没有向共享 RAG 写合成资料。
- 实际 G1 检索使用真实已有 IAM/RAG 只读授权和既有资料；取消测试、IAM 集成测试使用本轮独立资源。这两类证据不能混称“完整隔离合成环境”。
- 本轮测试登录已退出；未扩大正式 air-reader 权限。自建容器/临时进程已回收，未删除共享卷或镜像。
- 聊天前端最终构建已加载；[frontend-runtime-refresh.txt](frontend-runtime-refresh.txt) 和 `runtime-final.txt` 记录服务状态。**IAM/RAG/原生 Agent 后端补修源码没有切换到正式运行进程**；服务健康不等于补修已上线。
- IAM V25/V26、Agent 0023 属于原实施状态；本轮没有新增或改写已执行迁移。RAG 增加 RFC8785 锁依赖，正式启用前需按锁更新对应环境。
- 时间以各执行日志、JUnit 和探针实测为准；模型实施耗时、总人工等待、Token 和费用未采集，不能记 0。未测 P50/P95、吞吐或生产容量。

受控回归可从 Agent 根目录运行，关闭外部追踪以减少环境干扰：

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false .venv/bin/python -m pytest \
  tests/unit_tests/test_knowledge_acceptance.py \
  tests/unit_tests/test_knowledge_contracts.py \
  tests/unit_tests/test_observability_core.py \
  tests/unit_tests/test_observability_takeover.py \
  tests/unit_tests/test_tenant_runtime_guard.py \
  tests/unit_tests/test_guard_middleware_framework_compat.py \
  tests/unit_tests/test_thread_claim_protocol.py \
  tests/unit_tests/tracing/test_gate_security.py \
  tests/unit_tests/tracing/test_publishing.py \
  tests/unit_tests/tracing/test_subagents_deepagents.py -q
```

真实层复跑必须新建独立空资源及本轮凭据，按 [acceptance.md](../../../../openspec/changes/add-shared-knowledge-retrieval-tool/acceptance.md) 冻结题库、资料、请求 ID 与计数。不能复用已删除临时 DSN，也不能对正式 IAM 执行 create/drop 测试库脚本。

## 下一轮必须补齐的交付

1. 先在完整隔离 IAM/RAG PG/Qdrant/Neo4j/record 范围建立企业 A/B 和 F-A/F-W/F-B/F-I/F-L；采用正确企业知识角色及业务使用权限，不修改正式账号来凑测试。
2. 在最终三仓运行构建上完成 12 格；至少明确 QA 原题真正答出库内范围、分析菜单边界、tracing 正常与 fallback 报告/画布/回读。
3. 实测原生 state/history/replay/stream 与压缩后依赖闭包，含第 51 条拒绝、撤权、删除墓碑、晚到写入和备份恢复；当前保守整线程关闭不能冒称已实现所有细粒度生命周期要求。
4. 补齐 PDF 大文件流式限界、真实成功分页和 A→B→A；对观测子请求与费用做真实对账。
5. 功能/安全门禁通过后再按 A23 性能计划测试；在此之前不将本报告当成容量结论。

本轮修复与证据可供下一位实施/验收者继续使用，但完整 change 维持未验收完成状态，OpenSpec 任务不批量勾选。
