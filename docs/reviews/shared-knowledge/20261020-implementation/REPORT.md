# 三图共享知识库检索工具——实施与验收记录

- 运行目录：`docs/reviews/shared-knowledge/20261020-implementation/`
- 日期：2026-10-20
- Change：`openspec/changes/add-shared-knowledge-retrieval-tool/`
- 结论：**三仓核心链路已落地并联调通过；完整 A01—A25/K01—K23 验收矩阵未全部执行，不能宣称全面验收完成。** 本报告按 PASS/FAIL/BLOCKED/NOT_RUN 分层记录，未执行项不勾选。

## 1. 基线

| 仓库 | 分支 | HEAD | 工作区 |
| --- | --- | --- | --- |
| Agent | dev | `3b6890b` | 本 change 未提交 diff（17 个修改文件 + 新增 knowledge/引用/迁移/前端） |
| IAM | dev | `c040855` | 本 change 未提交 diff（11 个修改文件 + 8 个新增文件 + V25/V26） |
| RAG | dev | `7f4cf374` | 本 change 未提交 diff（5 个修改文件 + 1 个新增测试） |

本机运行态：IAM 19100 / IAM PG 19132、Agent PG 15432、RAG 8099、LangGraph 2024；未重建任何已有容器、卷或正式知识索引。

## 2. 已实现内容

### IAM

- `V25__knowledge_user_delegations.sql`：委托表、签发函数、按摘要查询、`last_used_at` 与过期清理；令牌只存 SHA-256 摘要，TTL 1..60 秒且不超过源引用/会话绝对到期；校验源引用归属、runtime 受众、企业/成员、四项修订与至少一项实时有效 `knowledge:lib:*:read`。
- `V26__knowledge_delegation_trusted_snapshot.sql`：修复签发必须走 `load_trusted_authority_snapshot`（直接调用普通快照会把有效成员读成 `membership_disabled`）。
- `POST /v1/internal/knowledge-delegations`：新 scope `knowledge-delegation:issue`，目标受众固定 `knowledge`。
- 内省新增 `kdl_` 委托分支：仅登记 consumer（默认 `knowledge-i4`）可调用，每次重读源会话/企业/成员/修订/知识权限；四个 operation 使用同一知识权限投影。
- 运行客户端预置 scope/受众扩展为 `identity:introspect`、`runtime-ticket:issue`、`knowledge-delegation:issue` 与 `langgraph-runtime`、`knowledge`。

### RAG

- 严格解析 `iam.knowledge-delegation-introspection.v1`；委托 operation 与端点一一匹配，query token 不能读 source/PDF/record。
- `POST /api/knowledge-references/validate`：1..50 项、同 owner/auth_binding、返回 `valid/forbidden/stale/unknown`，不返回原文；>50 整体拒绝。
- source/document 文本分页（offset/limit≤8000 字符、`X-Content-Offset/Next-Offset/Total-Chars`），PDF 有界 Range/安全 `Content-Disposition`，均 `no-store`。
- 请求总预算 `X-Knowledge-Remaining-Ms`（服务端上限 20s）；超时返回 504，取消传播到 asyncpg 任务。
- 合法空结果新增 `retrieval_reason=empty_discovery`，与其它 missing 区分。
- 每个 evidence 返回 `source_version`、`location`（缺省 null）与 `source_digest`（与 Agent 引用存储共用规范化摘要）。

### Agent

