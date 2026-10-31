# Spec Delta

## Purpose

定义智能溯源主图在 dev 业务授权和真实 MCP 数据下的四角色委派、数据执行、发布门禁和生命周期隔离行为，确保受控报告来自可追溯的真实数据而不是模型自由回答。

## ADDED Requirements

### Requirement: 四角色与最小工具集

系统 SHALL 使用 `station-data`、`grid-data`、`meteo-analyst`、`evidence-reviewer` 四个角色；前两者 MUST 为零模型确定性分析，只写各自 findings；meteo 只能使用受限气象/风场工具；reviewer 只能建议降级或指出反证，MUST NOT 成为事实源或最高证据裁判。主图 MUST NOT 持有 MCP 数据工具、文件工具或自由执行工具。

#### Scenario: 主图工具边界
- **WHEN** 构建 `intelligent-tracing`
- **THEN** 主图暴露的工具只包含冻结请求、策略摘要、报告构建和受控画布发布
- **AND** 不存在 general-purpose、文件、execute、create_artifact 或 MCP 数据工具

#### Scenario: reviewer 实际工具集
- **WHEN** 检查 reviewer 最终模型可见工具
- **THEN** 只有结构化审查输出，不存在数据、文件或执行工具
- **AND** `tools=[]` 本身不作为无工具证明

### Requirement: 冻结请求跨委派传递

原生委派参数只有任务描述和角色类型，MUST NOT 作为事实、授权或业务字段来源；确定性/隔离子图 MUST 从父状态读取已冻结的 `TracingRequest` 和业务作用域；改变站点、时间窗或半径 MUST 产生新修订并重算依赖发现。

#### Scenario: 描述中伪造站点
- **WHEN** 委派描述写入另一站点或半径，但父状态请求未变
- **THEN** 子图仍使用冻结请求，描述不改变取数范围

#### Scenario: 同线程扩大半径
- **WHEN** 用户在同一线程把调查半径从 10km 改为 20km
- **THEN** 产生新 revision，重算邻站和空间发现
- **AND** 旧 revision 的迟到结果不覆盖新结果

### Requirement: 并行数据专员互不覆盖

station-data 与 grid-data SHALL 可在同一轮并行执行，各自只回写 `station_findings` / `grid_findings`；结构化摘要与父 findings MUST 分开更新；单专员失败时 MUST 保留显式数据不可用发现和限制，不得伪造空数组或成功。

#### Scenario: 反序完成
- **WHEN** grid-data 先于 station-data 完成
- **THEN** 两个 findings 均正确写回，互不覆盖

#### Scenario: 单侧失败
- **WHEN** 网格取数失败但站点成功
- **THEN** 报告仍可生成，明确标记网格不可用
- **AND** 不把缺测当作无异常或空间格局正常

### Requirement: 真实数据经统一授权执行

真实站点目录、目标站/邻站序列、网格、历史气象和 NWP MUST 通过 dev 的 `execute_mcp_tool` / `resolve_connection` / `ScopedMCPToolFactory` 执行；MUST NOT 接受裸 HTTP、通用 executor、浏览器地址或自造凭据；站点目录 MUST 按当前租户、业务和授权范围解析，不能全局无条件拉取。

#### Scenario: 无业务快照
- **WHEN** 运行缺少可信业务码、策略版本、权限或图映射
- **THEN** 取数在发起 MCP 请求前失败关闭，网络请求数为 0

#### Scenario: 真实站点目录
- **WHEN** 用户请求按名称查询授权站点
- **THEN** 目录提供方经授权 MCP 工具解析候选，并只返回当前业务/企业范围
- **AND** 同名多站仍要求补问

#### Scenario: 连接撤权
- **WHEN** 运行中业务权限被撤销或连接被停用
- **THEN** 下一次发送失败关闭，不重试、不匿名回退

### Requirement: Tracing 专用工具白名单

`intelligent-tracing` SHALL 在通用 `approved_query_tools` 之上使用更窄的业务工具子集，明确排除 `mcp_station_analysis_trajectory`、`mcp_station_analysis_single_pscf`、`mcp_station_analysis_history_pscf`；执行层 MUST 同时拒绝轨迹/PSCF 工具，业务白名单与连接白名单的交集规则保持不变。

#### Scenario: 通用清单含轨迹工具
- **WHEN** 连接 `approved_query_tools` 包含轨迹/PSCF
- **THEN** tracing 业务仍不允许装配或调用
- **AND** 其他业务的白名单不受影响

#### Scenario: 伪造工具 ID
- **WHEN** 模型或子任务尝试调用被排除的轨迹/PSCF 工具
- **THEN** 在发起网络请求前拒绝，实际外呼数为 0

### Requirement: 发布前实时复查

报告发布前 SHALL 重新确认当前身份、业务权限、运行/尝试归属、取消事实和墓碑；墓碑存在、取消已确认、状态为失败/超时、作用域不一致或台账不可用时 MUST 抑制发布；停止意图不等于已取消，MUST NOT 用测试注入的 config 标志代替实时事实。

#### Scenario: 发布前撤销业务权限
- **WHEN** 报告已生成但发布前业务权限被撤销
- **THEN** 不发布报告，返回受控失败状态

#### Scenario: 删除后迟到报告
- **WHEN** 线程已删除，晚到的子任务生成报告
- **THEN** 墓碑检查抑制发布和画布恢复

#### Scenario: 台账不可用
- **WHEN** 运行/尝试状态查询后端不可用
- **THEN** 失败关闭，不猜测成功或继续发布

### Requirement: 统一发布、持久化与回读

聊天正文、画布、持久化和历史回读 MUST 使用同一份 `ValidatedReport`；正式运行 MUST 使用持久化报告存储，`InMemoryReportStore` 只限测试；画布 MUST 经服务端渲染并绑定报告摘要，MUST NOT 接受任意正文旁路。

#### Scenario: 重开历史
- **WHEN** 用户重新打开已完成的溯源线程
- **THEN** 显示与发布时相同的报告正文/画布摘要，或明确标记不可验证
- **AND** 不回退到未验证的最后一条 AIMessage

#### Scenario: 画布发布
- **WHEN** 用户打开画布
- **THEN** 画布使用已验证报告的服务端渲染内容
- **AND** 任意 HTML/Markdown 提交被拒绝

### Requirement: 上下文与生命周期隔离

系统 SHALL 按用户、企业、线程、图、context_version 和执行 epoch 隔离发现、报告、画布和缓存；A→B→A、旧修订迟到、停止、删除和恢复 MUST 丢弃旧结果；权限或参数错误 MUST NOT 自动重试，预算耗尽时只发布已验证部分结果和限制。

#### Scenario: A-B-A
- **WHEN** 用户从企业 A 切到 B 再切回 A
- **THEN** 旧 A 上下文的结果不覆盖新 A 会话

#### Scenario: 预算耗尽
- **WHEN** task 数、模型调用、并发或总超时达到冻结上限
- **THEN** 停止追加调查，只发布带预算限制的已验证部分报告
