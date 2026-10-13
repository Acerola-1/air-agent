"""生成/复核台账 API 的冻结 OpenAPI（任务 6.1）.

台账 API 面（`/agent/*`）一经冻结即作为跨 change 的接口契约：任何新增路由或签名变更
都必须重新生成本文件并在评审中显式复核，避免"契约在不知不觉中漂移"。

    admin/api/.venv/bin/python -m scripts.freeze_ledger_openapi        # 生成/更新
    # 校验（在 admin/api 下）：pytest tests/test_ledger_openapi_freeze.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402

TARGET = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "contracts"
    / "agent-events-v1"
    / "openapi-agent-ledger.json"
)


def ledger_surface() -> dict:
    """抽取台账 API 面（排除随部署/版本变化的 info/servers 字段）."""
    spec = app.openapi()
    return {
        "contract": "agent-events-v1",
        "scope": "台账 API 面（/agent/*）的冻结 OpenAPI；不含部署相关字段",
        "paths": {
            path: item
            for path, item in sorted(spec.get("paths", {}).items())
            if path.startswith("/agent/")
        },
    }


def main() -> int:
    """写入冻结件并打印路由清单（人工复核用）."""
    surface = ledger_surface()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(surface, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    sys.stdout.write(f"frozen {len(surface['paths'])} paths -> {TARGET}\n")
    for path in surface["paths"]:
        sys.stdout.write(f"  {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
