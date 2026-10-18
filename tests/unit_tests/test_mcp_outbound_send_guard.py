"""缓存MCP客户端逐请求采用当前身份，撤销发生在握手后也不可发送。"""

from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest

from common.identity import mcp_outbound, remote
from common.identity.runtime import RuntimeIdentity
from common.mcp_authorization import McpAuthorizationError, ScopeBinding

pytestmark = pytest.mark.anyio


async def test_cached_mcp_auth_rechecks_each_current_execution_reference(monkeypatch):
    import common.mcp_authorization as authz

    monkeypatch.setattr(remote, "is_remote", lambda: True)
    binding = ScopeBinding(
        "t", "m", "fixture", "external", "env:I2_TEST", "v1-fixture", 1
    )
    monkeypatch.setenv("I2_TEST", "synthetic-bearer")
    monkeypatch.setattr(authz, "resolve_binding", AsyncMock(return_value=binding))
    seen = []

    async def validate(identity):
        seen.append(identity.verification_ref)
        if identity.verification_ref == "revoked":
            raise McpAuthorizationError("revoked")

    monkeypatch.setattr(mcp_outbound, "revalidate", validate)
    sent = []

    async def send(request):
        sent.append(request.url.path)
        return httpx.Response(200)

    identity = RuntimeIdentity("u", "t", "m", "s", verification_ref="first")
    async with httpx.AsyncClient(
        auth=mcp_outbound.MCPOutboundAuth(binding), transport=httpx.MockTransport(send)
    ) as client:
        with mcp_outbound.mcp_identity_scope(identity):
            await client.post("http://fixture/initialize")
        with mcp_outbound.mcp_identity_scope(
            replace(identity, verification_ref="second")
        ):
            await client.post("http://fixture/tools-list")
        with mcp_outbound.mcp_identity_scope(
            replace(identity, verification_ref="revoked")
        ):
            with pytest.raises(McpAuthorizationError):
                await client.post("http://fixture/tools-call")
        with pytest.raises(McpAuthorizationError):
            await client.post("http://fixture/no-current-identity")
    assert seen == ["first", "second", "revoked"]
    assert sent == ["/initialize", "/tools-list"]
