## ADDED Requirements

### Requirement: 固定版本与独立升级关口

系统 SHALL 以 Python 3.13、deepagents 0.7.28 及升级 change 验收的完整依赖锁运行溯源；MUST NOT 将 0.7.5 作为等价业务基线。独立研发与正式配置联调 SHALL 使用不同验收关口。

#### Scenario: 升级尚未交付
- **WHEN** 主环境仍为 0.7.5 或升级 change 缺少当前基线验收
- **THEN** MAY 在独立 0.7.28 环境开展纯函数、fixture 和 fake model 验证
- **AND** MUST 标记正式配置联调未就绪，不替换正式环境或将模拟标为真实通过

#### Scenario: 当前图清单复验
- **WHEN** 使用旧升级报告作为依赖证据
- **THEN** MUST 按实际 `langgraph.json`、HEAD、锁和正式清单复核
- **AND** MUST NOT 恢复旧六图、旧账号库或旧模型网关来满足历史描述

### Requirement: 结构化请求校验与冻结

系统 SHALL 在数据委派前生成并校验 `TracingRequest`，包含结构版本、分析标识/修订、规范目标、污染物、带时区时间窗、半径、基线窗口、分析策略版本和受理选择引用；可信身份与执行 scope MUST 来自运行上下文。

#### Scenario: 请求不明确
- **WHEN** 站点存在歧义或必要时间/目标字段无法确定
- **THEN** MUST 补问并暂停数据委派
- **AND** MUST NOT 用模型猜测扩大查询范围或发起 MCP 查询

#### Scenario: 伪造选择或授权字段
- **WHEN** 候选请求声明的选择引用、范围或上下文与可信受理记录不符
- **THEN** MUST 拒绝该候选或要求重新受理
- **AND** 不一致能力的实际外呼 MUST 为零

#### Scenario: task 参数桥接
- **WHEN** lead 调用原生 `task(description, subagent_type)`
- **THEN** 确定性子图 MUST 从受控状态取得已经校验的领域请求
- **AND** MUST NOT 将 description 内任意站点/时间或模型自填字段作为授权及冻结请求的替代

### Requirement: 四专员调度与最小工具集合

溯源 lead SHALL 通过四个角色 `station-data`、`grid-data`、`meteo-analyst`、`evidence-reviewer` 进行委派；lead MUST NOT 持有 MCP 数据工具。系统 MUST 移除 unrestricted GP，并限制所有隐式文件/执行工具。不得装配轨迹专员。

#### Scenario: 数据任务被委派
- **WHEN** 已冻结的有效分析请求需要数据
- **THEN** lead MUST 委派相应数据/分析专员
- **AND** 不得通过 GP、自由执行、任意文件访问或直接 HTTP 绕过分域

#### Scenario: reviewer 实际无工具
- **WHEN** 构建 evidence-reviewer
- **THEN** 实际模型工具 schema 与 dispatch MUST 不含数据、文件或执行工具
- **AND** 仅设置 `tools=[]` 而仍注入默认文件工具 MUST 判定失败

### Requirement: 轨迹工具固定禁用

系统 MUST 将轨迹/PSCF 能力声明为不可用；lead、专员、复合工具、重试及恢复 MUST NOT 探测或调用相关工具，MUST 填写 `trajectory_unavailable`。

#### Scenario: 用户要求后向轨迹
- **WHEN** 用户要求查看轨迹或 PSCF
- **THEN** MUST 说明当前不提供该能力，可提供现有证据允许的风场关联分析
- **AND** 三个已停用轨迹/PSCF 工具实际请求数 MUST 为零

### Requirement: CompiledSubAgent 参数结果与状态隔离

`station-data` 和 `grid-data` SHALL 使用包含 `messages` 的确定性预编译图；MUST 零模型调用。结构化回传和父结果字段更新 MUST 显式实现，原始序列不得进入 lead 上下文。

#### Scenario: 结构化返回
- **WHEN** 确定性子图执行完成
- **THEN** MUST 返回可验证的结构化摘要及所属结果字段
- **AND** MUST NOT 假定 `structured_response` 自动更新父图同名发现字段

#### Scenario: 并行专员不覆盖共享状态
- **WHEN** station-data 与 grid-data 并发且以不同顺序完成
- **THEN** MUST 各自只导出所属发现字段，保留唯一冻结请求
- **AND** MUST NOT 产生共享键冲突、重复累加、原始数据泄露或旧结果覆盖

### Requirement: 选择与复合工具依赖保持交集

