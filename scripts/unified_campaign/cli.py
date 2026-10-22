"""Command line for the shared campaign tool."""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from scripts.unified_campaign.dataset import load_plan
from scripts.unified_campaign.expectations import build_expectations
from scripts.unified_campaign.guard import (
    CampaignGuardError,
    assert_execution_allowed,
    assert_targets_allowed,
)
from scripts.unified_campaign.iam_catalog import inspect_migrations
from scripts.unified_campaign.reconcile import reconcile
from scripts.unified_campaign.records import SCHEMA, write_performance_bundle
from scripts.unified_campaign.sender import send_sample

SHANGHAI = ZoneInfo("Asia/Shanghai")


def main(argv: list[str] | None = None) -> int:
    """Dispatch plan, smoke, reconcile, or the IAM catalog check."""
    parser = argparse.ArgumentParser(description="Change6/7 与 Basic QA 共用验收工具")
    parser.add_argument("--enable-real-models", action="store_true")
    parser.add_argument("--enable-load", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--out", type=Path, required=True)
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--endpoints", type=Path, required=True)
    smoke.add_argument("--performance-out", type=Path, required=True)
    smoke.add_argument("--evidence-out", type=Path, required=True)
    reconcile_cmd = sub.add_parser("reconcile")
    reconcile_cmd.add_argument("--campaign", type=Path, required=True)
    reconcile_cmd.add_argument("--sent", type=Path, required=True)
    reconcile_cmd.add_argument("--metrics", type=Path)
    reconcile_cmd.add_argument("--sources", type=Path)
    reconcile_cmd.add_argument("--out", type=Path, required=True)
    catalog = sub.add_parser("iam-catalog")
    catalog.add_argument("--migration-dir", type=Path, required=True)
    catalog.add_argument("--out", type=Path, required=True)
    export = sub.add_parser("insights-export")
    export.add_argument("--admin-base-url", required=True)
    export.add_argument("--tenant-id", required=True)
    export.add_argument("--context-version", type=int, required=True)
    export.add_argument("--cookie-env", default="CAMPAIGN_COOKIE")
    export.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            return _plan(args.out)
        if args.command == "smoke":
            return _smoke(args)
        if args.command == "reconcile":
            return _reconcile(args)
        if args.command == "insights-export":
            return _insights_export(args)
        return _catalog(args.migration_dir, args.out)
    except CampaignGuardError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2


def _plan(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=False)
    samples, digests = load_plan()
    expectations = build_expectations(samples)
    campaign_id = _campaign_id(digests["cases_sha256"])
    payload = {
        "campaign_id": campaign_id,
        "executed": False,
        "digests": digests,
        "samples": [_public_sample(sample) for sample in samples],
        "expectations": expectations,
        "allow_real_models": False,
        "allow_load": False,
    }
    (out / "campaign.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    (out / "expectations.json").write_text(
        json.dumps(expectations, ensure_ascii=False, indent=2) + "\n"
    )
    sys.stderr.write(f"已写计划 {campaign_id}；这不是冒烟或性能执行。\n")
    return 0


def _smoke(args: argparse.Namespace) -> int:
    config = json.loads(args.endpoints.read_text())
    urls = [config["bff_base_url"], config["admin_base_url"]]
    assert_targets_allowed(urls)
    assert_execution_allowed(
        layer=config["layer"],
        profile=config["profile"],
        allow_real_models=bool(config["allow_real_models"]),
        allow_load=bool(config["allow_load"]),
        enable_real_models=args.enable_real_models,
        enable_load=args.enable_load,
    )
    if config["layer"] != "P" or config["profile"] != "S":
        raise CampaignGuardError("smoke 子命令只执行 P/S")
    samples, digests = load_plan()
    expectations = build_expectations(samples)
    campaign_id = _campaign_id(digests["cases_sha256"])
    run_id = datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S") + "-" + campaign_id[-6:]
    # Create the evidence directory before any measurement so an existing
    # directory cannot waste a complete run at the write-out step.
    args.evidence_out.mkdir(parents=True, exist_ok=False)
    cookie = os.environ.get(config["cookie_env"], "")
    reachable = bool(cookie) and _reachable(config["bff_base_url"])
    sent: list[dict[str, Any]] = []
    blocked = None
    calls: list[dict[str, Any]] = []
    resources: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []
    if not reachable:
        blocked = "隔离 BFF 不可达或缺少 cookie 环境变量；未发送题目"
        sent = [_unsent(sample, run_id) for sample in samples]
        resources = [_not_collected_resource(run_id, "未发送题目")]
        checks = [_blocked_check("campaign", blocked)]
    else:
        containers = [str(item) for item in config.get("containers") or []]
        probe = _AdminProbe(
            config["admin_base_url"],
            cookie,
            config["tenant_id"],
            int(config["context_version"]),
        )
        controlled_url = config.get("controlled_stats_url")
        metrics_before = probe.metrics_summary()
        containers_before = _container_stats(containers)
        controlled_before = _controlled_stats(controlled_url)
        client_before = resource.getrusage(resource.RUSAGE_SELF)
        started_wall = time.monotonic()
        started_at = datetime.now(UTC)
        threads: dict[str, str] = {}
        interval = float(config.get("request_interval_seconds") or 0)
        with httpx.Client(timeout=None) as client:
            for index, sample in enumerate(samples):
                thread_id = (
                    threads.get(sample.case_id)
                    if sample.graph_id == "basic-qa"
                    else None
                )
                row = send_sample(
                    client,
                    sample,
                    bff_base_url=config["bff_base_url"],
                    cookie=cookie,
                    tenant_id=config["tenant_id"],
                    context_version=int(config["context_version"]),
                    thread_id=thread_id,
                )
                row.update(_common(run_id))
                row["request_interval_seconds"] = interval
                if row.get("thread_id") and sample.cohort == "history":
                    threads[sample.case_id] = str(row["thread_id"])
                sent.append(row)
                if interval and index + 1 < len(samples):
                    time.sleep(interval)
        wall_seconds = time.monotonic() - started_wall
        client_after = resource.getrusage(resource.RUSAGE_SELF)
        containers_after = _container_stats(containers)
        # The observability collector flushes asynchronously; give it a short
        # bounded settle before exporting calls. Missing rows stay not_collected.
        time.sleep(3)
        metrics_after = probe.metrics_summary()
        controlled_after = _controlled_stats(controlled_url)
        calls = _collect_calls(
            probe, sent, since=started_at - timedelta(seconds=5), run_id=run_id
        )
        if controlled_before is not None and controlled_after is not None:
            calls.append(
                {
                    **_common(run_id),
                    "kind": "model",
                    "source": "controlled-stats",
                    "status": "measured",
                    "chat_calls_delta": controlled_after["chat_calls"]
                    - controlled_before["chat_calls"],
                    "mcp_calls_delta": controlled_after["mcp_calls"]
                    - controlled_before["mcp_calls"],
                    "usage": None,
                    "usage_status": "not_collected",
                    "detail": "受控依赖窗口计数；不含逐请求关联",
                }
            )
        resources = _resource_rows(
            run_id,
            containers_before,
            containers_after,
            client_before,
            client_after,
            wall_seconds,
        )
        checks = _business_checks(run_id, sent, calls, metrics_before, metrics_after)
    measured = [row for row in sent if row.get("cohort") == "measured"]
    pipeline_ok = bool(measured) and all(
        row.get("primary_result") == "stream_completed" and row.get("answer_associated")
        for row in measured
    )
    failed_checks = [
        item
        for item in checks
        if item.get("status") == "FAIL"
        and (
            item.get("cohort") in {"measured", None}
            or str(item.get("check_id", "")).startswith("aggregate:")
        )
    ]
    if blocked:
        conclusion = "BLOCKED"
    elif pipeline_ok and not failed_checks:
        conclusion = "冒烟链路完成，质量与浏览器未验收"
    else:
        conclusion = "FAIL"
    (args.evidence_out / "expectations.json").write_text(
        json.dumps(expectations, ensure_ascii=False, indent=2) + "\n"
    )
    (args.evidence_out / "sent.json").write_text(
        json.dumps(sent, ensure_ascii=False, indent=2) + "\n"
    )
    manifest = {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "campaign_id": campaign_id,
        "layer": "P",
        "profile": "S",
        "recorded_at": datetime.now(UTC).isoformat(),
        "digests": digests,
        "allow_real_models": False,
        "allow_load": False,
        "blocked_reason": blocked,
        "conclusion": conclusion,
        "browser": "未执行",
        "targets": urls,
    }
    write_performance_bundle(
        args.performance_out,
        run_id=run_id,
        campaign_id=campaign_id,
        manifest=manifest,
        requests=sent,
        calls=calls,
        resources=resources,
        checks=checks,
        browser=[],
    )
    sys.stderr.write(f"{manifest['conclusion']} {run_id}\n")
    if blocked:
        return 2
    return 0 if pipeline_ok else 1


def _unsent(sample: object, run_id: str) -> dict[str, Any]:
    public = _public_sample(sample)
    public.update(_common(run_id))
    public.update(
        {"sent": False, "primary_result": "not_sent", "answer_associated": False}
    )
    return public


def _reconcile(args: argparse.Namespace) -> int:
    campaign = json.loads((args.campaign / "campaign.json").read_text())
    sent = json.loads(args.sent.read_text())
    metrics = json.loads(args.metrics.read_text()) if args.metrics else None
    sources = json.loads(args.sources.read_text()) if args.sources else None
    result = reconcile(
        expectations=campaign["expectations"],
        sent_rows=sent,
        metrics=metrics,
        insight_sources=sources,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result["status"] == "PASS" else 1


def _catalog(migration_dir: Path, out: Path) -> int:
    payload = inspect_migrations(migration_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0 if payload["insights_manage_registered"] else 2


def _insights_export(args: argparse.Namespace) -> int:
    """Export the intent-level insight index through the product API."""
    cookie = os.environ.get(args.cookie_env, "")
    if not cookie:
        sys.stderr.write("缺少 cookie 环境变量\n")
        return 2
    assert_targets_allowed([args.admin_base_url])
    probe = _AdminProbe(
        args.admin_base_url, cookie, args.tenant_id, args.context_version
    )
    clusters, cluster_status = _cursor_pages(
        probe,
        "/agent/insights/questions",
        {"rank": "intents", "limit": 200},
    )
    if cluster_status != 200:
        sys.stderr.write(f"问题榜不可用：HTTP {cluster_status}\n")
        return 2
    rows: list[dict[str, Any]] = []
    errors: dict[str, int | None] = {}
    for cluster in clusters:
        cluster_id = str(cluster.get("cluster_id"))
        sources, status = _offset_pages(
            probe,
            f"/agent/insights/questions/{cluster_id}/sources",
            {"limit": 200},
        )
        if status != 200:
            errors[cluster_id] = status
            continue
        for source in sources:
            rows.append(
                {
                    "request_id": source.get("request_id"),
                    "intent_id": source.get("intent_id"),
                    "split_index": source.get("split_index"),
                    "cluster_id": cluster_id,
                    "source": "cluster",
                }
            )
    unclassified, gaps, unclassified_status = _unclassified_pages(probe)
    if unclassified_status != 200:
        errors["unclassified"] = unclassified_status
    for item in unclassified:
        rows.append(
            {
                "request_id": item.get("request_id"),
                "intent_id": item.get("intent_id"),
                "split_index": item.get("split_index"),
                "cluster_id": None,
                "source": "unclassified",
            }
        )
    for gap in gaps:
        rows.append(
            {
                "request_id": gap.get("request_id"),
                "gap_reason": gap.get("reason"),
                "source": "gap",
            }
        )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "sources.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    )
    summary = {
        "schema_version": "insight-index-export.v1",
        "clusters": len(clusters),
        "cluster_sources": len([row for row in rows if row["source"] == "cluster"]),
        "unclassified_intents": len(unclassified),
        "gaps": len(gaps),
        "requests": len(
            {str(row["request_id"]) for row in rows if row.get("request_id")}
        ),
        "complete": not errors,
        "errors": errors,
    }
    (args.out / "export-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    sys.stderr.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return 0 if not errors else 1


def _cursor_pages(
    probe: _AdminProbe, path: str, params: dict[str, Any]
) -> tuple[list[dict[str, Any]], int | None]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    status: int | None = None
    for _ in range(200):
        query = dict(params)
        if cursor:
            query["cursor"] = cursor
        status, body = probe.get(path, query)
        if status != 200 or not isinstance(body, dict):
            return items, status
        batch = body.get("items")
        if isinstance(batch, list):
            items.extend(row for row in batch if isinstance(row, dict))
        cursor = body.get("next_cursor")
        if not cursor:
            break
    return items, status


def _offset_pages(
    probe: _AdminProbe, path: str, params: dict[str, Any]
) -> tuple[list[dict[str, Any]], int | None]:
    items: list[dict[str, Any]] = []
    offset = 0
    status: int | None = None
    for _ in range(200):
        query = dict(params)
        query["offset"] = offset
        status, body = probe.get(path, query)
        if status != 200 or not isinstance(body, dict):
            return items, status
        batch = body.get("items")
        if not isinstance(batch, list):
            break
        items.extend(row for row in batch if isinstance(row, dict))
        if not body.get("has_more"):
            break
        offset += len(batch)
        if not batch:
            break
    return items, status


def _unclassified_pages(
    probe: _AdminProbe,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int | None]:
    items: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    offset = 0
    status: int | None = None
    for _ in range(200):
        status, body = probe.get(
            "/agent/insights/unclassified", {"limit": 200, "offset": offset}
        )
        if status != 200 or not isinstance(body, dict):
            return items, gaps, status
        batch = body.get("items")
        batch_gaps = body.get("gaps")
        if isinstance(batch, list):
            items.extend(row for row in batch if isinstance(row, dict))
        if isinstance(batch_gaps, list):
            gaps.extend(row for row in batch_gaps if isinstance(row, dict))
        if not body.get("has_more"):
            break
        offset += len(batch) if isinstance(batch, list) else 0
        if not isinstance(batch, list) or not batch:
            break
    return items, gaps, status


def _public_sample(sample: object) -> dict[str, Any]:
    data = dict(sample.__dict__)
    data.pop("content", None)
    return data


def _campaign_id(digest: str) -> str:
    stamp = datetime.now(SHANGHAI).strftime("%Y%m%d-%H%M%S")
    return f"campaign-{stamp}-{digest[:8]}"


def _common(run_id: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "round_id": "round-1",
        "layer": "P",
        "profile": "S",
        "stage": "smoke",
        "recorded_at": datetime.now(UTC).isoformat(),
    }


class _AdminProbe:
    """Read-only admin queries with the campaign session; failures stay visible."""

    def __init__(
        self,
        base_url: str,
        cookie: str,
        tenant_id: str,
        context_version: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "cookie": cookie,
            "x-tenant-id": tenant_id,
            "x-tenant-context-version": str(context_version),
        }

    def get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[int | None, Any]:
        try:
            response = httpx.get(
                f"{self.base_url}{path}",
                headers=self.headers,
                params=params,
                timeout=15,
            )
        except httpx.HTTPError:
            return None, None
        if response.status_code != 200:
            return response.status_code, None
        try:
            return response.status_code, response.json()
        except json.JSONDecodeError:
            return response.status_code, None

    def metrics_summary(self) -> dict[str, Any] | None:
        status, body = self.get("/agent/metrics/summary")
        if status != 200 or not isinstance(body, dict):
            return None
        questions = body.get("questions")
        value = questions.get("value") if isinstance(questions, dict) else None
        if not isinstance(value, int):
            return None
        return {
            "question_count": value,
            "status": questions.get("status"),
            "as_of": body.get("as_of"),
            "data_lag_seconds": body.get("data_lag_seconds"),
        }


def _container_stats(names: list[str]) -> dict[str, dict[str, Any]]:
    """Return one snapshot per container; an unavailable CLI is not a failure."""
    if not names:
        return {}
    try:
        result = subprocess.run(
            ["container", "stats", "--no-stream", "--format", "json", *names],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, list):
        return {}
    return {str(item.get("id")): item for item in payload if isinstance(item, dict)}


def _controlled_stats(url: str | None) -> dict[str, int] | None:
    if not url:
        return None
    try:
        response = httpx.get(url, timeout=5)
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    chat = payload.get("chat_calls")
    mcp = payload.get("mcp_calls")
    if not isinstance(chat, int) or not isinstance(mcp, int):
        return None
    return {"chat_calls": chat, "mcp_calls": mcp}


def _collect_calls(
    probe: _AdminProbe,
    sent: list[dict[str, Any]],
    *,
    since: datetime,
    run_id: str,
) -> list[dict[str, Any]]:
    """Export real per-call observations for this run; never fabricate calls."""
    attempt_ids = {str(row["attempt_id"]) for row in sent if row.get("attempt_id")}
    if not attempt_ids:
        return [_not_collected_call(run_id, "本次没有已关联 attempt")]
    until = datetime.now(UTC) + timedelta(seconds=30)
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    status: int | None = None
    for _ in range(50):
        params: dict[str, Any] = {
            "limit": 200,
            "since": since.isoformat(),
            "until": until.isoformat(),
        }
        if cursor:
            params["cursor"] = cursor
        status, body = probe.get("/agent/observability/observations", params)
        if status != 200 or not isinstance(body, dict):
            break
        batch = body.get("items")
        if isinstance(batch, list):
            items.extend(
                row
                for row in batch
                if isinstance(row, dict) and str(row.get("attempt_id")) in attempt_ids
            )
        cursor = body.get("next_cursor")
        if not cursor:
            break
    if status != 200:
        return [_not_collected_call(run_id, f"观测接口不可用（HTTP {status}）")]
    if not items:
        return [_not_collected_call(run_id, "观测接口无本窗口记录")]
    rows = []
    for item in items:
        usage_fields = {
            key: item.get(key)
            for key in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "reasoning_tokens",
            )
            if item.get(key) is not None
        }
        rows.append(
            {
                **_common(run_id),
                "kind": "model",
                "source": "admin-observations",
                "call_id": item.get("observation_id"),
                "attempt_id": item.get("attempt_id"),
                "request_id": item.get("request_id"),
                "platform_run_id": item.get("platform_run_id"),
                "logical_call_id": item.get("logical_call_id"),
                "role": item.get("role"),
                "model": item.get("model"),
                "status": item.get("status"),
                "duration_ms": item.get("duration_ms"),
                "ttft_ms": item.get("ttft_ms"),
                "usage": usage_fields or None,
                "usage_status": "provided" if usage_fields else "not_provided",
            }
        )
    return rows


def _not_collected_call(run_id: str, detail: str) -> dict[str, Any]:
    return {
        **_common(run_id),
        "kind": "model",
        "source": "admin-observations",
        "status": "not_collected",
        "usage": None,
        "usage_status": "not_collected",
        "detail": detail,
    }


def _resource_rows(
    run_id: str,
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    client_before: Any,
    client_after: Any,
    wall_seconds: float,
) -> list[dict[str, Any]]:
    """Record measured client/container resources and explicit gaps."""
    rows = [
        {
            **_common(run_id),
            "component": "campaign-client",
            "sample_scope": "client process over the campaign window",
            "measurement_status": "measured",
            "wall_seconds": round(wall_seconds, 3),
            "ru_maxrss": client_after.ru_maxrss,
            "cpu_user_seconds": round(
                client_after.ru_utime - client_before.ru_utime, 3
            ),
            "cpu_system_seconds": round(
                client_after.ru_stime - client_before.ru_stime, 3
            ),
            "memory_definition": "ru_maxrss（macOS 为字节）；非全机内存",
        }
    ]
    for name in sorted(set(before) | set(after)):
        start = before.get(name, {})
        end = after.get(name, {})
        rows.append(
            {
                **_common(run_id),
                "component": name,
                "sample_scope": "container cumulative counters: before/after delta",
                "measurement_status": "measured" if start and end else "partial",
                "cpu_usage_usec_delta": _delta(start, end, "cpuUsageUsec"),
                "network_rx_bytes_delta": _delta(start, end, "networkRxBytes"),
                "network_tx_bytes_delta": _delta(start, end, "networkTxBytes"),
                "block_read_bytes_delta": _delta(start, end, "blockReadBytes"),
                "block_write_bytes_delta": _delta(start, end, "blockWriteBytes"),
                "memory_usage_bytes": end.get("memoryUsageBytes"),
                "memory_limit_bytes": end.get("memoryLimitBytes"),
                "num_processes": end.get("numProcesses"),
            }
        )
    if not before and not after:
        rows.append(
            {
                **_common(run_id),
                "component": "server-containers",
                "sample_scope": "未采样",
                "measurement_status": "not_collected",
                "detail": "端点文件未配置 containers；本轮未启动负载或容量测试",
            }
        )
    return rows


def _delta(start: dict[str, Any], end: dict[str, Any], key: str) -> int | None:
    first = start.get(key)
    second = end.get(key)
    if isinstance(first, int) and isinstance(second, int) and second >= first:
        return second - first
    return None


def _business_checks(
    run_id: str,
    sent: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    metrics_before: dict[str, Any] | None,
    metrics_after: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Per-request business outcomes plus aggregate conservation checks.

    The P/S conclusion is driven by the 40 measured basic-qa samples and the
    conservation aggregates. History and representative rows are reported with
    their cohort so a separate graph check cannot silently pass as the smoke.
    """
    checks: list[dict[str, Any]] = []
    for row in sent:
        if not row.get("sent"):
            continue
        sample_id = str(row.get("sample_id"))
        cohort = row.get("cohort")
        for suffix, expected, actual in (
            ("stream", "stream_completed", row.get("primary_result")),
        ):
            checks.append(
                _check_row(run_id, f"{sample_id}:{suffix}", expected, actual, cohort)
            )
        if row.get("primary_result") != "reused":
            checks.append(
                _check_row(
                    run_id,
                    f"{sample_id}:answer",
                    True,
                    row.get("answer_nonempty"),
                    cohort,
                )
            )
        checks.append(
            _check_row(
                run_id,
                f"{sample_id}:association",
                True,
                row.get("answer_associated"),
                cohort,
            )
        )
    sent_rows = [row for row in sent if row.get("sent")]
    measured = [row for row in sent if row.get("cohort") == "measured"]
    checks.append(
        _check_row(
            run_id,
            "aggregate:measured-outcomes",
            {"stream_completed": len(measured)},
            dict(Counter(row.get("primary_result") for row in measured)),
            "measured",
        )
    )
    checks.append(
        _check_row(
            run_id,
            "aggregate:first-answer-recorded",
            len(measured),
            len(
                [
                    row
                    for row in measured
                    if row.get("client_first_answer_ms") is not None
                ]
            ),
            "measured",
        )
    )
    checks.append(
        _check_row(
            run_id,
            "aggregate:association",
            len(measured),
            len([row for row in measured if row.get("answer_associated")]),
            "measured",
        )
    )
    checks.append(
        {
            **_common(run_id),
            "check_id": "aggregate:cohort-outcomes",
            "cohort": "all",
            "expected": None,
            "actual": {
                cohort: dict(
                    Counter(
                        row.get("primary_result")
                        for row in sent_rows
                        if row.get("cohort") == cohort
                    )
                )
                for cohort in sorted({row.get("cohort") for row in sent_rows})
            },
            "status": "INFO",
            "detail": "历史与代表场景仅作分组记录，不并入 40 题冒烟结论",
        }
    )
    admitted = {str(row["request_id"]) for row in sent_rows if row.get("request_id")}
    if metrics_before is not None and metrics_after is not None:
        checks.append(
            _check_row(
                run_id,
                "aggregate:change6-question-delta",
                len(admitted),
                metrics_after["question_count"] - metrics_before["question_count"],
            )
        )
    else:
        checks.append(
            _blocked_check(
                "aggregate:change6-question-delta",
                f"统计接口不可用；before={metrics_before} after={metrics_after}",
                run_id=run_id,
            )
        )
    observed_attempts = {
        str(row["attempt_id"]) for row in calls if row.get("attempt_id")
    }
    measured_attempts = {
        str(row["attempt_id"])
        for row in measured
        if row.get("primary_result") == "stream_completed" and row.get("attempt_id")
    }
    missing_calls = sorted(measured_attempts - observed_attempts)
    observability_status = (
        "not_collected"
        if any(row.get("status") == "not_collected" for row in calls)
        else "measured"
    )
    checks.append(
        {
            **_common(run_id),
            "check_id": "aggregate:model-call-coverage",
            "expected": 0 if observability_status == "measured" else None,
            "actual": len(missing_calls)
            if observability_status == "measured"
            else None,
            "status": "PASS"
            if observability_status == "measured" and not missing_calls
            else ("BLOCKED" if observability_status != "measured" else "FAIL"),
            "detail": "已完成请求在观测导出中都有物理模型调用；未开启观测时写 BLOCKED",
        }
    )
    return checks


def _check_row(
    run_id: str,
    check_id: str,
    expected: object,
    actual: object,
    cohort: str | None = None,
) -> dict[str, Any]:
    return {
        **_common(run_id),
        "check_id": check_id,
        "cohort": cohort,
        "expected": expected,
        "actual": actual,
        "status": "PASS" if expected == actual else "FAIL",
    }


def _blocked_check(check_id: str, detail: str, *, run_id: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {
        "check_id": check_id,
        "expected": None,
        "actual": None,
        "status": "BLOCKED",
        "detail": detail,
    }
    if run_id:
        row.update(_common(run_id))
    return row


def _not_collected_resource(run_id: str, detail: str) -> dict[str, Any]:
    return {
        **_common(run_id),
        "component": "campaign-client",
        "sample_scope": "未采样",
        "measurement_status": "not_collected",
        "detail": detail,
    }


def _reachable(url: str) -> bool:
    try:
        httpx.get(url, timeout=2)
    except httpx.HTTPError:
        return False
    return True
