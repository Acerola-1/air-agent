"""单元测试导入路径设置."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

for key in (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "MCP_SERVER_URL",
    "MCP_SERVER_URL2",
    "SILICONFLOW_API_KEY",
    "DEEPSEEK_API_KEY",
    "XFYUN_MAAS_API_KEY",
    "XIAOMI_MIMO_API_KEY",
    "SERPER_API_KEY",
    "ARK_API_KEY",
    "OPENCODE_GO_DEEPSEEK_V4_FLASH_API_KEY",
):
    os.environ.setdefault(key, "test")


@pytest.fixture(autouse=True)
def _clear_permission_fact_caches():
    """每个用例前清空权限事实缓存，避免跨用例污染."""
    from common.permission.mcp_tools import clear_permission_fact_caches

    clear_permission_fact_caches()
    yield
    clear_permission_fact_caches()
