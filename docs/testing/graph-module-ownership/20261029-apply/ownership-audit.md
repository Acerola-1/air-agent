# 图模块归属独立审计（2026-10-29）

## 范围及结论

以 `dev` / `98ce4ff33aecdcdcec6ef87a9c190ee1e14eefe1` 加实时未提交工作区为对象；基线冻结清单位于 `openspec/changes/refactor-graph-module-ownership/source-inventory.json`，不是当前文件摘要。审计仅修改本报告及架构测试，未修改生产文件、提交、部署或启动正式服务。工作区并发变化可能使本报告的路径和行号过时，整合时应重新核对。

**阻断（发现时）**：`admin/api/tests/test_observability_deepagents.py:211` 仍 `from common.skill_discovery import create_find_skill_tool`，被删除模块不可导入。它是生产引用以外的测试残留，不能以旧接口无生产调用为由忽略。该文件的两个测试还共用 `test_event_ledger_api.py` 的旧身份夹具：创建本地用户／企业后调用登录，当前登录入口已转发远程 IAM，原测试又直接从本地 `auth_sessions` 查询身份。仅替换导入无法使其验证当前身份与业务库链路；应先在自有独占业务库中配置可控远程 IAM 及有效受理，再将第二个测试的 matched／selected／loaded／executed、失败分支和正文不进入事件断言迁到真实现行 Skill 调用。第一个真实 DeepAgents 子任务／摘要测试必须保留，并修复其同一夹具。原文件保存在 `preimplementation/admin/api/tests/test_observability_deepagents.py`；本次未修改或删用例，阻断仍未解除。

6.1 **部分支持**：旧公共业务包、旧四个上下文控制文件、旧 `skill_discovery.py`、旧 `context.py` 不存在；源码、管理 API、测试、脚本及入口声明的旧路径文本扫描发现上列测试残留，故未满足完整清理。两图编译入口的独立进程导入／网络阻断测试通过；并不覆盖每种惰性运行分支及外部消费者。

6.2 **部分支持**：下列 127 个 inventory 公共源与资产逐项列出，所有原定保留文件存在；两个摘要不同的保留文件特别标注。按关键模块复查，材料读取及共享提示规则有正当使用方，图流程和生命周期分别归位；单消费者提示、旧内联读取和历史误导性提示等疑点见下文。存在上述旧测试引用，不能声称清理已经完整。

6.3 **部分支持**：`tests/unit_tests/test_graph_module_ownership.py` 现检查 common 到图／application、两图相互之间的直接导入，包括各作用域（含 TYPE_CHECKING）、相对引用、`__import__` 和 `importlib.import_module` 字面量及常见别名，并拒绝可识别的变量动态目标；附负控证明检测器能识别这些样例。独立进程测试禁止另一图并编译入口、核查三图固定装配及分别加载两图策略。它不证明动态计算／反射／外部插件所有可能路径，也不替代受控执行、跨版本恢复及真实业务验收。测试代码扫描不到经第三方注入的回调具体行为；要对照固定装配和业务测试审查。

## 实测与边界

- `.venv/bin/python -m pytest tests/unit_tests/test_graph_module_ownership.py -q`：25 passed（11.21 s），含导入无网络检查；限合成和导入／编译，不证明业务行为。
- `.venv/bin/ruff check tests/unit_tests/test_graph_module_ownership.py` 与 `.venv/bin/ruff format --check tests/unit_tests/test_graph_module_ownership.py`：通过。
- 原文扫描范围为 `src`、`admin/api`、`tests`、`scripts`、`langgraph.json`、Dockerfile、compose；扫描旧完整模块名及旧文件路径。排除历史文档和冻结快照，发现上述管理测试与一条故意验证文件已删除的断言；不等于证明所有动态反射安全。
- 管理观测测试只尝试收集，显式配置回环端口 1 的无效测试 DSN，不连接正式库；根虚拟环境缺少管理 API 的 `asyncpg`，在加载 conftest 时失败，未收集、未执行。不得将此结果描述为测试通过。
- 清单 `retain` 项 SHA-256 与捕获时比对：除 `event_ledger/store.py`、`identity/mcp_outbound.py` 外一致；仅描述相对冻结清单的变化，不推断归因或安全性。并发修改可能改变这一结论。
- `application/business_policies.py` 固定分支按实际图延迟导入并登记；`basic_qa/graph.py` 和 `data_analysis/graph.py` 分别注册自己的发送策略。静态扫描 common 没有图／application 的直接 import；公共策略持有宿主注入的调用接口属于预期依赖反转。

