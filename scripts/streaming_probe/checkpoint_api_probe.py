"""Verify the actual tracing DeepAgents graph across an official API restart.

Creates only three named temporary containers. The sole deployment credential
read is the configured LangSmith development license key; tracing stays off.
Evidence contains public identifiers/hashes, not payloads or credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/unit_tests/tracing"))
from fixtures import make_request  # noqa: E402

HEADERS = {"Authorization": "Bearer streaming-probe"}


def command(*args: str) -> str:
    """Run the native CLI without printing environment or connection strings."""
    result = subprocess.run(["container", *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"container {args[0]} failed: {result.stderr[-600:]}")
    return result.stdout


def address(name: str) -> str:
    """Read only the address of an owned dependency."""
    item = json.loads(command("inspect", name))[0]
    networks = item.get("status", {}).get("networks") or []
    if isinstance(networks, dict):
        networks = list(networks.values())
    return networks[0]["ipv4Address"].split("/")[0]


def ready(url: str) -> None:
    """Wait for this probe's API, with a bounded deadline."""
    started = time.monotonic()
    while time.monotonic() - started < 90:
        try:
            if httpx.get(url + "/ok", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("isolated API readiness timed out")


def stream(url: str, thread_id: str, body: dict) -> list[dict]:
    """Read the actual native SSE lifecycle."""
    events = []
    event = ""
    with httpx.stream(
        "POST",
        f"{url}/threads/{thread_id}/runs/stream",
        json=body,
        headers=HEADERS,
        timeout=90,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                if event == "error":
                    raise RuntimeError(f"API stream error: {payload}")
                if event == "custom" and payload.get("type", "").startswith(
                    "final_output_"
                ):
                    events.append(payload)
    return events


def main() -> None:
    """Own the isolated resources from creation through cleanup."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=12624)
    parser.add_argument(
        "--use-configured-license",
        action="store_true",
        help="Explicitly permit use of the local LangSmith license key; tracing stays off",
    )
    args = parser.parse_args()
    names = [
        "aa-streamfix-20261021-pg",
        "aa-streamfix-20261021-redis",
        "aa-streamfix-20261021-api",
    ]
    if set(command("ls", "--all", "--quiet").split()) & set(names):
        raise RuntimeError(
            "probe resource name already exists; refusing to reuse or delete it"
        )
    created = []
    result = {
        "entry": "official-api-actual-deepagents",
        "test_data": "synthetic",
        "iam": "not_under_test",
        "passed": False,
        "phase": "setup",
    }

    def frames(events):
        return [
            {
                k: event.get(k)
                for k in (
                    "type",
                    "seq",
                    "section_id",
                    "output_id",
                    "message_id",
                    "run_id",
                    "reason_code",
                    "section_count",
                )
            }
            for event in events
        ]

    try:
        password = secrets.token_hex(20)
        created.append(names[0])
        command(
            "run",
            "-d",
            "--name",
            names[0],
            "--memory",
            "512M",
            "-e",
            "POSTGRES_USER=probe",
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-e",
            "POSTGRES_DB=probe",
            "docker.io/library/postgres:17-alpine",
        )
        created.append(names[1])
        command(
            "run",
            "-d",
            "--name",
            names[1],
            "--memory",
            "256M",
            "docker.io/library/redis:7-alpine",
        )
        pg, redis = address(names[0]), address(names[1])
        env = {
            "DATABASE_URI": f"postgres://probe:{password}@{pg}:5432/probe?sslmode=disable",
            "REDIS_URI": f"redis://{redis}:6379",
            "LANGGRAPH_AUTH_TYPE": "custom",
            "LANGGRAPH_AUTH": json.dumps(
                {
                    "path": "/deps/air_agent/scripts/streaming_probe/tracing_resume_fixture.py:auth",
                    "disable_studio_auth": True,
                }
            ),
            "LANGGRAPH_HTTP": "{}",
            "LANGGRAPH_API_VARIANT": "local",
            "LANGGRAPH_STORE_BACKEND": "postgres",
            "DB_MIGRATION_BY_CORE_API": "false",
            "LANGSERVE_GRAPHS": json.dumps(
                {
                    "intelligent-tracing": "/deps/air_agent/scripts/streaming_probe/tracing_resume_fixture.py:graph"
                }
            ),
            "TENANT_ENFORCE_RUNTIME_IDENTITY": "false",
            "TENANT_BUSINESS_SNAPSHOT_ENFORCED": "false",
            "AGENT_IDENTITY_BACKEND": "local",
            "LANGCHAIN_TRACING_V2": "false",
            "LANGSMITH_TRACING": "false",
            "LANGFUSE_ENABLED": "false",
            "N_WORKERS": "1",
            "N_JOBS_PER_WORKER": "2",
            "AGENT_MCP_CONNECTIONS_JSON": '{"schema_version":"agent.mcp-connections.v1","revision":1,"servers":[]}',
        }
        # Official development licensing requires the application's configured
        # key. Do not load any database/identity/provider credentials or trace.
        for filename in (
            (".env.langgraph", ".env") if args.use_configured_license else ()
        ):
            values = dotenv_values(ROOT / filename)
            key = values.get("LANGSMITH_API_KEY") or values.get("LANGCHAIN_API_KEY")
            if key:
                env["LANGSMITH_API_KEY"] = key
                break
        for key in (
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "MCP_SERVER_URL",
            "SILICONFLOW_API_KEY",
            "DEEPSEEK_API_KEY",
            "XFYUN_MAAS_API_KEY",
            "XIAOMI_MIMO_API_KEY",
            "SERPER_API_KEY",
            "ARK_API_KEY",
            "MAIN_LLM_API_KEY",
        ):
            env[key] = "synthetic-test"
        with tempfile.TemporaryDirectory(prefix="aa-streamfix-") as directory:
            envfile = Path(directory) / "api.env"
            envfile.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
            os.chmod(envfile, 0o600)
            run = [
                "run",
                "-d",
                "--name",
                names[2],
                "--memory",
                "2G",
                "-p",
                f"127.0.0.1:{args.port}:8000",
                "--env-file",
                str(envfile),
            ]
            for folder in ("src", "scripts", "tests"):
                run += ["-v", f"{ROOT / folder}:/deps/air_agent/{folder}:ro"]
            run += ["air-agent/langgraph-api:tracing-merge-20261020"]
            created.append(names[2])
            command(*run)
        url = f"http://127.0.0.1:{args.port}"
        ready(url)
        result["versions"] = json.loads(
            command(
                "exec",
                names[2],
                "python",
                "-c",
                "import importlib.metadata as m,json; print(json.dumps({p:m.version(p) for p in ['langgraph-api','langgraph','deepagents','langchain-core']}))",
            )
        )
        response = httpx.post(url + "/threads", json={}, headers=HEADERS, timeout=10)
        response.raise_for_status()
        thread_id = response.json()["thread_id"]
        request = make_request()
        config = {
            "configurable": {
                "execution_epoch": "epoch-1",
                "ledger_attempt_id": "synthetic-resume-attempt",
                "ledger_business_code": request.business_code,
                "ledger_business_policy_version": request.business_policy_version,
                "ledger_business_permission": request.business_permission,
                "ledger_graph_id": request.graph_id,
            }
        }
        first = stream(
            url,
            thread_id,
            {
                "assistant_id": "intelligent-tracing",
                "config": config,
                "input": {
                    "messages": [{"type": "human", "content": "分析目标站 PM2.5"}],
                },
                "stream_mode": ["custom", "values"],
            },
        )
        result["first"] = frames(first)
        result["phase"] = "paused"
        assert [
            e.get("section_id") for e in first if e["type"] == "final_output_delta"
        ] == ["header", "claims-1"], [e["type"] for e in first]
        assert not any(
            e["type"] in {"final_output_done", "final_output_aborted"} for e in first
        )
        state = httpx.get(
            f"{url}/threads/{thread_id}/state", headers=HEADERS, timeout=10
        ).json()
        assert state.get("tasks"), "missing interrupted task"
        command("stop", names[2])
        command("start", names[2])
        ready(url)
        result["phase"] = "restarted"
        second = stream(
            url,
            thread_id,
            {
                "assistant_id": "intelligent-tracing",
                "config": config,
                "command": {"resume": True},
                "stream_mode": ["custom", "values"],
            },
        )
        result["resumed"] = frames(second)
        result["phase"] = "resumed"
        final_state_response = httpx.get(
            f"{url}/threads/{thread_id}/state", headers=HEADERS, timeout=10
        )
        final_state_response.raise_for_status()
        final_state = final_state_response.json()
        values = final_state.get("values") or {}
        progress = values.get("publication_session") or {}
        result["final_progress"] = {
            k: progress.get(k)
            for k in ("failed_reason", "completed", "published_section_ids", "last_seq")
        }
        result["pending_tasks"] = len(final_state.get("tasks") or [])
        events = first + second
        deltas = [e for e in events if e["type"] == "final_output_delta"]
        # The normal freeze/four-specialist path may select more claim groups
        # than the direct fallback fixture. Compare against its frozen manifest,
        # not a hardcoded chapter count from another report path.
        expected_sections = progress["section_ids"]
        assert len(expected_sections) == first[0]["section_count"]
        assert [e["section_id"] for e in deltas] == expected_sections
        assert len(set(expected_sections)) == len(expected_sections)
        assert not (
            {e["section_id"] for e in first if e["type"] == "final_output_delta"}
            & {e["section_id"] for e in second if e["type"] == "final_output_delta"}
        )
        assert len({e["output_id"] for e in events}) == 1
        assert len({e["message_id"] for e in events}) == 1
        assert [e["seq"] for e in events] == list(range(len(events)))
        assert events[-1]["type"] == "final_output_done"
        assert "".join(e["message"] for e in deltas) == events[-1]["message"]
        state = httpx.get(
            f"{url}/threads/{thread_id}/state", headers=HEADERS, timeout=10
        ).json()["values"]
        visible = [
            m
            for m in state["messages"]
            if m.get("additional_kwargs", {}).get("ui_visible")
        ]
        assert len(visible) == 1 and visible[0]["id"] == events[-1]["message_id"]
        assert len(state["child_executions"]) == 4
        assert {e["role"] for e in state["child_executions"]} == {
            "station-data",
            "grid-data",
            "meteo-analyst",
            "evidence-reviewer",
        }
        assert all(e["status"] == "succeeded" for e in state["child_executions"])
        result.update(
            passed=True,
            restart=True,
            sections=[e["section_id"] for e in deltas],
            first_events=len(first),
            resumed_events=len(second),
            output_id=events[-1]["output_id"],
            message_id=events[-1]["message_id"],
            sequences=[e["seq"] for e in events],
            specialist_count=len(state["child_executions"]),
            specialist_statuses={
                e["role"]: e["status"] for e in state["child_executions"]
            },
            content_sha256=hashlib.sha256(events[-1]["message"].encode()).hexdigest(),
        )
    finally:
        for name in reversed(created):
            subprocess.run(["container", "stop", name], capture_output=True)
            subprocess.run(["container", "delete", name], capture_output=True)
        result["cleanup_requested"] = created
        result["cleanup_verified"] = not (
            set(command("ls", "--all", "--quiet").split()) & set(created)
        )
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))  # noqa: T201 - CLI result


if __name__ == "__main__":
    main()
