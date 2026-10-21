# 三图流式独立补修结果

日期：2026-10-22。基线：`dev / 7711914932a68f283800d9e6e4175e3a4c5bea01` 加原实施者未提交 diff。对应独立验收：`docs/reviews/unify-answer-streaming-independent-review-20261021.md`。

**四项代码缺陷已修复，定向本地回归通过；尚未宣称整个 change 的真实环境最终验收通过。** 所有改动未 commit/push/合并/归档；原性能任务、原报告和其他未提交工作均保留。

## 修复与实际证据

| 独立验收问题 | 修改 | 本轮实际验证 |
|---|---|---|
| R1 知识库检索不可达 | 只有纯问候跳过规划；知识问答保留检索/产物工具。每轮清空旧 planning_draft，避免下一问复用旧规划材料 | 真实编译 Basic QA 图，从“请根据知识库解释臭氧是怎么形成的”进入知识车道，实际执行受控 knowledge_search 一次，再进入无工具流式回答；不是仅检查工具列表 |
| R2 溯源失败重发/无恢复进度 | 发布改为继承父 checkpointer 的子图：初始化身份→start→逐节→最终保存/确认。冻结计划和证据元数据在首帧前保存，章节、序号及正文进度逐节点保存；普通失败写回失败会话，after_agent 不另建报告 | 实际 DeepAgents 四专员图在 claims-2 前暂停，关闭 SQLite 文件连接，重建全部图/模型/内存 registry 后恢复。只得到 header、claims-1、claims-2、limits 各一次，输出/消息 ID 不变，seq 连续，全文与 done 一致。另覆盖恢复前撤权、换 attempt、换 epoch，均无新增正文/无 done |
| R3 溯源消息 ID 不一致 | 发布器 message_id 使用唯一 report_id，与历史 AIMessage、工具摘要和后续可信终答映射一致 | 实际编译溯源图断言 done.message_id=可见消息 ID=report_id；真实 chat-store 契约模拟 done→values→末次回读失败，只保留一条回答，values 到达即清除流式占位 |
| R4 超时后另发完成稿 | 外层整理超时依据 publisher 已发正文裁决：有正文仅保留 aborted 和不可见不完整消息；只有零正文失败允许固定 single_shot。客户端仅允许“已 aborted 且零正文”的固定文案替代输出 | 50ms 外层超时场景：start→delta→aborted，唯一 output，无 done，无可评价完整终答。另测零正文替代可接收，有正文后的替代仍拒绝 |

恢复组合测试还暴露并修复了旧重试边界：`TracingBudgetMiddleware` 会按异常字符串中的 `503/connection/timeout` 判断重试，暂停信号的载荷或随机 ID 可能误命中。现在 `GraphBubbleUp` 明确原样上抛；磁盘恢复测试使用包含这些词的真实 interrupt 载荷，验证不重复执行。

共享发布器同时拒绝完成后不同全文的第二次 finish，并正确支持契约声明的同步/异步授权探针。

## 定向验证结果

```text
LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false .venv/bin/python -m pytest \
  tests/unit_tests/tracing \
  tests/unit_tests/test_output_stream_contract.py \
  tests/unit_tests/test_output_stream_publisher.py \
  tests/unit_tests/test_business_graph_config.py \
  tests/unit_tests/test_business_graph_nodes.py -q
154 passed，16.26s

npm --prefix frontend run test:contracts
66 passed（其中 agent-output-stream 独立 harness 内含9个断言场景）

npm --prefix frontend exec -- tsc --noEmit -p frontend/tsconfig.json
通过

受影响 Python 文件 Ruff check / format：通过
git diff --check：通过
OpenSpec strict：通过
前端静态 audit_project.py --mode strict --no-write：0 findings
```

Python 留有一条既有装配注解警告：ledger_finalize 的 config 参数类型不是 RunnableConfig；没有借此删除断言或跳过用例。本轮未重跑根全量套件、生产构建或全量浏览器矩阵，不把此前实施者的 679 项结果当成本轮复跑。未运行真实模型、40题基线或容量/SQL压测。

## 官方 API 复验的阶段记录（以下阻塞已于本轮解除）

已补 `scripts/streaming_probe/tracing_resume_fixture.py`：使用生产 `build_tracing_graph` 和真实四专员编排，只将模型/数据替换为合成受控源，不再导出另一张专用发布 StateGraph 冒充产品图。

`scripts/streaming_probe/checkpoint_api_probe.py` 创建自有 PG、Redis、官方 API 容器，设计为首段暂停后重启 API 再原生 resume，最终自动清理。复跑入口：

```bash
.venv/bin/python scripts/streaming_probe/checkpoint_api_probe.py \
  --use-configured-license \
  --out docs/testing/streaming-unify-20261021/repair/official-api-restart-NEXT.json
```

`--use-configured-license` 需要用户明确许可；默认不读取私有环境。它只向官方开发许可校验提供现有 LangSmith 键，tracing 关闭，不发送聊天正文，不读取数据库/身份/模型真实凭据。

前三次隔离准备分别止于 Redis 最小内存参数、当前 container inspect 地址字段、官方 API 开发许可校验；均不是图运行通过，也不是产品图失败。前两项参数已修正。原始未通过记录保留在 `repair/official-api-restart*.json`。第三次镜像实测 API 版本为0.16.2、revision 8d6666b；不能沿用旧报告的镜像版本描述。

