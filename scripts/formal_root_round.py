# ruff: noqa: D101, D102, D103, D107, D415, T201
"""2026-10-25 正式环境 root DeepSeek 单轮授权评测采集器。

用户明确授权的正式环境例外轮（docs/handoffs/deepseek-formal-root-langfuse-performance-goal.md）：
目标模型 cn:deepseek-v4.1-flash、root 正常登录、现有正式栈、Langfuse 观测全保留。
本工具只走正常产品路径（同源 BFF + 受理台账 + 原生图），不读正式数据库，
不修改/绕过 scripts/unified_campaign/guard.py；模型/工具观测对账走 Langfuse
公开 API（v2 observations，按 attempt 种子确定性 trace_id 关联）。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from scripts.product_chat_perf.formal_review import (
    history_complete,
    identity_evidence,
    review_bundle,
)
from scripts.product_chat_perf.run_baseline import _git_state
from scripts.unified_campaign.dataset import PlannedSample, load_plan
from scripts.unified_campaign.sender import send_sample

SHANGHAI = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / ".local"
BFF = "http://127.0.0.1:3000"
IAM = "http://127.0.0.1:19100"
LANGFUSE = os.environ.get("LANGFUSE_BASE_URL", "http://127.0.0.1:3001")
ROUND_BUDGET_SECONDS = 120 * 60
CONCURRENCY_LEVELS = (1, 2, 5, 10)
CONCURRENCY_PER_LEVEL = 10


def _now_utc() -> str:
    return datetime.now(UTC).isoformat()


def _load_round_config(run_id: str) -> dict[str, Any]:
    path = PRIVATE_ROOT / f"{run_id}-private" / "round.json"
    if not path.is_file():
        raise SystemExit(f"缺少轮次配置 {path}；先执行 login 子命令")
    return json.loads(path.read_text())


def _private_dir(run_id: str) -> Path:
    return PRIVATE_ROOT / f"{run_id}-private"


def _perf_dir(run_id: str) -> Path:
    return ROOT / "docs/reviews/basic-qa-performance" / run_id


def _read_root_password() -> str:
    path = PRIVATE_ROOT / "i4-live" / "root-password"
    if not path.is_file():
        raise SystemExit("缺少 root 受保护凭据 .local/i4-live/root-password；执行阻塞")
    return path.read_text().strip()


def _langfuse_auth() -> tuple[str, str]:
    """从正式 .env 读取 Langfuse 公开 API 密钥（只在本进程内使用）。"""

    def find(prefix: str) -> str:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith(prefix):
                value = line.split("=", 1)[1].strip().strip("'\"")
                if value:
                    return value
        raise SystemExit(f".env 缺少 {prefix}")

    return find("LANGFUSE_PUBLIC_KEY="), find("LANGFUSE_SECRET_KEY=")


def _cookie_header(client: httpx.Client) -> str:
    return "; ".join(f"{cookie.name}={cookie.value}" for cookie in client.cookies.jar)


def cmd_login(args: argparse.Namespace) -> int:
    run_id = args.run_id
    perf = _perf_dir(run_id)
    private = _private_dir(run_id)
    if perf.exists() or private.exists():
        raise SystemExit("报告/私有目录已存在；不得覆盖旧报告")
    perf.mkdir(parents=True)
    private.mkdir(parents=True, mode=0o700)
    os.chmod(private, 0o700)

    password = _read_root_password()
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        # 1) 同源 BFF 发起 SSO，取授权地址与 state 一次性 cookie
        start = client.post(
            f"{BFF}/api/auth/iam/start",
            headers={"origin": BFF, "content-type": "application/json"},
        )
        if start.status_code != 200:
            raise SystemExit(f"iam/start HTTP {start.status_code}")
        authorize_url = start.json()["url"]

        # 2) IAM 取 CSRF 与匿名句柄
        csrf = client.get(f"{IAM}/web-api/auth/csrf")
        if csrf.status_code != 200:
            raise SystemExit(f"csrf HTTP {csrf.status_code}")
        csrf_token = csrf.json().get("csrf_token") or csrf.json().get("token") or ""

        # 3) root 正常登录
        login = client.post(
            f"{IAM}/web-api/auth/login",
            headers={
                "origin": IAM,
                "content-type": "application/json",
                "x-csrf-token": csrf_token,
            },
            json={"account": "root", "password": password},
        )
        if login.status_code != 200:
            raise SystemExit(f"IAM 登录 HTTP {login.status_code}（不猜测、不换号）")
        identity = login.json()

        # 4) SSO authorize → 303 回调（带 ticket）
        authz = client.get(authorize_url)
        if authz.status_code != 303:
            raise SystemExit(f"authorize HTTP {authz.status_code}，未获得回调跳转")
        callback_url = authz.headers.get("location", "")
        if "/api/auth/iam/callback" not in callback_url:
            raise SystemExit(f"authorize 跳转异常：{callback_url[:80]}")

        # 5) BFF 回调兑换应用会话 handle
        callback = client.get(
            callback_url if callback_url.startswith("http") else BFF + callback_url
        )
        if callback.status_code != 303:
            raise SystemExit(f"回调 HTTP {callback.status_code}")

        # 6) 读会话上下文（user UUID、企业列表）
        me = client.get(f"{BFF}/api/auth/me")
        if me.status_code != 200:
            raise SystemExit(f"auth/me HTTP {me.status_code}")
        ctx = me.json()

        tenant_items = [item["tenant"] for item in ctx.get("tenants") or []]
        target = None
        if args.tenant_id:
            target = next(
                (t for t in tenant_items if t.get("id") == args.tenant_id), None
            )
            if target is None:
                listed = [
                    {"id": t.get("id"), "name": t.get("name"), "code": t.get("code")}
                    for t in tenant_items
                ]
                raise SystemExit(f"未找到指定企业 {args.tenant_id}；可选：{listed}")
        elif ctx.get("needs_tenant_selection") or not ctx.get("current_tenant"):
            target = tenant_items[0] if tenant_items else None
        else:
            target = ctx.get("current_tenant")

        if target is None:
            raise SystemExit("root 无可选企业；执行阻塞")

        # 7) 企业切换（走正常 BFF 端点）
        if (ctx.get("current_tenant") or {}).get("id") != target.get("id"):
            switch = client.post(
                f"{BFF}/api/auth/switch-tenant",
                headers={"origin": BFF, "content-type": "application/json"},
                json={
                    "tenant_id": target["id"],
                    "expected_ctx_version": ctx["context_version"],
                },
            )
            if switch.status_code != 200:
                raise SystemExit(f"switch-tenant HTTP {switch.status_code}")
            me = client.get(f"{BFF}/api/auth/me")
            ctx = me.json()

        cookie = _cookie_header(client)
        if "air_agent_iam_handle" not in cookie:
            raise SystemExit("BFF 会话 cookie 缺失")

    session = {
        "account": ctx["user"]["account"],
        "user_id": ctx["user"]["id"],
        "display_name": ctx["user"]["display_name"],
        "session_id": ctx["session"]["session_id"],
        "tenant_id": ctx["current_tenant"]["id"],
        "tenant_name": ctx["current_tenant"].get("name"),
        "tenant_code": ctx["current_tenant"].get("code"),
        "context_version": ctx["context_version"],
        "available_tenants": [
            {"id": t.get("id"), "name": t.get("name"), "code": t.get("code")}
            for t in tenant_items
        ],
        "permissions": len(ctx.get("permissions") or []),
        "iam_identity_keys": sorted(identity.keys()),
        "cookie": cookie,
        "logged_in_at": _now_utc(),
    }
    (private / "session.json").write_text(
        json.dumps(session, ensure_ascii=False, indent=2)
    )
    round_config = {
        "run_id": run_id,
        "authorized_round": "formal-root-deepseek-20261025",
        "handoff": "docs/handoffs/deepseek-formal-root-langfuse-performance-goal.md",
        "model_requested": "cn:deepseek-v4.1-flash",
        "bff": BFF,
        "iam": IAM,
        "langfuse": LANGFUSE,
        "round_budget_seconds": ROUND_BUDGET_SECONDS,
        "retention": "formal_data_retained_no_cleanup",
        "concurrency_levels": list(CONCURRENCY_LEVELS),
        "created_at": _now_utc(),
        "git": _git_state(),
    }
    (private / "round.json").write_text(
        json.dumps(round_config, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            {
                "run_id": run_id,
                "account": session["account"],
                "user_id": session["user_id"],
                "tenant": f"{session['tenant_name']}({session['tenant_id'][:8]}…)",
                "context_version": session["context_version"],
                "available_tenants": len(session["available_tenants"]),
                "permissions": session["permissions"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def _stage_common(run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    config = _load_round_config(run_id)
    session = json.loads((_private_dir(run_id) / "session.json").read_text())
    return config, session


def _send_one(
    client: httpx.Client,
    sample: Any,
    session: dict[str, Any],
    thread_id: str | None,
    stage: str,
    run_id: str,
    level: int | None = None,
) -> dict[str, Any]:
    sample = replace(
        sample,
        sample_id=f"{run_id}:{stage}:c{level or 1}:{sample.sample_id}",
    )
    planned_at = _now_utc()
    row = send_sample(
        client,
        sample,
        bff_base_url=BFF,
        cookie=session["cookie"],
        tenant_id=session["tenant_id"],
        context_version=int(session["context_version"]),
        thread_id=thread_id,
        include_answer_text=True,
    )
    row.update(
        {
            "run_id": run_id,
            "stage": stage,
            "planned_at": planned_at,
            "ended_at": _now_utc(),
            "category": sample.category,
            "deadline_seconds": sample.deadline_seconds,
            "concurrency_level": level,
        }
    )
    return row


def _write_row(path: Path, row: dict[str, Any], answer_sink) -> None:
    answer_text = row.pop("_answer_text", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if answer_text is not None:
        with answer_sink.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "sample_id": row.get("sample_id"),
                        "submission_key": row.get("submission_key"),
                        "attempt_id": row.get("attempt_id"),
                        "thread_id": row.get("thread_id"),
                        "stage": row.get("stage"),
                        "concurrency_level": row.get("concurrency_level"),
                        "answer_sha256": row.get("answer_sha256"),
                        "case_id": row.get("case_id"),
                        "answer": answer_text,
                        "recorded_at": _now_utc(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


class _InflightSampler(threading.Thread):
    def __init__(self, interval: float = 1.0) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self.samples: list[tuple[str, int]] = []
        self.inflight = 0
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            self.samples.append((_now_utc(), self.inflight))

    def stop(self) -> None:
        self._stop.set()


def cmd_precheck(args: argparse.Namespace) -> int:
    config, session = _stage_common(args.run_id)
    del config
    questions = [
        ("precheck-huzhou", "帮我查一下湖州市今天的空气质量情况。", 120),
        ("precheck-q1", None, 60),
    ]
    samples, _ = load_plan("round-1")
    q1 = next(s for s in samples if s.cohort == "measured" and s.category == "Q1")
    rows: list[dict[str, Any]] = []
    answers_path = _private_dir(args.run_id) / "answers.jsonl"
    requests_path = _perf_dir(args.run_id) / "requests.precheck.jsonl"
    started_wall = datetime.now(UTC)
    with httpx.Client(timeout=None) as client:
        for label, content, deadline in questions:
            import hashlib

            sample_content = content or q1.content
            s = PlannedSample(
                sample_id=f"{args.run_id}:{label}",
                case_id=label,
                category="precheck",
                graph_id="basic-qa",
                cohort="precheck",
                content=sample_content,
                content_sha256=hashlib.sha256(sample_content.encode()).hexdigest(),
                deadline_seconds=deadline,
                expected_intent=None,
                menu_name=None,
                parent_case_id=None,
                history_index=None,
                counts_as_semantic_sample=False,
            )
            row = _send_one(client, s, session, None, "precheck", args.run_id)
            _write_row(requests_path, row, answers_path)
            rows.append(row)
            print(
                json.dumps(
                    {
                        "label": label,
                        "thread_id": row.get("thread_id"),
                        "stream_status": row.get("stream_status"),
                        "business_status": row.get("business_status"),
                        "first_answer_ms": row.get("client_first_answer_ms"),
                        "complete_ms": row.get("client_complete_ms"),
                        "attempt_id": row.get("attempt_id"),
                        "run_id_platform": row.get("platform_run_id"),
                    },
                    ensure_ascii=False,
                )
            )
    ended_wall = datetime.now(UTC)
    (_perf_dir(args.run_id) / "precheck.json").write_text(
        json.dumps(
            {
                "started_at": started_wall.isoformat(),
                "ended_at": ended_wall.isoformat(),
                "local_date": datetime.now(SHANGHAI).date().isoformat(),
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    config, session = _stage_common(args.run_id)
    samples, digests = load_plan("round-1")
    samples = [
        s
        for s in samples
        if s.graph_id == "basic-qa" and s.cohort in {"measured", "history"}
    ]
    if args.limit is not None:
        measured_ids = {s.case_id for s in samples if s.cohort == "measured"}
        keep = set(list(sorted(measured_ids))[: args.limit])
        samples = [s for s in samples if s.cohort != "measured" or s.case_id in keep]

    requests_path = _perf_dir(args.run_id) / "requests.baseline.jsonl"
    answers_path = _private_dir(args.run_id) / "answers.jsonl"
    threads: dict[str, str] = {}
    started_wall = datetime.now(UTC)
    started_mono = time.monotonic()
    round_deadline = started_mono + config["round_budget_seconds"]
    stopped_reason = None
    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=None) as client:
        for sample in samples:
            if time.monotonic() > round_deadline:
                stopped_reason = "round_budget_exhausted"
                break
            thread_id = threads.get(sample.case_id) if sample.category == "Q4" else None
            if (
                sample.category == "Q4"
                and sample.cohort == "measured"
                and not history_complete(
                    {
                        "category": "Q4",
                        "case_id": sample.case_id,
                        "stage": "baseline",
                        "concurrency_level": None,
                        "thread_id": thread_id,
                    },
                    rows,
                )
            ):
                import uuid

                row = {
                    "sample_id": f"{args.run_id}:baseline:c1:{sample.sample_id}",
                    "submission_key": uuid.uuid4().hex,
                    "case_id": sample.case_id,
                    "category": "Q4",
                    "cohort": "measured",
                    "stage": "baseline",
                    "run_id": args.run_id,
                    "primary_result": "history_invalid",
                    "sent": False,
                    "thread_id": thread_id,
                    "concurrency_level": None,
                    "answer_nonempty": False,
                    "answer_associated": False,
                    "planned_at": _now_utc(),
                    "ended_at": _now_utc(),
                }
                _write_row(requests_path, row, answers_path)
                rows.append(row)
                continue
            row = _send_one(client, sample, session, thread_id, "baseline", args.run_id)
            _write_row(requests_path, row, answers_path)
            rows.append(row)
            if row.get("thread_id") and sample.category == "Q4":
                threads[sample.case_id] = str(row["thread_id"])
            print(
                json.dumps(
                    {
                        "sample": sample.sample_id,
                        "status": row.get("stream_status"),
                        "business": row.get("business_status"),
                        "first_ms": row.get("client_first_answer_ms"),
                        "complete_ms": row.get("client_complete_ms"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    ended_wall = datetime.now(UTC)
    manifest_extra = {
        "started_at": started_wall.isoformat(),
        "ended_at": ended_wall.isoformat(),
        "started_at_shanghai": started_wall.astimezone(SHANGHAI).isoformat(),
        "ended_at_shanghai": ended_wall.astimezone(SHANGHAI).isoformat(),
        "wall_seconds": round(time.monotonic() - started_mono, 2),
        "stopped_reason": stopped_reason,
        "digests": digests,
        "measured_sent": sum(
            1
            for r in rows
            if r.get("stage") == "baseline"
            and str(r.get("sample_id", "")).endswith(":measured")
        ),
        "history_sent": sum(
            1
            for r in rows
            if str(r.get("sample_id", "")).endswith("history:")
            or ":history:" in str(r.get("sample_id", ""))
        ),
    }
    (_perf_dir(args.run_id) / "baseline.json").write_text(
        json.dumps(manifest_extra, ensure_ascii=False, indent=2)
    )
    print(json.dumps(manifest_extra, ensure_ascii=False))
    return 0


def cmd_concurrency(args: argparse.Namespace) -> int:
    config, session = _stage_common(args.run_id)
    del config
    samples_all, _ = load_plan("round-1")
    q1 = [s for s in samples_all if s.cohort == "measured" and s.category == "Q1"][:5]
    q2 = [s for s in samples_all if s.cohort == "measured" and s.category == "Q2"][:5]
    mix = q1 + q2  # 固定 5×Q1 + 5×Q2

    results: dict[int, list[dict[str, Any]]] = {}
    for level in CONCURRENCY_LEVELS:
        requests_path = _perf_dir(args.run_id) / f"requests.concurrency{level}.jsonl"
        answers_path = _private_dir(args.run_id) / "answers.jsonl"
        sampler = _InflightSampler()
        sampler.start()
        stage_started = datetime.now(UTC)
        stage_started_mono = time.monotonic()
        rows: list[dict[str, Any]] = []

        def dispatch(sample: Any) -> dict[str, Any]:
            sampler.inflight += 1
            try:
                with httpx.Client(timeout=None) as client:
                    return _send_one(
                        client, sample, session, None, "concurrency", args.run_id, level
                    )
            finally:
                sampler.inflight -= 1

        with ThreadPoolExecutor(max_workers=level) as pool:
            futures = {pool.submit(dispatch, s): s for s in mix}
            for future in as_completed(futures):
                row = future.result()
                _write_row(requests_path, row, answers_path)
                rows.append(row)
                print(
                    json.dumps(
                        {
                            "level": level,
                            "sample": row.get("sample_id"),
                            "status": row.get("stream_status"),
                            "first_ms": row.get("client_first_answer_ms"),
                            "complete_ms": row.get("client_complete_ms"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        sampler.stop()
        stage_json = {
            "level": level,
            "planned": len(mix),
            "started_at": stage_started.isoformat(),
            "ended_at": datetime.now(UTC).isoformat(),
            "wall_seconds": round(time.monotonic() - stage_started_mono, 2),
            "inflight_samples": len(sampler.samples),
            "max_observed_inflight": max((n for _, n in sampler.samples), default=0),
            "mix": [s.case_id for s in mix],
        }
        (_perf_dir(args.run_id) / f"concurrency{level}.json").write_text(
            json.dumps(stage_json, ensure_ascii=False, indent=2)
        )
        results[level] = rows
        if level != CONCURRENCY_LEVELS[-1]:
            time.sleep(20)  # 档间排水与 Langfuse 异步入库
    print(
        json.dumps(
            {
                level: {
                    "ok": sum(1 for r in rows if r.get("stream_status") == "completed"),
                    "total": len(rows),
                }
                for level, rows in results.items()
            },
            ensure_ascii=False,
        )
    )
    return 0


def _lf_get(
    client: httpx.Client, path: str, public_key: str, secret_key: str
) -> dict[str, Any]:
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    resp = client.get(
        f"{LANGFUSE}{path}",
        headers={"authorization": f"Basic {token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Langfuse {path} HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _clickhouse(query: str) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [
            "container",
            "exec",
            "air-agent-langfuse-clickhouse",
            "clickhouse-client",
            "--query",
            query + " FORMAT JSONEachRow",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"ClickHouse 查询失败: {completed.stderr[:300]}")
    return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]


def cmd_reconcile(args: argparse.Namespace) -> int:
    import langfuse

    config, session = _stage_common(args.run_id)
    perf = _perf_dir(args.run_id)
    rows: list[dict[str, Any]] = []
    for name in sorted(perf.glob("requests*.jsonl")):
        with name.open() as fh:
            rows.extend(json.loads(line) for line in fh if line.strip())
    rows = [r for r in rows if r.get("attempt_id")]
    for r in rows:
        r["_source_file"] = "unknown"
    for name in sorted(perf.glob("requests*.jsonl")):
        with name.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                for r in rows:
                    if r.get("submission_key") == rec.get("submission_key"):
                        r["_source_file"] = name.name
    trace_of = {
        str(r["attempt_id"]): langfuse.Langfuse.create_trace_id(
            seed=f"air-agent:{session['tenant_id']}:{r['attempt_id']}"
        )
        for r in rows
    }
    trace_list = ",".join(f"'{t}'" for t in sorted(set(trace_of.values())))
    events = _clickhouse(
        "SELECT trace_id, session_id, user_id, type, name, provided_model_name, "
        "start_time, "
        "dateDiff('millisecond', start_time, end_time) dur_ms, "
        "dateDiff('millisecond', start_time, completion_start_time) ttft_ms, "
        "usage_details['input'] usage_in, usage_details['output'] usage_out, "
        "calculated_total_cost total_cost, level, status_message "
        f"FROM events_full WHERE trace_id IN ({trace_list})"
    )
    by_trace: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        by_trace.setdefault(str(ev.get("trace_id")), []).append(ev)

    assoc: list[dict[str, Any]] = []
    for row in rows:
        attempt_id = str(row["attempt_id"])
        expected_trace = trace_of[attempt_id]
        observations = by_trace.get(expected_trace, [])
        identity = identity_evidence(
            observations, session["user_id"], str(row.get("thread_id") or "")
        )
        generations = [o for o in observations if o.get("type") == "GENERATION"]
        tool_spans = [
            o
            for o in observations
            if o.get("type") == "TOOL"
            or str(o.get("name", "")).startswith(("mcp_", "knowledge_", "helper_"))
        ]
        skill_spans = [o for o in observations if o.get("name") == "resolve_skill"]
        models = Counter(str(g.get("provided_model_name")) for g in generations)
        usage_in = sum(int(g.get("usage_in") or 0) for g in generations)
        usage_out = sum(int(g.get("usage_out") or 0) for g in generations)
        usage_missing = any(
            not (g.get("usage_in") or g.get("usage_out")) for g in generations
        )
        errors = [o for o in observations if o.get("level") == "ERROR"]
        streamed = [g for g in generations if g.get("ttft_ms") is not None]
        assoc.append(
            {
                "sample_id": row.get("sample_id"),
                "case_id": row.get("case_id"),
                "stage": row.get("stage"),
                "source_file": row.get("_source_file"),
                "concurrency_level": row.get("concurrency_level"),
                "thread_id": row.get("thread_id"),
                "attempt_id": attempt_id,
                "expected_trace_id": expected_trace,
                "trace_found": bool(observations),
                "observations": len(observations),
                "generations": len(generations),
                "tool_spans": len(tool_spans),
                "tool_names": sorted({str(o.get("name")) for o in tool_spans})[:14],
                "skill_resolved": bool(skill_spans),
                "models": dict(models),
                "usage_input": usage_in or None,
                "usage_output": usage_out or None,
                "usage_missing": usage_missing,
                "generation_dur_ms": [g.get("dur_ms") for g in generations],
                "generation_ttft_ms": [g.get("ttft_ms") for g in streamed],
                "cost_total": [str(g.get("total_cost")) for g in generations][:4],
                "error_count": len(errors),
                "error_messages": [
                    str(o.get("status_message"))[:120] for o in errors[:3]
                ],
                "identity_user_status": identity["user_id"],
                "identity_session_status": identity["session_id"],
                "identity_user_ok": identity["user_id"] == "PASS",
                "identity_session_ok": identity["session_id"] == "PASS",
                "client_first_answer_ms": row.get("client_first_answer_ms"),
                "client_complete_ms": row.get("client_complete_ms"),
                "stream_status": row.get("stream_status"),
                "primary_result": row.get("primary_result"),
            }
        )
    assoc.sort(key=lambda a: str(a["sample_id"]))
    out = {
        "reconciled_at": _now_utc(),
        "total_requests": len(rows),
        "trace_coverage": sum(1 for a in assoc if a["trace_found"]),
        "identity_user_ok": sum(1 for a in assoc if a["identity_user_ok"]),
        "identity_session_ok": sum(1 for a in assoc if a["identity_session_ok"]),
        "usage_missing_requests": sum(1 for a in assoc if a["usage_missing"]),
        "association": assoc,
        "api_shape_note": "v4 events_only 列表 API 为瘦身视图；模型/usage/TTFT/费用取自 ClickHouse events_full（container exec 只读查询）",
        "langfuse_project_url": f"{LANGFUSE}/project/traces",
    }
    reconcile_out = perf / (
        "langfuse-reconcile-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    )
    reconcile_out.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "total": len(rows),
                "trace_coverage": out["trace_coverage"],
                "user_ok": out["identity_user_ok"],
                "session_ok": out["identity_session_ok"],
                "usage_missing": out["usage_missing_requests"],
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Write a separate offline revision; never replace retained evidence."""
    summary = review_bundle(
        _perf_dir(args.run_id),
        _private_dir(args.run_id) / "answers.jsonl",
        Path(args.out),
        oracles_path=Path(args.oracles) if args.oracles else None,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="正式环境 root DeepSeek 单轮授权评测")
    parser.add_argument("--run-id", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    p_login = sub.add_parser("login")
    p_login.add_argument("--tenant-id", default=None)
    p_login.set_defaults(func=cmd_login)
    p_pre = sub.add_parser("precheck")
    p_pre.set_defaults(func=cmd_precheck)
    p_base = sub.add_parser("baseline")
    p_base.add_argument("--limit", type=int, default=None)
    p_base.set_defaults(func=cmd_baseline)
    p_conc = sub.add_parser("concurrency")
    p_conc.set_defaults(func=cmd_concurrency)
    p_rec = sub.add_parser("reconcile")
    p_rec.set_defaults(func=cmd_reconcile)
    p_rep = sub.add_parser("report")
    p_rep.add_argument("--out", required=True)
    p_rep.add_argument("--oracles")
    p_rep.set_defaults(func=cmd_report)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
