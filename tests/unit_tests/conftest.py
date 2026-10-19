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
    "SILICONFLOW_API_KEY",
    "DEEPSEEK_API_KEY",
    "XFYUN_MAAS_API_KEY",
    "XIAOMI_MIMO_API_KEY",
    "SERPER_API_KEY",
    "ARK_API_KEY",
    "MAIN_LLM_API_KEY",
):
    os.environ.setdefault(key, "test")

# 单元测试默认关闭企业运行守卫强制（guard 专项测试自行开启并注入身份）
os.environ.setdefault("TENANT_ENFORCE_RUNTIME_IDENTITY", "false")
os.environ.setdefault("TENANT_BUSINESS_SNAPSHOT_ENFORCED", "false")
# 单元/集成测试固定本地身份后端：第三方库（litellm）导入时会加载仓库 .env，
# 生产配置里的 remote 不应改变测试语义；remote 专项用例自行 monkeypatch.setenv。
os.environ["AGENT_IDENTITY_BACKEND"] = "local"
# 测试默认不读取生产 MCP 连接配置，避免误发起真实外部请求。
os.environ.setdefault(
    "AGENT_MCP_CONNECTIONS_JSON",
    '{"schema_version":"agent.mcp-connections.v1","revision":1,"servers":[]}',
)



@pytest.fixture(autouse=True)
def _clear_permission_fact_caches():
    """每个用例前清空权限事实缓存，避免跨用例污染."""
    from common.permission.mcp_tools import clear_permission_fact_caches

    clear_permission_fact_caches()
    yield
    clear_permission_fact_caches()
