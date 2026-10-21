# ruff: noqa: D103, T201
"""Exercise the real Next.js BFF proxy against a controlled SSE upstream.

Starts the controlled fixture and a Next dev server, then reads the BFF's
``/api/threads/{id}/runs/stream`` response.  Records the first observed public
delta while the upstream source is still blocked at its barrier.

Usage:
    PYTHONPATH=src .venv/bin/python -m scripts.streaming_probe.bff_probe \
        --graph basic-qa --out docs/testing/<run>/bff-basic-qa.json
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PORT = 18081
FRONTEND_PORT = 13100


def _wait(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    raise TimeoutError(url)


def _parse_sse(lines):
    event = None
    data = None
    for line in lines:
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
        elif line == "" and event:
            yield event, data
            event = None
            data = None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", choices=["basic-qa", "data-analysis", "intelligent-tracing"], required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "PROBE_GRAPH": args.graph,
            "PYTHONPATH": str(ROOT / "src"),
            "POSTGRES_USER": "x",
            "POSTGRES_PASSWORD": "x",
            "MCP_SERVER_URL": "x",
            "SILICONFLOW_API_KEY": "x",
            "DEEPSEEK_API_KEY": "x",
            "XFYUN_MAAS_API_KEY": "x",
            "XIAOMI_MIMO_API_KEY": "x",
            "SERPER_API_KEY": "x",
            "ARK_API_KEY": "x",
            "MAIN_LLM_API_KEY": "x",
            "TENANT_ENFORCE_RUNTIME_IDENTITY": "false",
            "TENANT_BUSINESS_SNAPSHOT_ENFORCED": "false",
            "AGENT_IDENTITY_BACKEND": "local",
            "AGENT_MCP_CONNECTIONS_JSON": '{"schema_version":"agent.mcp-connections.v1","revision":1,"servers":[]}',
        }
    )
    fixture = subprocess.Popen(
        [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "uvicorn",
            "scripts.streaming_probe.bff_fixture:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(FIXTURE_PORT),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=open(out.parent / "fixture-probe.log", "w"),
        stderr=subprocess.STDOUT,
    )
    # Next 16 loads .env.local with higher precedence than the parent process for
    # server routes in this repo; swap in a probe-only file and restore it.
    env_local = ROOT / "frontend/.env.local"
    original_env = env_local.read_bytes() if env_local.exists() else None
    probe_env = (
        f"ADMIN_API_URL=http://127.0.0.1:{FIXTURE_PORT}\n"
        f"LANGGRAPH_API_URL=http://127.0.0.1:{FIXTURE_PORT}\n"
        "INTERNAL_API_TOKEN=probe-internal\n"
        "AGENT_IDENTITY_BACKEND=local\n"
        "AUTH_SECRET=probe-secret\n"
        "ARCHIVE_SCOPE_KEYS=[\"probe-archive-key\"]\n"
        "RUNTIME_TICKET_AUDIENCE=langgraph-runtime\n"
    )
    env_local.write_text(probe_env, encoding="utf-8")
    next_env = {
        **os.environ,
        "ADMIN_API_URL": f"http://127.0.0.1:{FIXTURE_PORT}",
        "LANGGRAPH_API_URL": f"http://127.0.0.1:{FIXTURE_PORT}",
        "INTERNAL_API_TOKEN": "probe-internal",
        "AGENT_IDENTITY_BACKEND": "local",
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    dev = subprocess.Popen(
        ["npm", "run", "dev", "--", "-p", str(FRONTEND_PORT)],
        cwd=ROOT / "frontend",
        env=next_env,
        stdout=open(out.parent / "next-probe.log", "w"),
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    try:
        _wait(f"http://127.0.0.1:{FIXTURE_PORT}/ok")
        _wait(f"http://127.0.0.1:{FRONTEND_PORT}/login", timeout=120)
        events: list[dict] = []
        times: dict[str, float] = {}
        headers = {
            "content-type": "application/json",
            "cookie": "air_agent_session=00000000-0000-4000-8000-000000000000.probe",
            "x-tenant-id": "tenant-probe",
            "x-tenant-context-version": "1",
            "x-submission-key": "probe-submission",
            "x-user-message-id": "probe-user-message",
            "x-operation-kind": "initial",
        }
        body = {
            "assistant_id": args.graph,
            "input": {"messages": [{"type": "human", "content": "controlled probe"}]},
            "config": {"configurable": {"chat_mode": "fast"}},
        }
        times["t_submit"] = time.perf_counter()
        at_barrier: dict = {}
        debug = {}
        with httpx.stream(
            "POST",
            f"http://127.0.0.1:{FRONTEND_PORT}/api/threads/probe-thread/runs/stream",
            headers=headers,
            json=body,
            timeout=60,
        ) as response:
            debug["content_type"] = response.headers.get("content-type")
            debug["headers"] = dict(response.headers)
            if response.status_code != 200:
                state = httpx.get(
                    f"http://127.0.0.1:{FIXTURE_PORT}/probe/state", timeout=5
                ).json()
                raise SystemExit(
                    f"BFF status {response.status_code}: {response.read()[:300]!r} state={state!r}"
                )
            for event, data in _parse_sse(response.iter_lines()):
                if event != "custom" or not data:
                    continue
                payload = json.loads(data)
                if not str(payload.get("type", "")).startswith("final_output_"):
                    continue
                now = time.perf_counter()
                payload["_t"] = now
                events.append(payload)
                if payload["type"] == "final_output_delta":
                    times.setdefault("t_first_safe_delta", now)
                    times["t_last_safe_delta"] = now
                # Release as soon as the first public delta has traversed the BFF.
                state = httpx.get(
                    f"http://127.0.0.1:{FIXTURE_PORT}/probe/state", timeout=5
                ).json()
                if (
                    not at_barrier
                    and payload["type"] == "final_output_delta"
                    and state.get("at_barrier")
                ):
                    at_barrier = {
                        "source_finished": state.get("source_finished"),
                        "deltas": state.get("deltas"),
                        "sections": state.get("sections_at_barrier"),
                    }
                    httpx.post(
                        f"http://127.0.0.1:{FIXTURE_PORT}/probe/release", timeout=5
                    )
        times["t_stream_end"] = time.perf_counter()
        times["t_confirmation_end"] = times["t_stream_end"]
        deltas = [e for e in events if e["type"] == "final_output_delta"]
        done = [e for e in events if e["type"] == "final_output_done"]
        result = {
            "fixture_state": httpx.get(f"http://127.0.0.1:{FIXTURE_PORT}/probe/state", timeout=5).json(),
            "debug": debug,
            "graph": args.graph,
            "entry": "next-bff-controlled-upstream",
            "events": len(events),
            "deltas": len(deltas),
            "delta_lengths": [len(e.get("message", "")) for e in deltas],
            "sections": [e.get("section_id") for e in deltas if e.get("section_id")],
            "done_count": len(done),
            "first_delta_source": "new_delta" if deltas else None,
            "barrier": at_barrier,
            "answer_chars": len(done[-1]["message"]) if done else 0,
            **times,
        }
    finally:
        try:
            os.killpg(os.getpgid(dev.pid), signal.SIGTERM)
        except Exception:  # noqa: BLE001
            dev.terminate()
        fixture.terminate()
        dev.wait(timeout=20)
        fixture.wait(timeout=10)
        if original_env is None:
            env_local.unlink(missing_ok=True)
        else:
            env_local.write_bytes(original_env)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
