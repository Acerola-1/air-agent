"""导出console真实OpenAPI及TypeScript契约；不访问数据库或运行服务。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.main import app

ROOT = Path(__file__).resolve().parents[3]


def contract():
    source = app.openapi()
    paths = {
        key: value for key, value in source["paths"].items() if key.startswith("/agent/console/")
    }
    schemas = {}

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
        "info": {"title": "Agent运营后台", "version": "admin-console-v1"},
        "paths": paths,
        "components": {"schemas": dict(sorted(schemas.items()))},
    }


def ts_type(schema):
    if not isinstance(schema, dict):
        return "unknown"
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)
    if "enum" in schema:
        return " | ".join(json.dumps(value, ensure_ascii=False) for value in schema["enum"])
    if "anyOf" in schema or "oneOf" in schema:
        return " | ".join(ts_type(item) for item in schema.get("anyOf", schema.get("oneOf", [])))
    kind = schema.get("type")
    if kind in ("number", "integer"):
        return "number"
    if kind in ("boolean", "string", "null"):
        return kind
    if kind == "array":
        return f"Array<{ts_type(schema.get('items', {}))}>"
    if kind == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        fields = [
            f"{json.dumps(key)}{'' if key in required else '?'}: {ts_type(value)};"
            for key, value in properties.items()
        ]
        extra = schema.get("additionalProperties", False)
        if extra:
            types = [ts_type(extra)] + [ts_type(value) for value in properties.values()]
            fields.append("[key: string]: " + " | ".join(dict.fromkeys(types)) + ";")
        return "{ " + " ".join(fields) + " }"
    return "unknown"


def outputs():
    data = contract()
    typescript = "// 自动生成；运行 admin/api/scripts/export_console_contract.py 更新，勿手改。\n"
    typescript += (
        "\n".join(
            f"export type {name} = {ts_type(schema)};"
            for name, schema in data["components"]["schemas"].items()
        )
        + "\n"
    )
    return {
        ROOT / "docs/contracts/admin-console-v1/openapi.json": json.dumps(
            data, ensure_ascii=False, indent=2
        )
        + "\n",
        ROOT / "admin/web/lib/operations/contracts.generated.ts": typescript,
    }


def main():
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
    print("console OpenAPI与TypeScript契约一致")


if __name__ == "__main__":
    main()
