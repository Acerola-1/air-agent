"""Data-source boundary for deterministic tracing subagents."""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.schemas import DataBundle, RuntimeScope, TracingRequest


class DataSourceUnavailable(RuntimeError):
    """The scoped data source cannot serve the frozen request."""


class TracingDataSource(Protocol):
    """Protocol implemented by fixture and scoped MCP data sources."""

    async def fetch(
        self,
        request: TracingRequest,
        *,
        scope: RuntimeScope,
        policy: AnalysisPolicy,
    ) -> DataBundle:
        """Fetch a snapshot for the frozen request; must not expand scope."""
        ...


class FixtureDataSource:
    """Controlled fixture source used by G0 tests and deterministic demos."""

    def __init__(self, bundle: DataBundle) -> None:
        """Store an immutable fixture bundle."""
        self._bundle = bundle

    async def fetch(
        self,
        request: TracingRequest,
        *,
        scope: RuntimeScope,
        policy: AnalysisPolicy,
    ) -> DataBundle:
        """Return the fixture only when it matches the frozen request target."""
        if self._bundle.target_station_code != request.target.station_code:
            raise DataSourceUnavailable("fixture 站点与冻结请求不一致")
        if self._bundle.region_code and request.target.region_code:
            if self._bundle.region_code != request.target.region_code:
                raise DataSourceUnavailable("fixture 区域与冻结请求不一致")
        return self._bundle


class UnavailableDataSource:
    """Fail-closed source for real MCP wiring that is not yet configured."""

    def __init__(self, reason: str = "mcp_source_not_configured") -> None:
        """Record the unavailable reason."""
        self.reason = reason

    async def fetch(
        self,
        request: TracingRequest,
        *,
        scope: RuntimeScope,
        policy: AnalysisPolicy,
    ) -> DataBundle:
        """Fail closed; never fabricate data."""
        raise DataSourceUnavailable(self.reason)


def load_fixture_bundle(path: Path) -> DataBundle:
    """Load and validate a JSON fixture bundle."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return DataBundle.model_validate(payload)


class ScopedMCPDataSource:
    """Scoped MCP adapter skeleton with dependency-intersection checks.

    Real tool names and response shapes vary by deployment, so the mapping and
    normalizer are explicit configuration.  The adapter validates the current
    capability snapshot **before** any executor call and refuses excluded
    dependencies without a network attempt.
    """

    def __init__(
        self,
        *,
        tool_map: dict[str, str],
        normalizer: Callable[[str, Any, TracingRequest], DataBundle],
        executor: Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[Any]]
        | None = None,
        allow_unscoped: bool = False,
    ) -> None:
        """Bind product-to-tool mapping and a scoped executor."""
        forbidden = ("trajectory", "pscf")
        for product, tool_id in tool_map.items():
            if any(word in tool_id.lower() for word in forbidden):
                raise ValueError(f"禁止装配轨迹/PSCF 工具: {product}={tool_id}")
        self._tool_map = dict(tool_map)
        self._normalizer = normalizer
        self._executor = executor
        self._allow_unscoped = allow_unscoped

    def _run_config(self) -> dict[str, Any]:
        try:
            from langgraph.config import get_config

            return dict(get_config() or {})
        except Exception:  # pragma: no cover - offline direct calls
            return {}

    def _check_dependencies(self, config: dict[str, Any]) -> None:
        configurable = config.get("configurable") or {}
        metadata = config.get("metadata") or {}
        mode = (
            configurable.get("capability_mcp_mode")
            or metadata.get("capability_mcp_mode")
            or "auto"
        )
        if mode != "allowlist":
            if self._allow_unscoped:
                return
            raise DataSourceUnavailable("capability_mcp_mode_not_allowlist")
        allowed = set(
            configurable.get("capability_tool_ids")
            or metadata.get("capability_tool_ids")
            or []
        )
        missing = [
            product
            for product, tool_id in self._tool_map.items()
            if tool_id not in allowed
        ]
        if missing:
            raise DataSourceUnavailable(
                "dependency_excluded:" + ",".join(sorted(missing))
            )

    async def fetch(
        self,
        request: TracingRequest,
        *,
        scope: RuntimeScope,
        policy: AnalysisPolicy,
    ) -> DataBundle:
        """Check all dependencies, then execute through the scoped executor."""
        config = self._run_config()
        self._check_dependencies(config)
        if self._executor is None:
            raise DataSourceUnavailable("mcp_source_not_configured")
        products: dict[str, Any] = {}
        args = {
            "station": request.target.station_code,
            "start": request.time_window.start.isoformat(),
            "end": request.time_window.end.isoformat(),
            "radius_km": request.neighbor_radius_km,
        }
        for product, tool_id in self._tool_map.items():
            attempts = 0
            while True:
                try:
                    products[product] = await self._executor(tool_id, args, config)
                    break
                except Exception as exc:  # noqa: BLE001 - classified below
                    attempts += 1
                    if (
                        attempts > policy.budget.max_tool_retries
                        or not _is_transient_source_error(exc)
                    ):
                        raise
        return self._normalizer("bundle", products, request)


class CachedDataSource:
    """Request-scoped snapshot cache shared by concurrent specialists.

    The cache key pins tenant/user/thread/graph/context/revision so a late
    result from another scope or revision cannot be reused.  A cache hit still
    performs the real-time identity guard before returning data.
    """

    def __init__(self, wrapped: TracingDataSource, *, max_entries: int = 16) -> None:
        """Wrap a source with a bounded request-scoped cache."""
        self._wrapped = wrapped
        self._max_entries = max_entries
        self._cache: OrderedDict[tuple[str, ...], DataBundle] = OrderedDict()
        self._locks: dict[tuple[str, ...], asyncio.Lock] = {}

    @staticmethod
    async def _authorize() -> None:
        from langgraph.config import get_config

        from common.identity.runtime import guard_runtime_call

        try:
            config = dict(get_config() or {})
        except RuntimeError:
            return
        await guard_runtime_call(config)

    async def fetch(
        self,
        request: TracingRequest,
        *,
        scope: RuntimeScope,
        policy: AnalysisPolicy,
    ) -> DataBundle:
        """Return one shared snapshot per trusted scope/request revision."""
        await self._authorize()
        key = (
            scope.tenant_id,
            scope.user_id,
            scope.thread_id,
            scope.graph_id,
            scope.context_version,
            scope.epoch,
            request.analysis_id,
            str(request.revision),
        )
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            bundle = await self._wrapped.fetch(request, scope=scope, policy=policy)
            self._cache[key] = bundle
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
            return bundle


def _is_transient_source_error(exc: BaseException) -> bool:
    """Retry only transport-like MCP errors, never authorization/validation."""
    text = f"{type(exc).__name__} {exc}".lower()
    if any(
        word in text
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
        word in text
        for word in ("timeout", "timed out", "connection", "temporarily", "503")
    )
