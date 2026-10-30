# 撤销额外回答校验：本人回归记录

日期：2026-10-28。变更：`add-basic-qa-conversational-skill-loop`。

用户明确撤销本次新增的时间、城市、指标回显与回答证据声明校验，并禁止本轮继续使用子代理。尚在运行的子代理已中断；本次清理及以下验证由主代理本人完成。

## 最终行为

- `finish_planning` 无参数；模型无工具调用也可自然进入既有终答。
- 移除结果结构适配器、回答类别和证据ID声明，以及据这些信息筛选回答历史的逻辑。
- JSON、数组、文本等工具原文继续进入既有上下文流程；供应商不回显查询城市或时间不会因此阻断回答。
- 历史、摘要及已有资料的计算结果继续按原上下文预算使用。旧加载消息中的规则正文仍由当前活动材料重建，避免过期规则重复生效。
- 保留原有身份、业务和工具调用范围约束。这些约束不检查供应商是否返回时间字段，也不是新增的Skill/MCP人员授权。

## 验证

最终定向回归：**106项通过，6.44秒**。

```bash
PYTHONPATH=src:admin/api .venv/bin/python -m pytest \
  tests/unit_tests/test_basic_qa_loop_integration.py \
  tests/unit_tests/test_basic_qa_computation_scope.py \
  tests/unit_tests/test_business_graph_nodes.py \
  tests/unit_tests/test_business_graph_config.py \
  tests/unit_tests/test_langgraph_business_graphs.py \
  tests/unit_tests/test_basic_qa_runtime.py \
  tests/unit_tests/test_basic_qa_skill_materials.py \
  tests/unit_tests/test_basic_qa_skill_search.py \
  tests/unit_tests/test_skill_tool_scope.py \
  tests/unit_tests/test_skill_search_privacy.py \
  tests/unit_tests/test_basic_qa_native_state_boundary.py \
  tests/unit_tests/test_basic_qa_prompt_version.py \
  tests/unit_tests/test_basic_qa_dependency_catalog.py \
  tests/unit_tests/test_capability_catalog_business_grant.py -q
```

其中编译图测试直接观察最终模型收到的消息，覆盖不回显城市/时间的排名JSON、占比数组、文本说明、受限计算结果和跨轮普通复述；不是只测试辅助字符串函数。测试使用受控模型及工具替身，不将其描述为真实模型或真实MCP验收。

Ruff检查通过，19个文件格式检查通过；`git diff --check`通过。检查范围为`src/common/business_graph/`及本次相关范围、路由、能力、身份模块和循环集成测试。

`openspec validate add-basic-qa-conversational-skill-loop --strict`通过。CLI另提示三个原有主规格仍有delta格式问题，会影响未来归档；本轮未归档或顺手改写这些主规格。

任务5.3已勾选，并重新读取apply指令确认：当前4/31项完成。其他已编写代码仍须按各任务逐项核验；本记录不代表整个变更完成。

## 尚未完成

完整Skill观测接线、真实配置模型多轮、隔离持久化SDK及真实浏览器验收仍未完成。未进行新的提交、推送、正式部署或归档；历史接口采样不充当新版循环验收。
