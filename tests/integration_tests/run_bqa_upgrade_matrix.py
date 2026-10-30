"""Owned SDK matrix and coordinated lifecycle/policy/cache upgrade drill.

Only staged source copies and new empty containers are changed. The real
runtime, IAM service, repository sources and production data remain untouched.
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import psycopg
from langgraph_sdk import get_sync_client

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "owned_native", HERE / "run_bqa_native_capabilities.py"
)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
base = native.base
base.PREFIX = "bqa-cap-v2-matrix-20261029"
base.PORTS = {"pg": 25604, "redis": 25605, "api": 25606, "mock": 25607, "admin": 25608}
native.MCP_PORT = 25609
STAGED_ADMIN_PORT = 25610
REPORT = native.EVIDENCE
STAGE = native.STAGE
DOMAIN = "mcp_city_common_get_air_quality_realtime_stat"
ADMIN_PROCESS = None
MCP_SERVER = None
ORIGINAL_API_ARGS = None
OLD_REGISTRY = (
    base.REPO
    / ".local/bqa-session-capabilities-v2/preimplementation/src/common/business_registry.py"
)


class Session:
    """Use actual admission, tickets, platform SDK and state observations."""

    def __init__(self):
        self.admin = httpx.Client(
            base_url=f"http://127.0.0.1:{STAGED_ADMIN_PORT}",
            headers={"origin": "http://127.0.0.1:3000"},
            timeout=90,
        )
        self.threads = []
        self.refresh()

    def refresh(self):
        base.check_response(
            self.admin.post(
                "/auth/login",
                json={"account": "fixture", "password": "synthetic-password"},
            )
        )
        self.admin.headers.update(
            {
                "x-tenant-id": base.IDS["tenant"],
                "x-tenant-context-version": "1",
                "x-internal-token": base.INTERNAL,
            }
        )
        ticket = base.check_response(
            self.admin.post(
                "/auth/runtime-ticket",
                json={
                    "target_tenant_id": base.IDS["tenant"],
                    "expected_ctx_version": 1,
                },
            )
        )["ticket"]
        self.headers = {"authorization": "Bearer " + ticket}
        self.sdk = get_sync_client(
            url=f"http://127.0.0.1:{base.PORTS['api']}",
            headers=self.headers,
            timeout=90,
        )

    def thread(self):
        thread = self.sdk.threads.create(
            metadata={
                "tenant_id": base.IDS["tenant"],
                "user_id": base.IDS["user"],
                "membership_id": base.IDS["membership"],
                "session_id": base.IDS["session"],
            }
        )["thread_id"]
        with httpx.Client() as http:
            result = http.post(
                f"http://127.0.0.1:{base.PORTS['api']}/internal/threads/claim-confirmation",
                json={"thread_id": thread},
                headers={**self.headers, "x-internal-token": base.INTERNAL},
            )
            assert base.check_response(result)["confirmed"]
        self.threads.append(thread)
        return thread

    def admit(self, thread, question, *, source=None):
        body = {
            "thread_id": thread,
            "submission_key": str(uuid.uuid4()),
            "graph_id": "basic-qa",
            "content": question,
        }
        if source:
            body.update(operation_kind="resume", source_attempt_id=source["attempt_id"])
        else:
            body["capability_selection"] = {
                "skill": {"mode": "auto"},
                "mcp": {"mode": "auto"},
            }
        return base.check_response(self.admin.post("/agent/requests", json=body))

    def run(
        self,
        thread,
        admission,
        question=None,
        *,
        pause=False,
        resume=False,
        mode="fast",
    ):
        metadata = {
            "ledger_request_id": admission["request_id"],
            "ledger_attempt_id": admission["attempt_id"],
            "ledger_submission_id": admission["submission_id"],
            "ledger_graph_id": "basic-qa",
            "ledger_business_code": "basic-qa",
            "ledger_operation_kind": "resume" if resume else "initial",
        }
        before = len(native.STATE["executions"])
        self.sdk.runs.wait(
            thread,
            "basic-qa",
            input=None
            if resume
            else {"messages": [{"role": "user", "content": question}]},
            metadata=metadata,
            config={"configurable": {"chat_mode": mode}},
            interrupt_before=["call_model"] if pause else None,
        )
        info = self.sdk.runs.list(thread)[0]
        base.check_response(
            self.admin.post(
                "/agent/runs/admission-confirmed",
                json={
                    "request_id": admission["request_id"],
                    "attempt_id": admission["attempt_id"],
                    "submission_id": admission["submission_id"],
                    "outcome": "confirmed",
                    "platform_run_id": info["run_id"],
                },
            )
        )
        state = self.sdk.threads.get_state(thread)
        if pause:
            base.check_response(
                self.admin.post(
                    f"/agent/runs/{admission['attempt_id']}/state",
                    json={
                        "state": "waiting_input",
                        "evidence_source": "platform_read",
                        "platform_status": info["status"],
                    },
                )
            )
        return state, native.STATE["executions"][before:]

    def query(self, thread, question):
        return self.run(thread, self.admit(thread, question), question)


def admin_process(start):
    global ADMIN_PROCESS
    if ADMIN_PROCESS is not None:
        ADMIN_PROCESS.terminate()
        ADMIN_PROCESS.wait(timeout=15)
        ADMIN_PROCESS = None
    if start:
        env = dict(
            os.environ,
            PYTHONPATH=f"{STAGE}:{base.REPO / 'admin/api'}",
            LANGGRAPH_API_URL=f"http://127.0.0.1:{base.PORTS['api']}",
        )
        log = (REPORT / f"staged-admin-{time.time_ns()}.log").open("w")
        ADMIN_PROCESS = subprocess.Popen(
            [
                str(base.REPO / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(STAGED_ADMIN_PORT),
                "--no-access-log",
            ],
            env=env,
            cwd=STAGE,
            stdout=log,
            stderr=log,
        )
        log.close()
        base.wait_http(f"http://127.0.0.1:{STAGED_ADMIN_PORT}/health")


def stop_api(label):
    name = base.PREFIX + "-api"
    (REPORT / f"{label}-api.log").write_text(
        base.command(["container", "logs", "-n", "180", name])
    )
    base.command(["container", "delete", "--force", name])
    base.CREATED.remove(name)


def start_api():
    native.ORIGINAL_START("api", native.API_ARGS)
    base.wait_http(f"http://127.0.0.1:{base.PORTS['api']}/ok")


def set_api_env(name, value):
    prefix = name + "="
    for i, arg in enumerate(native.API_ARGS):
        if arg.startswith(prefix):
            native.API_ARGS[i] = prefix + value
            return
    native.API_ARGS[-1:-1] = ["-e", prefix + value]


def calls(state):
    rows = state["values"]["messages"]
    last = max(i for i, m in enumerate(rows) if m["type"] == "human")
    return [c["name"] for m in rows[last + 1 :] for c in m.get("tool_calls", [])]


def rejected_resume(session, thread, source, *, mode="fast", reason):
    resumed = session.admit(thread, "", source=source)
    before = len(native.STATE["executions"])
    try:
        session.run(thread, resumed, resume=True, mode=mode)
    except Exception as exc:  # noqa: BLE001 - an actual SDK error is the negative result
        error_type = type(exc).__name__
    else:
        # runs.wait can return the saved values even when the worker errored.
        # The authoritative platform status and physical call counter below
        # remain required; HTTP/SDK return alone is not successful execution.
        error_type = None
    actual = session.sdk.runs.list(thread)[0]
    assert actual["status"] == "error"
    assert len(native.STATE["executions"]) == before
    # This is the real platform observation, never an invented SQL state edit.
    base.check_response(
        session.admin.post(
            f"/agent/runs/{source['attempt_id']}/state",
            json={
                "state": "failed",
                "evidence_source": "platform_read",
                "platform_status": actual["status"],
            },
        )
    )
    base.EVIDENCE["checks"].append(
        {
            "name": reason,
            "actual_platform_status": actual["status"],
            "sdk_error_type": error_type,
            "new_business_calls": 0,
        }
    )


def drain(session):
    rows = [r for thread in session.threads for r in session.sdk.runs.list(thread)]
    assert not [r for r in rows if r["status"] in {"pending", "running"}]
    assert all(session.sdk.threads.get(t)["status"] == "idle" for t in session.threads)
    base.EVIDENCE["checks"].append(
        {
            "name": "owned_all_registered_graphs_drain",
            "live_owned_runs": 0,
            "known_affected_graphs": [
                "basic-qa",
                "data-analysis",
                "intelligent-tracing",
            ],
            "limitation": "only Basic QA had runs in this owned fixture; formal runs were not inspected or stopped",
        }
    )


def migration(target, label):
    log = base.command(
        [
            str(base.REPO / ".venv/bin/python"),
            "-m",
            "alembic",
            "upgrade" if target == "head" else "downgrade",
            target,
        ],
        env=dict(os.environ),
        cwd=base.REPO / "admin/api",
    )
    (REPORT / f"{label}-migration.log").write_text(log)


async def cache_control():
    from langchain_core.tools import StructuredTool

    from basic_qa.capabilities.mcp_catalog import (
        ToolCatalog,
        content_version,
        document_for,
    )
    from basic_qa.capabilities.mcp_index import PostgresIndexStore
    from common.event_ledger.store import close_pool

    async def sample(region: str):
        return region

    doc = document_for(
        "ipp",
        StructuredTool.from_function(
            coroutine=sample, name=DOMAIN, description="owned synthetic cache"
        ),
    )
    cat = ToolCatalog(
        (doc,),
        content_version("owned-cache-scope"),
        content_version("owned-cache-catalog"),
        base.IDS["tenant"],
        base.IDS["user"],
        base.IDS["membership"],
    )
    async with PostgresIndexStore().transaction(
        cat, content_version("owned-cache-model")
    ) as tx:
        assert await tx.existing() == {}
        key = content_version(doc.text)
        await tx.publish({key: [1.0, 0.0]}, {doc.tool_id: key})
    await close_pool()


def checks(_admin, _native):
    global ORIGINAL_API_ARGS
    ORIGINAL_API_ARGS = copy.deepcopy(native.API_ARGS)
    migration("0029_insight_retention", "old-cache-schema")
    admin_process(True)
    session = Session()
    try:
        thread = session.thread()
        first, executions = session.query(thread, "湖州市今天空气质量如何？")
        assert first["values"]["bqa_active_skill"] and len(executions) == 1
        original_load = first["values"]["bqa_active_skill"]["load_call_id"]
        # Pause and legally finish an old run before changing the shared policy.
        waiting = session.admit(thread, "那杭州呢？")
        paused, _ = session.run(thread, waiting, "那杭州呢？", pause=True)
        assert paused["next"] == ["call_model"]
        resumed = session.admit(thread, "", source=waiting)
        session.run(thread, resumed, resume=True)
        drain(session)
        old_count = len(session.sdk.threads.get_state(thread)["values"]["messages"])
        admin_process(False)
        stop_api("old-drained")
        native.lifecycle_source(False)
        shutil.copy2(
            base.REPO / "src/common/business_registry.py",
            STAGE / "common/business_registry.py",
        )
        migration("head", "new-cache-schema")
        asyncio.run(cache_control())
        start_api()
        admin_process(True)
        session.refresh()
        upgraded, executed = session.query(thread, "那金华呢？")
        assert len(executed) == 1 and "load_skill" not in calls(upgraded)
        assert len(upgraded["values"]["messages"]) > old_count
        caps = upgraded["values"]["bqa_capabilities"]
        ref = caps["loaded_skills"]["skill:basic-qa:air-quality-basic-query"]
        base.EVIDENCE["checks"].append(
            {
                "name": "coordinated_policy_lifecycle_prompt_cache_upgrade",
                "load_skill": 0,
                "policy_before": "2026-10-19.1",
                "policy_after": "2026-10-28.1",
                "checkpoint_version": caps["version"],
                "history_retained": True,
            }
        )
        source = session.admit(thread, "那杭州呢？")
        session.run(thread, source, "那杭州呢？", pause=True)
        rejected_resume(
            session,
            thread,
            source,
            mode="expert",
            reason="same_attempt_mode_change_rejected",
        )
        source = session.admit(thread, "那杭州呢？")
        session.run(thread, source, "那杭州呢？", pause=True)
        # A new connection revision and changed descriptor cannot silently reuse
        # an old schema. Test a new user round, without rewriting its selection.
        tool = next(
            t for t in MCP_SERVER._tool_manager.list_tools() if t.name == DOMAIN
        )
        old_description = tool.description
        tool.description += " owned schema revision two"
        stop_api("before-connection-revision")
        connection = next(
            a for a in native.API_ARGS if a.startswith("AGENT_MCP_CONNECTIONS_JSON=")
        )
        payload = json.loads(connection.split("=", 1)[1])
        payload["revision"] = 2
        set_api_env("AGENT_MCP_CONNECTIONS_JSON", json.dumps(payload))
        start_api()
        session.refresh()
        rejected_resume(
            session,
            thread,
            source,
            reason="same_attempt_schema_connection_change_rejected",
        )
        changed, executed = session.query(thread, "再查湖州")
        assert len(executed) == 1 and calls(changed).count("load_skill") == 1
        changed_ref = changed["values"]["bqa_capabilities"]["loaded_skills"][
            "skill:basic-qa:air-quality-basic-query"
        ]
        assert (
            changed_ref["schema_versions"] != ref["schema_versions"]
            and changed_ref["catalog_scope"] != ref["catalog_scope"]
        )
        base.EVIDENCE["checks"].append(
            {
                "name": "new_round_schema_and_connection_change",
                "reloads": 1,
                "business_calls": 1,
            }
        )
        # Re-deploy both consumers of the same staged material; the accepted
        # old request and its history stay unchanged.
        source = session.admit(thread, "那杭州呢？")
        session.run(thread, source, "那杭州呢？", pause=True)
        admin_process(False)
        stop_api("before-material-change")
        marker = "自有验收材料修订标记"
        fast = STAGE / "basic_qa/skills/air-quality-basic-query/references/fast.md"
        fast.write_text(fast.read_text() + "\n\n" + marker + "。\n")
        start_api()
        admin_process(True)
        session.refresh()
        rejected_resume(
            session, thread, source, reason="same_attempt_material_change_rejected"
        )
        material, executed = session.query(thread, "那杭州呢？")
        assert len(executed) == 1 and calls(material).count("load_skill") == 1
        assert marker in json.dumps(
            native.STATE["model_requests"][-1], ensure_ascii=False
        )
        material_ref = material["values"]["bqa_capabilities"]["loaded_skills"][
            "skill:basic-qa:air-quality-basic-query"
        ]
        assert material_ref["material_version"] != changed_ref["material_version"]
        base.EVIDENCE["checks"].append(
            {
                "name": "new_round_material_change",
                "reloads": 1,
                "new_complete_rules_sent": True,
            }
        )
        stop_api("before-forced-summary")
        set_api_env("CONVERSATION_SUMMARY_TRIGGER_RATIO", "0.01")
        set_api_env("CONVERSATION_SUMMARY_KEEP_TURNS", "1")
        start_api()
        session.refresh()
        summarized, executed = session.query(thread, "那金华呢？")
        assert summarized["values"].get("summary") and summarized["values"].get(
            "summary_covered_message_id"
        )
        assert len(executed) == 1 and "load_skill" not in calls(summarized)
        assert (
            summarized["values"]["bqa_capabilities"]["loaded_skills"][
                "skill:basic-qa:air-quality-basic-query"
            ]["load_ref"]
            == material_ref["load_ref"]
        )
        planning = next(
            r for r in reversed(native.STATE["model_requests"]) if r.get("tools")
        )
        assert marker in json.dumps(planning, ensure_ascii=False)
        assert original_load not in json.dumps(planning)
        base.EVIDENCE["checks"].append(
            {
                "name": "persistent_summary_then_capability_reuse",
                "reloads": 0,
                "full_current_rules": True,
                "old_receipt_outside_projection": True,
            }
        )
        drain(session)
        admin_process(False)
        stop_api("new-drained")
        native.lifecycle_source(True)
        shutil.copy2(OLD_REGISTRY, STAGE / "common/business_registry.py")
        shutil.copytree(
            base.REPO / "src/basic_qa/skills",
            STAGE / "basic_qa/skills",
            dirs_exist_ok=True,
        )
        tool.description = old_description
        native.API_ARGS[:] = copy.deepcopy(ORIGINAL_API_ARGS)
        start_api()
        admin_process(True)
        session.refresh()
        rolled, executed = session.query(thread, "再查湖州")
        assert len(executed) == 1 and calls(rolled).count("load_skill") == 1
        assert len(rolled["values"]["messages"]) > old_count
        with psycopg.connect(os.environ["DATABASE_URI"]) as conn:
            count = conn.execute(
                "SELECT count(*) FROM bqa_mcp_index_documents"
            ).fetchone()[0]
            assert count == 1
        base.EVIDENCE["checks"].append(
            {
                "name": "coordinated_rollback_preserves_history_and_cache",
                "policy_restored": "2026-10-19.1",
                "legacy_reload": 1,
                "cache_rows_retained": count,
            }
        )
        base.EVIDENCE["limitations"] = [
            "only owned Basic QA runs existed; no formal runs drained or deployment performed",
            "controlled IAM/model/MCP; real model and browser are separate evidence",
            "cached schemas are refreshed through the tested protected connection revision/redeployment; out-of-band remote hot schema changes were not tested",
        ]
    finally:
        session.admin.close()
        admin_process(False)


if __name__ == "__main__":
    if STAGE.exists():
        raise FileExistsError(STAGE)
    shutil.copytree(
        base.REPO / "src", STAGE, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    native.lifecycle_source(True)
    shutil.copy2(OLD_REGISTRY, STAGE / "common/business_registry.py")
    base.MOCK.router.routes[:] = [
        r
        for r in base.MOCK.router.routes
        if getattr(r, "path", "") != "/v1/chat/completions"
    ]
    base.MOCK.post("/v1/chat/completions")(native.completion)
    actual_fastmcp = native.FastMCP

    def capture_server(*args, **kwargs):
        global MCP_SERVER
        MCP_SERVER = actual_fastmcp(*args, **kwargs)
        return MCP_SERVER

    native.FastMCP = capture_server
    native.NAMES = native.make_mcp()
    base.start_container = native.start_container
    base.run_checks = checks
    base.main()
