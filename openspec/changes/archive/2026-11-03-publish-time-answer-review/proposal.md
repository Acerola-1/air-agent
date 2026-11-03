# Proposal

## Why

法规数值与文号的知识库门禁目前散落在规划节点（`call_model`）和终答生成内部，触发条件看用户问题，制裁是整篇替换，证据只读 `knowledge_search`。结果是：知识库未命中、但联网搜索已经取得可用数字时，整篇回答被换成一则知识库拒答，联网结果被丢弃；规划草稿也会被预先改成拒答并作为候选材料进入终答提示词。门禁本身要防的事成立，但判定单元、证据通道和生效位置都需要收拢到唯一的发布出口。

## What Changes

- Basic QA 终答通过 `stream_final_answer(answer_review=True)` 按句审核。问题命中法规正则不再把整篇缓冲成拒答。
- **BREAKING（仅 Basic QA 规划）**：移除 `basic_qa` 规划阶段的 `apply_answer_policy`。`planning_draft` 保留模型原文。数据分析与溯源不在本次范围，共享终答函数默认仍走原有 `policy_bound`。
- 证据读取同时覆盖本轮 `knowledge_search` 与 `web_search` 回执；`verified` 仍只表示知识库引用的身份、快照和权限。
- 具体限值、文号或条款按句处理：知识库原文能逐字支撑时，该句换成对应摘录；只有网页能支撑时，换成服务端写的归因句，不把数值说成标准规定；都不能支撑时只替换该句。
- 被拦数值再次出现时，后句只要自己有知识库或网页证据就照常发布；没有证据才继续替换。
- 不新增图节点，不把审核挂进 `OutputPublisher`。控制消息不重审。

## Capabilities

### New Capabilities

- `agent-answer-publish-review`: 终答发布前的统一审核边界，定义检查顺序、证据通道、逐句判定、失败关闭和扩展接口。

### Modified Capabilities

- `basic-qa-approval-websearch`: 明确联网数字在回答中的可发布边界，以及“不得用网页替代法规限值”按句判定而非整篇否决。

## Impact

- `src/common/knowledge/policy.py`：证据读取、触发条件、逐句制裁与网页通道。
- `src/common/output_streaming/answer.py`、`publisher.py`：发布前审核管道接入、取消问题级 `policy_bound`。
- `src/basic_qa/nodes.py`：移除规划阶段的策略调用。不修改数据分析。
- 单元测试与契约测试：知识政策接受/拒绝、网页证据、逐句替换、规划草稿保真。
- `docs/contracts/agent-output-stream-v1/`：`policy_bound` 语义登记为兼容保留、不再新增产生路径。
