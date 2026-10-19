"""Request freezing, station disambiguation and evidence scope tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fixtures import (
    END,
    START,
    make_request,
    make_scope,
)

from intelligent_tracing.evidence import EvidenceRegistry, EvidenceRegistryError
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.request import (
    RequestValidationError,
    resolve_station,
    validate_and_freeze_request,
)
from intelligent_tracing.schemas import StationMeta


def _catalog() -> tuple[StationMeta, ...]:
    return (
        StationMeta(
            station_code="S001",
            name="同名站",
            latitude=34.75,
            longitude=113.62,
            distance_km=0,
        ),
        StationMeta(
            station_code="S002",
            name="同名站",
            latitude=34.70,
            longitude=113.60,
            distance_km=6,
        ),
    )


def test_station_name_ambiguity_returns_candidates_without_query():
    resolution = resolve_station(station_name="同名站", catalog=_catalog())
    assert resolution.ambiguous is True
    assert {item.station_code for item in resolution.candidates} == {"S001", "S002"}
    assert resolution.target is None


def test_freeze_binds_capability_snapshot_and_digest():
    request = make_request()
    config = {
        "configurable": {
            "capability_selection_id": "sel-001",
            "capability_selection_digest": "sha256:abc",
        }
    }
    frozen = validate_and_freeze_request(request, policy=DEFAULT_POLICY, config=config)
    assert frozen.request_digest == request.canonical_digest()
    assert frozen.request_digest


def test_forged_capability_snapshot_is_rejected():
    request = make_request()
    config = {"configurable": {"capability_selection_id": "sel-other"}}
    with pytest.raises(RequestValidationError, match="能力选择引用"):
        validate_and_freeze_request(request, policy=DEFAULT_POLICY, config=config)


def test_baseline_must_precede_target_window():
    request = make_request()
    bad = request.model_copy(
        update={
            "baseline_window": request.time_window.model_copy(
                update={"start": START + timedelta(hours=1)}
            )
        }
    )
    with pytest.raises(RequestValidationError, match="基线窗口"):
        validate_and_freeze_request(
            bad,
            policy=DEFAULT_POLICY,
            config={"configurable": {"capability_selection_id": "sel-001"}},
        )


def test_evidence_registry_rejects_cross_scope_and_old_revision():
    request = make_request()
    scope = make_scope()
    registry = EvidenceRegistry()
    record = registry.register(
        scope=scope,
        analysis_id=request.analysis_id,
        revision=request.revision,
        tool_id="fixture",
        parameters={"station": "S001"},
        source_type="fixture",
        product="station_series",
        fetched_at=datetime.now(UTC),
        valid_from=START,
        valid_to=END,
        unit="ug/m3",
        algorithm_version="v1",
        analysis_policy_version=DEFAULT_POLICY.version,
        raw_payload={"rows": [1, 2, 3]},
        sample_count=3,
    )
    assert (
        registry.get(
            record.evidence_id,
            scope=scope,
            analysis_id=request.analysis_id,
            revision=request.revision,
        ).product
        == "station_series"
    )
    with pytest.raises(EvidenceRegistryError):
        registry.get(
            record.evidence_id,
            scope=make_scope(context_version="cv-2"),
            analysis_id=request.analysis_id,
            revision=request.revision,
        )
    with pytest.raises(EvidenceRegistryError):
        registry.get(
            record.evidence_id,
            scope=scope,
            analysis_id=request.analysis_id,
            revision=2,
        )


@pytest.mark.anyio
async def test_freeze_writes_scope_from_trusted_config_only():
    from types import SimpleNamespace

    from fixtures import END, START, build_bundle
    from langgraph.types import Command

    from intelligent_tracing.tools import FreezeRequestInput, build_freeze_request_tool

    tool = build_freeze_request_tool(
        station_catalog=build_bundle().stations,
        policy=DEFAULT_POLICY,
    )
    runtime = SimpleNamespace(
        config={
            "configurable": {
                "thread_id": "thread-trusted",
                "execution_epoch": "epoch-trusted",
                "run_id": "run-trusted",
                "capability_selection_id": "sel-001",
                "langgraph_auth_user": {
                    "identity": "user-trusted",
                    "tenant_id": "tenant-trusted",
                    "membership_id": "membership-trusted",
                    "session_id": "session-trusted",
                    "session_ctx_version": 7,
                },
            }
        }
    )
    candidate = FreezeRequestInput(
        station_code="S001",
        start=START,
        end=END,
        analysis_id="analysis-trusted",
        revision=3,
        capability_snapshot_ref="sel-001",
        neighbor_radius_km=10,
    )
    result = await tool.coroutine(  # type: ignore[union-attr]
        runtime, **candidate.model_dump()
    )
    assert isinstance(result, Command)
    scope = result.update["scope"]
    request = result.update["request"]
    assert scope.tenant_id == "tenant-trusted"
    assert scope.user_id == "user-trusted"
    assert scope.thread_id == "thread-trusted"
    assert scope.context_version == "7"
    assert scope.epoch == "epoch-trusted"
    assert request.analysis_id == "analysis-trusted"
    assert request.revision == 3


@pytest.mark.anyio
async def test_freeze_fails_closed_without_trusted_identity():
    from types import SimpleNamespace

    from fixtures import END, START, build_bundle

    from intelligent_tracing.tools import FreezeRequestInput, build_freeze_request_tool

    tool = build_freeze_request_tool(
        station_catalog=build_bundle().stations,
        policy=DEFAULT_POLICY,
    )
    runtime = SimpleNamespace(
        config={
            "configurable": {
                "thread_id": "thread-a",
                "capability_selection_id": "sel-001",
            }
        }
    )
    candidate = FreezeRequestInput(
        station_code="S001",
        start=START,
        end=END,
        analysis_id="analysis-a",
        capability_snapshot_ref="sel-001",
        neighbor_radius_km=10,
    )
    result = await tool.coroutine(  # type: ignore[union-attr]
        runtime, **candidate.model_dump()
    )
    assert result["status"] == "invalid"
    assert "身份" in result["message"]


@pytest.mark.anyio
async def test_freeze_requires_radius_clarification():
    from types import SimpleNamespace

    from fixtures import END, START, build_bundle

    from intelligent_tracing.tools import FreezeRequestInput, build_freeze_request_tool

    tool = build_freeze_request_tool(
        station_catalog=build_bundle().stations,
        policy=DEFAULT_POLICY,
        allow_missing_capability=True,
    )
    runtime = SimpleNamespace(
        config={
            "configurable": {
                "thread_id": "thread-a",
                "langgraph_auth_user": {
                    "identity": "u",
                    "tenant_id": "t",
                    "membership_id": "m",
                    "session_id": "s",
                    "session_ctx_version": 1,
                },
            }
        }
    )
    candidate = FreezeRequestInput(
        station_code="S001",
        start=START,
        end=END,
        analysis_id="analysis-a",
        neighbor_radius_km=None,
    )
    result = await tool.coroutine(  # type: ignore[union-attr]
        runtime, **candidate.model_dump()
    )
    assert result["status"] == "needs_clarification"
    assert "neighbor_radius_km" in result["missing"]


@pytest.mark.anyio
async def test_freeze_rejects_missing_capability_snapshot_by_default():
    from types import SimpleNamespace

    from fixtures import END, START, build_bundle

    from intelligent_tracing.tools import FreezeRequestInput, build_freeze_request_tool

    tool = build_freeze_request_tool(
        station_catalog=build_bundle().stations,
        policy=DEFAULT_POLICY,
    )
    candidate = FreezeRequestInput(
        station_code="S001",
        start=START,
        end=END,
        analysis_id="analysis-a",
        capability_snapshot_ref="",
        neighbor_radius_km=10,
    )
    runtime = SimpleNamespace(
        config={
            "configurable": {
                "thread_id": "thread-a",
                "langgraph_auth_user": {
                    "identity": "u",
                    "tenant_id": "t",
                    "membership_id": "m",
                    "session_id": "s",
                    "session_ctx_version": 1,
                },
            }
        }
    )
    result = await tool.coroutine(  # type: ignore[union-attr]
        runtime, **candidate.model_dump()
    )
    assert result["status"] == "invalid"
    assert "capability_snapshot_ref" in result["message"]


def test_region_filter_uses_trusted_catalog_field():
    from fixtures import build_bundle

    catalog = build_bundle().stations
    resolved = resolve_station(
        station_name="目标站", region_code="410100", catalog=catalog
    )
    assert resolved.target is not None
    assert resolved.target.region_code == "410100"
    with pytest.raises(RequestValidationError, match="不属于"):
        resolve_station(station_code="S001", region_code="999999", catalog=catalog)
    with pytest.raises(RequestValidationError, match="未找到"):
        resolve_station(station_name="目标站", region_code="999999", catalog=catalog)
