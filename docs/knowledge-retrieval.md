# 共享知识库检索工具使用与故障定位

`knowledge_search` 是三张公开图（`basic-qa`、`data-analysis`、`intelligent-tracing`）主 Agent 默认可见的受管本地 HTTP 工具。它不受 MCP allowlist 影响；关闭 MCP 不会移除它。

## 运行链路

```
模型调用 knowledge_search
  → 可信运行身份（verification_ref + 四项修订）
  → IAM POST /v1/internal/knowledge-delegations（scope knowledge-delegation:issue，audience knowledge）
  → 60s 单操作委托
  → RAG POST /api/query（Bearer 委托，route=pg，workspace=all/缩小集合）
  → Agent 引用登记（knowledge_citations，RLS）
  → ToolMessage（schema agent.knowledge-search.v1）
```

原文读取：

```
浏览器 GET /api/knowledge/citations/{id}/text|pdf（Next 同源 BFF）
  → 运行票据 → Agent /knowledge/citations/{id}/{kind}
  → 引用表 + 线程归属/会话复核
  → 新的 source:read/document:read 委托
  → RAG /api/source 或 /api/document（分页/Range，no-store）
```

模型可读片段与持久化摘录分别限界：模型单片段最多 2000 字符、内容合计最多 12000 字符；引用最多 8 个，持久化短摘录最多 500 字符。前端文本每页最多 8000 字符。若片段正文超过上限，不能将候选结果说成完整资料。

## 配置

Agent：

- `KNOWLEDGE_API_URL`：部署固定的 Knowledge API 基址。容器内使用私有桥 `http://192.168.65.1:19110/rag`（桥地址按实际容器网关调整）。
- `KNOWLEDGE_DEADLINE_SECONDS`：总预算，默认 20，允许 1..20。
- `IAM_BASE_URL` / `IAM_INTERNAL_CLIENT_ID` / `IAM_INTERNAL_CLIENT_SECRET`：签发委托。

IAM：

- `IAM_KNOWLEDGE_DELEGATION_TTL_SECONDS`：默认 60，最大 60。
- `IAM_KNOWLEDGE_CONSUMER_CLIENTS`：允许消费委托内省的内部客户端，默认 `knowledge-i4`。
- Agent 内部客户端需要 scope `knowledge-delegation:issue` 与受众 `knowledge`；消费端需要 `identity:introspect` 与受众 `knowledge`。

RAG：

- 现有 `ENV_RAG_IAM_*` 配置；委托消费使用同一 introspection client。
- 不需要新数据库迁移。

## 状态与排查

| 现象 | 含义 | 处理 |
| --- | --- | --- |
| `outcome=permission_denied` | 企业未开通、无知识库读权限或显式库无权 | 查 IAM 权限与 RAG mapping；不要改用共享账号 |
| `outcome=no_match` + `retrieval_reason=empty_discovery` | 合法空结果 | 明确告知未找到依据 |
| `outcome=evidence_unusable` + `retrieval_status=ambiguous/partial/stale` | 上游未给出可用证据 | 不得据此给确定法规结论；缩小检索范围 |
| `outcome=context_changed` | 会话/企业/修订已变化 | 重新登录/重新发起；不重放旧委托 |
| `outcome=unavailable` | IAM/RAG 不可用或配置缺失 | 检查 IAM 19100、RAG 8099、私有桥 19110 |
| `outcome=timeout` | 20s 总预算耗尽 | 检查 RAG 慢查询；不要自动重发 |
| `citation_store_unavailable` | 引用表缺失/运行角色无权限/旧进程未加载新代码 | 执行 `0023_knowledge_citations`；重启 Agent 进程 |
| 回答被替换为“未取得可核验依据” | 服务端依据门禁阻止未核验具体条款/限值 | 让模型实际检索并引用 `[[cite:<id>]]`；或换更精确问题 |
| 点击查看来源 403/410/503 | 当前会话无权/来源变化/服务故障 | 页面会显示对应中文；失效时重新检索 |
| 返回资料但模型说没有范围正文 | 可能是资料没有命中，也可能是适配层截断 | 比较受保护检索片段与模型上下文，不要直接重建索引；已修复仅传前 500 字符的问题 |
| 新登录打开旧会话引用返回 410 | 引用绑定原源会话，新登录不等于原会话 | 在有业务权限的当前会话重新检索；不要放松 owner 校验 |
| PDF 403，但文本能读 | 检查原文种类与委托 operation 是否一致 | text 要求 source:read，PDF 要求 document:read；query 委托不能读原文 |

具体法规/条款/限值答复需要当前轮实际检索及可核验引用；无法证明模型自由改写受原文支持时，服务端退回原文引述或明确拒答。引用不能单凭“verified=true”授权任何数值、单位、年份或适用范围。知识资料中的归因描述不能当成本次污染证据。

旧会话读回与运行采用持久化引用依赖实时复核，最多每批 50 条、全部批次均通过才能继续。当前实现偏保守：线程已有失效知识依赖时可关闭整条线程；这不能替代完整的派生摘要、原生回放及删除恢复验收。

知识检索在观测中使用独立服务键 `knowledge-api`，按业务结果区分有效成功、无命中、无权、超时等；它不是 MCP 连接。未证实发生 HTTP 请求时，网络尝试记未知，不补成 0。完整子请求次数与费用对账仍待实际验收。

## 可复跑检查

```bash
# Agent 定向单测
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false .venv/bin/python -m pytest tests/unit_tests/test_knowledge_contracts.py tests/unit_tests/test_knowledge_acceptance.py -q

# RAG 委托/远程鉴权
cd ../RAG/env-rag-final && PYTHONPATH=.:src uv run pytest tests/test_knowledge_delegation.py tests/test_remote_http.py -q

# IAM：先建立本轮自有空 PG/Redis，再将测试数据源明确指向它们。
# 不使用正式 .env.local 的数据源运行 create/drop 测试库脚本。
# 数据源与 Redis 通过本轮独立环境变量注入后，在 IAM/server 执行：
./mvnw test -Dtest=InternalClientIntegrationTest,KnowledgeDelegationServiceTest -Dspring.profiles.active=it
```

真实链路检查需要有效用户会话、Agent 业务使用权限、执行引用和具体资料库读权限；不要使用共享机器账号代替用户。若页面显示“无可用业务”，不能把历史阅读或直接 RAG 接口成功当作新 Agent 回答成功。

冻结验收矩阵见 [acceptance.md](../openspec/changes/add-shared-knowledge-retrieval-tool/acceptance.md)。本次独立发现、补修、真实证据和未验收项见 [独立验收报告](reviews/shared-knowledge/20261020-independent-acceptance/REPORT.md)；[原实施报告](reviews/shared-knowledge/20261020-implementation/REPORT.md) 保留供溯源。

截至本轮交付，聊天前端已加载最终构建；IAM/RAG/原生 Agent 的补修源码尚未正式切换运行进程，不能仅凭健康检查视为已上线。RAG 新增 RFC8785 锁依赖，正式启用需同步其锁环境。三图 12 格、完整撤权/删除/原生回放与性能仍未验收，不应归档 change。

当前成果可以保存为本地 `dev` 阶段提交，继续实施 Change 6/7；Git 提交不等于完整验收或正式切换。用户授权及本次额外提交检查见 [阶段提交记录](reviews/shared-knowledge/20261020-dev-commit/REPORT.md)。
