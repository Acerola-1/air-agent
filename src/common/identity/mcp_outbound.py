"""MCP实际HTTP发送守卫；缓存SDK工具不得缓存调用者身份或撤销结果."""

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
    """仅持有绑定快照，每次发送都读取当次身份并重查IAM与业务绑定."""

    def __init__(self, binding):
        """保存非秘密绑定，绝不捕获首个用户的运行引用."""
        self.binding = binding

    async def async_auth_flow(self, request):
        """覆盖initialize、通知、工具调用、HTTP重试与SDK重连的真实发送."""
        from common.identity.remote import is_remote
        from common.mcp_authorization import (
            DEFAULT_PROVIDER,
            McpAuthorizationError,
            resolve_binding,
        )

        if is_remote():
            identity = _CURRENT_IDENTITY.get()
            if identity is None:
                raise McpAuthorizationError("MCP发送缺少当前可信身份")
            await revalidate(identity)
            current = await resolve_binding(
                tenant_id=identity.tenant_id,
                membership_id=identity.membership_id,
                server_key=self.binding.server_key,
            )
            if current.cache_scope != self.binding.cache_scope:
                raise McpAuthorizationError("MCP绑定已轮换，请重新发现工具")
            request.headers.update(DEFAULT_PROVIDER.auth_headers(current))
        yield request