- `src/common/knowledge/`：契约、IAM 委托签发、RAG 客户端、`knowledge_search` 工具、服务端依据策略、引用存储、原文代理。
- `knowledge_search` 注册进 `get_business_tools`；不受 MCP allowlist 影响；QA 纯问候也保留 schema 但不主动调用；分析图空 allowed-tools 保留知识工具；tracing lead 4→5 工具且内部专员边界不变。
- 回答依据门禁：具体法规/条款/限值须有本轮核验引用；未知 citation ID、数值/单位/年份与引用原文不一致时阻止公开；模型漏写引用标记时只追加服务端登记的 citation_id，不追加事实。
- `0023_knowledge_citations`：受保护引用表，FORCE RLS + `air_agent.*` GUC + 线程墓碑；只存短摘录与来源标识。
- `GET /knowledge/citations/{citation_id}/{kind}`（Agent 自定义 app）与前端同源 BFF `/api/knowledge/citations/...`：每次读取重新委托 source/document 权限并复核 owner/thread/快照。
- tracing：`ReportPlan.knowledge_reference_ids`、`ValidatedReport.knowledge_basis`、分版本 digest（旧报告空 basis 保持原 digest）、独立“知识依据”段；知识段不参与正文归因扫描。
- 前端：`[[cite:<id>]]` 渲染为“查看来源”按钮，`CitationViewer` 每次打开重新请求、支持加载/无权/失效/503 文案与 Esc 关闭；PDF 同源打开。

## 3. 验证结果

使用与故障定位说明见 [docs/knowledge-retrieval.md](../../../knowledge-retrieval.md)。

### 3.1 自动化

| 层次 | 命令/范围 | 结果 |
| --- | --- | --- |
| IAM 编译/打包 | `./mvnw -s <aliyun-mirror> -q -DskipTests package` | PASS |
| IAM 迁移 | 空 `iam_test` + `InternalClientIntegrationTest`（it profile） | PASS；Flyway `Successfully applied 26 migrations ... v26` |
| RAG 全量 | `PYTHONPATH=.:src uv run pytest tests -q` | 731 passed / 2 failed（既有环境问题：`LLM_BINDING_HOST` 缺 `127.0.0.1:8787`，与本次改动无关） |
| RAG 定向 | delegation/remote_http/remote_boundaries | 32 passed |
| Agent 定向 | knowledge/business_graph/bridge/tracing | 42 passed |
| Agent 全量单测 | `tests/unit_tests` | 576 passed / 49 skipped / 30 failed；失败均为既有：`SkillSemanticRouter.match` 已删除（23）、`common.tools.clear_knowledge_route_cache` 已删除（4）、checkpoint 关闭断言（1）、其余同属旧知识路由/既有基线；未删除或放宽断言 |
| 前端契约 | `npm --prefix frontend run test:contracts` | 63 passed |
| 前端类型 | `npx tsc --noEmit` | PASS（无输出） |
| 前端构建 | `npm --prefix frontend run build -- --webpack` | PASS，生成 `/api/knowledge/citations/[citationId]/[kind]` |

### 3.2 真实服务联调（G1）

使用正式本机 IAM/RAG/Agent PG（只读真实 `air` 资料，不写入共享语料）：

| 用例 | 结果 |
| --- | --- |
| 用户登录→切企业→runner 内省含执行引用 | PASS |
| Agent 客户端用自己 ref 申请 `query` 委托 | PASS（HTTP 200，单 operation，60s 内） |
| 委托调 `POST /api/query` | PASS（200，`status=retrieved`，`record_id` 存在，`workspaces=["air"]`） |
| 同源 session 申请 `source:read` 委托并读原文 | PASS（200，文本分页头存在） |
| `query` 委托读 source | PASS（403 `operation_mismatch`） |
| `record:read` 委托批量 validate | PASS（200 `valid`，含 `source_digest`） |
| 51 项 validate | PASS（403，整体拒绝） |
| Agent `knowledge_search` 真实调用 | PASS（`outcome=ok`，citation_id 已登记，结果不含委托令牌） |
| 受保护 citation API（runtime ticket） | PASS（200 原文；跨登录 session 同 citation → 410） |
| QA 图真实模型调用 | PASS（真实模型调用 `knowledge_search` 2—4 次，返回 citation_id；一次未带 markdown 标记的答案被服务端追加“依据”后通过；另一次因模型引用与原文版本不一致被 `unsupported_version` 阻止，属于门禁生效而非静默放行） |
| 真实浏览器（桌面） | PASS（登录→选择企业→会话→发送问题；页面显示 `已执行 2 个工具操作 (knowledge_search)`；模型回答中出现 4 个“📄 查看来源”按钮；点击后弹出“知识库原文”对话框并显示真实原文/分页；Esc 关闭生效）。注意：本轮为让 air-reader 通过业务受理，临时授予其 alpha-env 的 `tenant_agent_test_admin` 角色，测试后已删除并推进成员修订；membership_roles 现只剩 `air-reader` 角色，未保留测试用户或业务授权。窄屏/键盘 Tab/A→B→A 未执行。 |

