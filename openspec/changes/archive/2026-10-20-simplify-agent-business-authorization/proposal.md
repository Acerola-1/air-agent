# Proposal

## Why

当前人员业务使用资格混在企业/成员 MCP 绑定、测试协议与能力选择中，导致 root 也无法直接使用查询工具。用户已确认改为“企业开通业务 ∩ 本企业角色授权”，普通 Skill/MCP 不再逐项做人员 RBAC；需要同时清除旧授权存储与兼容代码，避免两套体系长期并存。

## What Changes

- 建立稳定业务目录和 Graph 映射，消费 IAM 实时有效业务权限；覆盖目录、台账受理、原生执行、恢复及每次模型/工具发送。
- **BREAKING**：移除人员/成员 MCP 授权绑定及普通 Skill/MCP 独立使用资格，删除 `mcp_identity_bindings`、残留数据、接口、旧权限码、测试协议开关及运行期读取路径；保留非授权的能力选择事实与观测记录。
- MCP 改为平台部署维护的服务连接配置，支持明确声明的共享只读数据连接或企业连接；凭据、连接修订、业务工具范围仍受保护，不再要求成员绑定。
- 普通 Skill 可直接加载，模型也可不经过 Skill 直接调用业务允许的 MCP；保留材料/依赖/菜单约束及本次用户选择。
- 联合 IAM 为现有 root 配好当前所有已注册业务和 Agent 业务数据查看资格，通过正式身份真实运行验证；不设置用户名绕过、跨租户 RLS 绕过或未来业务自动全选。
- 用户明确要求最终不留备份：一次受控发布完成替换及物理清理，不创建 dump、快照、备份表或旧授权镜像；采用事务回滚与向前修复，不提供旧授权恢复模式。

## Capabilities

### New Capabilities

- `agent-business-access`：业务目录、可信 Graph 映射、全入口准入与实时撤权。
- `agent-service-connections`：业务内 MCP 连接、外部认证及旧授权数据彻底退役。

### Modified Capabilities

- `agent-authorized-capability-catalog`：目录从成员绑定改为已获准业务及服务连接派生。
- `agent-request-capability-selection`：选择只约束执行范围，不再承担普通 Skill/MCP 人员授权。
- `agent-capability-observability`：拒绝事实按业务资格、连接和已受理范围解释，区分企业认证连接与显式共享只读连接，不再引用旧成员工具绑定门禁。

## Impact

- 配套 IAM change：`/Users/acerola/Dev/Java/air_agent_iam/openspec/changes/archive/2026-10-20-add-agent-business-entitlements/`。两边以本目录 `business-authorization-contract.md` 相同内容为契约。
- 涉及 `src/common/identity/`、`capabilities.py`、`mcp_authorization.py`、`mcp_scoped.py`、模型/工具守卫、台账、admin API/迁移、聊天及 Agent 管理端、事件契约与回归测试。
- 当前 `langgraph.json` 仅公开 basic-qa、data-analysis、intelligent-tracing；不把历史六图全部开通，也不覆盖正在实施的溯源与 DeepAgents 改动。
- `add-tenant-identity-access` 的旧 MCP 规格在本项正式实施后被替代；历史验收及任务7.6缺失事实保留。MCP 服务端数据权限后续实施，本项不得宣称已完成其跨企业数据隔离。
- 本轮只交付规划，全部实施任务未勾选；不更改数据库、root、部署、业务代码，不自动 commit/push/archive。
