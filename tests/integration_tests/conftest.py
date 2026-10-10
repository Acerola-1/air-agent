"""集成测试配置."""

from __future__ import annotations

import os
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

for key in (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "MCP_SERVER_URL",
    "SILICONFLOW_API_KEY",
    "DEEPSEEK_API_KEY",
    "SERPER_API_KEY",
    "MAIN_LLM_API_KEY",
):
    os.environ.setdefault(key, "test")
