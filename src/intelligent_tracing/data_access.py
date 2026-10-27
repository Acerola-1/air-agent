"""Authorized MCP data access for tracing sources.

所有取数只经 `common.mcp_scoped.execute_mcp_tool`：每次调用重新解析身份、
业务许可、连接修订与 `approved_query_tools`，再按业务工具子集过滤。这里
不读浏览器参数、不直接用 HTTP、不保存连接凭据，也不接受通用 executor 绕过
授权层（测试用注入 executor 仅替换传输，不替换授权语义）。
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from common.identity.runtime import RuntimeIdentityError, identity_from_config
from common.mcp_authorization import McpAuthorizationError
from intelligent_tracing.data_windows import plan_station_windows, within_window
from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.runtime import (
    BusinessBinding,
    TracingScopeError,
    business_binding_from_config,
)
from intelligent_tracing.schemas import (
    DataBundle,
    GridCell,
    HourlyObservation,
    HourlyWeather,
    RuntimeScope,
    StationMeta,
    TimeWindow,
    TracingRequest,
)
from intelligent_tracing.sources import DataSourceUnavailable

SERVER_KEY = "ipp"
STATION_TYPE = "S-10"  # 国控站
TIMEZONE = "Asia/Shanghai"
_TZ = ZoneInfo(TIMEZONE)

# 已核实（2026-10-20 真实 tools/list + 最小样本）的产品 → 工具映射。
# 每个产品有独立参数构造与归一化，禁止共用万能参数。
TRACING_PRODUCT_TOOLS: dict[str, str] = {
    "station_catalog": "mcp_station_base_air_station_list",
    "station_series": "mcp_station_common_air_data",
    "neighbor_series": "mcp_station_common_air_data",
    "nwp": "mcp_weather_forecast_hourly_history",
    "grid_series": "mcp_grid_common_air_data",
    "grid_geometry": "mcp_grid_base_grid_info_by_codes",
}

FORBIDDEN_TOOL_WORDS = ("trajectory", "pscf")

# 污染物列名与单位（CO 按平台口径 mg/m3，其余 ug/m3）。
POLLUTANT_FIELDS: dict[str, str] = {
    "pm2.5": "pm25",
    "pm10": "pm10",
    "o3": "o3",
    "no2": "no2",
    "so2": "so2",
    "co": "co",
}
POLLUTANT_UNITS: dict[str, str] = {
    "pm2.5": "ug/m3",
    "pm10": "ug/m3",
    "o3": "ug/m3",
    "no2": "ug/m3",
    "so2": "ug/m3",
    "co": "mg/m3",
}


ToolExecutor = Callable[[str, str, dict[str, Any], dict[str, Any]], Awaitable[Any]]


class MCPToolError(RuntimeError):
    """归一化后的 MCP 失败：区分瞬时错误与权限/参数错误."""

    def __init__(self, tool_id: str, message: str, *, transient: bool = False) -> None:
        """Record the tool and whether a retry is allowed."""
        super().__init__(f"{tool_id}: {message}")
        self.tool_id = tool_id
        self.transient = transient


async def default_executor(
    server_key: str, tool_name: str, args: dict[str, Any], config: dict[str, Any]
) -> Any:
    """唯一真实执行入口：统一授权执行层."""
    from common.mcp_scoped import execute_mcp_tool

    return await execute_mcp_tool(server_key, tool_name, args, config)


def _run_config() -> dict[str, Any]:
    try:
        from langgraph.config import get_config

        return dict(get_config() or {})
    except Exception:  # pragma: no cover - offline direct calls
        return {}


def _is_transient_message(text: str) -> bool:
    lowered = text.lower()
    if any(
        word in lowered
        for word in (
            "authorization",
            "permission",
            "validation",
            "forbidden",
            "401",
            "403",
        )
    ):
        return False
    return any(
        word in lowered
        for word in ("timeout", "timed out", "connection", "temporarily", "503", "502")
    )


def _parse_payload(tool_id: str, result: Any) -> Any:
    """Parse MCP text/JSON and reject business-level failure payloads."""
    is_error = bool(getattr(result, "is_error", False))
    payload: Any = result
    if isinstance(result, str):
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            raise MCPToolError(tool_id, "返回非 JSON 文本") from None
    if isinstance(payload, dict):
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else payload.get("code")
        if str(code).lower() in {
            "401",
            "403",
            "unauthenticated",
            "forbidden",
            "unauthorized",
        }:
            raise TracingScopeError("上游鉴权失败，停止后续取数")
    if is_error:
        raise MCPToolError(tool_id, "工具协议返回失败")
    if isinstance(payload, dict):
        if payload.get("success") is False:
            message = str(payload.get("msg") or "业务返回失败")
            raise MCPToolError(
                tool_id, message, transient=_is_transient_message(message)
            )
        code = payload.get("code")
        if code not in (None, 200):
            message = str(payload.get("msg") or f"业务返回码 {code}")
            raise MCPToolError(
                tool_id, message, transient=_is_transient_message(message)
            )
    return payload


async def _call_tool(
    executor: ToolExecutor,
    config: dict[str, Any],
    tool_id: str,
    args: dict[str, Any],
    *,
    max_retries: int = 1,
) -> Any:
    """Call one tool, retrying only classified transient failures."""
    attempts = 0
    while True:
        try:
            result = await executor(SERVER_KEY, tool_id, args, config)
            return _parse_payload(tool_id, result)
        except (TracingScopeError, RuntimeIdentityError, McpAuthorizationError):
            raise
        except MCPToolError as exc:
            attempts += 1
            if attempts > max_retries or not exc.transient:
                raise
            await asyncio.sleep(0.2 * attempts)
        except Exception as exc:  # noqa: BLE001 - normalized at the boundary
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                raise TracingScopeError("上游鉴权失败，停止后续取数") from exc
            message = f"{type(exc).__name__}: {exc}"
            attempts += 1
            transient = _is_transient_message(message)
            if attempts > max_retries or not transient:
                raise MCPToolError(tool_id, message, transient=transient) from exc
            await asyncio.sleep(0.2 * attempts)


def _rows(payload: Any) -> list[dict[str, Any]]:
    """Flatten common ``{"data": ...}`` envelopes, including list-of-lists."""
    node = payload.get("data") if isinstance(payload, dict) else payload
    if node is None:
        return []
    if isinstance(node, dict):
        for key in ("rows", "list", "result"):
            if isinstance(node.get(key), list):
                node = node[key]
                break
        else:
            return [node]
    if not isinstance(node, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in node:
        if isinstance(item, list):
            rows.extend(row for row in item if isinstance(row, dict))
        elif isinstance(item, dict):
            rows.append(item)
    return rows


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = str(value).strip()
    if text in {"", "-", "--", "null", "None", "N/A"}:
        return None
    try:
        number = float(text)
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def _dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
        return (
            parsed.replace(tzinfo=_TZ)
            if parsed.tzinfo is None
            else parsed.astimezone(_TZ)
        )
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=_TZ)
        except ValueError:
            continue
    return None


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def _same_binding(left: BusinessBinding, right: BusinessBinding) -> bool:
    return left == right


def _require_business_config(config: dict[str, Any]) -> BusinessBinding:
    if identity_from_config(config) is None:
        raise TracingScopeError("缺少已认证运行身份，拒绝 MCP 数据访问")
    return business_binding_from_config(config)


def _verify_scope(
    config: dict[str, Any], request: TracingRequest, scope: RuntimeScope
) -> None:
    """Re-check trusted config against the frozen request/scope before fetch."""
    binding = _require_business_config(config)
    request_binding = BusinessBinding(
        business_code=request.business_code,
        business_policy_version=request.business_policy_version,
        business_permission=request.business_permission,
        graph_id=request.graph_id,
    )
    scope_binding = BusinessBinding(
        business_code=scope.business_code,
        business_policy_version=scope.business_policy_version,
        business_permission=scope.business_permission,
        graph_id=scope.graph_id,
    )
    if not (
        _same_binding(binding, request_binding)
        and _same_binding(binding, scope_binding)
    ):
        raise TracingScopeError("请求/作用域业务字段与可信业务快照不一致")


# --------------------------------------------------------------------- 站点目录


class StationCatalog(Protocol):
    """站点目录提供者：只提供候选，不构成授权."""

    async def list_stations(
        self, *, config: dict[str, Any], region_name: str
    ) -> tuple[StationMeta, ...]:
        """Return authorized catalogue rows for one region name."""
        ...


class StaticStationCatalog:
    """Controlled catalogue for G0 tests and fixture runs."""

    def __init__(self, stations: tuple[StationMeta, ...]) -> None:
        """Store the immutable fixture catalogue."""
        self._stations = stations

    async def list_stations(
        self, *, config: dict[str, Any], region_name: str
    ) -> tuple[StationMeta, ...]:
        """Return the fixture catalogue, ignoring the region (tests only)."""
        return self._stations


class StationCatalogProvider:
    """Authorized station catalogue served through the scoped executor.

    目录只用于消歧和几何计算；每次缓存命中前仍复查可信身份与业务快照，
    撤权后不会复用旧目录。
    """

    def __init__(
        self,
        *,
        executor: ToolExecutor | None = None,
        station_type: str = STATION_TYPE,
        cache_ttl_seconds: float = 300.0,
        max_entries: int = 32,
    ) -> None:
        """Bind the authorized executor and a bounded cache."""
        self._executor = executor or default_executor
        self._station_type = station_type
        self._ttl = cache_ttl_seconds
        self._max_entries = max_entries
        self._cache: OrderedDict[
            tuple[str, str, str, str], tuple[float, tuple[StationMeta, ...]]
        ] = OrderedDict()

    async def list_stations(
        self, *, config: dict[str, Any], region_name: str
    ) -> tuple[StationMeta, ...]:
        """Return catalogue rows; fail closed without trusted business scope."""
        region = str(region_name or "").strip()
        if not region:
            raise DataSourceUnavailable("缺少行政区名称，无法读取授权站点目录")
        identity = identity_from_config(config)
        if identity is None:
            raise TracingScopeError("缺少已认证运行身份，拒绝读取站点目录")
        binding = _require_business_config(config)
        key = (
            identity.tenant_id,
            binding.business_code,
            binding.business_policy_version,
            region,
        )
        cached = self._cache.get(key)
        if cached is not None and time.monotonic() - cached[0] <= self._ttl:
            self._cache.move_to_end(key)
            return cached[1]
        try:
            payload = await _call_tool(
                self._executor,
                config,
                TRACING_PRODUCT_TOOLS["station_catalog"],
                {"region": region, "stationType": self._station_type},
            )
        except MCPToolError as exc:
            raise DataSourceUnavailable("station_catalog_unavailable") from exc
        stations = tuple(self._normalize(payload, region))
        if not stations:
            raise DataSourceUnavailable("授权站点目录为空")
        self._cache[key] = (time.monotonic(), stations)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)
        return stations

    @staticmethod
    def _normalize(payload: Any, region_name: str) -> list[StationMeta]:
        stations: list[StationMeta] = []
        for row in _rows(payload):
            code = str(row.get("stationKey") or "").strip()
            latitude = _num(row.get("latitude"))
            longitude = _num(row.get("longitude"))
            if not code or latitude is None or longitude is None:
                continue
            stations.append(
                StationMeta(
                    station_code=code,
                    name=str(row.get("name") or "").strip(),
                    region_code=str(row.get("regionKey") or row.get("region") or ""),
                    region_name=str(row.get("regionName") or region_name),
                    latitude=latitude,
                    longitude=longitude,
                    distance_km=_num(row.get("distance")) or 0.0,
                    station_type=str(row.get("stationType") or ""),
                )
            )
        return stations

    def clear(self) -> None:
        """Drop cached catalogues (tests/scope changes)."""
        self._cache.clear()


# --------------------------------------------------------------------- 数据源


class BusinessMCPDataSource:
    """真实业务 MCP 数据源：每个产品独立参数与归一化."""

    def __init__(
        self,
        *,
        station_catalog: StationCatalog | None = None,
        executor: ToolExecutor | None = None,
        station_type: str = STATION_TYPE,
        max_neighbors: int = 5,
        max_grid_cells: int = 24,
    ) -> None:
        """Bind catalogue, authorized executor and bounded fan-out."""
        self._catalog = station_catalog or StationCatalogProvider(
            executor=executor, station_type=station_type
        )
        self._executor = executor or default_executor
        self._station_type = station_type
        self._max_neighbors = max_neighbors
        self._max_grid_cells = max_grid_cells
        for tool_id in TRACING_PRODUCT_TOOLS.values():
            if any(word in tool_id.lower() for word in FORBIDDEN_TOOL_WORDS):
                raise ValueError(f"禁止装配轨迹/PSCF 工具: {tool_id}")

    async def fetch(
        self,
        request: TracingRequest,
        *,
        scope: RuntimeScope,
        policy: AnalysisPolicy,
    ) -> DataBundle:
        """Fetch one scoped snapshot; never broaden the frozen request."""
        config = _run_config()
        _verify_scope(config, request, scope)
        windows = plan_station_windows(request, policy)
        retries = int(policy.budget.max_tool_retries)
        region = request.target.region_name
        missing: list[str] = []
        source_tools: dict[str, str] = {}

        stations = await self._catalog.list_stations(config=config, region_name=region)
        source_tools["station_catalog"] = TRACING_PRODUCT_TOOLS["station_catalog"]

        target = request.target
        try:
            target_obs, tool = await self._station_series(
                config,
                request,
                station_code=target.station_code,
                region=region,
                max_retries=retries,
            )
        except MCPToolError as exc:
            raise DataSourceUnavailable("station_series_unavailable") from exc
        source_tools["station_series"] = tool
        if not target_obs:
            raise DataSourceUnavailable("station_series_empty")

        history = list(target_obs)
        for window in windows.auxiliary:
            try:
                rows, _ = await self._station_series(
                    config,
                    request,
                    station_code=target.station_code,
                    region=region,
                    max_retries=retries,
                    window=window,
                )
                history.extend(rows)
            except MCPToolError:
                missing.append("station_history_unavailable")
        baseline_obs = within_window(tuple(history), windows.baseline)
        context_obs = within_window(tuple(history), windows.context)
        source_tools["baseline_series"] = tool
        source_tools["station_context_series"] = tool
        if not baseline_obs:
            missing.append("baseline")
        if windows.context and not context_obs:
            missing.append("evaluation_context")

        neighbors: dict[str, tuple[HourlyObservation, ...]] = {}
        selected = sorted(
            [
                item
                for item in stations
                if item.station_code != target.station_code
                and _distance_km(
                    target.latitude, target.longitude, item.latitude, item.longitude
                )
                <= request.neighbor_radius_km
            ],
            key=lambda item: (
                _distance_km(
                    target.latitude, target.longitude, item.latitude, item.longitude
                ),
                item.station_code,
            ),
        )
        if len(selected) > self._max_neighbors:
            missing.append("neighbors_truncated")
        for item in selected[: self._max_neighbors]:
            distance = _distance_km(
                target.latitude, target.longitude, item.latitude, item.longitude
            )
            if distance > request.neighbor_radius_km:
                continue
            try:
                rows, _ = await self._station_series(
                    config,
                    request,
                    station_code=item.station_code,
                    region=region,
                    max_retries=0,
                )
            except (MCPToolError, DataSourceUnavailable):
                missing.append(f"neighbor:{item.station_code}")
                continue
            if rows:
                neighbors[item.station_code] = rows
            else:
                missing.append(f"neighbor:{item.station_code}")
        if not neighbors:
            missing.append("neighbors")
        source_tools["neighbor_series"] = TRACING_PRODUCT_TOOLS["neighbor_series"]

        nwp: tuple[HourlyWeather, ...] = ()
        try:
            payload = await _call_tool(
                self._executor,
                config,
                TRACING_PRODUCT_TOOLS["nwp"],
                {
                    "region": region,
                    "sTime": request.time_window.start.astimezone(_TZ).strftime(
                        "%Y-%m-%d %H:00"
                    ),
                    "eTime": request.time_window.end.astimezone(_TZ).strftime(
                        "%Y-%m-%d %H:00"
                    ),
                },
                max_retries=retries,
            )
            nwp = tuple(
                row
                for row in self._normalize_weather(payload)
                if request.time_window.start <= row.timestamp < request.time_window.end
            )
            if not nwp:
                missing.append("nwp")
            source_tools["nwp"] = TRACING_PRODUCT_TOOLS["nwp"]
        except (MCPToolError, DataSourceUnavailable):
            missing.append("nwp")

        grid_cells: tuple[GridCell, ...] = ()
        try:
            grid_cells = await self._grid_cells(
                config, request, region=region, max_retries=retries
            )
            if not grid_cells:
                missing.append("grid")
            else:
                missing.append("grid_bounded_snapshot")
                if any(cell.row is None or cell.col is None for cell in grid_cells):
                    missing.append("grid_topology_unknown")
                source_tools["grid_series"] = TRACING_PRODUCT_TOOLS["grid_series"]
                source_tools["grid_geometry"] = TRACING_PRODUCT_TOOLS["grid_geometry"]
        except (MCPToolError, DataSourceUnavailable):
            missing.append("grid")

        snapshot_parts = [
            target.station_code,
            request.analysis_id,
            str(request.revision),
            str(request.request_digest),
        ]
        return DataBundle(
            snapshot_id="mcp_" + "-".join(part for part in snapshot_parts if part),
            target_station_code=target.station_code,
            region_code=target.region_code,
            observations=target_obs,
            baseline_observations=baseline_obs,
            context_observations=context_obs,
            neighbors=neighbors,
            stations=stations,
            grid_cells=grid_cells,
            nwp=nwp,
            source_type="mcp",
            missing=tuple(sorted(set(missing))),
            source_tools=source_tools,
        )

    async def _station_series(
        self,
        config: dict[str, Any],
        request: TracingRequest,
        *,
        station_code: str,
        region: str,
        max_retries: int,
        window: TimeWindow | None = None,
    ) -> tuple[tuple[HourlyObservation, ...], str]:
        window = window or request.time_window
        field = POLLUTANT_FIELDS.get(request.pollutant.lower())
        if field is None:
            raise DataSourceUnavailable(f"未登记污染物: {request.pollutant}")
        tool_id = TRACING_PRODUCT_TOOLS["station_series"]
        payload = await _call_tool(
            self._executor,
            config,
            tool_id,
            {
                "region": region,
                "station": station_code,
                "type": "hourly",
                "sTime": window.start.astimezone(_TZ).strftime("%Y-%m-%d %H:00:00"),
                "eTime": window.end.astimezone(_TZ).strftime("%Y-%m-%d %H:00:00"),
            },
            max_retries=max_retries,
        )
        unit = POLLUTANT_UNITS[request.pollutant.lower()]
        rows: list[HourlyObservation] = []
        for row in _rows(payload):
            station = str(row.get("stationKey") or "").strip()
            if station and station != station_code:
                continue
            timestamp = _dt(row.get("updateTime"))
            if timestamp is None or not window.start <= timestamp < window.end:
                continue
            rows.append(
                HourlyObservation(
                    station_code=station_code,
                    timestamp=timestamp,
                    value=_num(row.get(field)),
                    unit=unit,
                    source="mcp",
                )
            )
        rows.sort(key=lambda item: item.timestamp)
        return tuple(rows), tool_id

    @staticmethod
    def _normalize_weather(payload: Any) -> tuple[HourlyWeather, ...]:
        rows: list[HourlyWeather] = []
        for row in _rows(payload):
            timestamp = _dt(row.get("updateTime"))
            if timestamp is None:
                continue
            run_time = _dt(row.get("runTime"))
            lead = _num(row.get("leadHours"))
            lead_hours = (
                int(lead)
                if lead is not None and lead >= 0 and lead.is_integer()
                else None
            )
            flags: list[str] = []
            if (row.get("runTime") is not None and run_time is None) or (
                row.get("leadHours") is not None and lead_hours is None
            ):
                flags.append("nwp_time_inconsistent")
            if run_time is not None and (
                run_time > timestamp
                or (
                    lead_hours is not None
                    and (timestamp - run_time).total_seconds() != lead_hours * 3600
                )
            ):
                flags.append("nwp_time_inconsistent")
            if flags:
                run_time = None
                lead_hours = None
            wind_speed = _num(row.get("windSpeed"))
            wind_direction = _num(row.get("windDirection"))
            u = v = None
            if wind_speed is not None and wind_direction is not None:
                radians = math.radians(wind_direction)
                u = -wind_speed * math.sin(radians)
                v = -wind_speed * math.cos(radians)
            rows.append(
                HourlyWeather(
                    timestamp=timestamp,
                    wind_speed=wind_speed,
                    wind_direction=wind_direction,
                    u=u,
                    v=v,
                    pblh=_num(row.get("pblh")),
                    humidity=_num(row.get("humidity")),
                    pressure=_num(row.get("pressure")),
                    temperature=_num(row.get("temperature")),
                    source_type="nwp",
                    run_time=run_time,
                    lead_hours=lead_hours,
                    qc_flags=tuple(flags),
                )
            )
        rows.sort(key=lambda item: item.timestamp)
        return tuple(rows)

    async def _grid_cells(
        self,
        config: dict[str, Any],
        request: TracingRequest,
        *,
        region: str,
        max_retries: int,
    ) -> tuple[GridCell, ...]:
        index = POLLUTANT_FIELDS.get(request.pollutant.lower())
        if index is None:
            return ()
        payload = await _call_tool(
            self._executor,
            config,
            TRACING_PRODUCT_TOOLS["grid_series"],
            {
                "region": region,
                "type": "hourly",
                "index": index,
                "sTime": request.time_window.start.astimezone(_TZ).strftime(
                    "%Y-%m-%d %H:00:00"
                ),
                "eTime": request.time_window.end.astimezone(_TZ).strftime(
                    "%Y-%m-%d %H:00:00"
                ),
            },
            max_retries=max_retries,
        )
        frames: dict[datetime, dict[str, float]] = {}
        for row in _rows(payload):
            name = str(row.get("name") or "").strip()
            value = _num(row.get("value"))
            timestamp = _dt(row.get("updateTime"))
            if not name or value is None or timestamp is None:
                continue
            if not request.time_window.start <= timestamp < request.time_window.end:
                continue
            frame = frames.setdefault(timestamp, {})
            # Conflicting duplicates have no unique value and are excluded below.
            if name in frame and frame[name] != value:
                frame[name] = float("nan")
            else:
                frame[name] = value
        if not frames:
            return ()
        timestamp = max(frames)
        ranked = [
            (name, value)
            for name, value in sorted(frames[timestamp].items())
            if math.isfinite(value)
        ][: self._max_grid_cells]
        if not ranked:
            return ()
        names = ",".join(name for name, _ in ranked)
        geometry_payload = await _call_tool(
            self._executor,
            config,
            TRACING_PRODUCT_TOOLS["grid_geometry"],
            {"region": region, "name": names},
            max_retries=max_retries,
        )
        geometry: dict[str, dict[str, Any]] = {}
        for row in _rows(geometry_payload):
            name = str(row.get("name") or "").strip()
            if name:
                geometry[name] = row
        cells: list[GridCell] = []
        for name, value in ranked:
            row = geometry.get(name)
            if row is None:
                continue
            latitude = _num(row.get("latitude"))
            longitude = _num(row.get("longitude"))
            if latitude is None or longitude is None:
                continue
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                continue
            if (
                _distance_km(
                    request.target.latitude,
                    request.target.longitude,
                    latitude,
                    longitude,
                )
                > request.neighbor_radius_km
            ):
                continue
            grid_row, grid_col = _num(row.get("row")), _num(row.get("col"))
            cells.append(
                GridCell(
                    cell_id=name,
                    row=int(grid_row)
                    if grid_row is not None and grid_row.is_integer()
                    else None,
                    col=int(grid_col)
                    if grid_col is not None and grid_col.is_integer()
                    else None,
                    latitude=latitude,
                    longitude=longitude,
                    value=value,
                    observed_at=timestamp,
                    source_relation="unknown",
                )
            )
        return tuple(cells)


class TracingDataSourceGuard:
    """Shared auth re-check for request-scoped caches."""

    @staticmethod
    async def recheck(config: dict[str, Any]) -> None:
        """Revalidate identity and business snapshot from trusted config."""
        from common.identity.runtime import guard_runtime_call

        await guard_runtime_call(config)
        _require_business_config(config)
