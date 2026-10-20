"""Data-source boundary for deterministic tracing subagents."""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from pathlib import Path
from typing import Protocol

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
    async def _authorize(request: TracingRequest, scope: RuntimeScope) -> None:
        """Recheck trusted identity/business before a cache hit is returned."""
        from langgraph.config import get_config

        from common.identity.runtime import guard_runtime_call
        from intelligent_tracing.runtime import (
            TracingScopeError,
            business_binding_from_config,
        )

        try:
            config = dict(get_config() or {})
        except RuntimeError:
            return
        await guard_runtime_call(config)
        binding = business_binding_from_config(config)
        if (
            binding.business_code != scope.business_code
            or binding.business_policy_version != scope.business_policy_version
            or binding.business_permission != scope.business_permission
            or binding.graph_id != scope.graph_id
            or request.business_code != scope.business_code
            or request.business_policy_version != scope.business_policy_version
            or request.graph_id != scope.graph_id
        ):
            raise TracingScopeError("缓存作用域与可信业务快照不一致")

    async def fetch(
        self,
        request: TracingRequest,
        *,
        scope: RuntimeScope,
        policy: AnalysisPolicy,
    ) -> DataBundle:
        """Return one shared snapshot per trusted scope/request revision."""
        await self._authorize(request, scope)
        key = (
            scope.tenant_id,
            scope.user_id,
            scope.thread_id,
            scope.graph_id,
            scope.context_version,
            scope.epoch,
            scope.business_code,
            scope.business_policy_version,
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
