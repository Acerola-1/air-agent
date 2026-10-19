# Agent 业务授权与 MCP 连接

日期：2026-10-19。对应 change `simplify-agent-business-authorization` 与 IAM 配套 change
`add-agent-business-entitlements`。共同契约见
`openspec/changes/archive/2026-10-20-simplify-agent-business-authorization/business-authorization-contract.md`。

本文是 Agent 侧扩展与运维入口，不重复 IAM 登录/SSO 细节。

## 1. 稳定业务目录

| 稳定业务码 | 中文名 | IAM 使用权限 | 实际 Graph |
|---|---|---|---|
| `basic-qa` | 基础问答 | `agent:business:basic-qa:use` | `basic-qa` |
| `data-analysis` | 数据分析 | `agent:business:data-analysis:use` | `data-analysis` |
| `intelligent-tracing` | 智能溯源 | `agent:business:intelligent-tracing:use` | `intelligent-tracing` |

有效业务 = 当前企业有效开通 ∩ 当前企业内有效成员角色授权并集。平台管理员、root、
全局企业模板和用户名都不会绕过求交；没有企业上下文就没有业务使用权。

Agent 侧唯一策略源：`src/common/business_registry.py`（策略版本
`BUSINESS_POLICY_VERSION`）。实际 Graph 由平台可信映射解析，客户端声明的
`graph_id`/`assistant_id` 只作为请求目标，不是授权证据。

## 2. 准入点与失败语义

同一套规则覆盖：

- 能力目录（原生 `/capabilities` 与 BFF 代理）；
- 台账受理（`POST /agent/requests`）；
- 原生 `create_run`（stream/wait、resume、重新生成）；
- 运行中每次模型/工具/辅助模型发送（执行引用 + 四修订 + 冻结业务快照）；
- 重试、SDK 重试、真实子任务（继承父业务，不因委派获得另一独立业务）。

状态语义：`401` 身份缺失/无效；`403` 业务未登记或未授权；`409` 上下文/版本冲突；
`503` IAM 或台账后端不可用。认证失败不匿名降级。

受理记录固定 `business_code`、`business_policy_version` 和实际 Graph；旧运行缺少
新快照时拒绝恢复/重试，要求重新受理。停止、本人删除等安全收尾仍按原规则可用，
不要求继续拥有已撤回的业务使用资格。

## 3. MCP 连接配置

连接是**平台部署配置**，独立于人员授权。来源只能是：

- `AGENT_MCP_CONNECTIONS_JSON`（单行 JSON，推荐；容器 env_file 可直接携带）；
- `AGENT_MCP_CONNECTIONS_FILE`（受保护文件）。

两者同时设置会拒绝启动式加载。浏览器、模型、checkpoint、metadata 都不能指定
地址、凭据或工具范围。

```json
{
  "schema_version": "agent.mcp-connections.v1",
  "revision": 1,
  "servers": [
    {
      "server_key": "ipp",
      "display_name": "空气质量数据服务（共享只读）",
      "url": "https://example.invalid/product/datacenter/api2/mcp",
      "scope": "shared_readonly",
      "auth_mode": "none",
      "enabled": true,
      "approved_query_tools": ["query_city_air", "query_station_air"]
    }
  ]
}
```

字段约束：

- `server_key`：`[a-z0-9][a-z0-9_-]{0,63}`。
- `scope`：`shared_readonly`（不伪造 Bearer/user_id/租户范围）或 `tenant`
  （必须声明 UUID `tenant_id`，且 `auth_mode=bearer_env`）。
- `auth_mode`：`none` 或 `bearer_env`；后者用 `credential_ref: "env:VAR"`，
  真实值只在发送边界解析，不进入模型、checkpoint、事件、Trace 或浏览器。
- `approved_query_tools`：服务侧审核过的查询工具**显式清单**。未登记的新工具
  不会自动开放；生产 `ipp` 当前 109 项，见
  `src/common/business_registry.py::IPP_APPROVED_QUERY_TOOLS`。
- 同一 `server_key` 不能同时声明共享只读与企业连接；同一企业/服务只能有一个
  启用连接。配置非法时失败关闭，不回退环境变量地址或旧绑定。

业务注册表与连接白名单取交集：业务未声明的服务/工具即使连接允许也不可执行。

## 4. 新增业务或工具

新增业务（不得跳步）：

1. IAM 后续迁移登记 `agent:business:<code>` 叶子资源与 `:use` 权限；
2. 更新本目录与 `business_registry.py`，冻结稳定编码、中文名、Graph 映射、
   连接与工具白名单；
3. 平台管理员显式开通企业；企业管理员在**本企业专属角色**中显式授予；
4. 不自动授予既有企业、既有角色或 root。

新增工具：先在连接配置 `approved_query_tools` 中审核登记，再在对应业务的
`BusinessConnection.allowed_tools` 中登记。只改连接不会自动向任何业务开放。

## 5. Skill、用户选择与执行范围

- 普通 Skill 不需要独立人员授权；材料版本、必需/可选依赖、菜单映射继续校验。
- 允许直接回答、直接 MCP、Skill 后 MCP 三条路径。
- 用户本次选择（显式 Skill / MCP allowlist / 空集合）仍约束执行；MCP 空集合时
  网络请求为 0。
- 子任务继承父业务与选择；不能通过文件读取、旧 schema 或静态工具集扩大范围。

## 6. 旧授权退役

`mcp_identity_bindings`、`/mcp-bindings`、`enterprise:mcp`、
`mcp:binding:read/manage`、fixture 协议门禁与旧读取路径已物理退役：

- 迁移 `0021_business_authorization` 增加业务快照字段；
- 迁移 `0022_retire_mcp_bindings` 显式删除旧表/索引/策略/约束/ACL 与旧权限定义，
  不使用 `DROP CASCADE`，不创建备份/快照/影子表；
- 旧接口返回 404/405；历史迁移、审计、事件/选择/观测事实保留。

## 7. 协调发布与无备份

`deployment/run_business_release.py` 是单一协调入口：

```bash
.venv/bin/python deployment/run_business_release.py preflight
.venv/bin/python deployment/run_business_release.py cleanup --root-evidence <root读回JSON>
.venv/bin/python deployment/run_business_release.py verify
```

- `preflight`：只读盘点 → 有界排空 → 停止 Agent 服务（维护拒绝）→ 迁移 → 启动 →
  验证新链路。
- `cleanup`：要求 root 正常登录/切企业读回证据（`ok=true`），然后停止服务 →
  调用 IAM `scripts/iam-final-cleanup.sh --apply` → 启动 → 最终核验。
- 任一步失败保持服务停止，不重启旧鉴权；检查点只存步骤/计数/版本/时间。
- 启动器在本次发布设置 `AGENT_RELEASE_NO_BACKUP=1`，跳过 `native.env` 备份；
  全程不创建 dump/快照/备份表/影子库/配置 `.backup`。

## 8. 外部数据权限边界

Agent 只负责本系统身份、企业、成员、业务开通/角色与连接配置。共享只读连接
不声称企业数据隔离；真实 MCP 的服务端数据范围必须以该服务能力为准。没有可
验证的全范围协议时，交付必须分别报告“业务授权/本系统数据查看已通过”和
“外部数据隔离待后续”，不得用 root 特判、模型提示或 HTTP 200 宣称完成。
