# Agent 业务授权联合变更 AGY 独立验收补充复核报告（Addendum）

- **审查者**：AGY（独立第二意见评审者）
- **复核者**：Codex（主导复核）
- **日期**：2026-10-19
- **关联主报告**：[`docs/reviews/agent-business-authorization-agy-review-20261019.md`](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/agent-business-authorization-agy-review-20261019.md)
- **原始证据目录**：`docs/reviews/evidence/run/agy-business-acceptance-20261019/`（保持只读引用，未覆盖）

---

## 1. 修订后评审结论（Revised Verdict）

**结论更正：`REQUEST CHANGES`（变更请求 / 需补齐关键证据与测试缺口后再交付）**

在 Codex 定向复核提示下，结合对代码实现细节与测试用例的二次深入审查，本评审者更正首轮偏乐观的 `COMMENT` 结论。由于当前存在未独立证实的关键验收项、明确的代码覆盖退化以及未获用户豁免的规格缺口，**不能先验推定系统无阻断**，必须保持保守的质量门禁标准：

1. **root 登录及有效权限读回在外部评审视角为“未独立证实”**：AGY 无法证明 `formal-root-readback.json` 必由脚本真实正常登录产生，且 BCrypt 哈希无法推导明文口令长度或前后未篡改事实；
2. **MCP 执行测试存在 3 处实质性覆盖退化与断层**：新增的 `test_mcp_connection_execution.py` 虽有 9 项测试，但 A/B 隔离退化为串行 `await`，且完全遗漏了旧测试中最核心的“initialize 握手后撤业务权限、tools/call 之前网络发送严格为 0”以及“重试前撤权拦截”的测试；
3. **真实 LangGraph 恢复（resume）未在平台运行态物理闭环**：用户明确未给予豁免，平台具备静态中断与恢复能力，未验收即构成规格/验收缺口；
4. **引用勘误与事实更正**：废止首轮报告中对不存在的文件 `test_mcp_outbound.py` 的虚构引用，纠正测试用例实际计数（40 项）与执行耗时区分。

---

## 2. 原报告结论与陈述的明确撤回（Retractions & Corrections）

### 2.1 撤回关于 root 口令与正常登录的独立证实断言
- **撤回内容**：撤回首轮报告中“证据真实生成、代码逻辑完全吻合、实际密码>=8且摘要未改动”以及“建议 Codex 直接勾选 IAM 6.1”的陈述。
- **更正认定**：**未独立证实（Unverified by AGY）**。
- **事实与依据**：
  1. AGY 仅能观察到文件的存在性、时间戳与作者脚本源码，无法直接证实该文件系由 Python 脚本通过真实网络调用 19100 完成 `/v1/auth/login` 产生；
  2. 首轮报告建议通过 SQL 检查 BCrypt 哈希长度（60 字符）与前缀（`$2b$10$`）属于严重逻辑缺陷——BCrypt 哈希长度与前缀对任意长度的口令（无论是 4 字符还是 20 字符）均完全恒定，根本不能推导明文口令长度，也无法在没有基线比对快照的情况下单凭单次查询证明密码未被修改；
  3. IAM 作者在 `agent-business-entitlements-delivery-20261019.md` 明确记录了 4 字符口令被 8..256 策略拦截的事实。在 Codex 提供本地独立交叉证据之前，AGY 判定该项处于未决状态，**禁止建议在未经证实前勾选 IAM 任务 6.1**。

### 2.2 撤回对虚构测试文件 `test_mcp_outbound.py` 的引用
- **撤回内容**：撤回主报告“规格覆盖矩阵”第四行证据栏引用的 `test_mcp_outbound.py`。
- **更正认定**：当前代码库 `tests/` 目录下**根本不存在** `test_mcp_outbound.py` 文件（仅存在源码模块 `src/common/identity/mcp_outbound.py`）。该引用属于误写/虚构引用，现予以完全撤销。

### 2.3 撤回“新测试已实质完全接替旧测试”的概括性结论
- **撤回内容**：撤回首轮报告中“新测试（32/40项）与前端 63 项契约测试已实质完全接替旧安全与隔离覆盖”的结论。
- **更正认定**：新测试虽然在数据结构与静态策略上覆盖了业务权限求交，但在**执行时屏障并发**与**长连接握手后的出站动态拦截**两个核心安全防御点上存在明显的用例缺失与断层（详见第 3 节）。

