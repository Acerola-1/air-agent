"""每个异步模型/向量请求在发送前核对当前可信身份.

共享传输不缓存身份，覆盖框架内置摘要与子代理等辅助调用。
"""

from collections.abc import AsyncGenerator, Generator

import httpx
from langgraph.config import get_config

from common.identity.runtime import guard_runtime_call, guard_runtime_call_sync


class TenantOutboundAuth(httpx.Auth):
    """不持有凭据或租户状态；认证头仍由原 SDK 提供."""

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        """允许通过当前授权核验的请求进入 SDK 发送路径."""
        try:
            config = dict(get_config())
        except RuntimeError:
            config = {}
        await guard_runtime_call(config)
        yield request

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response]:
        """同步区域抽取等辅助模型也在真正发送前校验当前身份."""
        try:
            config = dict(get_config())
        except RuntimeError:
            config = {}
        guard_runtime_call_sync(config)
        yield request
