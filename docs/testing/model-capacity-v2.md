# 模型与产品容量测试 v2

本轮实现入口为 `scripts.product_chat_perf.capacity`，计划版本 `model-capacity-plan.v2`，输出版本 `model-capacity-perf.v2`。先阅读本文件与 `basic-qa-performance-regression.md`。旧 `run_baseline` 仅执行 E/S；旧 40 题、并发 1 的报告可分析单题等待，不能回答并发容量。

## 当前产品接入状态（2026-10-25 撤回后）

用户已撤回本轮正文输出修改。三图、前端和浏览器事件契约恢复到此前的正文流式行为，当前生产端不提供 `public_unit_version=1` 的新增公开单元。本文的首有效正文/首结果判据仍是测试目标；采集器只保留独立兼容解析和受控校准能力。现有产品请求按 legacy 来源单列，首核验结果缺失记未采集，不能将最终正文或工具状态补作首结果。完整 E/F 的新判据集成尚未完成，U 层容量及调度工具可独立使用。禁止通过性能任务重新启用已撤回的正文实现。

## 分开回答两个容量问题

- U：直接经过本次获准模型网关和账号，记录每个物理调用的实际模型标识、正文/推理/工具参数首片、usage、429 和在途数。工具模式只验证调用参数，不执行业务工具。结论仅适用于这个网关、账号、时段和冻结负载。
- P：真实平台与身份/持久化链路，模型及业务工具为受控依赖，用来定位平台承载瓶颈。
- E：真实产品链路与有效知识/MCP 依赖，独立用户问题的并发。一个问题可能产生多个物理模型调用，所以 E 的并发数不能代替 U。

S 是冒烟/探索，R 是小样本回归，F 才是容量验收。CLI 能执行显式 U/P/E 阶段，不代表自动完成现行规范的全部 F 场景。当前产品适配器只覆盖固定 Basic QA 题库；其他图 profile 必须新增适配和证据，不能只改标签。

## 先冻结计划，再发请求

计划至少记录 run_id、目标、保留策略、总时间/请求/Token 上限以及各阶段的模型、负载、并发或到达率、持续时长、样本下限、截止时间、SLO 和质量目标。模型密钥和会话只用环境变量名称引用，计划不内嵌值。

离线校验不发网络请求：

```bash
.venv/bin/python -m scripts.product_chat_perf.capacity validate --plan <本次计划.json>
.venv/bin/python -m scripts.product_chat_perf.capacity --help
```

真实执行必须同时具备计划开关和进程开关；以下命令仅在已有本轮预算、目标及自有环境后执行：

```bash
.venv/bin/python -m scripts.product_chat_perf.capacity run \
  --plan <本次计划.json> --out docs/reviews/basic-qa-performance/<新run_id> \
  --evidence .local/<新run_id>-private --enable-real-models --enable-load
```

`--runner run_baseline` 和 `--runner load` 用于能力校验；不能拿 S 入口执行 F。运行输出目录与受控证据目录必须不存在，禁止覆盖旧报告。`examples/capacity-hy3.json`、`examples/capacity-deepseek.json` 是关闭真实调用的 **U 探索示例**，不是本轮已批准预算或可发布容量结论的 F 计划。

F 仍按主规范执行：闭环从 1/2/5/10/20/30 等档探索；候选同一档重复至少三轮，每轮至少 10 分钟且每类至少 200 个发送样本；开放到达各档至少 15 分钟并重复；参考到达率至少 60 分钟长稳；独立突发、恢复、故障与隔离场景。不能把一次有限批量发送后等待当成持续加压。计划校验只是必要条件，独立复核还须核对这些实际覆盖和资源/队列趋势。

当前没有冻结本轮两模型的费用上限、实际配额、每类首正文/完成 SLO 和可用完整 E 依赖，故尚未生成“已批准 F 执行计划”，也没有执行真实容量测试。

## 调度、预算与未知结果

闭环保持真实在途，完成一条就补一条；时长和样本同时达到才停止新发。开放到达按独立时钟计划，到连接上限时记录 not_sent，不静默降低到达率。记录 planned/sent/ended、调度延迟、计量窗口和排空窗口；吞吐只数计量窗口内完成。60 秒错误窗按同一协调器的实际结束时刻统计，不能把单请求 elapsed 当结束时刻。

