# Tasks

## 1. Basic QA 规划草稿

- [x] 1.1 删除 `src/basic_qa/nodes.py::_planning_updates` 中的 `apply_answer_policy` 调用，保留规划消息 `content=""`、`ui_visible=False`
- [x] 1.2 不修改 `data_analysis` 与溯源。共享 `stream_final_answer` 默认仍走原有 `policy_bound`
- [x] 1.3 规划草稿含法规数值时保留模型原文：`tests/unit_tests/test_business_graph_nodes.py::test_planning_draft_keeps_regulation_wording`

## 2. 终答按句审核

- [x] 2.1 新增 `src/common/output_streaming/review.py`。具体值由知识库摘录、网页归因句或单句弃答处理；检查抛错时法规句失败关闭
- [x] 2.2 Basic QA 的 `compose_answer` 传入 `answer_review=True`。审核发生在 `publish` 之前，已发布前缀不改写
- [x] 2.3 问题命中正则不再把 Basic QA 终答送入 `_stream_policy_bound`。该路径保留给其他调用方
- [x] 2.4 在 `docs/architecture/basic-qa-graph.md` 写明审核在 `answer_model` 内，不是图节点，也不覆盖数据分析与溯源

## 3. 证据与话术

- [x] 3.1 本轮 `knowledge_search` 区分未调用、失败、无命中、已取得。`verified` 仍只表示身份、快照、权限
- [x] 3.2 本轮 `web_search` 的 summary/snippet/content_excerpt 按 `[web:n]` 索引，审核不重新调用工具
- [x] 3.3 知识库能逐字支撑的句子改为摘录；只有网页能支撑的句子改为服务端归因句；同一标准的冲突限值不改用网页
- [x] 3.4 被弃数值再次出现时，后句有自己的证据则发布，否则继续替换。计数或年份后缀不整句拦截
- [x] 3.5 未调用、失败、无命中、与原文不一致使用不同话术，不出现审批、额度或外部服务

## 4. 验证

- [x] 4.1 `tests/unit_tests/test_basic_qa_publish_review.py` 覆盖：问题命中但回答是背景、知识库未命中时网页浓度仍发布、网页限值被改写且后句保留、知识库摘录、跨句复述、未知引用、冲突限值、检索失败话术、审核异常
- [x] 4.2 既有终答与知识政策单测仍通过：`test_output_stream_publisher.py`、`test_knowledge_acceptance.py`、`test_knowledge_contracts.py`、`test_business_graph_nodes.py`、`test_compaction_boundaries.py`（79 + 25 passed）
- [ ] 4.3 真实联网冒烟仍未做：需要一轮“知识库未命中 + 已批准联网有数据”的实际轨迹，确认页面上非规范数字留下、法规句被改写、整篇不再换成固定拒答。归档时保留为未实测，不记为通过；页面手工验证只覆盖了知识库命中后的摘录可读性。

## 未纳入

- 不建检查注册表、超时或 shadow 运行时
- 不删除 `with_server_citations` 与数据分析侧的 `apply_answer_policy`
- 不把审核挂到溯源报告或控制消息
