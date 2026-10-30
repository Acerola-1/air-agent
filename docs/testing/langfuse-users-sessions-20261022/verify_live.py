"""Official Langfuse handler, fixed DeepAgents, LangSmith disabled."""

# Environment must be isolated before importing graph fixtures; output is a CLI report.
# ruff: noqa: E402, T201

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

root = Path(__file__).resolve().parents[3]
os.chdir(root)
sys.path[:0] = [str(root / "src"), str(root / "tests/unit_tests/tracing")]
exec(
    compile(
        (root / "tests/unit_tests/conftest.py").read_text(),
        str(root / "tests/unit_tests/conftest.py"),
        "exec",
    ),
    {"__file__": str(root / "tests/unit_tests/conftest.py")},
)
os.environ.update(
    LANGFUSE_ENABLED="false",
    AGENT_OBSERVABILITY_ENABLED="false",
    LANGCHAIN_TRACING_V2="false",
    LANGSMITH_TRACING="false",
    LANGCHAIN_TRACING="false",
)
import pytest
from dotenv import dotenv_values
from langfuse import Langfuse
from langfuse.api import LangfuseAPI
from langsmith import Client
from test_langfuse_trace_tree import export_run, run_config

import common.event_ledger.lifecycle as lifecycle
import common.event_ledger.store as ledger_store
import common.identity.runtime as identity_runtime

settings = dotenv_values(root / ".env.langgraph")
for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
    os.environ[key] = settings[key]
os.environ.update(LANGFUSE_BASE_URL="http://127.0.0.1:3001", LANGFUSE_ENABLED="true")
from datetime import UTC, datetime

run_stamp = datetime.now(UTC).strftime("%y%m%d%H%M%S%f")
environment = "users-sessions-" + run_stamp
output_dir = Path(__file__).resolve().parent / "reruns" / run_stamp
output_dir.mkdir(parents=True)
client = Langfuse(environment=environment, timeout=5)


