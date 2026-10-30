"""Reopen an owned SDK fixture in a new empty service stack and process.

Usage: python restore_probe.py NEW_EVIDENCE_DIR ORIGINAL_EVIDENCE_DIR SOURCE_SRC
All data and credentials are synthetic and confined to the task's .local directory.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import baseline_native as native
import psycopg
from langgraph_sdk import get_sync_client

base = native.base
if "--new-host" in sys.argv:
    base.REPO = Path(__file__).resolve().parents[2]
fixture = Path(sys.argv[2]).resolve()
source = Path(sys.argv[3]).resolve()
allowed = Path(
    "/Users/acerola/Dev/Python/air_agent/.local/graph-module-ownership/20261029-apply"
)
if not fixture.is_relative_to(allowed):
    raise ValueError("fixture must belong to this task")
bootstrap = json.loads((fixture / "fixture-bootstrap.json").read_text())
base.PASSWORD = bootstrap["password"]
base.RUNTIME_PASSWORD = bootstrap["runtime_password"]
base.KEY = bootstrap["key"]
base.INTERNAL = bootstrap["internal"]
base.IDS.update(bootstrap["ids"])
old = json.loads((fixture / "result.json").read_text())


def _restore_database(dsn, env, evidence):
    import time

    for _ in range(80):
        try:
            with psycopg.connect(dsn):
                break
        except psycopg.Error:
            time.sleep(0.25)
    with psycopg.connect(dsn) as conn:
        for role in (
            "air_agent_worker",
            "air_agent_ledger_worker",
            "air_agent_maintenance",
        ):
            conn.execute(
                psycopg.sql.SQL("CREATE ROLE {} NOLOGIN").format(
                    psycopg.sql.Identifier(role)
                )
            )
        conn.execute(
            psycopg.sql.SQL("CREATE ROLE air_agent_runtime LOGIN PASSWORD {}").format(
                psycopg.sql.Literal(base.RUNTIME_PASSWORD)
            )
        )
    with (fixture / "checkpoint-fixture.dump").open("rb") as dump:
        result = subprocess.run(
            [
                "container",
                "exec",
                "-i",
                base.PREFIX + "-pg",
                "pg_restore",
                "-U",
                "air_agent",
                "-d",
                "air_agent_business",
                "--no-owner",
            ],
            stdin=dump,
            capture_output=True,
        )
    (evidence / "restore.log").write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError("fixture restore failed; see private restore.log")


def _checks(admin, http):
    check = base.check_response
    check(
        admin.post(
            "/auth/login", json={"account": "fixture", "password": "synthetic-password"}
        )
    )
    admin.headers.update(
        {
            "x-tenant-id": base.IDS["tenant"],
            "x-tenant-context-version": "1",
            "x-internal-token": base.INTERNAL,
        }
    )
    ticket = check(
        admin.post(
            "/auth/runtime-ticket",
            json={"target_tenant_id": base.IDS["tenant"], "expected_ctx_version": 1},
        )
    )["ticket"]
    sdk = get_sync_client(
        url=str(http.base_url).rstrip("/"),
        headers={"authorization": "Bearer " + ticket},
        timeout=120,
    )
    records = []
    if "--hooks" in sys.argv:
        import uuid

        ticket = check(
            admin.post(
                "/auth/runtime-ticket",
                json={
                    "target_tenant_id": base.IDS["tenant"],
                    "expected_ctx_version": 1,
                },
            )
        )["ticket"]
        http.headers["authorization"] = "Bearer " + ticket
        sdk = get_sync_client(
            url=str(http.base_url).rstrip("/"),
            headers={"authorization": "Bearer " + ticket},
            timeout=120,
        )
        tracing_thread = str(uuid.uuid4())
        sdk.threads.create(
            thread_id=tracing_thread,
            metadata={
                "tenant_id": base.IDS["tenant"],
                "user_id": base.IDS["user"],
                "membership_id": base.IDS["membership"],
                "session_id": base.IDS["session"],
            },
        )
        assert check(
            http.post(
                "/internal/threads/claim-confirmation",
                json={"thread_id": tracing_thread},
                headers={"x-internal-token": base.INTERNAL},
            )
        )["confirmed"]
        content = "你好"
        admission = check(
            admin.post(
                "/agent/requests",
                json={
                    "thread_id": tracing_thread,
                    "submission_key": uuid.uuid4().hex,
                    "graph_id": "intelligent-tracing",
                    "content": content,
                },
            )
        )
        before = (len(native.STATE["model_requests"]), len(native.STATE["executions"]))
        sdk.runs.wait(
            tracing_thread,
            "intelligent-tracing",
            input={"messages": [{"role": "user", "content": content}]},
            metadata={
                "ledger_request_id": admission["request_id"],
                "ledger_attempt_id": admission["attempt_id"],
                "ledger_submission_id": admission["submission_id"],
                "ledger_graph_id": "intelligent-tracing",
                "ledger_business_code": "intelligent-tracing",
                "ledger_operation_kind": "initial",
            },
        )
        state = sdk.threads.get_state(tracing_thread)
        assert (
            not state["next"]
            and sdk.runs.list(tracing_thread)[0]["status"] == "success"
        )
        assert len(native.STATE["model_requests"]) - before[0] == 1
        assert len(native.STATE["executions"]) - before[1] == 0
        assert any(
            m.get("type") == "ai" and m.get("content")
            for m in state["values"].get("messages", [])
        )
        records.append(
            {
                "name": "tracing_native_conversation_positive",
                "run_status": "success",
                "model_requests": 1,
                "tool_requests": 0,
            }
        )
    for key in (
        "thread_id",
        "data_thread_id",
        "paused_thread_id",
        "data_paused_thread_id",
    ):
        if key not in old:
            continue
        state = sdk.threads.get_state(old[key])
        values = state["values"]
        records.append(
            {
                "name": key,
                "next": state["next"],
                "message_count": len(values.get("messages", [])),
                "tool_message_count": sum(
                    m.get("type") == "tool" for m in values.get("messages", [])
                ),
                "capability_version": (values.get("bqa_capabilities") or {}).get(
                    "version"
                ),
                "selected_skill": values.get("selected_skill"),
                "summary": bool(values.get("summary")),
                "watermark": values.get("summary_covered_message_id"),
            }
        )
        assert records[-1]["message_count"] > 0
        if key == "thread_id":
            assert (
                records[-1]["tool_message_count"] > 0
                and records[-1]["capability_version"] == 2
            )
            if any(
                c.get("name") in {"controlled_summary_watermark", "thread_id"}
                and c.get("watermark")
                for c in old["checks"]
            ):
                expected = next(
                    c["watermark"]
                    for c in old["checks"]
                    if c.get("name") in {"controlled_summary_watermark", "thread_id"}
                    and c.get("watermark")
                )
                assert records[-1]["summary"] and records[-1]["watermark"] == expected
        elif key in {"data_thread_id", "data_paused_thread_id"}:
            assert records[-1]["selected_skill"] == "broadcast-hour"
            if old.get("data_paused_thread_id"):
                assert records[-1]["summary"] and records[-1]["watermark"]
                assert records[-1]["tool_message_count"] > 0
                expected = next(
                    c["watermark"]
                    for c in old["checks"]
                    if c.get("name")
                    in {"data_summary_and_pending_checkpoint", "data_thread_id"}
                    and c.get("watermark")
                )
                assert records[-1]["watermark"] == expected
                if key == "data_paused_thread_id" and "--read-only" not in sys.argv:
                    assert state["next"] == ["call_model"]
        else:
            if "--read-only" not in sys.argv:
                assert state["next"] == ["call_model"]
    if old.get("paused_thread_id") and "--read-only" not in sys.argv:
        resumed = check(
            admin.post(
                "/agent/requests",
                json={
                    "thread_id": old["paused_thread_id"],
                    "submission_key": __import__("uuid").uuid4().hex,
                    "graph_id": "basic-qa",
                    "operation_kind": "resume",
                    "source_attempt_id": old["paused_attempt_id"],
                    "content": "",
                },
            )
        )
        before_model, before_tool = (
            len(native.STATE["model_requests"]),
            len(native.STATE["executions"]),
        )
        sdk.runs.wait(
            old["paused_thread_id"],
            "basic-qa",
            input=None,
            metadata={
                "ledger_request_id": resumed["request_id"],
                "ledger_attempt_id": resumed["attempt_id"],
                "ledger_submission_id": resumed["submission_id"],
                "ledger_graph_id": "basic-qa",
                "ledger_business_code": "basic-qa",
                "ledger_operation_kind": "resume",
            },
        )
        final = sdk.threads.get_state(old["paused_thread_id"])
        assert not final["next"] and len(native.STATE["executions"]) > before_tool
        records.append(
            {
                "name": "resume_old_paused_checkpoint",
                "next": final["next"],
                "model_requests": len(native.STATE["model_requests"]) - before_model,
                "tool_requests": len(native.STATE["executions"]) - before_tool,
            }
        )
    if old.get("data_paused_thread_id") and "--read-only" not in sys.argv:
        import uuid

        data_resume = check(
            admin.post(
                "/agent/requests",
                json={
                    "thread_id": old["data_paused_thread_id"],
                    "submission_key": uuid.uuid4().hex,
                    "graph_id": "data-analysis",
                    "operation_kind": "resume",
                    "source_attempt_id": old["data_paused_attempt_id"],
                    "content": "",
                    "menu_name": "CityHourBroadcast",
                },
            )
        )
        before = len(native.STATE["executions"])
        before_model = len(native.STATE["model_requests"])
        sdk.runs.wait(
            old["data_paused_thread_id"],
            "data-analysis",
            input=None,
            config={"configurable": {"menu_name": "CityHourBroadcast"}},
            metadata={
                "ledger_request_id": data_resume["request_id"],
                "ledger_attempt_id": data_resume["attempt_id"],
                "ledger_submission_id": data_resume["submission_id"],
                "ledger_graph_id": "data-analysis",
                "ledger_business_code": "data-analysis",
                "ledger_operation_kind": "resume",
            },
        )
        final = sdk.threads.get_state(old["data_paused_thread_id"])
        base.EVIDENCE["data_resume_diagnostic"] = {
            "next": final["next"],
            "model_requests": len(native.STATE["model_requests"]) - before_model,
            "new_tool_requests": len(native.STATE["executions"]) - before,
            "last_messages": [
                {
                    "type": m.get("type"),
                    "additional_keys": sorted(m.get("additional_kwargs", {})),
                }
                for m in final["values"].get("messages", [])[-4:]
            ],
            "model_tool_names": [
                [t["function"]["name"] for t in req.get("tools", [])]
                for req in native.STATE["model_requests"][before_model:]
            ],
        }
        assert (
            not final["next"]
            and final["values"].get("summary")
            and final["values"].get("summary_covered_message_id")
        )
        assert any(
            row["name"] == "mcp_city_common_get_air_quality_forecast"
            for row in native.STATE["executions"][before:]
        )
        records.append(
            {
                "name": "data_summary_resume",
                "summary": True,
                "watermark": final["values"]["summary_covered_message_id"],
                "next": final["next"],
                "new_tool_requests": len(native.STATE["executions"]) - before,
            }
        )
    if "--new-host" in sys.argv:
        import uuid

        content = "固定数据分析验收：按菜单分析合成数据。"
        bad = admin.post(
            "/agent/requests",
            json={
                "thread_id": old["data_thread_id"],
                "submission_key": uuid.uuid4().hex,
                "graph_id": "data-analysis",
                "content": content,
                "menu_name": "CityHourBroadcast",
                "capability_selection": {
                    "skill": {
                        "mode": "explicit",
                        "skill_id": "skill:data-analysis:monitoring-data",
                    },
                    "mcp": {"mode": "auto"},
                },
            },
        )
        assert bad.status_code == 422, bad.text
        admission = check(
            admin.post(
                "/agent/requests",
                json={
                    "thread_id": old["data_thread_id"],
                    "submission_key": uuid.uuid4().hex,
                    "graph_id": "data-analysis",
                    "content": content,
                    "menu_name": "CityHourBroadcast",
                },
            )
        )
        metadata = {
            "ledger_request_id": admission["request_id"],
            "ledger_attempt_id": admission["attempt_id"],
            "ledger_submission_id": admission["submission_id"],
            "ledger_graph_id": "data-analysis",
            "ledger_business_code": "data-analysis",
            "ledger_operation_kind": "initial",
        }
        before = len(native.STATE["model_requests"])
        sdk.runs.wait(
            old["data_thread_id"],
            "data-analysis",
            input={"messages": [{"role": "user", "content": content}]},
            config={"configurable": {"menu_name": "CityHourBroadcast"}},
            metadata=metadata,
        )
        data_state = sdk.threads.get_state(old["data_thread_id"])
        assert (
            data_state["values"]["selected_skill"] == "broadcast-hour"
            and not data_state["next"]
        )
        records.append(
            {
                "name": "new_host_admission_and_data_continuation",
                "menu_conflict_status": bad.status_code,
                "selected_skill": data_state["values"]["selected_skill"],
                "model_requests": len(native.STATE["model_requests"]) - before,
            }
        )
    if "--hooks" in sys.argv:
        import uuid

        import httpx

        content = "湖州市今天空气质量如何？"
        admission = check(
            admin.post(
                "/agent/requests",
                json={
                    "thread_id": old["thread_id"],
                    "submission_key": uuid.uuid4().hex,
                    "graph_id": "basic-qa",
                    "content": content,
                },
            )
        )
        metadata = {
            "ledger_request_id": admission["request_id"],
            "ledger_attempt_id": admission["attempt_id"],
            "ledger_submission_id": admission["submission_id"],
            "ledger_graph_id": "basic-qa",
            "ledger_business_code": "basic-qa",
            "ledger_operation_kind": "initial",
        }
        inp = {"messages": [{"role": "user", "content": content}]}

        def reject(fn, status):
            before = (
                len(native.STATE["model_requests"]),
                len(native.STATE["executions"]),
            )
            try:
                fn()
            except httpx.HTTPStatusError as exc:
                assert exc.response.status_code == status, (
                    status,
                    exc.response.status_code,
                )
            else:
                raise AssertionError("native hook did not reject")
            assert before == (
                len(native.STATE["model_requests"]),
                len(native.STATE["executions"]),
            )

        reject(
            lambda: sdk.runs.wait(
                old["thread_id"], "data-analysis", input=inp, metadata=metadata
            ),
            409,
        )
        attack = {**inp, "bqa_capabilities": {"version": 2}}
        reject(
            lambda: sdk.runs.wait(
                old["thread_id"], "basic-qa", input=attack, metadata=metadata
            ),
            403,
        )
        reject(
            lambda: list(
                sdk.runs.stream(
                    old["thread_id"], "basic-qa", input=attack, metadata=metadata
                )
            ),
            403,
        )
        path = native.STAGE / "application/business_policies.py"
        original = path.read_text()
        try:
            path.write_text(
                original.replace(
                    'if graph_id == "basic-qa":',
                    'if graph_id == "basic-qa":\n        raise RuntimeError("owned missing-policy fixture")',
                    1,
                )
            )
            native.restart_api("missing-policy")
            reject(
                lambda: sdk.runs.wait(
                    old["thread_id"], "basic-qa", input=inp, metadata=metadata
                ),
                503,
            )
        finally:
            path.write_text(original)
            native.restart_api("policy-restored")
        events = list(
            sdk.runs.stream(old["thread_id"], "basic-qa", input=inp, metadata=metadata)
        )
        assert sdk.runs.list(old["thread_id"])[0]["status"] == "success"
        records.append(
            {
                "name": "native_actual_graph_input_missing_policy_hooks",
                "graph_conflict": 409,
                "invalid_input_wait": 403,
                "invalid_input_stream": 403,
                "missing_policy": 503,
                "zero_external_requests_on_rejection": True,
                "same_reservation_valid_stream": "success",
                "event_count": len(events),
            }
        )
    for key in (
        "thread_id",
        "data_thread_id",
        "paused_thread_id",
        "paused_attempt_id",
        "data_paused_thread_id",
        "data_paused_attempt_id",
    ):
        if key in old:
            base.EVIDENCE[key] = old[key]
    base.EVIDENCE["checks"] = records
    base.EVIDENCE["original_fixture"] = str(fixture)
    base.EVIDENCE["source"] = str(source)
    base.EVIDENCE["limitations"] = [
        "controlled IAM/model and synthetic data; no real-model/browser quality acceptance",
        "data analysis pending tool execution is not covered; the pause is before call_model",
    ]


if native.STAGE.exists():
    raise FileExistsError(native.STAGE)
shutil.copytree(
    source, native.STAGE, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
)
base.MOCK.router.routes[:] = [
    r
    for r in base.MOCK.router.routes
    if getattr(r, "path", "") != "/v1/chat/completions"
]
base.MOCK.post("/v1/chat/completions")(native.completion)
native.NAMES = []
base.prepare_database = _restore_database
base.start_container = native.start_container
base.run_checks = _checks
base.main()
