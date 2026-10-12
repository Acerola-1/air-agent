"""完整 JSON 数据的有界字段与范围说明."""

from __future__ import annotations

import json
from typing import Any


def parse_payload(raw: str) -> Any:
    """兼容 MCP 文本块中包裹的 JSON，保持完整载荷."""
    value = json.loads(raw)
    if (
        isinstance(value, list)
        and len(value) == 1
        and isinstance(value[0], dict)
        and value[0].get("type") == "text"
    ):
        return json.loads(value[0]["text"])
    return value


def collections(value: Any, path: str = "") -> list[tuple[str, list[Any]]]:
    """发现表型数组；不把每条记录中的嵌套数组误当独立输入."""
    if isinstance(value, list):
        return [(path, value)]
    if isinstance(value, dict):
        found = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                found.extend(collections(item, f"{path}/{escaped}"))
        return found
    return []


def build_manifest(raw: str, artifact_id: str) -> dict[str, Any] | None:
    """原始 JSON 完整解析，说明与样本明确为导航，不充当统计结论."""
    try:
        data = parse_payload(raw)
    except (ValueError, TypeError, RecursionError):
        return None
    if not isinstance(data, (dict, list)):
        return None
    tables = collections(data)
    if not tables:
        tables = [("", [data])]
    info = []
    for path, rows in tables[:32]:
        fields = list(
            dict.fromkeys(
                str(key) for row in rows[:20] if isinstance(row, dict) for key in row
            )
        )[:40]
        sample = json.dumps(rows[:2], ensure_ascii=False, default=str)
        info.append(
            {
                "path": path,
                "record_count": len(rows),
                "sampled_fields": fields,
                "sample_preview": sample[:600],
                "sample_preview_truncated": len(sample) > 600,
            }
        )
    return {
        "kind": "structured_data",
        "artifact_id": artifact_id,
        "storage_status": "stored",
        "characters": len(raw),
        "collections": info,
        "paths_truncated": len(tables) > 32,
        "instruction": "字段和样本仅用于编写代码，不能从样本推断全量结论。使用 compute_artifact，code 必须为同步 JavaScript 函数体，通过 helpers.scan(path, callback) 完整遍历选定数组，末尾显式 return {result:统计对象,scope:'时间、站点、因子、过滤与单位'}。最后一行变量不会自动返回；不可截取前缀、声称未计算结果。",
    }
