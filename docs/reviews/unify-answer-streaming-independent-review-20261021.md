# 三图正文流式：独立验收

日期：2026-10-21。冻结基线：`dev`，`7711914932a68f283800d9e6e4175e3a4c5bea01` 加实施者未提交 diff。验收对象：`unify-user-visible-answer-streaming`。

**结论：暂不通过。** 正常路径已有实质实现，但发现四项可复现缺陷；不是仅因真实外部服务证据不足而拒绝。未修改产品实现，未提交、推送或归档。此前性能工作及实施者所有 diff 原样保留。

## 1. 已独立验证

- 定向 Python 回归：**55 passed**，覆盖输出契约、发布器、业务图配置/节点、溯源分节/守卫/发布；耗时 14.68 秒。
- 前端契约：**65 passed**，包括新输出消费者及原聊天/取消/身份契约。
- `openspec validate unify-user-visible-answer-streaming --strict`：通过。
- `git diff --check`：通过。
- 前端技能静态审计 `audit_project.py . --mode strict --no-write`：0 findings。它不证明浏览器或业务状态正确。
- 额外使用当前实际 Python 发布链及当前 TypeScript/聊天 store 做受控反例，确认下述问题；未调用真实模型、IAM、MCP 或数据库，未新建容器。

上述正常路径测试包括：DeepAgents 实际编译图运行四个受控专员；后续章节屏障未解除时，已收到前面章节，尚无 done。可以认可逐节生成/发布确有实现，不能据此认可故障/恢复路径。

## 2. 必须修复的代码问题

### R1 · P1：知识问答被改为无法检索知识库

位置：`src/common/business_graph/nodes.py:412`、`src/common/business_graph/builder.py:55`。

`_needs_planning()` 对 knowledge 车道仅在 `is_specific_regulation_text(question)` 成立时返回 true。其余知识问题直达不绑定任何工具的 `answer_model`，因此原本可用的 `knowledge_search`、创建产物等能力无法执行；工具目录单测仍显示 schema 存在，但实际控制流根本不经过它。

本机复现：

| 问题 | intent | needs_planning |
|---|---|---|
| PM2.5是什么？ | knowledge | false |
| 请根据知识库解释臭氧是怎么形成的 | knowledge | false |
| 请解释重污染天气应急预案的基本原理 | knowledge | false |

第二项明确要求知识库仍被跳过。流式修复不能通过删除检索机会来实现快路径。

修复要求：保留知识检索和产物能力的实际可达性，区分纯问候直答与需要/可能需要检索的知识问答；同时维持最终无工具生成。回归必须从入口路由到工具执行，不能只断言 `_available_tools()` 包含工具名。

### R2 · P1：溯源半途失败后重新发布，恢复会话也未真正持久化

位置：`src/intelligent_tracing/section_publishing.py:59`、`:126`；`src/intelligent_tracing/tools.py:472`、`:517`；`src/intelligent_tracing/publishing.py:263`。

每次调用生成新的 output/message ID；`PublicationSession` 只在所有章节、保存和 done 全部成功之后返回并写入图状态。已输出章节之后发生错误时，没有 checkpoint 中的部分会话进度可供恢复；工具又每次生成新的 report ID，使传入的旧会话匹配条件失效。所谓 resume 单测只是对一个**已经完成**的会话再次调用帮助函数，并断言 replay 标记，并非原生暂停后恢复。

本机在实际 DeepAgents 图的 `claims-2` 屏障第一次触发时注入一次异常，得到：

```text
output A / report A: start → header → claims-1 → aborted
output B / report B: start → header → claims-1 → claims-2 → limits → done
最终 publication_session.completed = true
最终 validated_report.published = true
```

后半段由 after_agent 自动重建并发布，不是用户重新受理的运行。当前 TS 消费者收到 output B 的 start 实际返回 `resync/output_changed`，随后 B 的正文按 unknown_output 忽略，因此不能靠 replay=true 达到正确恢复。

