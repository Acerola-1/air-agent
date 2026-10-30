# Basic QA 旧版受控基线（部分完成）

记录时间：2026-10-28 08:49，Asia/Shanghai。代码 HEAD `7d79526bb4cdd4c5d04911f62943fd11a50c9153`（`dev`）；本次使用的旧运行源码未修改，文件及输入 SHA-256 见 [证据](evidence-20261028-0849/sha256.txt)。工作区已有其他未提交文件；本测试不使用它们改变旧路由实现。Python 3.13.3，固定输入为 [验收集 C01](../data/basic-qa-conversation-loop.v1.json)，历史回复为合成文本，逐节点构造状态，非真实持久化多轮对话。

## 结果及边界

受控 router 的 `amatch` 返回空列表；身份发送守卫仅在测试中由无外呼的替身替换。以原版 `classify_intent`、真实条件边和 `resolve_skill` 分别执行 C01 五轮：

| 当前句 | 分类 | 分支 | 传给受控 `amatch` 的参数 |
| --- | --- | --- | --- |
| 湖州市今天的空气质量如何？ | data_query | resolve_skill | 湖州市今天的空气质量如何？ |
| 那杭州呢？ | data_query | resolve_skill | 那杭州呢？ |
| 可能原因是什么？ | knowledge | prepare_model | 未调用 |
| 缩成两句。 | data_query | resolve_skill | 缩成两句。 |
| 谢谢。 | chitchat | prepare_model | 未调用 |

这证明上述实例中分类和语义路由参数只取最新用户句，无法从参数本身获得此前城市、指标或任务。`amatch` 替身调用共 3 次，结果均由测试设定为空；这不是实际向量命中质量或真实搜索/embedding 计数。各轮分别构造合成历史，没有运行真实模型、业务查询或最终回答，因此**串城、遗漏最终回答上下文、成功率、真实调用数均未验证**，不能据此断言任何真实回答一定错误。每轮 `resolve_skill` 以空候选返回是替身行为，并非对实际生产技能路由未命中的观察。

首次受控测试误将“可能原因是什么？”预期为 data_query，得到实际 `knowledge` 导致 1 failed、4 passed；这是测试预设错误，原始失败记录保留。修正为观察到的知识车道后 5 passed。格式检查首次发现测试输出的 `print` 不符合 lint，保留记录并增加说明后通过。现有定向测试 27 passed、24 skipped、1 warning；24 个依赖真实 SiliconFlow embedding 的测试通过将 `SILICONFLOW_API_KEY=test` 明确跳过，没有使用真实 embedding。warning 为 LangGraph 节点 config 类型提示；还出现废弃上下文配置项警告。本次没有将这些提示算作本变更新增问题。

## 可复跑命令与结果

从仓库根目录执行，真实 API 测试被显式跳过：

```sh
SILICONFLOW_API_KEY=test .venv/bin/python -m pytest tests/unit_tests/test_basic_qa_conversation_baseline.py -q -s
SILICONFLOW_API_KEY=test .venv/bin/python -m pytest tests/unit_tests/test_skill_router.py tests/unit_tests/test_basic_qa_skill_routing.py tests/unit_tests/test_business_graph_nodes.py tests/unit_tests/test_business_graph_config.py tests/unit_tests/test_langgraph_business_graphs.py tests/unit_tests/test_skill_prompt_rules.py -q
.venv/bin/ruff check tests/unit_tests/test_basic_qa_conversation_baseline.py
.venv/bin/ruff format --check tests/unit_tests/test_basic_qa_conversation_baseline.py
```

证据： [受控测试初次失败](evidence-20261028-0849/controlled-nodes.txt)、[修正后](evidence-20261028-0849/controlled-nodes-rerun.txt)、[现有测试](evidence-20261028-0849/existing-regression.txt)、[lint 初次](evidence-20261028-0849/ruff.txt)、[lint 修正后](evidence-20261028-0849/ruff-rerun.txt)、[格式检查](evidence-20261028-0849/format-check.txt)。

## 未采集项和资源

真实 search、embedding 尝试、Skill load、模型、业务外呼次数、请求参数、首正文/总延迟、usage、错误及回答质量：`not_collected`。本测试中仅记录受控 `amatch` 替身的节点调用，不测真实发送。旧版完整核心多轮链、真实模型、真实 SDK/checkpointer、隔离计数 MCP、浏览器和对应串城/遗漏反例均未运行；任务 1.4 **部分完成**。后续需使用固定输入、隔离持久化及计数服务，记录工具真实参数和结果；真实模型另列证据。没有访问共享正式数据库、连接外网或创建持久化测试资源；测试由内存构造和 pytest 临时目录完成。
