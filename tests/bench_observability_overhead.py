"""真实有界队列和PG写入的本地开销基准；受控模型不包含外部网络等待.

调用run_benchmark时必须传入隔离测试库中真实存在的ObservationContext；
未采集、丢弃或没有写入均判失败，不能把空队列报告为开启观测的性能。
"""

from __future__ import annotations

import asyncio
import os
import resource
import statistics
import time

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from common.observability.collector import get_collector, reset_collector_for_tests
from common.observability.model_adapter import attach_observation_callbacks


def percentile(values, p):
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p
    low = int(rank)
    return ordered[low] * (1 - (rank - low)) + ordered[
        min(low + 1, len(ordered) - 1)
    ] * (rank - low)


async def run_group(enabled, ctx, n=35):
    reset_collector_for_tests()
    os.environ["AGENT_OBSERVABILITY_ENABLED"] = "true" if enabled else "false"
    collector = get_collector()
    model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="controlled benchmark",
                    usage_metadata={
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                    },
                )
            ]
            * (n + 5)
        )
    )
    samples = []
    cpu = time.process_time()
    for i in range(n + 5):
        start = time.perf_counter()
        config = attach_observation_callbacks({"callbacks": []}, ctx)
        await model.ainvoke("controlled benchmark", config=config)
        if i >= 5:
            samples.append((time.perf_counter() - start) * 1000)
    cpu = time.process_time() - cpu
    background = time.perf_counter()
    for _ in range(15):
        stats = await asyncio.to_thread(collector.flush, 2)
        if stats["pending"] == 0:
            break
    stats = collector.stats()
    assert not stats["pending"] and not stats["dropped"] and not stats["failed"], stats
    assert stats["enqueued"] == stats["written"] == (2 * (n + 5) if enabled else 0), (
        stats
    )
    result = {
        "enabled": enabled,
        "samples": n,
        "p50_ms": statistics.median(samples),
        "p95_ms": percentile(samples, 0.95),
        "collector": stats,
        "background_drain_seconds": time.perf_counter() - background,
        "local_cpu_seconds": cpu,
        "process_max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    await collector.close()
    reset_collector_for_tests()
    return result


async def run_benchmark(ctx):
    previous = os.environ.get("AGENT_OBSERVABILITY_ENABLED")
    try:
        groups = [
            await run_group(enabled, ctx) for enabled in (False, True, False, True)
        ]
        delta = {
            name: statistics.mean(g[name] for g in groups if g["enabled"])
            - statistics.mean(g[name] for g in groups if not g["enabled"])
            for name in ("p50_ms", "p95_ms")
        }
        assert delta["p95_ms"] <= 50, delta
        return {
            "conditions": "真实隔离PG、实际LangChain回调与有界队列；受控模型；35次×2轮，预热5次",
            "groups": groups,
            "delta_ms": delta,
            "external_model_wait": "excluded_controlled_model",
            "langfuse_wait": "not_part_of_this_local_benchmark",
            "threshold_p95_ms": 50,
            "passed": True,
        }
    finally:
        if previous is None:
            os.environ.pop("AGENT_OBSERVABILITY_ENABLED", None)
        else:
            os.environ["AGENT_OBSERVABILITY_ENABLED"] = previous
