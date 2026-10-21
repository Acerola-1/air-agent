"""导出需求洞察真实 OpenAPI 与 TypeScript 契约；不访问数据库或运行服务."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.main import app
from scripts.export_console_contract import ts_type

ROOT = Path(__file__).resolve().parents[3]


def contract() -> dict:
    source = app.openapi()
    paths = {
        key: value for key, value in source["paths"].items() if key.startswith("/agent/insights/")
    }
    schemas: dict = {}

    def collect(value):
        if isinstance(value, dict):
            if "$ref" in value:
                name = value["$ref"].rsplit("/", 1)[-1]
                if name not in schemas:
                    schemas[name] = source["components"]["schemas"][name]
                    collect(schemas[name])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(paths)
    return {
        "openapi": source["openapi"],
        "info": {"title": "Agent需求洞察", "version": "demand-insights-v1"},
        "paths": paths,
        "components": {"schemas": dict(sorted(schemas.items()))},
    }


def outputs() -> dict[Path, str]:
    data = contract()
    typescript = "// 自动生成；运行 admin/api/scripts/export_demand_contract.py 更新，勿手改。\n"
    typescript += (
        "\n".join(
            f"export type {name} = {ts_type(schema)};"
            for name, schema in data["components"]["schemas"].items()
        )
        + "\n"
    )
    return {
        ROOT / "docs/contracts/demand-insights-v1/openapi.json": json.dumps(
            data, ensure_ascii=False, indent=2
        )
        + "\n",
        ROOT / "admin/web/lib/operations/demand-contracts.generated.ts": typescript,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for path, value in outputs().items():
        if args.check:
            if not path.exists() or path.read_text() != value:
                raise SystemExit(f"契约副本未同步: {path.relative_to(ROOT)}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
    print("需求洞察 OpenAPI 与 TypeScript 契约一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