async def main():
    """Verify fixed Users/Sessions inputs against the configured local Langfuse."""
    patch = pytest.MonkeyPatch()
    patch.setattr(identity_runtime, "revalidate", AsyncMock())
    patch.setattr(identity_runtime, "_guard_knowledge_context", AsyncMock())
    patch.setattr(lifecycle, "_on_graph_end", AsyncMock())
    patch.setattr(ledger_store, "thread_deletion_exists", AsyncMock(return_value=False))
    import psycopg

    async def no_database(*args, **kwargs):
        raise AssertionError("business database access forbidden")

    patch.setattr(psycopg.AsyncConnection, "connect", no_database)

    def no_sync_database(*args, **kwargs):
        raise AssertionError("business database access forbidden")

    patch.setattr(psycopg, "connect", no_sync_database)
    writes = []

    def forbidden(*a, **kw):
        writes.append(True)
        raise AssertionError("LangSmith client must remain unused")

    patch.setattr(Client, "__init__", forbidden)
    patch.setattr(Client, "create_run", forbidden)
    patch.setattr(Client, "update_run", forbidden)
    rows = []
    user_a, user_b, tenant_a, tenant_b, thread_a, thread_b, thread_c = [
        str(uuid.uuid4()) for _ in range(7)
    ]
    try:
        cases = [
            (
                "native-ainvoke",
                False,
                True,
                user_a,
                tenant_a,
                thread_a,
                "alice-fixture",
            ),
            ("native-stream", True, True, user_a, tenant_a, thread_a, "alice-fixture"),
            (
                "direct-stream-new-thread",
                True,
                False,
                user_a,
                tenant_a,
                thread_b,
                "alice-fixture",
            ),
            (
                "other-user-and-tenant",
                True,
                True,
                user_b,
                tenant_b,
                thread_c,
                "bob-fixture",
            ),
        ]
        for mode, streaming, native, user, tenant, thread, account in cases:
            config = run_config(thread=thread, tenant=tenant)
            config["configurable"]["langgraph_auth_user"].update(
                identity=user, display_name=account
            )
            started = time.monotonic()
            trace_id = await export_run(
                patch, config, streaming=streaming, native_context=native
            )
            with client.start_as_current_observation(
                name="unrelated-after-" + mode
            ) as unrelated:
                unrelated_id = unrelated.trace_id
            rows.append(
                dict(
                    mode=mode,
                    trace_id=trace_id,
                    user_id=user,
                    session_id=thread,
                    tenant_id=tenant,
                    account=account,
                    unrelated_trace_id=unrelated_id,
                    attempt_id=config["configurable"]["ledger_attempt_id"],
                    duration_seconds=round(time.monotonic() - started, 3),
                )
            )
        client.flush()
        api = LangfuseAPI(
            base_url=os.environ["LANGFUSE_BASE_URL"],
            username=os.environ["LANGFUSE_PUBLIC_KEY"],
            password=os.environ["LANGFUSE_SECRET_KEY"],
            timeout=30,
        )
        for row in rows:
            for _ in range(15):
                observations = api.observations.get_many(
                    trace_id=row["trace_id"],
                    fields="core,basic,io,metadata",
                    limit=1000,
                ).data
                if len(observations) >= 65 and all(
                    o.end_time is not None for o in observations
                ):
                    break
                await asyncio.sleep(2)
            by_id = {o.id: o for o in observations}
            sql = f"SELECT name,span_id,parent_span_id,is_app_root FROM default.events_core FINAL WHERE trace_id='{row['trace_id']}' AND is_deleted=0 FORMAT JSONEachRow"
            core = subprocess.run(
                [
                    "container",
                    "exec",
                    "air-agent-langfuse-clickhouse",
                    "/bin/sh",
                    "-c",
                    'exec clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --query "$1"',
                    "query",
                    sql,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            stored = [json.loads(line) for line in core.stdout.splitlines()]
            root_ids = {o["span_id"] for o in stored if o["is_app_root"]}
            roots = [o for o in observations if o.id in root_ids]
            missing = [
                o.name
                for o in observations
                if o.id not in root_ids and o.parent_observation_id not in by_id
            ]
            bad = [
                o.name
                for o in observations
                if (
                    o.name == "ScriptedChatModel"
                    and by_id.get(o.parent_observation_id).name != "model"
                )
                or (
                    o.name
                    in {"task", "freeze_tracing_request", "build_validated_report"}
                    and by_id.get(o.parent_observation_id).name != "tools"
                )
            ]
            io = [
                o
                for o in observations
                if o.name
                in {
                    "ScriptedChatModel",
                    "task",
                    "freeze_tracing_request",
                    "build_validated_report",
                }
            ]
            row.update(
                spans=len(observations),
                roots=[o.name for o in roots],
                missing_child_parents=missing,
                incorrect_critical_parents=bad,
                models=sum(o.name == "ScriptedChatModel" for o in observations),
                tasks=sum(o.name == "task" for o in observations),
                critical_io_present=all(bool(o.input) and bool(o.output) for o in io),
                root_input_present=bool(roots[0].input),
                root_output_present=bool(roots[0].output),
            )
            assert row["spans"] == 65 and row["roots"] == ["intelligent-tracing"], row
            assert not missing and not bad and row["critical_io_present"], row
            row["all_user_attributes_match"] = all(
                o.user_id == row["user_id"] for o in observations
            )
            row["all_session_attributes_match"] = all(
                o.session_id == row["session_id"] for o in observations
            )
            row["all_account_attributes_match"] = all(
                isinstance(o.metadata, dict)
                and o.metadata.get("user_account") == row["account"]
                for o in observations
            )
            assert (
                row["all_user_attributes_match"]
                and row["all_session_attributes_match"]
                and row["all_account_attributes_match"]
            ), row
            unrelated = api.observations.get_many(
                trace_id=row["unrelated_trace_id"],
                fields="core,basic,metadata",
                limit=100,
            ).data
            assert (
                len(unrelated) == 1
                and not unrelated[0].user_id
                and not unrelated[0].session_id
            )
            row["unrelated_after_run_has_no_identity"] = True
        user_groups = []
        for user, expected in ((user_a, 195), (user_b, 65)):
            grouped = api.observations.get_many(
                user_id=user, environment=environment, fields="core,basic", limit=1000
            ).data
            assert len(grouped) == expected and all(o.user_id == user for o in grouped)
            user_groups.append(
                dict(
                    user_id=user,
                    observations=len(grouped),
                    traces=len({o.trace_id for o in grouped}),
                )
            )
        session_groups = []
        for session, expected in ((thread_a, 130), (thread_b, 65), (thread_c, 65)):
            grouped = api.observations.get_many(
                session_id=session,
                environment=environment,
                fields="core,basic",
                limit=1000,
            ).data
            assert len(grouped) == expected and all(
                o.session_id == session for o in grouped
            )
            session_groups.append(
                dict(
                    session_id=session,
                    observations=len(grouped),
                    traces=len({o.trace_id for o in grouped}),
                )
            )
        result = dict(
            environment=environment,
            langfuse="actual local server",
            langsmith="disabled; client construction and writes forbidden",
            business_database="connections forbidden",
            model="ScriptedChatModel",
            data="FixtureDataSource",
            langsmith_client_calls=len(writes),
            runs=rows,
            user_groups=user_groups,
            session_groups=session_groups,
        )
        assert not writes
        path = output_dir / "live-probe.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        patch.undo()
        client.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