### 2.4 纠正测试计数与耗时口径
- **撤回内容**：纠正主报告中“32 项新测试”的不准确陈述与耗时混淆。
- **精确事实**（依据 [`python-unit-tests.txt`](file:///Users/acerola/Dev/Python/air_agent/docs/reviews/evidence/run/agy-business-acceptance-20261019/python-unit-tests.txt)）：
  - 实际运行的 5 个测试文件共收集并执行 **40 项测试**：
    - [`test_business_access.py`](file:///Users/acerola/Dev/Python/air_agent/tests/unit_tests/test_business_access.py)：**12 项**（含参数化用例）
    - [`test_business_registry.py`](file:///Users/acerola/Dev/Python/air_agent/tests/unit_tests/test_business_registry.py)：**7 项**
    - [`test_mcp_connections.py`](file:///Users/acerola/Dev/Python/air_agent/tests/unit_tests/test_mcp_connections.py)：**8 项**
    - [`test_mcp_connection_execution.py`](file:///Users/acerola/Dev/Python/air_agent/tests/unit_tests/test_mcp_connection_execution.py)：**9 项**
    - [`test_deep_explicit_skill.py`](file:///Users/acerola/Dev/Python/air_agent/tests/unit_tests/test_deep_explicit_skill.py)：**4 项**
  - **耗时口径区分**：pytest 内部统计的纯用例执行时间为 **7.69s**；命令行整体进程启动、环境加载至结束退出的系统墙钟（Wall-clock time）为 **8.47s**。两者应明确区分记录。

---

## 3. 成立的覆盖缺口与技术影响（Confirmed Coverage Gaps & Impact）

### [HIGH] Gap 1: A/B 租户隔离测试退化为串行执行，丢失真正的屏障并发测试

- **文件与行号**：
  - 新测试：[`tests/unit_tests/test_mcp_connection_execution.py#L218-L237`](file:///Users/acerola/Dev/Python/air_agent/tests/unit_tests/test_mcp_connection_execution.py#L218-L237)
  - 被删旧测试：`tests/unit_tests/test_mcp_scoped_authorization.py:229-268`（`test_barrier_concurrency_ab_isolated`）
- **对比分析**：
  - 被删除的旧测试通过真实并发屏障验证：
    ```python
    # 旧测试使用 asyncio.gather 构造真实的并发屏障，A 故意延迟 300ms，验证并发下主体不互串
    a, b = await asyncio.gather(call("t-a", 300), call("t-b", 0))
    ```
  - 新增的 `test_ab_cache_and_credentials_are_isolated` 代码如下：
    ```python
    # 行 232：完全退化为串行执行！
    a, b = await call(TENANT_A), await call(TENANT_B)
    ```
- **技术影响**：
  串行执行只能证明“在单任务完全结束后，下一个租户调用能取到自己的配置”，完全无法检验在高并发或异步 IO 切换时，`_CURRENT_IDENTITY`（ContextVar）、httpx 连接池与 MCP 发现状态门闩是否存在竞态穿透或凭据串扰漏洞。

---

### [HIGH] Gap 2: 缺失“initialize 成功后撤业务权限、tools/call 之前网络发送严格为 0”的测试

- **文件与行号**：
  - 源码守卫位置：[`src/common/identity/mcp_outbound.py#L25-L60`](file:///Users/acerola/Dev/Python/air_agent/src/common/identity/mcp_outbound.py#L25-L60)（`MCPOutboundAuth.async_auth_flow`）
  - 被删旧测试：`tests/unit_tests/test_mcp_outbound_send_guard.py:20-56`
- **对比分析**：
  - 在旧架构中，`test_mcp_outbound_send_guard.py` 专门针对长连接的“阶段性撤权”进行了严密验证：
    ```python
    # 先以合法身份握手
    await client.post("http://fixture/initialize")
    await client.post("http://fixture/tools-list")
    # 随后身份在握手后被撤销
    with mcp_outbound.mcp_identity_scope(replace(identity, verification_ref="revoked")):
        with pytest.raises(McpAuthorizationError):
            await client.post("http://fixture/tools-call")
    # 核心断言：网络发送严格止步于握手阶段，tools-call 的网络发送严格为 0！
    assert sent == ["/initialize", "/tools-list"]
    ```
  - 新增的 `test_mcp_connection_execution.py`（共 9 项测试）中，**没有任何一项**测试在 `execute_mcp_tool` 建立发现/握手后动态撤销业务权限并断言下游网络增量为 0！
- **技术影响**：
  `src/common/identity/mcp_outbound.py` 虽然编写了 `async_auth_flow`，但缺乏任何自动化回归测试对其生效进行保护。一旦未来框架升级或调用层跳过 `mcp_identity_scope`，在 MCP 客户端缓存连接存活期间，已撤权主体仍可能违规向外部发起已初始化的工具外呼。

---

### [MEDIUM] Gap 3: 缺失“失败重试前撤权阻断新网络发送”的测试

- **文件与行号**：
  - 被删旧测试：`tests/unit_tests/test_mcp_scoped_authorization.py:381-404`（`test_retry_after_revocation_not_sent`）
- **对比分析**：
  - 旧测试覆盖场景：MCP 工具首发失败（如 flaky 抛错）后，主体被撤权；随后模型或框架尝试 retry 时，执行层直接拦截，断言服务端 `call_count` 不再增长；
  - 新测试 `test_mcp_connection_execution.py` 完全没有针对“重试窗口期撤权”的负向回归测试。
- **技术影响**：
  无法证明在业务请求重试机制介入时，失效的业务授权能先于底层重试逻辑完成网络拦截。

---

### [MEDIUM] Gap 4: 生产/平台原生长运行恢复（resume）未在运行环境中物理闭环

- **文件与行号**：
  - 规格要求：[`openspec/changes/simplify-agent-business-authorization/specs/agent-business-access/spec.md#L36-L39`](file:///Users/acerola/Dev/Python/air_agent/openspec/changes/simplify-agent-business-authorization/specs/agent-business-access/spec.md#L36-L39)
  - 任务清单：[`openspec/changes/simplify-agent-business-authorization/tasks.md#L18`](file:///Users/acerola/Dev/Python/air_agent/openspec/changes/simplify-agent-business-authorization/tasks.md#L18)
- **事实分析**：
  - 任务 2.6 明确要求：`覆盖resume/重新生成/旧运行缺快照处理，验证不可换业务；业务撤权后本人停止/删除仍按原安全收尾规则可用。` 并已勾选 `[x]`；
  - 作者报告以“当前三张公开图没有可用的真实中断样例”为由跳过物理长运行构造；
  - **判定定性**：这属于**明确的规格与验收缺口（Spec & Acceptance Gap）**，而非既定豁免（因为用户明确未曾授予此项豁免）。LangGraph API 具备静态 checkpoint 与 `interrupt_before` 能力，完全可以在标准图上挂起一个中断点并在运行时验证 resume 阻断，作者未在真实服务上完成该端到端验收。

---

## 4. 引用 Codex 本地只读确认的事实（非 AGY 独立直查）

根据 Codex 在本地只读连接正式数据库完成的核对（脱敏事实输入），记录如下已知平台现状：

1. **Agent 业务库（15432/air_agent_business）**：
   - `alembic_version` 已确认达到 `0022_retire_mcp_bindings`；
   - 旧表 `public.mcp_identity_bindings` 确认不存在（`to_regclass` 为空）；
   - `public` 命名空间下备份表（`%_backup%`、`%_shadow%`、`%_bak%`）确认数量为 0。
2. **IAM 身份库（19132/iam_identity）**：
   - 旧 Agent MCP 权限码 `enterprise:mcp`、`mcp:binding:read`、`mcp:binding:manage` 在权限表中确认数量为 0；
   - root（`dd5ca3c0-a454-466e-b2f7-a7fc5191867e`）在全部 4 个 active 企业中确认各配置了 6 个目标权限码。

> **声明**：上述 5 项数据库内部数据事实由 Codex 本地确认并提供；AGY 未直接持有正式库凭据，亦未直连查询。上述事实证明了“配置已入库”与“旧对象已删除”，但不等同于“真实登录与有效权限求交已验证”。

---

## 5. Codex 推进最终闭环所需的最小证据清单

为消除上述阻断性缺口，建议 Codex 在本地执行以下最小验证与补强：

### 5.1 关于 root 登录真实性与 IAM 6.1（最小证据）
1. **真实 HTTP 登录验证**：Codex 在本地运行环境（不传递明文密码至外部模型），执行 `verify-root-entitlements.py`，确认其打到 `http://127.0.0.1:19100/v1/auth/login` 的实际返回码为 200；
2. **审计日志/历史摘要一致性**：确认 `iam.users` 中 root 的 `password_hash` 在发布前后的变更记录（或确认自环境建立以来密码未被重置），排查是否曾通过临时手段更改为弱口令；
3. **完成上述核验后**，方可在 IAM `tasks.md` 回填 6.1/6.2/6.3。

### 5.2 关于 MCP 测试覆盖缺口（代码补强）
在 `tests/unit_tests/test_mcp_connection_execution.py` 中补齐被删除旧测试的核心安全断言：
1. **补回真实屏障并发**：将 `test_ab_cache_and_credentials_are_isolated` 中的串行调用改为 `asyncio.gather(call(TENANT_A), call(TENANT_B))`，并保留 A/B 不同延迟，物理验证并发下无凭据串扰；
2. **补回握手后动态撤权拦截测试**：编写一个测试，在 mock/fixture 上先完成初始化/目录握手，随后在调用具体 `query` 工具前将运行身份置为未授权状态，断言抛出 `McpAuthorizationError` 且 fixture 记录的工具调用网络计数严格为 0。

### 5.3 关于 Resume 规格缺口（验证建议）
在本地运行的 LangGraph 容器中，利用现有测试辅助手段（如在图执行前注入静态 interrupt 标记），对一个处于挂起态的 thread 执行 `runs.create(..., resume=...)`：
- 分别验证：在有效业务身份下成功恢复；在篡改 `ledger_business_code` 或剥夺业务权限后，被 `langgraph_auth.py` 严格拦截为 409 / 403。

---

## 6. 最终结论总结

AGY 撤回前一份报告中关于 root 已证实的结论与虚构测试文件引用。在 Codex 在本地完成 root 真实登录验证、补齐 MCP 握手后撤权与屏障并发测试、并对长运行 resume 缺口做出明确处置之前，**本轮独立验收的审慎结论维持为 `REQUEST CHANGES`**。
