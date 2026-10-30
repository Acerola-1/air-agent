"""Run one real-model synthetic BQA conversation without formal services.

Output and a source copy remain in the task-owned ignored directory. The
existing harness uses in-memory checkpoints and replaces IAM and MCP calls.
"""

from __future__ import annotations

import os
import runpy
import shutil
import sys
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[2]
owned = root / ".local/graph-module-ownership/20261029-apply"
host = Path(sys.argv[1]).resolve()
if not host.is_relative_to(owned):
    raise ValueError("the host directory must be task-owned and private")
host.mkdir(exist_ok=False)
(host / ".local").mkdir()
shutil.copytree(
    root / "src", host / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
)
for key, value in dotenv_values(root / ".env").items():
    if value is not None and (
        key.startswith("MAIN_LLM_")
        or key in {"MODEL_MAX_CONTEXT_TOKENS", "MODEL_OUTPUT_RESERVED_TOKENS"}
    ):
        os.environ[key] = value
os.environ.update(
    BQA_HARNESS_ROOT=str(host),
    BQA_HARNESS_SRC=str(host / "src"),
    LANGFUSE_ENABLED="false",
    LANGFUSE_PROMPTS_ENABLED="false",
    AGENT_OBSERVABILITY_ENABLED="false",
    SILICONFLOW_API_KEY="controlled-unused",
    SERPER_API_KEY="controlled-unused",
)
sys.path.insert(0, str(host / "src"))
from application.business_policies import configure  # noqa: E402

configure()
sys.argv = [
    "run_bqa_session_models.py",
    "--schemas",
    str(owned / "tool-documents.json"),
    "--out",
    str(host / ".local/smoke"),
    "--cases",
    "A02",
    "--repeat",
    "1",
]
runpy.run_path(
    str(root / "tests/integration_tests/run_bqa_session_models.py"), run_name="__main__"
)
