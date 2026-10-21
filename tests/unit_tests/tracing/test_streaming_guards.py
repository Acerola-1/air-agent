"""Authorization/deletion guards on the shared publisher and tracing sections."""

from __future__ import annotations

import pytest
from fixtures import build_bundle, make_request, make_scope

from common.output_streaming.contract import EVENT_ABORTED, EVENT_DELTA, EVENT_DONE
from common.output_streaming.publisher import (
    OutputAborted,
    OutputPublisher,
    runtime_authorization_probe,
)
from intelligent_tracing.claims import fallback_plan
from intelligent_tracing.evidence import EvidenceRegistry
from intelligent_tracing.gate import ReportGate
from intelligent_tracing.orchestrator import analyze_bundle
from intelligent_tracing.policy import DEFAULT_POLICY
from intelligent_tracing.report_store import InMemoryReportStore
from intelligent_tracing.section_publishing import publish_frozen_plan


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [event["type"] for event in self.events]


@pytest.mark.anyio
async def test_revocation_after_first_delta_stops_further_publication():
    recorder = Recorder()
    calls = 0

    async def probe() -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    publisher = OutputPublisher(
        {"configurable": {"thread_id": "thread-1"}},
        graph_id="basic-qa",
        writer=recorder,
        authorization_probe=probe,
    )
    publisher.start()
    await publisher.publish("第一段。")
    with pytest.raises(OutputAborted):
        await publisher.publish("第二段。")
    assert EVENT_ABORTED in recorder.types()
    assert EVENT_DONE not in recorder.types()
    assert sum(1 for event in recorder.events if event["type"] == EVENT_DELTA) == 1


@pytest.mark.anyio
async def test_runtime_probe_missing_ledger_context_is_controlled_only():
    async def _probe():
        await runtime_authorization_probe({"configurable": {}})()

    # No identity and no ledger context: the controlled/unit path stays open.
    await _probe()


@pytest.mark.anyio
async def test_section_publication_aborts_on_revocation_between_sections():
    request = make_request()
    scope = make_scope()
    registry = EvidenceRegistry()
    analysis = analyze_bundle(
        request,
        build_bundle(scenario="transport"),
        registry=registry,
        scope=scope,
        policy=DEFAULT_POLICY,
    )
    gate = ReportGate(registry=registry, policy=DEFAULT_POLICY)
    frozen, errors = gate.precheck(
        fallback_plan(analysis.allowlist),
        analysis.allowlist,
        request,
        scope,
        report_id="rep_guard_test",
    )
    assert frozen is not None and not errors
    recorder = Recorder()
    calls = 0

    async def probe() -> bool:
        nonlocal calls
        calls += 1
        return calls <= 1

    import intelligent_tracing.section_publishing as module

    original = module.runtime_authorization_probe
    module.runtime_authorization_probe = lambda config: probe
    try:
        with pytest.raises(Exception):
            await publish_frozen_plan(
                gate=gate,
                report_store=InMemoryReportStore(),
                frozen=frozen,
                config={"configurable": {"thread_id": "thread-a"}},
                writer=recorder,
            )
    finally:
        module.runtime_authorization_probe = original
    assert EVENT_DONE not in recorder.types()
    assert recorder.types()[-1] == EVENT_ABORTED