预算在发送前原子预留；缺 usage 保留预留值，不能记为零。U 的输入字节上界与最大输出共同受预留约束。E 还要求本任务自有物理模型计量 relay，避免把一个逻辑问题当一次模型消耗。relay 只监听回环，启动方式：

```bash
.venv/bin/python -m scripts.product_chat_perf.meter --config <受控计量配置.json>
```

配置包含 run_id、upstream_base_url、allowed_models、client_key_env、upstream_key_env、port、max_output_tokens、request_deadline_seconds 和 budgets。须把隔离平台的模型配置明确指向 relay；预算快照的 run_id/configuration_digest 与计划一致，物理计量器的时间/请求/Token 上限不得大于运行计划。E 使用独立计划，各阶段共用同一持续计量器，不与 U/P 混算或逐阶段重置预算。当前 relay 的计时包含转发开销，不混入无 relay 的旧系列。

到限停止新发并排空；SDK 已有重试必须逐物理调用计数。客户端超时不等于平台取消，不确定受理结果沿原幂等键核对，不盲目重发。当前 capacity 入口没有完整未知运行主动停止与故障编排，须配合产品支持入口逐项校准并补证，相关 OpenSpec 任务保持未完成。

## 产品前置与质量

产品目标声明本轮所有权、实际 BFF/admin 地址、测试企业/context_version、观测容器及库。正式端口/库名仍被 guard 拒绝，所有权声明不代替实际实例核验。E 在计划缺知识/MCP/身份正向探针引用、实际结果 oracle 或物理模型预算计量器时发送前拒绝；执行前还须核对引用确属本轮有效实例，不能手填 PASS 冒充联通。

每个并发样本各自建立完整 Q4 历史；必要历史未完整结束或未建立答案关联则不进入测量。历史的模型成本单列。当前只实现 `generated`；`fixed_replay` 没有获准历史导入适配，明确拒绝执行，不伪造助手历史。

质量规则版本 `source-aware-v2`：fixture 只匹配冻结合成值，real 必须提供同 case 的实际授权结果 oracle。Q4 可从可信历史回答，不机械要求新查工具；日期/因子/数值仍须正确。BQA-036 的合成峰值时刻不能套给真实结果。技术流成功、业务有效、质量规则通过分别汇总；规则匹配不是专家质量复核，完整率不代替正确率。实际 oracle 缺失则 BLOCKED，保留原评分和历史报告。

## 用户正文指标

`public_unit_version=1` / `meaningful-body-v1` 流只认经服务端检查的公开单元：explanation、finding、conclusion。首有效正文允许有用口径解释；首核验结果只认 finding。通用“正在查询”、问题复述、工具 JSON、心跳、推理和专员草稿均不计。完整结论不自动等同已核验事实。

发送器保存 `client_first_answer_ms`、`client_first_verified_result_ms`、`client_complete_ms`、`max_answer_gap_ms`、单元时序和判据来源。旧无语义元数据的流保留 legacy 口径，不能与新口径合并分位数。中止/缺 finding 是未观察到，不能用首正文时间代填首结果。最长观察静默包含最后有效单元至结束的尾部；同时独立记录 `max_inter_unit_gap_ms` 和 `answer_tail_silence_ms`，避免把相邻间隔和收尾等待混为一谈。中止标 right_censored。

接口到达时间与浏览器可见时间独立。浏览器在真实 DOM 布局、两帧绘制及当前可见窗口检查后计时；CLI 的 browser.jsonl 为空表示未采集，不是 0ms。

## 报告与可复算性

公开目录：manifest、requests/calls/resources/checks/browser.jsonl、逐阶段 arrivals、stage-measurements.json、summary.json 和 report.md；中断保留 requests.partial.jsonl。受控目录只存输入计划和必要答案，限制权限且不提交。

报告分别列层次、模型、图范围、类别、计量/历史/预热、发送/未发、技术完成/业务有效/质量合格、P50/P95、首结果/静默、物理模型调用与未知 usage、资源定义和缺项。容器资源当前仅有受控容器采样；未实现宿主/连接池/队列全集，必须标未采集，不能抄默认限额。不同轮次的 P95 不取平均。

`highest_passed_closed_concurrency` 是观察到通过的档位，`highest_confirmed_closed_concurrency` 才是三轮同档确认。未完成 F 覆盖时为 EXPLORATORY/PARTIAL；全部配置档通过仍只代表下界。程序给出的状态需独立核对现行规范全部必测段，不据自动摘要宣称绝对最大容量。