提供现有许可键的第四次启动被自动审批拒绝：理由是本次请求尚未明确授权读取本机密钥并用于第三方许可证验证。命令未执行，没有读取/注入该键；已向用户提交受限授权问题，等待答复，不通过改路径或其他方式绕过拒绝。

因此 **实际官方 API 的暂停→进程重启→恢复证据仍为受阻**。已通过的是上述真实 LangGraph/DeepAgents 与磁盘检查点重建测试，不能混写。

## 资源、任务状态与后续

- 三次尝试自建资源仅 `aa-streamfix-20261021-pg`、`aa-streamfix-20261021-redis`、`aa-streamfix-20261021-api`，均已 stop/delete；独立读取本机容器名称清单确认三个名字全部不存在。临时 env 由 TemporaryDirectory 回收，未写正式 `.env*`，未创建持久共享卷。
- 正式15432/19132、Langfuse、知识库和共享容器未修改/重启；拒绝的第四次命令未创建资源。
- OpenSpec补修7.1–7.4勾选；纠正原全勾选状态，4.4（官方原生恢复等最终证据）、5.3（独立慢确认）、6.1（最终隔离IAM/三图全矩阵）仍未勾选，当前20/23。
- 先前报告的S09真实企业/context、S12旧客户端实机、S14溯源真实外部依赖仍保留原受阻状态，不由本轮本地测试抵消。
- 本轮不自动归档、提交或部署。许可授权到达后可继续官方 API 复验；其余验证范围不擅自扩成全量性能评测。Token未采集。

## 用户授权后的官方 API 复验：通过（2026-10-22）

用户明确允许本次使用现有 LangSmith 开发许可键后，官方启动校验通过。最终证据为 `docs/testing/streaming-unify-20261021/repair/official-api-restart-10.json`，状态 `passed=true`、`cleanup_verified=true`。不是将先前失败记录改成通过。

- 实际环境：官方 langgraph-api **0.16.2** / LangGraph **1.2.16** / DeepAgents **0.7.28** / langchain-core **1.6.12**，自建空 PostgreSQL 与 Redis。
- 使用生产 `build_tracing_graph`，从正常 `freeze_tracing_request` 工具开始，再执行 station-data/grid-data/meteo-analyst/evidence-reviewer，四个子执行均 **succeeded**。模型、数据、身份后端和报告存储为受控合成依赖，**不代表真实 IAM、真实业务报告库或真实 MCP/RAG 联调**；真实的是官方 HTTP/SSE、实际 DeepAgents 编排/发布代码和 PG 检查点。
- 原生第一轮：start(seq=0) → header(seq=1) → claims-1(seq=2)，在 claims-2 渲染前实际 interrupt；尚无 done/aborted。
- 停止并重新启动 API 容器，丢弃整个 Python 进程内模型状态、证据 registry 和图实例；PG/Redis 保持本次测试数据。
- 原生 resume：只发送 claims-2、claims-3、claims-4、limits，随后 done；合计序号 **0–7 连续**，同一个 output_id/message_id，先前章节与后续章节集合无交集。章节数与首帧冻结 manifest 的6章一致，而非沿用直接 fallback 测试的4章假设。
- done 全文等于6个有效 delta 连接；最终历史只有一条可见 AIMessage，ID与done一致，4个子执行成功，最终无 pending task。正文 SHA-256 `3fb8df00152b7842ccaeb881ae19973c03f26b277d68054ec6bed99d373690fa`。
- 三个自建容器均已删除，并以独立容器名称查询再次确认残留为0；临时许可 env 文件已随 TemporaryDirectory 回收，未写正式 .env，tracing保持关闭。未提交、推送或归档。

中间04–07记录对应探针入口准备问题（JSON直接输入不等于正常冻结、身份注入/隔离身份后端未齐及一次修正尚未被容器加载）；08–09真实恢复已经完成，但探针错误固定期待4章而正常专员流程生成6章，故原记录继续保留FAIL。最终改为对照冻结manifest，并保持无重复、连续序号、同消息ID、四子执行成功、历史唯一及全文一致的全部断言；未通过删除失败样本或放宽产品要求达标。

**当前结论：R1–R4代码补修、本地154/66定向回归及本次官方API/PG重启恢复验证通过。** 先前的许可授权阻塞已解除；整个change尚有独立慢确认与最终IAM/三图完整组合等既有范围未全部实测，因此保持活动状态，不冒充整个change已最终验收或正式上线。

## 提交前定向复验（2026-10-22）

本次保存流式补修、既有产品性能工具/脱敏历史基线与新的全面测试交接。流式/溯源154项仍通过，另复验 `test_unified_campaign.py` 32项通过（6.65s）；前端契约66项通过，性能/探针Python Ruff通过，OpenSpec strict及diff检查通过。首次组合运行中的10项回环HTTP测试被沙盒绑定限制拒绝；以受限本地受控服务权限复跑32项后全部通过，没有删除断言或扩大skip。上述是定向验证，不是全面产品测试已经完成。

完整后续任务见 `docs/handoffs/agent-full-system-test-goal.md`，要求先校准测试工具计时/资源采集，再执行UI、后端及分阶段产品聊天评测；原性能工具与旧报告不因本次保存自动升级为已验收基线。Git操作仅按用户本次提交授权，不push或归档。
