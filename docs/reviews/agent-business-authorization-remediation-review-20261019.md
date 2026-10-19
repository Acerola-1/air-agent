# 业务授权补证据：超时交付复核

日期：2026-10-19。协调者Codex，执行者AGY。两仓实现及既有未提交改动保留。本报告追加记录，不覆盖首轮报告、补充更正或原始证据。

## 结论

**仍需补验，暂不归档。** AGY调用`implement-mvfa59p6-5f2a9cb1`从13:18:17Z运行至14:18:17Z，触及60分钟硬上限。返回内容为多条“已启动、正在等待”的过程说明，并非最终交付报告；诊断同时记录网络EOF及硬上限清理警告。`done`仅代表调用结束，不代表实施完成。未自动续跑，定时检查已暂停，等待用户决定后续执行方式。

## 本轮已核实的进展

- R1：A真实HTTP请求到达服务端屏障并挂起，B完成后才释放A；测试核对B→A完成顺序、两主体结果及计数，已补回实际并发覆盖。
- R2：新增握手/发现后撤权、失败后撤权再调用及恢复授权正对照，均走真实本机HTTP fixture和SDK认证路径。Codex在允许本机监听的环境复跑执行文件，**11 passed in 9.22s**。沙箱内先前11个setup错误均为`socket.bind`受限，未当作产品失败，原日志保留于临时目录。
- R3部分：官方API真实执行当前basic-qa图，在call_model前静态中断，state含next节点及checkpoint；负例出现401/403/409，合法恢复后run为success、模型调用增量1。使用合成IAM/LLM，不能称为正式IAM或真实模型验收。
- R4：Codex此前本机正常root登录、四企业切换及精确权限读回通过；Agent四企业运行列表/统计接口均200，两企业有root自有正文且读取200，另两企业无自有样本，不伪造通过。验证前后密码摘要一致、临时会话已退出；这不是历史所有发布阶段均未改密的追溯证明。

## 尚未闭合的具体事项

1. R3脚本直接INSERT请求/attempt/submission，并将attempt手工UPDATE为waiting_input，绕过真实受理及中断台账推进。实际checkpoint本身是真的，但这不能替代所要求的受理→中断→恢复完整路径。
2. R3没有篡改已受理Skill/MCP选择的恢复负例。换图负例的合成身份只拥有basic-qa权限，403只能证明目标业务准入，不能单独证明“同时有两项业务资格仍不能替换原attempt目标”。
3. R2重试测试称首次为HTTP503，但fixture实际是工具抛异常，通常由MCP以工具错误封装；未记录真实HTTP503响应。其错误断言带`or first_res is not None`，会接受任意非None成功值，应收紧为确实失败并记录实际失败层级。已有零新增工具调用断言保留。
4. 指定的最终补修报告未交付；R3脚本原在外部scratch，现已保存在本机证据目录。没有因此勾选IAM待验收任务或移动任何change。

以上是证据和覆盖缺口，不是已复现的跨租户越权漏洞。

## 证据与资源

- AGY日志及保留脚本：`docs/reviews/evidence/run/agy-business-remediation-20261019/`。
- Codex复跑：`docs/reviews/evidence/run/codex-business-completion-20261019/mcp-execution-independent-11.log`。
- root正常IAM登录：同目录`root-normal-login-125738.json`；Agent访问：`root-agent-view-133220.json`。
- 自建临时资源：aa-bizfix-langgraph-api（22024）、aa-bizfix-pg（25432）、aa-bizfix-redis（26379）。创建来源已核对，保存证据后已成功回收这三项，未删除共享容器、卷或镜像。正式15432/19132、知识库及共享LangGraph不在清理范围。
- Codex额外文档修正：为旧tenant-mcp-authorization规格标注被替代，并补充观测规格的业务/连接措辞；保留原7.6未实测事实。主规格尚未同步，归档尚未执行。两处EOF空行已移除，未改变功能。
- 无commit/push，无旧授权dump、快照或备份表创建。AGY回执提供的是会话累计Token统计，不能可靠拆成本次调用，故本次Token记为未采集。
