"""测试用 HTTP 客户端：模拟真实前端携带企业上下文声明头（R02 契约）。

后台写操作（departments/roles/tenants 成员/关注偏好）现在必须携带
`x-tenant-id` / `x-tenant-context-version`，值来自登录/me/切换响应。
ContextClient 在构造这些响应时自动更新声明，并为后续请求补默认头；
测试要验证"旧标签页"场景时显式传入更旧的头值即可。
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

_CTX_PATHS = ("/auth/login", "/auth/me", "/auth/switch-tenant")


def _claim_from(body: Any) -> tuple[str, str] | None:
    if not isinstance(body, dict) or "context_version" not in body:
        return None
    tenant = body.get("current_tenant") or {}
    tenant_id = tenant.get("id") if isinstance(tenant, dict) else None
    return (str(tenant_id) if tenant_id else "none", str(body["context_version"]))


class ContextClient(AsyncClient):
    """AsyncClient + 自动维护的企业上下文声明。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._claim: tuple[str, str] | None = None

    @property
    def claim(self) -> tuple[str, str] | None:
        return self._claim

    def stale_claim(self, tenant_id: str, version: int) -> None:
        """测试用：把声明固定为旧标签页持有的值。"""
        self._claim = (tenant_id, str(version))

    async def request(self, method: str, url: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        path = str(url)
        headers = dict(kwargs.get("headers") or {})
        lower = {str(k).lower() for k in headers}
        if self._claim and "x-tenant-id" not in lower and "x-tenant-context-version" not in lower:
            headers["x-tenant-id"] = self._claim[0]
            headers["x-tenant-context-version"] = self._claim[1]
        if headers:
            kwargs["headers"] = headers
        response = await super().request(method, url, **kwargs)
        if any(path.endswith(p) for p in _CTX_PATHS) and response.status_code == 200:
            try:
                claim = _claim_from(response.json())
            except ValueError:
                claim = None
            if claim:
                self._claim = claim
        return response
