"""本域纯函数测试不需要数据库；覆盖父级自动 seed 前置.

真实 PG/RLS/worker 测试在单独模块中显式创建自有临时资源，不能依赖父级
`ensure_test_catalog` 的共享库前置。
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def ensure_test_catalog() -> None:
    yield
