"""Tests for the shared campaign tool."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from scripts.unified_campaign.cli import (
    _business_checks,
    _collect_calls,
    _resource_rows,
    main,
)
from scripts.unified_campaign.controlled import (
    ControlledDependencyServer,
    _extract_city,
    _freeze_arguments,
    tracing_task_message,
)
from scripts.unified_campaign.dataset import load_plan
from scripts.unified_campaign.expectations import build_expectations
from scripts.unified_campaign.guard import (
    CampaignGuardError,
    assert_execution_allowed,
    assert_target_allowed,
)
from scripts.unified_campaign.iam_catalog import inspect_migrations
from scripts.unified_campaign.reconcile import reconcile
from scripts.unified_campaign.records import percentile_cont
from scripts.unified_campaign.sender import _visible_text, send_sample


def test_guard_rejects_formal_targets() -> None:
    for value in (
        "postgresql://role:secret@127.0.0.1:15432/other",
        "postgresql://role:secret@127.0.0.1:19132/other",
        "postgresql://role:secret@127.0.0.1:15440/air_agent_business",
        "postgresql://role:secret@127.0.0.1:15440/iam_identity",
        "http://127.0.0.1:15432",
    ):
        try:
            assert_target_allowed(value)
        except CampaignGuardError:
            continue
        raise AssertionError(value)
    assert_target_allowed("postgresql://role:secret@127.0.0.1:15440/campaign_empty")
    assert_target_allowed("http://127.0.0.1:18081")


def test_default_profile_rejects_load_and_real_models() -> None:
    assert_execution_allowed(
        layer="P",
        profile="S",
        allow_real_models=False,
        allow_load=False,
        enable_real_models=False,
        enable_load=False,
    )
    for kwargs in (
        {"layer": "E", "profile": "S", "allow_real_models": True, "allow_load": False},
        {"layer": "P", "profile": "R", "allow_real_models": False, "allow_load": True},
        {"layer": "P", "profile": "F", "allow_real_models": False, "allow_load": False},
    ):
        try:
            assert_execution_allowed(
                **kwargs, enable_real_models=False, enable_load=False
            )
        except CampaignGuardError:
            continue
        raise AssertionError(kwargs)


def test_plan_keeps_forty_question_denominator() -> None:
    samples, digests = load_plan()
    expectations = build_expectations(samples)
    measured = [sample for sample in samples if sample.cohort == "measured"]
    assert len(measured) == 40
    assert expectations["semantic_denominator"] == 40
    assert expectations["by_cohort"]["history"] == 50
    assert expectations["by_cohort"]["representative"] == 2
    assert {
        sample.graph_id for sample in samples if sample.cohort == "representative"
    } == {
        "data-analysis",
        "intelligent-tracing",
    }
    assert digests["dataset_version"] == "bqa-reference-202605-v1"
    assert all(sample.content for sample in samples)


def test_percentile_uses_linear_interpolation() -> None:
    assert percentile_cont([], 0.95) is None
    assert percentile_cont([10, 20, 30], 0.50) == 20
    assert percentile_cont([10, 20, 30], 0.95) == 29


def test_reconcile_requires_history_and_blocks_missing_apis() -> None:
    samples, _digests = load_plan()
    expectations = build_expectations(samples)
    sent = [
        {
            "request_id": sample.sample_id,
            "cohort": sample.cohort,
            "case_id": sample.case_id,
        }
        for sample in samples
    ]
    blocked = reconcile(
        expectations=expectations, sent_rows=sent, metrics=None, insight_sources=None
    )
    assert blocked["status"] == "BLOCKED"
    hidden_history = [row for row in sent if row["cohort"] != "history"]
    failed = reconcile(
        expectations=expectations,
        sent_rows=hidden_history,
        metrics={"question_count": len(hidden_history)},
        insight_sources=[
            {"request_id": row["request_id"], "cluster_id": "c1"}
            for row in hidden_history
        ],
    )
    assert failed["status"] == "FAIL"
    passed = reconcile(
        expectations=expectations,
        sent_rows=sent,
        metrics={"question_count": len(sent)},
        insight_sources=[
            {"request_id": row["request_id"], "cluster_id": "c1"} for row in sent
        ],
    )
    assert passed["status"] == "PASS"


def _sse_frame(event: str, payload: object) -> bytes:
    return (
        f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
    ).encode()


class _FakeBff:
    """Serve a fixed SSE sequence and the confirm endpoint on loopback."""

    def __init__(
        self,
        frames: list[tuple[float, bytes]],
        *,
        confirm: dict[str, object] | None = None,
        stream_content_type: str = "text/event-stream",
    ) -> None:
        self.frames = frames
        self.confirm = confirm or {
            "confirmed": True,
            "request_id": "req-1",
            "attempt_id": "att-1",
            "submission_id": "sub-1",
        }
        self.stream_content_type = stream_content_type
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or "0")
                self.rfile.read(length)
                if self.path == "/api/threads":
                    self._json({"thread_id": "thread-1"})
                    return
                if self.path.endswith("/runs/stream"):
                    self.send_response(200)
                    self.send_header("content-type", owner.stream_content_type)
                    self.end_headers()
                    for delay, chunk in owner.frames:
                        if delay:
                            time.sleep(delay)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    return
                self._json(owner.confirm)

            def _json(self, body: object) -> None:
                payload = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()


def _send_with(base: str, sample: object, deadline_seconds: int | None = None) -> dict:
    target = sample
    if deadline_seconds is not None:
        target = replace(sample, deadline_seconds=deadline_seconds)
    with httpx.Client(timeout=5) as client:
        return send_sample(
            client,
            target,
            bff_base_url=base,
            cookie="session=test",
            tenant_id="tenant-a",
            context_version=1,
        )


def test_sender_records_association_without_answer_text() -> None:
    frames = [
        (0.0, _sse_frame("metadata", {"run_id": "run-1"})),
        (
            0.0,
            _sse_frame(
                "messages",
                [
                    {
                        "type": "ai",
                        "content": "受控回答",
                        "id": "a1",
                        "tool_calls": [],
                        "invalid_tool_calls": [],
                        "additional_kwargs": {"ui_visible": True},
                    },
                    {
                        "run_id": "run-1",
                        "ledger_request_id": "req-1",
                        "ledger_attempt_id": "att-1",
                        "ledger_submission_id": "sub-1",
                    },
                ],
            ),
        ),
    ]
    sample = next(item for item in load_plan()[0] if item.case_id == "BQA-001")
    with _FakeBff(frames) as base:
        row = _send_with(base, sample)
    assert row["request_id"] == "req-1"
    assert row["attempt_id"] == "att-1"
    assert row["submission_id"] == "sub-1"
    assert row["platform_run_id"] == "run-1"
    assert row["answer_associated"] is True
    assert row["association_status"] == "confirmed"
    assert row["primary_result"] == "stream_completed"
    assert "受控回答" not in json.dumps(row)
    assert sample.content not in json.dumps(row)


def test_sender_ignores_history_and_tool_arguments_for_first_answer() -> None:
    history = [
        {"type": "human", "content": "旧问题", "id": "h1"},
        {
            "type": "ai",
            "content": "旧回答",
            "id": "a1",
            "tool_calls": [],
            "invalid_tool_calls": [],
        },
        {"type": "human", "content": "新问题", "id": "h2"},
    ]
    hidden_call = {
        "type": "ai",
        "content": "",
        "id": "a2",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"secret": 1}'},
            }
        ],
        "invalid_tool_calls": [],
        "additional_kwargs": {},
    }
    reasoning = {
        "type": "ai",
        "content": "推理中不应计为首答",
        "id": "a3",
        "tool_calls": [],
        "invalid_tool_calls": [],
        "additional_kwargs": {"ui_visible": False},
    }
    answer = {
        "type": "ai",
        "content": [{"type": "text", "text": "新回答"}],
        "id": "a4",
        "tool_calls": [],
        "invalid_tool_calls": [],
        "additional_kwargs": {"ui_visible": True},
    }
    frames = [
        (0.0, _sse_frame("metadata", {"run_id": "run-1"})),
        (0.0, _sse_frame("values", {"messages": history})),
        (0.0, _sse_frame("messages", [hidden_call, {"run_id": "run-1"}])),
        (0.0, _sse_frame("messages", [reasoning, {"run_id": "run-1"}])),
        (0.08, _sse_frame("messages", [answer, {"run_id": "run-1"}])),
        (
            0.0,
            _sse_frame("values", {"messages": [*history, answer]}),
        ),
    ]
    sample = next(item for item in load_plan()[0] if item.case_id == "BQA-001")
    with _FakeBff(frames) as base:
        row = _send_with(base, sample)
    assert row["primary_result"] == "stream_completed"
    assert row["answer_nonempty"] is True
    assert row["client_first_answer_ms"] is not None
    assert row["client_first_answer_ms"] >= 70
    assert row["answer_sha256"] == hashlib.sha256("新回答".encode()).hexdigest()


def test_sender_distinguishes_timeout_from_business_failure() -> None:
    frames = [
        (0.0, _sse_frame("metadata", {"run_id": "run-1"})),
        (
            0.0,
            _sse_frame(
                "values",
                {
                    "messages": [
                        {"type": "ai", "content": "旧回答", "id": "a1"},
                        {"type": "human", "content": "新问题", "id": "h2"},
                    ]
                },
            ),
        ),
        (0.6, _sse_frame("messages", [])),
    ]
    sample = next(item for item in load_plan()[0] if item.case_id == "BQA-001")
    with _FakeBff(frames) as base:
        row = _send_with(base, sample, deadline_seconds=0.2)
    assert row["stream_status"] == "timeout"
    assert row["primary_result"] == "client_timeout"
    assert row["client_first_answer_ms"] is None
    assert row["answer_nonempty"] is False


def test_sender_rejects_ledger_association_mismatch() -> None:
    frame = _sse_frame(
        "messages",
        [
            {
                "type": "ai",
                "content": "回答",
                "id": "a1",
                "tool_calls": [],
                "invalid_tool_calls": [],
            },
            {"run_id": "run-1", "ledger_request_id": "req-A"},
        ],
    )
    frames = [(0.0, _sse_frame("metadata", {"run_id": "run-1"})), (0.0, frame)]
    confirm = {
        "confirmed": True,
        "request_id": "req-B",
        "attempt_id": "att-1",
        "submission_id": "sub-1",
    }
    sample = next(item for item in load_plan()[0] if item.case_id == "BQA-001")
    with _FakeBff(frames, confirm=confirm) as base:
        row = _send_with(base, sample)
    assert row["association_status"] == "mismatch"
    assert row["primary_result"] == "association_mismatch"
    assert row["answer_associated"] is False


def test_sender_marks_unconfirmed_without_answer() -> None:
    frames = [(0.0, _sse_frame("metadata", {"run_id": "run-1"}))]
    sample = next(item for item in load_plan()[0] if item.case_id == "BQA-001")
    with _FakeBff(frames, confirm={"confirmed": False}) as base:
        row = _send_with(base, sample)
    assert row["primary_result"] == "unconfirmed"
    assert row["answer_nonempty"] is False
    with _FakeBff(frames) as base:
        row = _send_with(base, sample)
    assert row["primary_result"] == "no_answer"
    assert row["business_status"] == "confirmed"


def test_visible_text_excludes_reasoning_and_tool_calls() -> None:
    assert (
        _visible_text(
            {
                "type": "ai",
                "content": "<think>内部推理</think>最终回答",
                "tool_calls": [],
            }
        )
        == "最终回答"
    )
    assert (
        _visible_text(
            {
                "type": "ai",
                "content": [
                    {"type": "reasoning", "text": "推理"},
                    {"type": "text", "text": "正文"},
                ],
                "tool_calls": [],
            }
        )
        == "正文"
    )
    assert (
        _visible_text(
            {
                "type": "ai",
                "content": "准备调用",
                "tool_calls": [{"function": {"name": "x"}}],
            }
        )
        is None
    )
    assert _visible_text({"type": "human", "content": "问题"}) is None
    assert _visible_text({"type": "tool", "content": "结果"}) is None


def test_controlled_model_does_not_return_immediately() -> None:
    server = ControlledDependencyServer(first_delay_s=0.05, chunk_interval_s=0.0)
    server.start()
    try:
        started = __import__("time").monotonic()
        response = httpx.post(
            f"{server.url}/v1/chat/completions",
            json={"stream": False, "messages": [{"role": "user", "content": "ping"}]},
            timeout=5,
        )
        elapsed = __import__("time").monotonic() - started
        stats = httpx.get(f"{server.url}/stats", timeout=5)
    finally:
        server.stop()
    assert response.status_code == 200
    assert elapsed >= 0.05
    assert "什么是AQI" not in response.text
    assert server.chat_calls == 1
    assert stats.status_code == 200
    assert stats.json() == {
        "chat_calls": 1,
        "mcp_calls": 0,
        "mcp_tool_calls": 0,
        "embedding_calls": 0,
    }


def test_controlled_model_freezes_before_tracing_tasks() -> None:
    server = ControlledDependencyServer(first_delay_s=0.0, chunk_interval_s=0.0)
    server.start()
    tools = [
        {"type": "function", "function": {"name": "freeze_tracing_request"}},
        {"type": "function", "function": {"name": "task"}},
    ]
    try:
        first = httpx.post(
            f"{server.url}/v1/chat/completions",
            json={
                "stream": False,
                "tools": tools,
                "messages": [{"role": "user", "content": "洛阳市2026年5月7日PM2.5"}],
            },
            timeout=5,
        )
        blocked = httpx.post(
            f"{server.url}/v1/chat/completions",
            json={
                "stream": False,
                "tools": tools,
                "messages": [{"role": "tool", "content": '{"status": "invalid"}'}],
            },
            timeout=5,
        )
        delegated = httpx.post(
            f"{server.url}/v1/chat/completions",
            json={
                "stream": False,
                "tools": tools,
                "messages": [{"role": "tool", "content": '{"status": "frozen"}'}],
            },
            timeout=5,
        )
    finally:
        server.stop()
    first_call = first.json()["choices"][0]["message"]["tool_calls"][0]
    assert first_call["function"]["name"] == "freeze_tracing_request"
    assert json.loads(first_call["function"]["arguments"])["region_name"] == "洛阳市"
    assert "工业" not in first.text
    assert blocked.json()["choices"][0]["message"]["content"] == "受控依赖已收到请求。"
    kinds = [
        __import__("json").loads(item["function"]["arguments"])["subagent_type"]
        for item in delegated.json()["choices"][0]["message"]["tool_calls"]
    ]
    assert kinds == ["station-data", "grid-data"]


def test_plan_command_omits_question_text(tmp_path: Path) -> None:
    out = tmp_path / "plan"
    assert main(["plan", "--out", str(out)]) == 0
    text = (out / "campaign.json").read_text()
    assert "什么是AQI？它和污染物浓度有什么区别？" not in text
    assert "bqa-reference-202605-v1" in text
    assert '"allow_load": false' in text


def test_smoke_command_rejects_formal_port(tmp_path: Path) -> None:
    endpoints = tmp_path / "endpoints.json"
    endpoints.write_text(
        json.dumps(
            {
                "bff_base_url": "http://127.0.0.1:15432",
                "admin_base_url": "http://127.0.0.1:18081",
                "tenant_id": "t",
                "context_version": 1,
                "cookie_env": "CAMPAIGN_COOKIE",
                "layer": "P",
                "profile": "S",
                "allow_real_models": False,
                "allow_load": False,
            }
        )
    )
    code = main(
        [
            "smoke",
            "--endpoints",
            str(endpoints),
            "--performance-out",
            str(tmp_path / "perf"),
            "--evidence-out",
            str(tmp_path / "evidence"),
        ]
    )
    assert code == 2
    assert not (tmp_path / "perf").exists()


def test_iam_proposal_does_not_grant_root(tmp_path: Path) -> None:
    migration = tmp_path / "V27__agent_feedback_manage.sql"
    migration.write_text(
        "INSERT INTO iam.permissions VALUES ('agent:feedback:manage');"
    )
    (tmp_path / "V9__member_user_brief.sql").write_text("SELECT 1;")
    result = inspect_migrations(tmp_path)
    assert result["latest_version"] == "V27"
    assert result["proposed_next_version"] == "V28"
    assert result["insights_manage_registered"] is False
    assert result["feedback_manage_registered"] is True
    sql = str(result["proposed_sql"]).upper()
    assert "GRANT " not in sql
    assert "INSERT INTO IAM.ROLE" not in sql
    assert "agent:insights:manage" in str(result["proposed_sql"])


def test_sender_records_already_confirmed_as_reuse() -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or "0")
            self.rfile.read(length)
            if self.path == "/api/threads":
                body = {"thread_id": "thread-1"}
            else:
                body = {
                    "status": "already_confirmed",
                    "run_id": "run-1",
                    "ledger_request_id": "req-1",
                    "ledger_attempt_id": "att-1",
                    "ledger_submission_id": "sub-1",
                }
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    sample = next(item for item in load_plan()[0] if item.case_id == "BQA-001")
    try:
        with httpx.Client(timeout=5) as client:
            row = send_sample(
                client,
                sample,
                bff_base_url=f"http://{host}:{port}",
                cookie="session=test",
                tenant_id="tenant-a",
                context_version=1,
                thread_id="thread-1",
            )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
    assert row["primary_result"] == "reused"
    assert row["request_id"] == "req-1"
    assert row["platform_run_id"] == "run-1"
    assert row["counts_as_semantic_sample"] is False


def test_controlled_city_extraction_keeps_only_the_place_name() -> None:
    assert (
        _extract_city("请对测试城市洛阳市2026年5月7日的PM2.5做一次隔离溯源连通检查")
        == "洛阳市"
    )
    assert _extract_city("按小时播报菜单查询洛阳市2026年5月7日PM2.5") == "洛阳市"
    assert _extract_city("只使用本次测试数据") == ""
    arguments = json.loads(
        _freeze_arguments(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "请对测试城市洛阳市2026年5月7日的PM2.5做一次隔离溯源连通检查",
                    }
                ]
            }
        )
    )
    assert arguments["region_name"] == "洛阳市"
    assert arguments["start"].startswith("2026-05-07")


def test_controlled_delegates_after_a_freeze_tool_call() -> None:
    tools = [
        {"type": "function", "function": {"name": "freeze_tracing_request"}},
        {"type": "function", "function": {"name": "task"}},
    ]
    messages = [
        {
            "role": "user",
            "content": "请对测试城市洛阳市2026年5月7日的PM2.5做一次隔离溯源连通检查",
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "freeze-1",
                    "type": "function",
                    "function": {
                        "name": "freeze_tracing_request",
                        "arguments": "{}",
                    },
                }
            ],
        },
        {"role": "tool", "content": '{"status": "frozen"}'},
    ]
    delegation = tracing_task_message({"tools": tools, "messages": messages})
    assert delegation is not None
    assert [call["function"]["name"] for call in delegation["tool_calls"]] == [
        "task",
        "task",
    ]
    kinds = [
        json.loads(call["function"]["arguments"])["subagent_type"]
        for call in delegation["tool_calls"]
    ]
    assert kinds == ["station-data", "grid-data"]


def test_controlled_does_not_delegate_after_failed_freeze() -> None:
    tools = [
        {"type": "function", "function": {"name": "freeze_tracing_request"}},
        {"type": "function", "function": {"name": "task"}},
    ]
    messages = [
        {"role": "user", "content": "请对测试城市洛阳市2026年5月7日的PM2.5溯源"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "freeze-1",
                    "type": "function",
                    "function": {"name": "freeze_tracing_request", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "content": '{"status": "invalid", "message": "缺少行政区"}'},
    ]
    assert tracing_task_message({"tools": tools, "messages": messages}) is None


def _reconcile_expectations(**overrides: object) -> dict:
    payload = {
        "request_ids_expected_after_send": 2,
        "history_must_remain_visible": 0,
    }
    payload.update(overrides)
    return payload


def test_reconcile_allows_multi_intent_across_clusters() -> None:
    sent = [
        {"request_id": "r1", "cohort": "measured"},
        {"request_id": "r2", "cohort": "measured"},
    ]
    sources = [
        {"request_id": "r1", "intent_id": "i1", "split_index": 0, "cluster_id": "c1"},
        {"request_id": "r1", "intent_id": "i2", "split_index": 1, "cluster_id": "c2"},
        {"request_id": "r2", "intent_id": "i3", "split_index": 0, "cluster_id": None},
    ]
    result = reconcile(
        expectations=_reconcile_expectations(multi_intent_request_ids=["r1"]),
        sent_rows=sent,
        metrics={"question_count": 2},
        insight_sources=sources,
    )
    assert result["status"] == "PASS", result
    counts = next(
        check
        for check in result["checks"]
        if check["check_id"] == "change7-intent-counts"
    )
    assert counts["actual"]["intents"] == 3
    assert counts["actual"]["classified"] == 2
    assert counts["actual"]["unclassified"] == 1
    assert counts["actual"]["multi_intent_requests"] == 1


def test_reconcile_rejects_duplicate_cluster_for_same_intent() -> None:
    sent = [
        {"request_id": "r1", "cohort": "measured"},
        {"request_id": "r2", "cohort": "measured"},
    ]
    sources = [
        {"request_id": "r1", "intent_id": "i1", "split_index": 0, "cluster_id": "c1"},
        {"request_id": "r1", "intent_id": "i1", "split_index": 0, "cluster_id": "c2"},
        {"request_id": "r2", "intent_id": "i3", "split_index": 0, "cluster_id": "c1"},
    ]
    result = reconcile(
        expectations=_reconcile_expectations(),
        sent_rows=sent,
        metrics={"question_count": 2},
        insight_sources=sources,
    )
    assert result["status"] == "FAIL"
    failed = {
        check["check_id"] for check in result["checks"] if check["status"] == "FAIL"
    }
    assert "change7-single-effective-cluster" in failed


def test_reconcile_rejects_two_intents_for_one_split() -> None:
    sent = [{"request_id": "r1", "cohort": "measured"}]
    sources = [
        {"request_id": "r1", "intent_id": "i1", "split_index": 0, "cluster_id": "c1"},
        {"request_id": "r1", "intent_id": "i2", "split_index": 0, "cluster_id": "c2"},
    ]
    result = reconcile(
        expectations=_reconcile_expectations(request_ids_expected_after_send=1),
        sent_rows=sent,
        metrics={"question_count": 1},
        insight_sources=sources,
    )
    assert result["status"] == "FAIL"
    failed = {
        check["check_id"] for check in result["checks"] if check["status"] == "FAIL"
    }
    assert "change7-intent-identity" in failed


def test_reconcile_requires_declared_multi_intent_split() -> None:
    sent = [{"request_id": "r1", "cohort": "measured"}]
    sources = [
        {"request_id": "r1", "intent_id": "i1", "split_index": 0, "cluster_id": "c1"}
    ]
    result = reconcile(
        expectations=_reconcile_expectations(
            request_ids_expected_after_send=1, multi_intent_request_ids=["r1"]
        ),
        sent_rows=sent,
        metrics={"question_count": 1},
        insight_sources=sources,
    )
    assert result["status"] == "FAIL"
    assert any(
        check["check_id"] == "change7-declared-multi-intent"
        for check in result["checks"]
    )


def test_reconcile_blocks_missing_insight_source() -> None:
    sent = [{"request_id": "r1", "cohort": "measured"}]
    result = reconcile(
        expectations=_reconcile_expectations(request_ids_expected_after_send=1),
        sent_rows=sent,
        metrics={"question_count": 1},
        insight_sources=None,
    )
    assert result["status"] == "BLOCKED"
    blocked = {
        check["check_id"] for check in result["checks"] if check["status"] == "BLOCKED"
    }
    assert {"change7-source-coverage", "change7-intent-identity"} <= blocked


def test_business_checks_flag_failed_streams_and_missing_association() -> None:
    common = {"run_id": "run-1", "cohort": "measured", "sent": True}
    good = {
        **common,
        "sample_id": "s1",
        "primary_result": "stream_completed",
        "answer_nonempty": True,
        "answer_associated": True,
        "client_first_answer_ms": 10,
        "attempt_id": "a1",
        "request_id": "r1",
    }
    bad = {
        **common,
        "sample_id": "s2",
        "primary_result": "unconfirmed",
        "answer_nonempty": False,
        "answer_associated": False,
        "client_first_answer_ms": None,
        "attempt_id": "a2",
        "request_id": "r2",
    }
    calls = [
        {"attempt_id": "a1", "source": "admin-observations", "status": "ok"},
    ]
    checks = _business_checks(
        "run-1",
        [good, bad],
        calls,
        {"question_count": 10},
        {"question_count": 12},
    )
    by_id = {item["check_id"]: item for item in checks}
    assert by_id["s1:stream"]["status"] == "PASS"
    assert by_id["s2:stream"]["status"] == "FAIL"
    assert by_id["s2:association"]["status"] == "FAIL"
    assert by_id["aggregate:change6-question-delta"]["status"] == "PASS"


def test_resource_rows_state_not_collected_without_containers() -> None:
    class Usage:
        ru_maxrss = 10
        ru_utime = 1.0
        ru_stime = 0.5

    rows = _resource_rows("run-1", {}, {}, Usage(), Usage(), 1.5)
    components = {row["component"]: row for row in rows}
    assert components["campaign-client"]["measurement_status"] == "measured"
    assert components["server-containers"]["measurement_status"] == "not_collected"


def test_collect_calls_never_fabricates_missing_observations() -> None:
    class Probe:
        def __init__(self, responses: list[tuple[int | None, object]]) -> None:
            self.responses = responses
            self.index = 0

        def get(self, path: str, params: dict | None = None) -> tuple:
            response = self.responses[min(self.index, len(self.responses) - 1)]
            self.index += 1
            return response

    sent = [{"attempt_id": "a1", "request_id": "r1"}]
    missing = _collect_calls(
        Probe([(200, {"items": [], "next_cursor": None})]),
        sent,
        since=__import__("datetime").datetime.now(__import__("datetime").UTC),
        run_id="run-1",
    )
    assert missing[0]["usage_status"] == "not_collected"
    unavailable = _collect_calls(
        Probe([(503, None)]),
        sent,
        since=__import__("datetime").datetime.now(__import__("datetime").UTC),
        run_id="run-1",
    )
    assert unavailable[0]["usage_status"] == "not_collected"
    measured = _collect_calls(
        Probe(
            [
                (
                    200,
                    {
                        "items": [
                            {
                                "observation_id": "o1",
                                "attempt_id": "a1",
                                "request_id": "r1",
                                "status": "succeeded",
                                "input_tokens": 3,
                                "output_tokens": 4,
                                "total_tokens": 7,
                            },
                            {"observation_id": "o2", "attempt_id": "other"},
                        ],
                        "next_cursor": None,
                    },
                )
            ]
        ),
        sent,
        since=__import__("datetime").datetime.now(__import__("datetime").UTC),
        run_id="run-1",
    )
    assert len(measured) == 1
    assert measured[0]["usage_status"] == "provided"
    assert measured[0]["attempt_id"] == "a1"


def test_controlled_embeddings_are_deterministic_and_local() -> None:
    server = ControlledDependencyServer(first_delay_s=0.0, chunk_interval_s=0.0)
    server.start()
    try:
        response = httpx.post(
            f"{server.url}/v1/embeddings",
            json={
                "model": "controlled-embedding",
                "input": [
                    "洛阳市今天PM2.5",
                    "洛阳市今天PM2.5",
                    "郑州市今天PM2.5",
                ],
            },
            timeout=5,
        )
        stats = httpx.get(f"{server.url}/stats", timeout=5).json()
    finally:
        server.stop()
    vectors = [item["embedding"] for item in response.json()["data"]]
    assert len(vectors) == 3
    assert len(vectors[0]) == 64
    assert vectors[0] == vectors[1]
    assert sum(a * b for a, b in zip(vectors[0], vectors[2])) < 0.99
    assert stats["embedding_calls"] == 1
    assert server.embedding_calls == 1


def test_reconcile_counts_gap_as_coverage_not_intent() -> None:
    sent = [{"request_id": "r1", "cohort": "measured"}]
    sources = [
        {"request_id": "r1", "gap_reason": "missing_content"},
    ]
    result = reconcile(
        expectations=_reconcile_expectations(request_ids_expected_after_send=1),
        sent_rows=sent,
        metrics={"question_count": 1},
        insight_sources=sources,
    )
    checks = {check["check_id"]: check for check in result["checks"]}
    assert checks["change7-source-coverage"]["status"] == "PASS"
    assert checks["change7-intent-identity"]["status"] == "PASS"
    assert checks["change7-intent-counts"]["actual"]["intents"] == 0


def test_controlled_mcp_serves_tracing_tools() -> None:
    server = ControlledDependencyServer(first_delay_s=0.0, chunk_interval_s=0.0)
    server.start()
    try:
        initialize = httpx.post(
            f"{server.url}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            },
            timeout=5,
        )
        listing = httpx.post(
            f"{server.url}/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            timeout=5,
        )
        call = httpx.post(
            f"{server.url}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "mcp_station_base_air_station_list",
                    "arguments": {"region": "洛阳市"},
                },
            },
            timeout=5,
        )
        notification = httpx.post(
            f"{server.url}/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            timeout=5,
        )
    finally:
        server.stop()
    assert initialize.json()["result"]["protocolVersion"] == "2025-03-26"
    names = [item["name"] for item in listing.json()["result"]["tools"]]
    assert "mcp_station_common_air_data" in names
    payload = json.loads(call.json()["result"]["content"][0]["text"])
    assert payload["data"]["rows"][0]["stationKey"] == "S001"
    assert notification.status_code == 202
    assert server.mcp_tool_calls == 1


def test_controlled_delegates_specialists_in_required_stages() -> None:
    tools = [
        {"type": "function", "function": {"name": "freeze_tracing_request"}},
        {"type": "function", "function": {"name": "task"}},
    ]
    messages: list[dict] = [
        {"role": "user", "content": "请对测试城市洛阳市2026年5月7日的PM2.5溯源"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "freeze-1",
                    "type": "function",
                    "function": {"name": "freeze_tracing_request", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "content": '{"status": "frozen"}'},
    ]

    def kinds(message: dict | None) -> list[str]:
        assert message is not None
        return [
            json.loads(call["function"]["arguments"])["subagent_type"]
            for call in message["tool_calls"]
        ]

    first = tracing_task_message({"tools": tools, "messages": messages})
    assert kinds(first) == ["station-data", "grid-data"]
    messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "task-station-data-1",
                    "type": "function",
                    "function": {
                        "name": "task",
                        "arguments": json.dumps(
                            {"description": "站点", "subagent_type": "station-data"}
                        ),
                    },
                },
                {
                    "id": "task-grid-data-1",
                    "type": "function",
                    "function": {
                        "name": "task",
                        "arguments": json.dumps(
                            {"description": "网格", "subagent_type": "grid-data"}
                        ),
                    },
                },
            ],
        }
    )
    messages.append({"role": "tool", "content": '{"station":"ok"}'})
    messages.append({"role": "tool", "content": '{"grid":"ok"}'})

    second = tracing_task_message({"tools": tools, "messages": messages})
    assert kinds(second) == ["meteo-analyst"]
    messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "task-meteo-analyst-1",
                    "type": "function",
                    "function": {
                        "name": "task",
                        "arguments": json.dumps(
                            {"description": "气象", "subagent_type": "meteo-analyst"}
                        ),
                    },
                }
            ],
        }
    )
    messages.append({"role": "tool", "content": '{"summary":"meteo"}'})

    third = tracing_task_message({"tools": tools, "messages": messages})
    assert kinds(third) == ["evidence-reviewer"]
    messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "task-evidence-reviewer-1",
                    "type": "function",
                    "function": {
                        "name": "task",
                        "arguments": json.dumps(
                            {
                                "description": "审查",
                                "subagent_type": "evidence-reviewer",
                            }
                        ),
                    },
                }
            ],
        }
    )
    messages.append({"role": "tool", "content": '{"review":"ok"}'})
    assert tracing_task_message({"tools": tools, "messages": messages}) is None
