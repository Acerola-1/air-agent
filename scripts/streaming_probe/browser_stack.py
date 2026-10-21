# ruff: noqa: D103, D107, T201
"""Start fixture + Next dev for browser acceptance, holding until terminated.

This is an operator helper for the visual browser probe: it swaps a probe-only
frontend/.env.local, starts both servers, prints readiness, then waits.  It
restores the original env file on SIGTERM/SIGINT.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PORT = 18081
FRONTEND_PORT = 13100


def _wait(url: str, timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    raise TimeoutError(url)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", default="basic-qa")
    args = parser.parse_args()
    env_local = ROOT / "frontend/.env.local"
    original = env_local.read_bytes() if env_local.exists() else None
    env_local.write_text(
        f"ADMIN_API_URL=http://127.0.0.1:{FIXTURE_PORT}\n"
        f"LANGGRAPH_API_URL=http://127.0.0.1:{FIXTURE_PORT}\n"
        "INTERNAL_API_TOKEN=probe-internal\n"
        "AGENT_IDENTITY_BACKEND=local\n"
        "AUTH_SECRET=probe-secret\n"
        'ARCHIVE_SCOPE_KEYS=["probe-archive-key"]\n'
        "RUNTIME_TICKET_AUDIENCE=langgraph-runtime\n",
        encoding="utf-8",
    )
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
        stdout=open("/tmp/browser-fixture.log", "w"),
        stderr=subprocess.STDOUT,
    )
    dev = subprocess.Popen(
        ["npm", "run", "dev", "--", "-p", str(FRONTEND_PORT)],
        cwd=ROOT / "frontend",
        env={
            **os.environ,
            "ADMIN_API_URL": f"http://127.0.0.1:{FIXTURE_PORT}",
            "LANGGRAPH_API_URL": f"http://127.0.0.1:{FIXTURE_PORT}",
            "INTERNAL_API_TOKEN": "probe-internal",
            "AGENT_IDENTITY_BACKEND": "local",
            "NEXT_TELEMETRY_DISABLED": "1",
        },
        stdout=open("/tmp/browser-next.log", "w"),
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )

    def _stop(*_args):
        try:
            os.killpg(os.getpgid(dev.pid), signal.SIGTERM)
        except Exception:  # noqa: BLE001
            dev.terminate()
        fixture.terminate()
        dev.wait(timeout=20)
        fixture.wait(timeout=10)
        if original is None:
            env_local.unlink(missing_ok=True)
        else:
            env_local.write_bytes(original)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    _wait(f"http://127.0.0.1:{FIXTURE_PORT}/ok")
    _wait(f"http://127.0.0.1:{FRONTEND_PORT}/login")
    print(f"READY graph={args.graph} frontend=http://127.0.0.1:{FRONTEND_PORT}", flush=True)
    while True:
        time.sleep(5)


if __name__ == "__main__":
    main()