修复要求：已公开部分的失败保持准确失败终态，不能在同一流中自动拼接另一份报告；冻结计划、输出身份、序号及已发布章节必须有真实可恢复的进度方案。补官方 API 暂停/恢复或进程恢复的证据，检查前后身份/章节，而不是只测已完成函数再次调用。

### R3 · P2：溯源 SSE 与历史消息的 ID 不同，产生重复回答

位置：`src/intelligent_tracing/section_publishing.py:59`、`src/intelligent_tracing/publishing.py:355`；`frontend/lib/chat-store.ts:430`、`:549`。

发布器自动生成 `msg_<随机值>`，但 `_visible_update()` 保存的 AIMessage ID 是 `rep_<随机值>`，工具摘要也声明 message_id=report_id。正文消费者和历史使用不同 ID。

本机实际受控溯源图验证：done.message_id 为 `msg_…`，唯一可见 AIMessage ID 为 `rep_…`。再用项目现有聊天 store 测试 harness 验证：

```json
{"probe":"tracing_on_values","visible":["rep1"],"streamingText":"已验证报告。"}
{"probe":"tracing_final_fetch_failed","answers":[{"id":"rep1","text":"已验证报告。"},{"id":"msg1","text":"已验证报告。"}]}
```

即 values 已带最终报告时流式卡片仍保留；若末次 getThreadState 失败，兜底分支又追加一条相同内容，重复永久留在本地会话。

修复要求：统一 SSE、AIMessage、可信终答映射和恢复使用的消息身份（report_id 可保留为独立报告引用）；补“done→values→末次回读失败”的 store 集成反例及溯源刷新去重检查。

### R4 · P1：数据分析已公开正文后超时仍发新的完成回退稿

位置：`src/data_analysis/nodes.py:609`。

外层 `asyncio.timeout()` 超时后将 result 置 None，接着无条件落入“未公开任何正文”回退分支，没有检查 `publisher.total_chars`。因此已经输出的部分被 aborted，随后另开输出并 done，违背“部分失败保持未完成、不另拼一篇回退”的契约。

本机用 50ms 整理超时和先产出一句再等待的模型复现：

```text
output A: start → delta("这是一段已经通过检查的分析正文。\n") → aborted
output B: start → delta("分析结果已生成，但最终答案整理未完成。请重试。") → done
返回 AIMessage.ui_visible = true
```

当前前端对 B 的 start 同样返回 output_changed；服务端最终状态与已显示部分不一致。已有测试只覆盖没有安全正文发布时的失败，未覆盖这个外层超时分支。

修复要求：以发布器实际已发字数/状态裁决，已公开后只能保留同一输出的不完整终态；未公开任何正文时才可用固定失败文案，并保证该协议路径能被当前客户端接受。补真实外层超时与部分正文的联合测试。

## 3. 验收材料需要校正

- 实施报告 S07/S10/S11 的“通过”不能覆盖本次反例，应保留原报告并新增修复报告；`tasks.md` 全部勾选与实际缺口不一致。
- 官方 HTTP tracing 探针导出的是 `scripts/streaming_probe/tracing_native_graph.py` 的专用 StateGraph，内部调用真实 Gate/发布器，但不是产品的完整 DeepAgents 导出图。四专员执行由进程内/受控 BFF 另测；两个证据不能合并声称完整官方原生图已经通过暂停恢复矩阵。
- 报告已如实注明 S09（真实企业/context）、S12（旧客户端实机）、S13（慢确认）、S14（溯源真实外部依赖）受阻/部分通过；这些继续保持原状态，不伪报。本轮没有扩展为真实模型性能或容量测试。

## 4. 下一轮最小范围

先修 R1–R4，补针对实际入口/消费者的回归，再复跑上述 55/65 定向套件与 OpenSpec。恢复证据必须区分实际原生产品图和专用发布器探针。无需重跑 40 题性能评测或后台 SQL 压测。通过后再决定归档与 Git 交付。
