"""污染溯源能力探针：核实 MCP 目录中气象/轨迹/PSCF/源清单的真实可用性。

只读、匿名、不调用写接口；输出只保留工具名、字段名、可用性与服务端错误原文，
不落业务数据明细。用于回答"第一阶段溯源能做到哪一步"这一事实问题。

用法：
    set -a; . ./.env; set +a
    .venv/bin/python tests/mcp_probe/probe_tracing_capability.py [行政区名称]

默认行政区为郑州市。若 MCP_SERVER_URL 未设置则退出码 1。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_REGION = "郑州市"

# 溯源关键能力的关键词（工具名 + 描述）
CAPABILITY_KEYWORDS = {
    "trajectory": ("trajectory", "轨迹"),
    "pscf": ("pscf", "潜在源"),
    "emission_source": ("污染源", "排放源", "源清单", "企业"),
}

WIND_FIELDS = ("windDirection", "windSpeed", "pblh", "humidity", "pressure")


def _text(result: Any) -> str:
    return "\n".join(
        getattr(item, "text", "") for item in getattr(result, "content", []) or []
    )


def _payload(result: Any) -> Any:
    raw = _text(result)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _first_record(payload: Any) -> dict[str, Any]:
    """在常见的 {"data": ...} 包装中定位第一条记录。"""
    node = payload
    for _ in range(4):
        if isinstance(node, dict):
            for key in ("data", "rows", "list", "result"):
                if key in node:
                    node = node[key]
                    break
            else:
                return node
        elif isinstance(node, list):
            node = node[0] if node else {}
        else:
            return {}
    return node if isinstance(node, dict) else {}


def _has_value(record: dict[str, Any], field: str) -> bool:
    """判断字段是否给出了有效值（排除空串、'-'、-1、null 这类占位）。"""
    value = record.get(field)
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() not in {"", "-", "null", "None"}
    if isinstance(value, (int, float)):
        return value != -1
    return True


async def _probe(region: str) -> dict[str, Any]:
    url = os.environ["MCP_SERVER_URL"]
    report: dict[str, Any] = {"region": region, "url_scheme": url.split("://")[0]}

    async with streamablehttp_client(url, timeout=60) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            report["server"] = getattr(init.serverInfo, "name", None)
            tools = (await session.list_tools()).tools or []
            report["tool_count"] = len(tools)

            matched: dict[str, list[str]] = {}
            for capability, keywords in CAPABILITY_KEYWORDS.items():
                matched[capability] = [
                    tool.name
                    for tool in tools
                    if any(
                        keyword in tool.name or keyword in (tool.description or "")
                        for keyword in keywords
                    )
                ]
            report["catalog_matches"] = matched

            async def call(name: str, args: dict[str, Any]) -> tuple[bool, Any]:
                result = await session.call_tool(name, args)
                payload = _payload(result)
                ok = (
                    not getattr(result, "isError", False)
                    and isinstance(payload, dict)
                    and payload.get("success") is True
                )
                return ok, payload

            latest: dict[str, str] = {}
            try:
                _, payload = await call("helper_get_latest_time", {})
                for row in (payload or {}).get("data", []) or []:
                    latest[row.get("dataType", "")] = row.get("latestTime", "")
            except Exception as exc:  # noqa: BLE001
                report["latest_time_error"] = f"{type(exc).__name__}: {exc}"[:200]
            report["latest_times"] = latest

            # 取一个国控站作为探针目标（有数据才继续）
            station: dict[str, Any] = {}
            weather: dict[str, Any] = {}
            try:
                _, payload = await call(
                    "mcp_station_base_air_station_list",
                    {"region": region, "stationType": "S-10"},
                )
                stations = (payload or {}).get("data") or []
                if stations:
                    station = stations[0]
            except Exception as exc:  # noqa: BLE001
                report["station_error"] = f"{type(exc).__name__}: {exc}"[:200]
            report["station"] = {
                "found": bool(station),
                "has_lnglat": bool(
                    station.get("longitude") and station.get("latitude")
                ),
            }

            hour = latest.get("查询站点历史小时空气质量") or latest.get(
                "查询站点当日空气质量"
            )
            if station and hour:
                s_time = hour[:13] + ":00"
                e_time = s_time

                async def weather_probe(tool: str, args: dict[str, Any]) -> None:
                    try:
                        ok, payload = await call(tool, args)
                    except Exception as exc:  # noqa: BLE001
                        weather[tool] = {
                            "ok": False,
                            "error": f"{type(exc).__name__}: {exc}"[:200],
                        }
                        return
                    record = _first_record(payload)
                    weather[tool] = {
                        "ok": ok,
                        "fields": sorted(record.keys())[:40],
                        "wind_available": all(
                            _has_value(record, f) for f in WIND_FIELDS[:2]
                        ),
                        "pblh_available": _has_value(record, "pblh"),
                    }

                await weather_probe(
                    "mcp_weather_forecast_hourly_history",
                    {"region": region, "sTime": s_time, "eTime": e_time},
                )
                await weather_probe(
                    "mcp_weather_numeric_forecast_history",
                    {
                        "sTime": s_time,
                        "eTime": e_time,
                        "points": f"{station.get('longitude')},{station.get('latitude')}",
                        "expansion1": "gfs",
                    },
                )
            report["historical_weather"] = weather

            # 轨迹 / PSCF：只做单次可用性试探，记录服务端错误原文
            runtime: dict[str, Any] = {}
            station_key = station.get("stationKey")
            if station_key and hour:
                s_time = hour[:13] + ":00:00"
                trajectory_args = {
                    "station": station_key,
                    "type": "hourly",
                    "sTime": s_time,
                    "expansion1": "back",
                    "expansion2": "6",
                    "expansion3": "1",
                    "cl": "0",
                }
                pscf_args = {
                    "station": station_key,
                    "type": "hourly",
                    "index": "pm25",
                    "sTime": s_time,
                    "expansion1": "back",
                    "expansion2": "6",
                    "expansion3": "1",
                }
                for label, tool, args in (
                    ("trajectory", "mcp_station_analysis_trajectory", trajectory_args),
                    ("pscf", "mcp_station_analysis_single_pscf", pscf_args),
                ):
                    try:
                        ok, payload = await call(tool, args)
                    except Exception as exc:  # noqa: BLE001
                        runtime[label] = {
                            "ok": False,
                            "error": f"{type(exc).__name__}: {exc}"[:200],
                        }
                        continue
                    message = payload.get("msg") if isinstance(payload, dict) else None
                    runtime[label] = {
                        "ok": ok,
                        "code": payload.get("code")
                        if isinstance(payload, dict)
                        else None,
                        "msg": message[:200] if isinstance(message, str) else None,
                    }
            report["runtime"] = runtime

    return report


# cl 参数矩阵：同一组其它参数下，只改 cl 的取值或字段名
CL_VARIANTS: list[tuple[str, Any]] = [
    ("cl='0'", "0"),
    ("cl='1'", "1"),
    ("cl=''", ""),
    ("cl='true'", "true"),
    ("cl='false'", "false"),
    ("cl=0(int)", 0),
    ("cl=1(int)", 1),
    ("cl=False", False),
    ("cl=True", True),
    ("cl=None", None),
    ("省略 cl", "OMIT"),
    ("Cl='0'", "OMIT:Cl"),
    ("cluster='0'", "OMIT:cluster"),
    ("isCl='0'", "OMIT:isCl"),
    ("expansion4='0'", "OMIT:expansion4"),
]


async def _resolve_target(
    session: ClientSession, region: str
) -> tuple[str | None, str | None]:
    _, payload = await _call_tool(
        session,
        "mcp_station_base_air_station_list",
        {"region": region, "stationType": "S-10"},
    )
    stations = (payload or {}).get("data") or []
    station_key = stations[0].get("stationKey") if stations else None
    _, latest = await _call_tool(session, "helper_get_latest_time", {})
    hour = next(
        (
            row.get("latestTime", "")
            for row in (latest or {}).get("data", []) or []
            if row.get("dataType") == "查询站点历史小时空气质量"
        ),
        "",
    )
    return station_key, (hour[:13] + ":00:00" if hour else None)


async def _call_tool(
    session: ClientSession, name: str, args: dict[str, Any]
) -> tuple[bool, Any]:
    result = await session.call_tool(name, args)
    payload = _payload(result)
    ok = (
        not getattr(result, "isError", False)
        and isinstance(payload, dict)
        and payload.get("success") is True
    )
    return ok, payload


def _outcome(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"raw": str(payload)[:120]}
    message = payload.get("msg")
    return {
        "code": payload.get("code"),
        "msg": message[:120] if isinstance(message, str) else None,
    }


async def _cl_matrix(region: str) -> dict[str, Any]:
    """固定其余入参，逐个变体调用轨迹工具，定位 cl 是否被透传到下游。"""
    url = os.environ["MCP_SERVER_URL"]
    async with streamablehttp_client(url, timeout=60) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            station_key, s_time = await _resolve_target(session, region)
            if not station_key or not s_time:
                return {"ok": False, "reason": "未取得站点或最新小时数据时间"}

            base: dict[str, Any] = {
                "station": station_key,
                "type": "hourly",
                "sTime": s_time,
                "expansion1": "back",
                "expansion2": "6",
                "expansion3": "1",
            }

            variants: list[dict[str, Any]] = []
            for label, value in CL_VARIANTS:
                args = dict(base)
                if isinstance(value, str) and value.startswith("OMIT:"):
                    args[value.split(":", 1)[1]] = "0"
                elif value != "OMIT":
                    args["cl"] = value
                _, payload = await _call_tool(
                    session, "mcp_station_analysis_trajectory", args
                )
                variants.append({"case": label, **_outcome(payload)})

            # 反向验证：其它必填缺失时应先报其它字段，证明参数确实被透传
            controls = []
            for label, overrides in (
                ("station='' + cl='0'", {"station": "", "cl": "0"}),
                ("缺 expansion3 + cl='0'", {"expansion3": None, "cl": "0"}),
            ):
                args = dict(base)
                for key, value in overrides.items():
                    if value is None:
                        args.pop(key, None)
                    else:
                        args[key] = value
                _, payload = await _call_tool(
                    session, "mcp_station_analysis_trajectory", args
                )
                controls.append({"case": label, **_outcome(payload)})

    return {
        "region": region,
        "station_key": station_key,
        "s_time": s_time,
        "tool": "mcp_station_analysis_trajectory",
        "variants": variants,
        "controls": controls,
    }


async def main() -> int:
    if not os.environ.get("MCP_SERVER_URL"):
        print(json.dumps({"reachable": False, "reason": "MCP_SERVER_URL 未设置"}))  # noqa: T201
        return 1
    args = [item for item in sys.argv[1:] if not item.startswith("--")]
    region = args[0] if args else DEFAULT_REGION
    if "--cl-matrix" in sys.argv:
        report: dict[str, Any] = await _cl_matrix(region)
    else:
        report = await _probe(region)
    print(json.dumps(report, ensure_ascii=False, indent=2))  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
