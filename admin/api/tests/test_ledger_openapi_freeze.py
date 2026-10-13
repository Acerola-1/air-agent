"""6.1 收口：台账 API 面的**冻结 OpenAPI** 漂移检测.

冻结方式为"生成 + 漂移检测"：任何新增/改签名都会让本用例失败，必须显式重新生成
（`admin/api/.venv/bin/python -m scripts.freeze_ledger_openapi`）并在评审中复核。
"""

from __future__ import annotations

import json
from pathlib import Path

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "contracts"
    / "agent-events-v1"
    / "openapi-agent-ledger.json"
)


def test_ledger_openapi_has_no_drift() -> None:
    """台账 API 面必须与冻结件逐条一致（路由 + 方法 + 参数/响应模型引用）."""
    from app.main import app

    spec = app.openapi()
    current = {
        path: item
        for path, item in sorted(spec.get("paths", {}).items())
        if path.startswith("/agent/")
    }
    frozen = json.loads(FROZEN.read_text())["paths"]
    assert frozen, "冻结件不得为空"
    missing = sorted(set(frozen) - set(current))
    added = sorted(set(current) - set(frozen))
    assert not missing, f"冻结的台账路由消失：{missing}"
    assert not added, f"新增台账路由必须先更新冻结件：{added}"
    drifted = sorted(
        path for path in frozen if json.dumps(frozen[path], sort_keys=True)
        != json.dumps(current[path], sort_keys=True)
    )
    assert not drifted, f"台账路由签名漂移，需重新生成并复核：{drifted}"