### 3.3 未执行 / 阻塞

- `F-A/F-W/F-B/F-I/F-L` 合成资料、企业 A/B 与 air/water/dual/none 角色矩阵：**BLOCKED**。本轮未在正式 RAG 语料中写入合成文档，未创建隔离 RAG PG/Qdrant/Neo4j 实例；不修改共享索引。
- 三图 × BFF/原生 × 流式/非流式 12 格：**NOT_RUN**（仅验证 QA 图真实模型路径；分析无菜单、tracing 报告知识区未做真实端到端）。
- `A01—A25`、`K01—K23` 全矩阵：**NOT_RUN/BLOCKED**；`K06/K10/K13/K14/K19/K20/K23` 等需要合成资料、删除/墓碑、慢查询与浏览器证据，本轮未执行。
- 浏览器桌面/390×844/键盘、A→B→A、PDF/原文真实点击：**桌面 PASS / 其余 NOT_RUN**。桌面引用按钮、原文对话框、Esc 关闭已验证；窄屏 390×844、Tab 键盘遍历、A→B→A 未执行。
- 并发 1/5/10 性能（P95≤100ms、真实检索 P50/P95）：**NOT_RUN**。
- tracing 旧报告 digest 兼容、fallback/画布/发布重建真实回读：单测覆盖，真实报告未跑。
- `on_thread_read/create_run/custom HTTP` 原生绕过探针：**NOT_RUN**；6.5 的派生依赖闭包/50 项分批全量回放未实现。
- RAG PG 语句取消与连接回收证据：**NOT_RUN**（仅实现 20s 总预算中间件）。
- 合成资料/浏览器/性能缺口导致本轮不能宣称 A01—A25/K01—K23 全部 PASS。

## 4. 迁移与回滚

- IAM：V25/V26 已在 `iam_test` 空库与正式 `iam_identity` 应用；`iam_test` 已删除。回滚不删除委托表；停用签发 scope/consumer 配置并等待 ≤60s，使用端实时拒绝仍然有效。
- Agent：`0023_knowledge_citations` 已在正式业务库应用（新增表，不修改既有表）；downgrade 明确拒绝破坏性删除。
- RAG：无数据库迁移；回滚到上一应用版本即可，不改索引/语料。
- 本轮未 commit/push/archive，未改正式部署清单之外的凭据；仅本机 `.env.langgraph` 增加 `KNOWLEDGE_API_URL` 并把桥地址随容器网关更新为 `192.168.65.1`。

## 5. 已知限制

1. 依据门禁是确定性启发式（法规/条款/限值句式 + 数值/单位/年份/限值数比对），不是完整语义蕴含校验；未实现“有界补检索一次”，当前直接阻止并要求重试。
2. 引用存储只保存短摘录；原文读取依赖 RAG record 仍有效，来源变化返回 410。
3. 分析图/ tracing 的 knowledge 引用只做了单测与代码接线，未做真实模型+浏览器闭环。
4. 未实现 `knowledge_basis` 的 50 项分批全量回放与删除墓碑优先级；`ValidateReport` 仅校验已解析引用集合。
5. 前端 citation viewer 依赖模型输出 `[[cite:...]]`；服务端会补 3 条已登记引用，模型自行写的畸形标记不会被识别。
6. IAM consumer 列表当前为部署配置 `IAM_KNOWLEDGE_CONSUMER_CLIENTS=knowledge-i4`，不是数据库表；切换 consumer 需改受保护配置并重启。