## 特殊去向、残余与限制

- `business_graph/intent.py` 的 `classify_intent_rule` 在迁移前由旧 `nodes.py` 调用，但旧 builder 仅非 BQA 通用分支进入该判断；当前公开 Basic QA 工厂的 v2 路径不需要该旧规则。迁移前原文与入口见 `.local/graph-module-ownership/20261029-apply/preimplementation/`，这里的结论只限已检查仓库生产入口，不包括外部私有调用。不要据此删除 checkpoint 中仍需的状态字段或重启旧意图路由。
- `find_skill` 的旧定义在基线仅有自身定义，旧 `skill_content.py` 使用的是其中材料读取辅助；当前租户中间件仍通过 `load_skill_rules` 显式加载材料，BQA v2 则使用自己的材料和会话能力流程。旧观测测试残留列为阻断，不能把旧流程退役等同测试均已迁移。
- 原 `tool_wrappers` 的重试、fallback、事件与结果生命周期位于两个图独立的 `tool_runtime.py`，作用域在各自 `tool_policy.py`；底层通用发送在 `common/tool_execution/mcp.py`，协议与存储仍为公共机制。设计和 inventory 所列 `common/tool_execution/results.py` 是未采用的拟定位置；该文件当前不存在，不应建回共享生命周期控制器。
- `common/prompts.py` 的共享空气质量单位、时间、数据真实性、地区规范及专家／快速模式片段由两图组装；`MAIN_AGENT_TOOL_USE_OUTPUT_GUARD`、`ANTI_PROCESS_RESIDUE_GUARD` 和 `ARTIFACT_GENERATION_GUIDE` 当前仅 BQA 引用，分别是工具输出、过程痕迹及制品的通用原子规则，主任务按职责将其保留；BQA 的组装顺序和业务提示已归本图。单消费者不能单独证明图专属。画布指南和 BQA 提示仍声称 fenced code 自动打开画布，已知历史行为可能不符当前链路，属既有产品风险，此项审计未进行真实画布请求验证或修改提示。
- 公共 `skill_router.py` 的 `resolve_skill_file` 与 `common/skills/materials.py` 的 `load_skill_rules` 仍为租户中间件显式材料加载所用，不能因旧 `find_skill` 退役而一起移除。材料正文、版本、领域边界及模型行为没有在本审计中更改。
- 清单外新增公共机制：`common/capability_policy.py`（策略提供者窄接口）、`common/skill_tool_scope.py`（Skill 工具范围）、`common/runtime_context.py`、`common/skills/{__init__,materials}.py`、`common/tool_output_summary.py`、`common/tool_execution/{__init__,mcp,policy,protocol,schema}.py`；分别为拆分接收目标或共同机制。`src/application/` 作为宿主装配层，不视为公共模块或两个图互相导入。
- 清单不涵盖本轮新增的各图模块及其材料完整语义；下方映射是模块职责与文件存在性复核，不是每个函数的行为等价证明。发送边界、真实 SDK 子任务、跨版本 checkpoint、真实模型与正式部署由主任务分别验收。

## 原 common 清单逐项最终去向

以下每条对应原 inventory 一条，摘要一致仅说明文件字节相同，不说明其运行行为经过验收；拆分项列最终位置并注明与拟定目标的差异。

