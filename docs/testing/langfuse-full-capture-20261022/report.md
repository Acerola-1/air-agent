# Langfuse 全量内容采集验收

2026-10-22，用户明确撤销此前默认不上传正文的限制，要求在 Langfuse 查看全量数据。本次按这一新需求实施，不要求再次批准。分支 `dev`，HEAD `b6a885d5d18c6515d3aa7256b5cf6912a58f6f94`；保留原有未提交改动，未 commit/push、归档或升级依赖。

## 变更

移除 `privacy_mask` 与 `privacy_otel_mask` 两层正文过滤，保留 SDK 可见的完整图/节点状态、系统提示与对话、模型响应、工具定义及入参/结果和元数据。取消原异常被替换为固定文本及带异常事件跨度被丢弃的行为。

SDK 单例提前由提示词查询创建时，同时清除 client、共享 resources 和实际 exporter 上的旧 mask，并恢复此前保存的正常跨度筛选器。回归覆盖了旧 mask 残留场景，而非仅验证全新 SDK。

保留上一阶段的 attempt trace 固定、可信用户/线程绑定、父子关联和平台回调去重。普通业务台账、权限查询和本地日志继续使用原字段规则；本次完整采集范围是配置的 Langfuse 项目。SDK 没有获得的供应商字段不会凭空生成，也不恢复历史已丢弃内容。

当前主规格 `agent-observability-access`、`agent-capability-observability`、活动 change 的设计/规格及 README、环境模板已同步新需求。旧验收报告保持原样，只代表当时策略。原“正文必须为空”的测试由真实 SDK 完整内容、实际异常及单例恢复的回归替代。

## 检查

[定向回归](pytest.txt)：162 passed，23.84 秒。覆盖完整 DeepAgents 四专员同步/流式、图/模型/工具内容、真实 ChatOpenAI 协议的受控 HTTP 响应、长文本、原始异常、原生 OTel 异常事件、跨 attempt 隔离、回调去重、导入零网络及既有观测回归。Ruff、格式检查、diff 空白检查通过。任务新增代码和证据中未发现当前配置的真实秘密值。

[活动 change strict](openspec-change.json)、[访问主规格 strict](openspec-access.json)、[能力主规格 strict](openspec-capability.json)均通过；这些只证明文档结构。

## 真实 Langfuse 入库和全文回读

使用实际本机 Langfuse，环境 `full-capture-validation-20261022`。模型与业务数据均为固定替身；业务数据库连接被探针禁止，没有调用真实模型、MCP、IAM 或知识库，也没有向业务数据库造数。

- [最终探针](live-v2/live-probe.json)：同步/流式各 65 跨度、8 模型调用、4 task 和冻结工具；另有一个实际 ChatOpenAI 类 + 受控 HTTP + 工具/异常样例。
- [链路入库核验](live-v2/tree-verification.json)：三个独立 trace 各一个应用根，子节点缺父为 0。
- [官方完整内容接口回读](live-v2/full-payload-readback.json)：两轮溯源图根输入为 145 字符，输出为 51,542 字符；全部 8 个模型、4 个 task 和冻结工具均有实际输入输出。
- 同接口确认 ChatOpenAI 的系统提示与完整用户输入存在；8,228 字符模型回答、工具入参和工具结果均与固定源文本 SHA-256 完全一致，原始异常上下文完整保留。只将长度、摘要和校验结果写入仓库，未复制大段正文。

Langfuse `events_core` 仅存 200 字符预览，直接对该字段进行长文本末尾/哈希验证会失败；这不是完整内容被截断。初次预览校验结果保留在 `live-v2/ingestion-verification.json`，最终全文验收使用 SDK 官方 `observations.get_many(fields=core,basic,io,metadata)`，不以预览冒充全文。

首轮独立进程探针的附加样例复用了先前的 ambient trace；原记录保留在 `live-probe.json`。最终样例显式使用独立 trace，并在新目录 `live-v2` 重跑，未覆盖首轮证据。

## 生效与边界

正式启动预检通过，确认原生平台没有 pending/running 任务后，仅停止原生应用，并由 `deployment/run_separated_local.py start` 重新启动。数据库、Redis、Langfuse 及其他服务保持运行；启动器更新私有桥的受限来源 IP。[最终服务状态](runtime-status.txt)另存。

当前三个公开图均使用共享完整采集入口。后续运行使用新策略；此前已经被过滤的历史 input/output 无法补回。未重放用户截图原业务问题，未宣称真实供应商模型或整个 OpenSpec 守卫/撤销验收完成。Token 成本不作估算。

重载镜像的[采集状态核对](runtime-capture-status.txt)确认：`basic-qa`、`data-analysis` 的回调为 `RunCallbackHandler`，正文 mask 与 OTel mask 均已关闭；`intelligent-tracing` 保持执行期工厂，schema 读取返回编译图。此项为重载镜像内的独立导入检查，不冒充一次正式用户业务请求。