当前身份许可、已受理选择、图/专员白名单与复合工具依赖 SHALL 共同限制实际执行。内部取数、缓存、重试、恢复与规则读取 MUST 通过既有作用域守卫，不得建立裸 MCP/HTTP 旁路。

#### Scenario: 复合计算依赖被排除
- **WHEN** 风场分析所需浓度或气象工具被当前选择排除
- **THEN** MUST 报告依赖冲突或受限不可用
- **AND** 被排除依赖的实际请求数 MUST 为零，不自动扩大选择

#### Scenario: 权限撤销后命中缓存
- **WHEN** 曾成功分析的身份在后续调用或缓存回读前失效
- **THEN** MUST 实时复查并失败关闭
- **AND** MUST NOT 通过缓存或子任务继续外呼/读取

### Requirement: 主子模型与摘要守卫和观测

lead、LLM 专员及摘要外呼 SHALL 执行实时身份守卫与独立角色观测。确定性专员 SHALL 保持工具观测和执行授权。每个真实子任务必须关联顶层运行，不得冒充新用户请求。

#### Scenario: 摘要期间身份失效
- **WHEN** 子代理上下文压缩需要调用摘要模型且身份已撤销或 IAM 不可用
- **THEN** MUST 阻止摘要外呼
- **AND** 不得仅凭业务模型节点已装守卫判定通过

#### Scenario: 子任务可追踪
- **WHEN** 执行多个专员或补查
- **THEN** MUST 逐个关联 child execution/observation、已有 attempt/run、实际工具与终态
- **AND** MUST NOT 为每次 task 伪造新顶层 request/submission/attempt

#### Scenario: 运行预算耗尽
- **WHEN** 达到版本化的 task/模型/补查/时间预算
- **THEN** MUST 停止追加调查并发布允许的部分结果或受限状态
- **AND** 不得无界循环、重试权限错误或把停止意图记为已取消

### Requirement: 确定性结论门禁先于全部发布

系统 SHALL 从可信证据生成 claim allowlist，校验报告计划中的类型、参数、引用、反证及限制；MUST 使用受控渲染产生 `ValidatedReport`。LLM reviewer 不能增加许可结论，任意自由文本不能绕过门禁。

#### Scenario: 引用存在但不支持结论
- **WHEN** 报告引用同步升高证据来断言具体企业或已确认区域输送
- **THEN** MUST 拒绝该 claim 并回退受限模板
- **AND** 不得仅因 ID 存在、数字可匹配或没有禁词而放行

#### Scenario: 正文与画布统一发布
- **WHEN** 聊天正文、图表说明或画布准备对用户展示
- **THEN** MUST 来源于同一已验证报告版本
- **AND** lead MUST NOT 调用任意 content 的原始 create_artifact 绕过验证

#### Scenario: 门禁失败
- **WHEN** 模型报告计划无效或审核器不可用
- **THEN** MUST 使用可信事实的确定性受限模板
- **AND** 不重试模型洗白，不回退未经验证的 AIMessage

### Requirement: 持久化回读与多轮隔离

系统 SHALL 在相同可信 scope 内复用可验证数据；报告、画布及回读 MUST 绑定分析修订、证据/策略版本与内容摘要。隔离键包含用户、企业、thread、graph、context_version 与执行 epoch，删除与授权撤销优先。

#### Scenario: 扩大调查半径
- **WHEN** 同 thread 从原半径改为 10km
- **THEN** MUST 创建新请求修订并重算依赖范围的发现
- **AND** 旧子任务晚到结果不得写入或发布新修订

#### Scenario: A 到 B 再到 A
- **WHEN** 企业/图/线程切换后旧任务返回
- **THEN** MUST 用上下文版本与执行 epoch 丢弃旧结果
- **AND** 不得因标识再次相同而展示旧响应

#### Scenario: 历史报告不可验证或已删除
- **WHEN** 回读/恢复时内容摘要、策略版本、身份或墓碑校验失败
- **THEN** MUST 重验或拒绝展示，并保持当前权限与删除事实
- **AND** 不得回退原始消息、重新创建已删除报告或发布旧画布

### Requirement: 进度与现有契约保持一致

系统 SHALL 使用现有进度/工具事件展示调查过程；用户可见最终正文 MUST 仅从已验证报告通过现有 `final_output_delta` / `final_output_done` 发出。不新增公共事件或修改其他图。

#### Scenario: 等待专员
- **WHEN** 专员正在取数或分析
- **THEN** 用户 MUST 可见真实进度
- **AND** MUST NOT 提前看到未验证草稿、原始工具正文或 reviewer 输出