- `src/common/__init__.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/artifact_http.py` → 保留公共 HTTP 服务实现，入口装配至 `src/application/http.py`。
- `src/common/artifact_scope.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/artifacts/__init__.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/artifacts/create_artifact_tool.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/business_graph/__init__.py` → 退役；无旧包或转发壳。
- `src/common/business_graph/basic_qa_integration.py` → 迁移／拆分至 `src/basic_qa/capabilities/projection.py`。
- `src/common/business_graph/basic_qa_runtime.py` → 迁移／拆分至 `src/basic_qa/capabilities/runtime.py`。
- `src/common/business_graph/builder.py` → 迁移／拆分至 `src/basic_qa/builder.py`。
- `src/common/business_graph/capability_observation.py` → 迁移／拆分至 `src/basic_qa/capabilities/observation.py`。
- `src/common/business_graph/intent.py` → 退役；非 BQA v2 分支的规则及入口无现用公开图消费者；保留节点字段兼容，见下文基线。
- `src/common/business_graph/mcp_tool_catalog.py` → 拆分至 `src/basic_qa/capabilities/mcp_catalog.py` 和 `src/common/tool_execution/schema.py`（schema 归一与版本）。
- `src/common/business_graph/mcp_tool_index.py` → 迁移／拆分至 `src/basic_qa/capabilities/mcp_index.py`。
- `src/common/business_graph/mcp_tool_search.py` → 迁移／拆分至 `src/basic_qa/capabilities/mcp_search.py`。
- `src/common/business_graph/nodes.py` → 迁移／拆分至 `src/basic_qa/nodes.py`。
- `src/common/business_graph/prompting.py` → 迁移／拆分至 `src/basic_qa/prompts.py`。
- `src/common/business_graph/session_capabilities.py` → 迁移／拆分至 `src/basic_qa/capabilities/state.py`。
- `src/common/business_graph/skill_content.py` → 材料读取至 `src/common/skills/materials.py`；旧单选内联工具退役，BQA 当前加载至 `src/basic_qa/capabilities/runtime.py`。
- `src/common/business_graph/skill_materials.py` → 迁移／拆分至 `src/basic_qa/capabilities/skill_materials.py`。
- `src/common/business_graph/skill_search.py` → 迁移／拆分至 `src/basic_qa/capabilities/skill_search.py`。
- `src/common/business_graph/state.py` → 迁移／拆分至 `src/basic_qa/state.py`。
- `src/common/business_graph/subagent_summarizer.py` → 改名至 `src/common/tool_output_summary.py`。
- `src/common/business_graph/tool_recovery.py` → BQA 恢复预算至 `src/basic_qa/tool_recovery.py` / `tool_policy.py`；数据分析工具策略至 `src/data_analysis/tool_policy.py`；协议辅助 `src/common/tool_execution/protocol.py`。
- `src/common/business_graph/tool_wrappers.py` → 拆分至两图 `tool_policy.py`、`tool_runtime.py`，发送机制 `src/common/tool_execution/mcp.py`、策略 `policy.py`、schema `schema.py`；不存在公共 `results.py`。
- `src/common/business_registry.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/capabilities.py` → 迁移／拆分至 `src/common/capabilities.py`、`src/basic_qa/capability_policy.py`、`src/data_analysis/capability_policy.py`、`src/application/business_policies.py`。
- `src/common/computation/__init__.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/computation/helpers.js` → 保留原路径；摘要与 inventory 一致。
- `src/common/computation/runner.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/computation/tool.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/config/__init__.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/config/checkpointing.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/config/configuration.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/config/logging_config.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/config/settings.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/context.py` → 改名至 `src/common/runtime_context.py`。
- `src/common/context_guard.py` → 迁移／拆分至 `src/basic_qa/context/projection.py`、`src/data_analysis/context/projection.py`、`src/common/tool_execution/protocol.py`。
- `src/common/context_preparation.py` → 迁移／拆分至 `src/basic_qa/context/preparation.py`、`src/basic_qa/context/recovery.py`、`src/data_analysis/context/preparation.py`、`src/data_analysis/context/recovery.py`。
- `src/common/conversation_summarizer.py` → 迁移／拆分至 `src/basic_qa/context/summarization.py`、`src/data_analysis/context/summarization.py`。
- `src/common/data_governance.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/data_manifest.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/event_ledger/__init__.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/event_ledger/__main__.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/event_ledger/answer_content.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/event_ledger/contract.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/event_ledger/contract_data/event-registry.json` → 保留原路径；摘要与 inventory 一致。
- `src/common/event_ledger/contract_data/metrics.json` → 保留原路径；摘要与 inventory 一致。
- `src/common/event_ledger/contract_data/samples.json` → 保留原路径；摘要与 inventory 一致。
- `src/common/event_ledger/contract_data/states.json` → 保留原路径；摘要与 inventory 一致。
- `src/common/event_ledger/lifecycle.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/event_ledger/reconcile.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/event_ledger/service_read.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/event_ledger/store.py` → 保留原路径；相对 inventory 摘要已变化，不能推断本次引入。
- `src/common/event_ledger/worker.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/identity/__init__.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/identity/business_access.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/identity/db.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/identity/langgraph_auth.py` → 公共认证留原处；BQA 输入策略至 `src/basic_qa/input_policy.py`；入口装配至 `src/application/langgraph_auth.py`。
- `src/common/identity/mcp_outbound.py` → 保留原路径；相对 inventory 摘要已变化，不能推断本次引入。
- `src/common/identity/outbound.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/identity/remote.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/identity/runtime.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/identity/ticket.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/identity/ticket_binding.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/input_limits.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/knowledge/__init__.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/knowledge/client.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/knowledge/contracts.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/knowledge/delegation.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/knowledge/policy.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/knowledge/proxy.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/knowledge/store.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/knowledge/tool.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/knowledge/validation.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/langfuse_prompts.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/langfuse_tracing.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/mcp_authorization.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/mcp_client.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/mcp_connections.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/mcp_scoped.py` → 保留公共机制；schema 摘要归 `src/common/tool_execution/schema.py`，执行接口和策略由 `src/common/tool_execution/` 承载。
- `src/common/mcp_utils.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/middleware/__init__.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/middleware/tenant_runtime_guard_middleware.py` → 保留通用身份与 DeepAgents 守卫；改引公共材料及发送机制。
- `src/common/middleware/time_context_middleware.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/model_budget.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/model_capacity.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/model_errors.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/models.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/observability/__init__.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/observability/collector.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/observability/context.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/observability/deepagents_adapter.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/observability/external_cleanup.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/observability/middleware.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/observability/model_adapter.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/observability/pricing.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/observability/skill_adapter.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/observability/store.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/observability/tool_adapter.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/output_contract.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/output_streaming/__init__.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/output_streaming/answer.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/output_streaming/contract.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/output_streaming/controls.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/output_streaming/publisher.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/output_streaming/safety.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/permission/__init__.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/permission/correction_templates.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/permission/engine.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/permission/facts.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/permission/mcp_tools.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/permission/region.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/permission/result.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/permission/rules.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/permission/slots.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/prompts.py` → 拆分：图提示组装分别至 `src/basic_qa/prompts.py` 与 `src/data_analysis/prompts.py`；原子提示及领域规则保留本文件，单消费者残余见下文。
- `src/common/question_normalizer.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/runtime_events.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/runtime_tools.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/skill_discovery.py` → 纯材料读取辅助至 `src/common/skills/materials.py`；旧 `find_skill` 状态工具退役；残余测试导入见阻断项。
- `src/common/skill_router.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/skill_tool_scope.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/storage/__init__.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/storage/artifact_db.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/text_reduction.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/tools.py` → 保留原路径；摘要与 inventory 一致。
- `src/common/turn_tool_governance.py` → 两图各自 `context/tool_results.py`；公共 `src/common/data_governance.py`、`src/common/storage/` 提供存储和材料机制；没有 `results.py`。

## 2026-10-30 主代理修复追记（不改写上方独立审计结论）

旧管理测试引用已迁为当前 SDK read_file，不恢复 find_skill。旧 matched 阶段不再适用；selected／loaded 成功与失败、executed、版本绑定及正文不入事件断言，已在独占恢复库＋合成远程 IAM 的 `send-pg-03` 实际通过（3 passed）。管理测试原本地登录夹具仍限制整个旧套件运行，不能声称全套 admin 已通过。

Basic QA 内未参与公开运行的非 BQA 构图分支、意图车道及旧提示组装已进一步删除；当前提示内容没有改写。旧离线题库脚本保留材料与算术断言，改验当前消息输入，旧意图字段仅存作历史元数据。原生产／测试／脚本路径扫描无漏迁；历史快照和迁移基线探针的旧路径只用于读取冻结旧实现，不作为现行转发入口。common 的可选 schema 声明已改为通用名 `mcp_disclosed_schema`。

主代理的修补和验证不等同上方评审者对最终版本再次独立签字；任务 7.4 尚未完成。
