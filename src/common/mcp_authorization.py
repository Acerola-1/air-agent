"""业务 MCP 连接解析与凭据提供.

业务准入与连接凭据边界：

- 业务准入由 `common.identity.business_access` 按 IAM 业务使用权限裁决；
- 服务连接来自受保护部署配置（`common.mcp_connections`），浏览器不可指定；
- `shared_readonly` 连接不伪造 Bearer/user_id/租户范围；
- `tenant` 连接必须使用真实认证（当前支持 `env:` 引用的 Bearer）；
- 凭据只保存引用，真实值仅在发送边界解析；认证失败绝不匿名重试。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from common.business_registry import BusinessConnection, business_for_code
from common.mcp_connections import MCPServerConnection, connection_for


class McpAuthorizationError(Exception):
    """MCP 连接不可用（拒绝发起调用；无匿名兜底）."""


class CredentialProvider:
    """credential_ref → 发送边界凭据（当前仅 `env:VAR`）."""

    def auth_headers(self, connection: MCPServerConnection) -> dict[str, str]:
        """返回该连接发送时所需的认证 headers."""
        if connection.auth_mode == "none":
            return {}
        secret = self.resolve_secret(connection.credential_ref or "")
        return {"Authorization": f"Bearer {secret}"}

    def resolve_secret(self, credential_ref: str) -> str:
        """把凭据引用解析为真实值（仅发送边界调用，不进入日志/状态）."""
        if not credential_ref:
            raise McpAuthorizationError("凭据引用缺失")
        if not credential_ref.startswith("env:"):
            # 其他后端（secret manager 等）不属于本 change；明确拒绝而非忽略
            raise McpAuthorizationError("不支持的凭据引用类型")
        name = credential_ref[len("env:") :]
        value = os.environ.get(name, "")
        if not value:
            raise McpAuthorizationError("凭据不可用")
        return value

    def credential_available(self, connection: MCPServerConnection) -> bool:
        """目录/状态判断用：不返回秘密，只判断引用是否可解析."""
        if connection.auth_mode == "none":
            return True
        try:
            self.resolve_secret(connection.credential_ref or "")
            return True
        except McpAuthorizationError:
            return False


DEFAULT_PROVIDER = CredentialProvider()


@dataclass(frozen=True)
class ResolvedConnection:
    """业务 + 连接联合校验后的执行快照（仅非秘密字段）."""

    business_code: str
    tenant_id: str
    connection: MCPServerConnection
    business_scope: BusinessConnection

    @property
    def server_key(self) -> str:
        """服务键（与连接配置一致）."""
        return self.connection.server_key

    @property
    def cache_scope(self) -> tuple[str, str, str, str, int, str]:
        """缓存键由连接层提供（企业/业务/策略/服务/修订/认证引用摘要）."""
        from common.business_registry import BUSINESS_POLICY_VERSION
        from common.mcp_connections import connection_scope_key

        return connection_scope_key(
            tenant_id=self.tenant_id,
            business_code=self.business_code,
            business_policy_version=BUSINESS_POLICY_VERSION,
            connection=self.connection,
        )

    def redacted(self) -> dict[str, Any]:
        """脱敏描述（可进入日志/普通事件）."""
        return {
            "business_code": self.business_code,
            **self.connection.redacted(),
        }


def resolve_connection(
    *,
    business_code: str,
    tenant_id: str,
    server_key: str,
    provider: CredentialProvider | None = None,
    require_credential: bool = False,
) -> ResolvedConnection:
    """解析业务允许的服务连接；任何不满足即抛 `McpAuthorizationError`.

    失败关闭：业务未登记、业务未声明该服务、企业无匹配连接、连接停用、
    认证方式不可用或凭据缺失都拒绝；不回退环境变量地址或旧绑定。
    """
    definition = business_for_code(business_code)
    if definition is None:
        raise McpAuthorizationError("业务未登记，拒绝 MCP 调用")
    scope = definition.connection(server_key)
    if scope is None:
        raise McpAuthorizationError("该业务未声明此 MCP 服务，拒绝调用")
    try:
        connection = connection_for(server_key, tenant_id)
    except Exception as exc:
        raise McpAuthorizationError("MCP 连接配置不可用") from exc
    if connection is None:
        raise McpAuthorizationError("当前企业未配置该服务的连接")
    if not connection.enabled:
        raise McpAuthorizationError("MCP 连接已停用")
    if not connection.approved_query_tools:
        raise McpAuthorizationError("MCP 连接未登记已审核查询工具")
    resolved = ResolvedConnection(
        business_code=business_code,
        tenant_id=str(tenant_id),
        connection=connection,
        business_scope=scope,
    )
    if require_credential and not (provider or DEFAULT_PROVIDER).credential_available(
        connection
    ):
        raise McpAuthorizationError("MCP 连接凭据不可用")
    return resolved


def business_allows_connection(business_code: str, server_key: str) -> bool:
    """该业务是否声明了此服务（目录可见性判断，不读取连接秘密）."""
    definition = business_for_code(business_code)
    return definition is not None and definition.connection(server_key) is not None
