"""Protocol-compatible model and MCP doubles with frozen delays."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _offered_tool_names(payload: dict) -> set[str]:
    names: set[str] = set()
    for tool in payload.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        source = function if isinstance(function, dict) else tool
        name = source.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def _tool_messages(payload: dict) -> list[dict]:
    messages = []
    for message in payload.get("messages") or []:
        if isinstance(message, dict) and message.get("role") == "tool":
            messages.append(message)
    return messages


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return ""


_PLACES: list[str] | None = None
_PCA_PATH = Path(__file__).resolve().parents[2] / "admin/api/datasets/pca-code.json"
_GENERIC_TOKENS = frozenset(
    {"城市", "都市", "全市", "本市", "该市", "地市", "县市", "区市"}
)


def _walk_places(nodes: object, names: set[str]) -> None:
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or "")
        if name and name not in {"市辖区", "县", "省直辖县级行政区划"}:
            names.add(name)
        _walk_places(node.get("children"), names)


def _known_places() -> list[str]:
    """Return the administrative names used to locate a city deterministically."""
    global _PLACES
    if _PLACES is None:
        names: set[str] = set()
        try:
            _walk_places(json.loads(_PCA_PATH.read_text()), names)
        except (OSError, json.JSONDecodeError):
            names = set()
        _PLACES = sorted(names, key=len, reverse=True)
    return _PLACES


def _extract_city(text: str) -> str:
    """Pick a known administrative name; never return surrounding prose.

    The previous suffix regex could turn "请对测试城市洛阳市" into the whole
    phrase. Matching against the bundled administrative catalog keeps the
    double deterministic and avoids reading a conclusion from the question.
    """
    for name in _known_places():
        if name in text:
            return name
    candidate = ""
    for token in text.split("市"):
        tail = token.strip()
        if 2 <= len(tail) <= 6 and all("\u4e00" <= char <= "\u9fff" for char in tail):
            candidate = tail + "市"
    if candidate in _GENERIC_TOKENS:
        return ""
    return candidate


def _freeze_arguments(payload: dict) -> str:
    """Build a freeze call from the user text without adding a conclusion."""
    text = ""
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") in {"user", "human"} or message.get("type") == "human":
            text += _message_text(message)
    region = _extract_city(text)
    start = "2026-05-07T00:00:00+08:00"
    end = "2026-05-07T23:00:00+08:00"
    if "2026年5月7日" not in text and "2026-05-07" not in text:
        start = ""
        end = ""
    return json.dumps(
        {
            "region_name": region,
            "station_name": "洛阳市国控站" if region else "",
            "pollutant": "pm2.5" if "PM2.5" in text or "pm2.5" in text else "",
            "start": start,
            "end": end,
            "timezone": "Asia/Shanghai",
            "neighbor_radius_km": 5,
            "analysis_id": "isolated-connectivity",
            "revision": 1,
        },
        ensure_ascii=False,
    )


def _structured_response(payload: dict) -> dict | None:
    """Return a schema-valid advisory object when the subagent asks for JSON."""
    response_format = payload.get("response_format")
    text = json.dumps(response_format, ensure_ascii=False) if response_format else ""
    if "MeteoNarrative" in text:
        return {
            "summary": "受控气象叙述：合成风场与边界层数据仅用于确定性分析。",
            "supplemental_checks": [],
            "limitations_observed": ["受控合成数据，不代表真实气象"],
        }
    return None


def tracing_task_message(payload: dict) -> dict | None:
    """Freeze before any specialist delegation.

    A task call is returned only after a tool result says the request is frozen.
    Later specialist turns keep the fixed non-answer. Arguments do not contain
    a conclusion or the reference gold text.
    """
    names = _offered_tool_names(payload)
    tool_messages = _tool_messages(payload)
    if "MeteoNarrative" in names:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "meteo-1",
                    "type": "function",
                    "function": {
                        "name": "MeteoNarrative",
                        "arguments": json.dumps(
                            {
                                "summary": "受控气象叙述：合成风场与边界层数据仅用于确定性分析。",
                                "supplemental_checks": [],
                                "limitations_observed": [
                                    "受控合成数据，不代表真实气象"
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        }
    if "EvidenceReview" in names:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "review-1",
                    "type": "function",
                    "function": {
                        "name": "EvidenceReview",
                        "arguments": json.dumps(
                            {
                                "analysis_id": "isolated-connectivity",
                                "revision": 1,
                                "issues": [],
                                "contradictions": [],
                                "omissions": [],
                                "downgrade_suggestions": [],
                                "reviewed_claim_ids": [],
                                "review_summary": "受控审查：未发现额外事实。",
                            },
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        }
    if "freeze_tracing_request" in names and not tool_messages:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "freeze-1",
                    "type": "function",
                    "function": {
                        "name": "freeze_tracing_request",
                        "arguments": _freeze_arguments(payload),
                    },
                }
            ],
        }
    frozen = any(
        "frozen" in str(message.get("content") or "") for message in tool_messages
    )
    if "task" in names and frozen:
        return _staged_task_message(payload)
    return None


def _dispatched_task_roles(payload: dict) -> set[str]:
    """Return the specialist roles already delegated in this conversation."""
    roles: set[str] = set()
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict) or function.get("name") != "task":
                continue
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            role = (
                arguments.get("subagent_type") if isinstance(arguments, dict) else None
            )
            if isinstance(role, str) and role:
                roles.add(role)
    return roles


def _task_message(kinds: tuple[str, ...]) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": f"task-{kind}-1",
                "type": "function",
                "function": {
                    "name": "task",
                    "arguments": json.dumps(
                        {"description": "隔离连通", "subagent_type": kind},
                        ensure_ascii=False,
                    ),
                },
            }
            for kind in kinds
        ],
    }


def _staged_task_message(payload: dict) -> dict | None:
    """Delegate in the order the tracing prompt requires.

    Station and grid results must exist before the meteo analyst runs, and the
    reviewer runs after the three analysis specialists. Dispatching all four
    tasks in one message makes the meteo subagent start without the parent's
    station findings, so its deterministic findings and child execution are
    skipped. Stages are derived from the assistant task calls already present.
    """
    dispatched = _dispatched_task_roles(payload)
    if not dispatched:
        return _task_message(("station-data", "grid-data"))
    if "meteo-analyst" not in dispatched:
        return _task_message(("meteo-analyst",))
    if "evidence-reviewer" not in dispatched:
        return _task_message(("evidence-reviewer",))
    return None


_TRACING_TOOL_NAMES = (
    "mcp_station_base_air_station_list",
    "mcp_station_common_air_data",
    "mcp_weather_forecast_hourly_history",
    "mcp_grid_common_air_data",
    "mcp_grid_base_grid_info_by_codes",
)

_STATIONS = (
    {
        "stationKey": "S001",
        "name": "洛阳市国控站",
        "regionKey": "410300",
        "regionName": "洛阳市",
        "latitude": 34.6197,
        "longitude": 112.4540,
    },
    {
        "stationKey": "S002",
        "name": "洛阳市西工站",
        "regionKey": "410300",
        "regionName": "洛阳市",
        "latitude": 34.6602,
        "longitude": 112.4301,
    },
    {
        "stationKey": "S003",
        "name": "洛阳市洛龙站",
        "regionKey": "410300",
        "regionName": "洛阳市",
        "latitude": 34.6001,
        "longitude": 112.4702,
    },
)


def _mcp_tool_payload(name: str) -> dict:
    """Return minimal deterministic rows for the tracing product tools."""
    if name == "mcp_station_base_air_station_list":
        return {"data": {"rows": [dict(row) for row in _STATIONS]}}
    if name == "mcp_station_common_air_data":
        rows = []
        for station_index, station in enumerate(_STATIONS):
            for hour in range(24):
                base = 30 + station_index * 6 + hour * 0.4
                rows.append(
                    {
                        "stationKey": station["stationKey"],
                        "updateTime": f"2026-05-07 {hour:02d}:00:00",
                        "pm25": round(base, 2),
                        "pm10": round(base * 1.8, 2),
                        "o3": round(60 + hour * 1.2, 2),
                        "no2": round(18 + station_index, 2),
                        "so2": round(7 + station_index * 0.5, 2),
                        "co": round(0.5 + station_index * 0.05, 3),
                    }
                )
        return {"data": {"rows": rows}}
    if name == "mcp_weather_forecast_hourly_history":
        rows = []
        for hour in range(24):
            rows.append(
                {
                    "updateTime": f"2026-05-07 {hour:02d}:00:00",
                    "windSpeed": round(2.5 + (hour % 6) * 0.4, 2),
                    "windDirection": (180 + hour * 5) % 360,
                    "pblh": round(600 + hour * 20, 2),
                    "humidity": round(50 + (hour % 8), 2),
                    "pressure": round(1004 + (hour % 5) * 0.4, 2),
                    "temperature": round(16 + (hour % 12) * 0.5, 2),
                }
            )
        return {"data": {"rows": rows}}
    if name == "mcp_grid_common_air_data":
        return {
            "data": {
                "rows": [
                    {
                        "name": f"G{index}",
                        "value": round(40 + index * 3.5, 2),
                        "updateTime": "2026-05-07 12:00:00",
                    }
                    for index in range(1, 6)
                ]
            }
        }
    if name == "mcp_grid_base_grid_info_by_codes":
        return {
            "data": {
                "rows": [
                    {
                        "name": f"G{index}",
                        "latitude": round(34.60 + index * 0.01, 4),
                        "longitude": round(112.44 + index * 0.01, 4),
                    }
                    for index in range(1, 6)
                ]
            }
        }
    return {"data": {"rows": []}}


def _embedding_vector(text: str, dimension: int) -> list[float]:
    """Hash character unigrams/bigrams into a deterministic L2-normalized vector.

    Exact repeats and near repeats share n-grams, so controlled clustering sees
    realistic similarity without calling an external embedding provider.
    """
    normalized = "".join(text.lower().split())
    tokens = list(normalized)
    tokens.extend(normalized[index : index + 2] for index in range(len(normalized) - 1))
    vector = [0.0] * dimension
    for token in tokens:
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign * (1.5 if len(token) == 2 else 1.0)
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        vector[0] = 1.0
        norm = 1.0
    return [round(value / norm, 6) for value in vector]


class ControlledDependencyServer:
    """Serve a slow chat stream and a counting MCP endpoint on loopback.

    The reply is a fixed non-answer. It does not read the reference gold file.
    Embeddings are deterministic local vectors, never an external model.
    """

    def __init__(
        self,
        first_delay_s: float = 0.4,
        chunk_interval_s: float = 0.05,
        port: int = 0,
        embedding_dimension: int = 64,
        embedding_delay_s: float = 0.0,
        debug_path: str = "",
    ) -> None:
        """Bind loopback and remember the frozen delays."""
        self.first_delay_s = first_delay_s
        self.chunk_interval_s = chunk_interval_s
        self.embedding_dimension = embedding_dimension
        self.embedding_delay_s = embedding_delay_s
        self.debug_path = debug_path
        self.chat_calls = 0
        self.mcp_calls = 0
        self.mcp_tool_calls = 0
        self.embedding_calls = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path.startswith("/stats"):
                    body = json.dumps(
                        {
                            "chat_calls": server.chat_calls,
                            "mcp_calls": server.mcp_calls,
                            "mcp_tool_calls": server.mcp_tool_calls,
                            "embedding_calls": server.embedding_calls,
                        }
                    ).encode()
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length)
                if self.path.startswith("/v1/chat/completions"):
                    server.chat_calls += 1
                    self._chat(raw)
                    return
                if self.path.startswith("/mcp"):
                    server.mcp_calls += 1
                    self._mcp(raw)
                    return
                if self.path.startswith("/v1/embeddings"):
                    server.embedding_calls += 1
                    self._embeddings(raw)
                    return
                self.send_error(404)

            def _embeddings(self, raw: bytes) -> None:
                if server.embedding_delay_s:
                    time.sleep(server.embedding_delay_s)
                payload = json.loads(raw or b"{}")
                inputs = payload.get("input")
                if isinstance(inputs, str):
                    texts = [inputs]
                elif isinstance(inputs, list) and all(
                    isinstance(item, str) for item in inputs
                ):
                    texts = inputs
                else:
                    self.send_error(400)
                    return
                data = [
                    {
                        "object": "embedding",
                        "index": index,
                        "embedding": _embedding_vector(
                            text, server.embedding_dimension
                        ),
                    }
                    for index, text in enumerate(texts)
                ]
                tokens = sum(len(text) for text in texts)
                body = json.dumps(
                    {
                        "object": "list",
                        "model": payload.get("model") or "controlled-embedding",
                        "data": data,
                        "usage": {
                            "prompt_tokens": tokens,
                            "total_tokens": tokens,
                        },
                    }
                ).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _chat(self, raw: bytes) -> None:
                payload = json.loads(raw or b"{}")
                if server.debug_path:
                    shape = {
                        "tools": sorted(_offered_tool_names(payload)),
                        "response_format": payload.get("response_format"),
                        "roles": [
                            m.get("role")
                            for m in payload.get("messages") or []
                            if isinstance(m, dict)
                        ][-4:],
                    }
                    with open(server.debug_path, "a", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(shape, ensure_ascii=False)[:4000] + "\n"
                        )
                delegation = tracing_task_message(payload)
                structured = (
                    _structured_response(payload) if delegation is None else None
                )
                if payload.get("stream") is False:
                    time.sleep(server.first_delay_s)
                    message = delegation or {
                        "role": "assistant",
                        "content": json.dumps(structured, ensure_ascii=False)
                        if structured is not None
                        else "受控依赖已收到请求。",
                    }
                    body = json.dumps(
                        {"choices": [{"message": message}], "usage": None}
                    ).encode()
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                time.sleep(server.first_delay_s)
                if delegation is not None:
                    calls = []
                    for index, call in enumerate(delegation["tool_calls"]):
                        calls.append(
                            {
                                "index": index,
                                "id": call["id"],
                                "type": "function",
                                "function": call["function"],
                            }
                        )
                    event = json.dumps(
                        {
                            "choices": [
                                {
                                    "delta": {
                                        "role": "assistant",
                                        "content": "",
                                        "tool_calls": calls,
                                    }
                                }
                            ]
                        }
                    )
                    self.wfile.write(f"data: {event}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return
                if structured is not None:
                    event = json.dumps(
                        {
                            "choices": [
                                {
                                    "delta": {
                                        "role": "assistant",
                                        "content": json.dumps(
                                            structured, ensure_ascii=False
                                        ),
                                    }
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                    self.wfile.write(f"data: {event}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    return
                pieces = ("受控", "依赖", "已收到请求。")
                for index, piece in enumerate(pieces):
                    delta = {"content": piece}
                    if index == 0:
                        delta["role"] = "assistant"
                    event = json.dumps({"choices": [{"delta": delta}]})
                    self.wfile.write(f"data: {event}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(server.chunk_interval_s)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

            def _mcp(self, raw: bytes) -> None:
                payload = json.loads(raw or b"{}")
                method = str(payload.get("method") or "")
                request_id = payload.get("id")
                if request_id is None:
                    # JSON-RPC notification (e.g. notifications/initialized).
                    self.send_response(202)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                if method == "initialize":
                    params = payload.get("params") or {}
                    result = {
                        "protocolVersion": params.get("protocolVersion")
                        or "2025-03-26",
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "controlled-mcp",
                            "version": "1.0",
                        },
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": name,
                                "description": "受控合成查询工具",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {},
                                    "additionalProperties": True,
                                },
                            }
                            for name in _TRACING_TOOL_NAMES
                        ]
                    }
                elif method == "tools/call":
                    server.mcp_tool_calls += 1
                    params = payload.get("params") or {}
                    name = str(params.get("name") or "")
                    result = {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    _mcp_tool_payload(name), ensure_ascii=False
                                ),
                            }
                        ],
                        "isError": False,
                    }
                else:
                    result = {}
                body = json.dumps(
                    {"jsonrpc": "2.0", "id": request_id, "result": result}
                ).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        """Return the loopback origin."""
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        """Start the background server."""
        self.thread.start()

    def stop(self) -> None:
        """Stop the background server."""
        self._httpd.shutdown()
        self.thread.join(timeout=2)
        self._httpd.server_close()
