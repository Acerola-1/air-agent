# 业务授权联合验收：Codex 最终复核

日期：2026-10-19。范围：Agent `simplify-agent-business-authorization` 与 IAM `add-agent-business-entitlements` 的当前未提交实现。用户指定采用Codex主导、AGY执行的协作模式，本轮为审查与验证，不包含补修、任务回填或Git交付。

## 最终结论

**REQUEST CHANGES：暂不通过联合验收，不建议现在归档。**

已确认旧授权存储清理和root权限配置落地，现有定向测试通过；仍缺少计划明确要求的关键回归证据。以下主要是验收和测试覆盖缺口，不是已复现的跨企业读取或撤权绕过漏洞。不得把“尚未证明安全”写成“已经证明存在漏洞”，同样不能用通过数量代替缺失场景。

## AGY结果及主导复核

- 首轮：`docs/reviews/agent-business-authorization-agy-review-20261019.md`，结论COMMENT，存在root登录证据推断过度、虚构测试文件引用、串行当并发和测试替代覆盖夸大。
- Codex读取代码及历史被删测试后发起一次聚焦补充核查；AGY追加`docs/reviews/agent-business-authorization-agy-addendum-20261019.md`，撤回上述结论并改为REQUEST CHANGES。原报告保留，**其“可以直接回填/归档”建议不作为最终结论**。
- 补充调用末尾有外部服务EOF诊断，但响应和补充报告已交付。Codex逐项核对了核心引用及缺口，结论不依赖把该调用状态视为无警告成功。
- AGY按授权范围阅读源码、规格及脱敏材料，未获准读取正式凭据或用户原文；正式数据库元数据由Codex在本机独立只读核对。

## 已独立确认的事实

Codex本地只读证据：`docs/reviews/evidence/run/codex-business-acceptance-20261019/live-metadata.json`，UTC 12:17:42。未导出密码、令牌、数据库正文或旧授权行，未执行迁移或改权。

| 项目 | 事实与证明范围 |
|---|---|
| Agent正式业务库 | 实际库名air_agent_business，迁移0022_retire_mcp_bindings |
| 旧绑定表 | public.mcp_identity_bindings不存在 |
| 备份命名表 | public中本次检查模式的backup/shadow/bak表计数0；这不是对全磁盘所有历史备份的删除证明 |
| IAM正式身份库 | 实际库名iam_identity；enterprise:mcp、mcp:binding:read/manage定义计数0 |
| root授权配置 | alpha-env、beta-env、gamma-env、root-acceptance四家企业均有active成员，角色上各配置六个目标权限 |
| root正常登录 | 本轮未重做。配置计数不等于正常登录和有效权限求交证明；不猜口令、不伪造会话 |

两仓共同契约内容相同。AGY运行日志记录：Python定向5文件40 passed（12+7+8+9+4），pytest内部统计7.69s；前端契约63 passed；IAM Vue 28 passed；IAM消费方协议45 passed；前端和admin/web类型检查通过。它没有在本轮重跑IAM数据库集成全量，不能把作者自报的99项当作AGY独立复跑。

## 需要闭合的事项

### R1：并发隔离证据退化（验收优先级高）

位置：`tests/unit_tests/test_mcp_connection_execution.py:232`。`a, b = await call(A), await call(B)`是顺序调用，且delay_ms=0，不能满足tasks 3.3的HTTP屏障并发要求。对照HEAD被删`test_mcp_scoped_authorization.py::test_barrier_concurrency_ab_isolated`，原用例至少用gather构造交错。

最小补验：独立合成HTTP服务上用明确屏障控制A/B同时处于请求阶段，A延迟、B先完成，核对实际认证主体、请求计数、结果、发现缓存及连接修订互不污染。不要求恢复旧成员授权模型。

### R2：握手后与重试前撤权测试缺失（验收优先级高）

位置：`tests/unit_tests/test_mcp_connection_execution.py`及`src/common/identity/mcp_outbound.py:25`。九项新执行测试没有“initialize成功后撤权，tools/call前阻断”的动态场景，也没有失败与重试之间撤权的场景。HEAD被删`test_mcp_outbound_send_guard.py`和`test_mcp_scoped_authorization.py::test_retry_after_revocation_not_sent`原有对应保护。

最小补验：按新业务授权模型补回两种场景，有合法正对照，观察真实HTTP发送/服务端计数增量为0；覆盖SDK发送认证路径，不能只断言辅助函数抛错。旧测试使用旧表不代表这些安全场景可以随表一起删除。

### R3：真实平台恢复尚未闭环（验收优先级中）

位置：Agent `openspec/changes/simplify-agent-business-authorization/tasks.md:18`已勾选；作者报告`docs/reviews/agent-business-authorization-acceptance-20261019.md:102`承认未构造真实resume。

不能以“业务图没有人工审批节点”为由直接豁免。最小补验是在自有隔离官方LangGraph API环境，用当前图/实现和平台支持的静态中断/checkpoint构造暂停：合法恢复成功，撤权后恢复拒绝，换业务或改选择拒绝。若实际平台不支持所需静态中断，要保留探针及错误，不能伪造成功。用户本轮没有接受此项豁免，也不要求在正式库撤权/造故障。

### R4：root正常登录仍需独立核验（未验证项）

位置：IAM `openspec/changes/add-agent-business-entitlements/tasks.md` 6.1；两份作者报告对短口令与正常登录有前后差异。

Codex确认四企业配置齐全，但本轮未使用真实口令正常登录。现有readback文件存在、脚本能生成该格式、bcrypt哈希长度固定，都不能单独证明登录过程。最小闭环是本机用受保护凭据正常登录19100、列企业、切企业、读取me，输出只含状态和精确权限。密码不发送AGY、不写证据；使用已有审计或非秘密摘要核对“未改密”主张，不能要求新建旧授权备份。

这不等于认定作者造假或root当前不能登录。只是尚不能将其记为本轮独立通过，不能直接勾IAM6.1。

## 其他事项

- 数据分析首次12项矩阵中3项整理失败、重试成功属于作者明确记录的稳定性限制；不改写为首次12/12成功，不单凭此判定授权失败。
- IAM任务清单与Agent清单的时序差异可以在证据核验后回填；目前不能直接全部勾选，也不判作已证实的IAM实现缺陷。
- Ruff format四文件漂移为低影响交付问题：0021、0022迁移和两个MCP新测试。未授权补修，本轮不格式化。
- 外部MCP服务端的数据范围隔离属于用户已接受的后续边界，不作为本次新增失败项；本轮也不把109项工具目录或单个时间工具成功说成所有业务查询已验证。

## 最小后续安排

一次聚焦补修/补验即可：补三种安全回归场景（并发、握手后撤权、重试前撤权），跑真实隔离API恢复正反例，本机闭合root正常登录，修正文档勾选及格式。产品代码只在出现可复现失败时做对应修复，不重写已确定的业务授权模型。

独立复验通过后再同步主规格、归档和按用户授权Git交付。当前保持全部实现和原证据，不自动commit/push/archive，不改正式授权和密码，不创建备份，不清理共享资源。
