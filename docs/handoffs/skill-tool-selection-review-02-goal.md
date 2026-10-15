# 匿名运行05：Change4第二轮定向修复目标

直接在支持目标持续执行的工具中启动以下目标；若工具没有`/goal`语法，把相同正文作为持续任务。不要创建新Change，不改IAM/知识库，不commit/push/合并/归档。

```text
/goal 完成 openspec/changes/add-skill-tool-selection 的第二轮修复，并以独立可复验的正反例证明通过。先完整读取 docs/reviews/flash-blind-05-repair-01-review.md、其中F01–F07及 docs/reviews/evidence/flash-05-repair-01-independent/；保留首次与首修证据、评分和用户时长，不修改协调者探针来使其变绿。当前仍未通过验收。

用户实测：首交131分钟、首轮修复83分钟，累计214分钟。报告中的约45分钟不得代替。记录本轮实际起止和外部等待，Token无法计量写未采集。

先冻结当前HEAD/diff/作者文件指纹，确认只使用自有aa-sel05-pg和aa-sel05-redis。不得重置共享库/角色/卷，不启动默认共享栈来替代专用环境。

按以下顺序逐项闭合：

1. F01：受理必须验证当前可信目录、租户/用户/成员/修订、材料和精确server+tool；不是校验catalog字符串格式。不能硬编码v1/opaque/expired测试值。目录为空、导入错误、发现失败应失败关闭；使用协议注册表判定可用，不能只拒绝unverified。不得用其他服务同名短工具通过授权存在性检查。接通BFF menu_name，菜单冲突在任何模型执行前422。复验：合法正例；合法格式伪造catalog；跨scope/过期catalog；未知工具；仅另一服务有同名工具；未知协议；发现异常；菜单冲突。网络零调用负例须有同路径成功正例。

2. F02：v2幂等比较必须使用请求实际规范选择，区分旧客户端省略与明确Auto。覆盖显式→Auto、Auto→显式、显式→另一选择、空MCP→Auto、顺序不同但语义相同，以及并发一个启动权。不同选择409，不能悄悄返回旧选择。保留旧v1重放/历史not_collected和现已恢复的正常native/BFF路径。

3. F03：稳定ID→合法唯一代理名→准确dispatch，处理service-a/service_a和原工具名碰撞；不得因另一租户发现过工具而向当前scope披露。三张生产DeepAgents图编译后发现工具，须在真实SDK模型披露与实际ToolNode/子任务调度都可用。现有中间件只filter固定空列表不算动态接线。主任务与实际子Agent都以受控模型产生T1/T2调用，独立HTTP MCP计数证明T1成功、T2为0；不得自建测试wrapper冒充生产路径。

4. F04：按当前安装SDK精确实现文件后端签名，包含glob(pattern,path=None)/aglob。证明S1同步/异步glob与真实文件工具成功，S2 read/download/search/list及路径逃逸按显式范围受限。三张SDK图均须有实际子任务证据，不能只测basic-qa后端对象。

5. F05：acceptedSelection必须来自服务端权威回读，selection_id/digest/版本/内容一一对应，不得从当前草稿拼接、用run_id充当selection_id或填空digest。测试发送S1后延迟确认、草稿变S2，当前运行仍显示服务端S1；下一轮草稿不变当前计划。验证retry/regenerate继承、刷新/历史回读、受理失败、user/tenant/graph/ctx_version/epoch、A→B→A迟到。现有企业ID切换清理已修好，要保留。

6. F06：可信选择ID/digest/submission贯穿实际Skill loaded/executed事实；缓存必须保留manual来源，executed不得重标loaded-rules。实际MCP调用/观测关联同一受理。真实落库回读证明；仅扩展函数参数或测试直接传值不算调用链完成。selected但未loaded/取消不计使用和成本。

7. F07：把所有未实测的任务和A项恢复为未完成，逐条对应证据。报告数字从本轮原始数据生成；真实错误数/总数/错误率都计算，不能写常量0、不能只统计成功。区分helper、ASGI+PG受理、native启动保护、模型外部等待与整图运行性能；P50/P95至少35样本×2轮，保留原始样本与故障身份。修新迁移9项Ruff错误。不要改写原始报告掩盖本轮不一致，另写新报告澄清。

已有有效修复不回退：新请求v2正常领取、默认前端JSON与上下文头、目录真实渲染、空库迁移、S2下载/搜索阻断、普通同名代理、submission关联与恢复继承、企业切换清草稿、分页与部分超时收集。

最后按原acceptance.md实测，特别是A03/A07/A12/A14/A16/A18/A19/A21：官方LangGraph API六图native/BFF×stream/wait的24格、各图显式成功及真实interrupt→resume、备份恢复重施墓碑、浏览器390×844/桌面/键盘/IME。受控模型/MCP可证明边界；真实配置模型和Langfuse另给证据，不擅自换模型或用GraphLoad/HTTP200替代实际回答。缺外部条件时精确标记格子，不能声称全部完成。

运行受影响测试、迁移/RLS、前端契约、干净构建/tsc、Ruff与Change strict，并逐项对照原失败身份。当前独立复验：核心28过；管理台账+选择44过/1既有collection_disabled失败；前端46过；根361过/75跳过/6同环境既有失败。不要删除、弱化或全局跳过失败。

交付 docs/reviews/skill-tool-selection-repair-02.md，原始输出另放 docs/reviews/evidence/flash-05-repair-02/，保留安全ID与实测类别、输入/预期/实际/证据映射、资源所有权、起始和最终指纹、剩余项。保留未提交diff，等待协调者独立验收。不替自己预填评分或猜模型身份。
```
