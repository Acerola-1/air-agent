"""显式开启的真实Langfuse读回/远端删除演练；只操作本用例新建的Trace。"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx
import pytest

from tests.test_event_ledger_api import ledger_env as ledger_env
from tests.test_observability_api import fixture

pytest.importorskip("common.observability")
from common.event_ledger.worker import OutboxWorker
from common.observability import store
from langfuse import Langfuse

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("AIR_AGENT_LANGFUSE_CLEANUP_TESTS") != "1",
        reason="需显式开启真实Langfuse临时Trace清理演练",
    ),
]


async def test_real_remote_trace_readback_then_restricted_cleanup(ledger_env):
    c, data, ctx = await fixture(ledger_env)
    trace_id = uuid.uuid4().hex
    observation_id = str(uuid.uuid4())
    client = Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        base_url=os.environ["LANGFUSE_BASE_URL"],
        environment="coordinator-cleanup-acceptance",
        mask=lambda **_: None,
        flush_at=1,
        timeout=2,
    )
    try:
        remote = client.start_observation(
            name="controlled-cleanup-acceptance",
            as_type="generation",
            trace_context={"trace_id": trace_id},
            model="controlled-no-network-model",
        )
        remote_id = remote.id
        remote.end()
        await asyncio.to_thread(client.flush)
        await store.record_model_observation(
            ctx,
            observation_id=observation_id,
            logical_call_id=str(uuid.uuid4()),
            role="main",
            model="controlled-no-network-model",
            provider="controlled",
            status="succeeded",
            measurement_status="usage_missing",
        )
        await store.record_trace_link(
            ctx,
            trace_id=trace_id,
            observation_id=observation_id,
            remote_observation_id=remote_id,
            project_scope=os.environ["LANGFUSE_PROJECT_SCOPE"],
        )
        async with httpx.AsyncClient(
            base_url=os.environ["LANGFUSE_BASE_URL"],
            auth=(os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]),
            timeout=5,
        ) as read:
            params = {"traceId": trace_id, "fields": "core,basic,io", "limit": 1000}
            for _ in range(20):
                response = await read.get("/api/public/v2/observations", params=params)
                response.raise_for_status()
                rows = response.json().get("data", [])
                if any(row.get("id") == remote_id for row in rows):
                    break
                await asyncio.sleep(0.5)
            assert any(row.get("id") == remote_id for row in rows), "新建跨度未真实读回"
            assert all(row.get("input") in (None, "null") for row in rows)
            deleted = await c.post("/agent/threads/deletion", json={"thread_id": ctx.thread_id})
            assert deleted.status_code == 200, deleted.text
            local = OutboxWorker(consumers=("deletion_cleanup",), tenant_id=ctx.tenant_id)
            for _ in range(20):
                local_result = await local.run_once()
                if local_result["done"]:
                    break
                await asyncio.sleep(0.05)
            assert local_result["done"] == 1, local_result
            external = OutboxWorker(consumers=("external_trace_cleanup",), tenant_id=ctx.tenant_id)
            # DELETE 异步传播时由真实outbox退避重投，不手工重置任务状态。
            deliveries = []
            for _ in range(30):
                result = await external.run_once()
                deliveries.append(result)
                if result["done"]:
                    break
                await asyncio.sleep(1)
            final = await read.get("/api/public/v2/observations", params=params)
            assert final.status_code == 200, final.status_code
            proof = {
                "trace_id": trace_id,
                "remote_observation_id": remote_id,
                "real_remote_rows_before": len(rows),
                "local_cleanup": local_result,
                "external_deliveries": deliveries,
                "remote_rows_after": len(final.json().get("data", [])),
                "model_network_called": False,
            }
            root = Path(__file__).resolve().parents[3]
            (root / "docs/reviews/evidence/takeover-03-05/langfuse-cleanup.json").write_text(
                json.dumps(proof, indent=2) + "\n"
            )
            assert any(row["done"] for row in deliveries), proof
            assert final.json().get("data") == [], proof
    finally:
        await c.aclose()
        await store.close_pool()
        await asyncio.to_thread(client.shutdown)
