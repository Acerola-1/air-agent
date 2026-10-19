"""MCP 实际 HTTP 发送守卫；缓存 SDK 工具不得缓存调用者身份或撤销结果."""

from contextlib import contextmanager
from contextvars import ContextVar

import httpx

from common.identity.runtime import RuntimeIdentity, revalidate

_CURRENT_IDENTITY: ContextVar[RuntimeIdentity | None] = ContextVar(
    "mcp_send_identity", default=None
)


@contextmanager
def mcp_identity_scope(identity: RuntimeIdentity):
    """为本次发现/调用绑定可信身份；SDK并发任务继承，退出后立即复位."""
    token = _CURRENT_IDENTITY.set(identity)
    try:
        yield
    finally:
        _CURRENT_IDENTITY.reset(token)


class MCPOutboundAuth(httpx.Auth):
    """仅持有连接快照；每次发送读取当次身份并复查业务/修订/连接轮换."""

    def __init__(self, resolved) -> None:  # noqa: ANN001 - ResolvedConnection
        """保存非秘密连接快照，绝不捕获首个用户的运行引用."""
        self.resolved = resolved

    async def async_auth_flow(self, request):
        """覆盖initialize、通知、工具调用、HTTP重试与SDK重连的真实发送."""
        from common.identity.remote import is_remote
        from common.mcp_authorization import (
            DEFAULT_PROVIDER,
            McpAuthorizationError,
            resolve_connection,
        )

        identity = _CURRENT_IDENTITY.get()
        if identity is None:
            raise McpAuthorizationError("MCP发送缺少当前可信身份")
        if is_remote():
            # 逐发送复查执行引用/四修订 + 受理时冻结的业务许可快照。
            await revalidate(identity)
        if not identity.business_code:
            raise McpAuthorizationError("MCP发送缺少受理时冻结的业务许可")
        current = resolve_connection(
            business_code=identity.business_code,
            tenant_id=identity.tenant_id,
            server_key=self.resolved.server_key,
        )
        if current.cache_scope != self.resolved.cache_scope:
            raise McpAuthorizationError("MCP连接已轮换，请重新发现工具")
        headers = DEFAULT_PROVIDER.auth_headers(current.connection)
        if headers:
            request.headers.update(headers)
        yield request
