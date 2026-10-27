# ruff: noqa: T201 -- Probe output is the evidence collector interface.
"""Own three temporary containers and an isolated frontend copy until stop-file.

No formal resource is reused. The only configured credential read is the
official API's local development license; observation exports stay disabled.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
RUN = os.environ.get("TRACING_PROBE_RUN", "03")
if not RUN.isdigit():
    raise RuntimeError("Numeric owned run id required")
OUT = ROOT / f"docs/testing/tracing-conversation-20261023/browser-run-{RUN}"
PREFIX = f"aa-conversation-20261023-r{RUN}"
NAMES = [PREFIX + "-pg", PREFIX + "-redis", PREFIX + "-api"]
PORTS = {"native": 12625, "admin": 18125, "frontend": 13125}


def cli(*args):
    """Run a container command and retain only bounded failure output."""
    result = subprocess.run(["container", *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("container " + args[0] + " failed: " + result.stderr[-400:])
    return result.stdout


def address(name):
    """Read only the owned container network address."""
    item = json.loads(cli("inspect", name))[0]
    networks = item.get("status", {}).get("networks") or []
    if isinstance(networks, dict):
        networks = list(networks.values())
    return networks[0]["ipv4Address"].split("/")[0]


def wait(url):
    """Wait for one owned endpoint with a bounded deadline."""
    started = time.monotonic()
    while time.monotonic() - started < 120:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError("owned endpoint readiness timed out")


def main():
    """Create isolated resources and clean them after the stop file."""
    OUT.mkdir(parents=True, exist_ok=False)
    if set(cli("ls", "--all", "--quiet").split()) & set(NAMES):
        raise RuntimeError(
            "Owned resource name already exists; refusing to reuse/delete it"
        )
    for port in PORTS.values():
        import socket

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
    created = []
    processes = []
    statefile = OUT / "browser-stack.json"
    stopfile = OUT / "browser-stack.stop"
    if statefile.exists() or stopfile.exists():
        raise RuntimeError("Refusing to overwrite stack evidence")
    with tempfile.TemporaryDirectory(prefix="aa-conversation-browser-") as directory:
        temp = Path(directory)
        try:
            password = secrets.token_urlsafe(24)
            cli(
                "run",
                "-d",
                "--name",
                NAMES[0],
                "--memory",
                "512M",
                "-e",
                "POSTGRES_USER=probe",
                "-e",
                f"POSTGRES_PASSWORD={password}",
                "-e",
                "POSTGRES_DB=conversation_probe",
                "docker.io/library/postgres:17-alpine",
            )
            created.append(NAMES[0])
            cli(
                "run",
                "-d",
                "--name",
                NAMES[1],
                "--memory",
                "256M",
                "docker.io/library/redis:7-alpine",
            )
            created.append(NAMES[1])
            pg, redis = address(NAMES[0]), address(NAMES[1])
            env = {
                "DATABASE_URI": f"postgres://probe:{password}@{pg}:5432/conversation_probe?sslmode=disable",
                "REDIS_URI": f"redis://{redis}:6379",
                "LANGGRAPH_AUTH_TYPE": "custom",
                "LANGGRAPH_AUTH": json.dumps(
                    {
                        "path": "/deps/air_agent/scripts/tracing_conversation_probe/native.py:auth",
                        "disable_studio_auth": True,
                    }
                ),
                "LANGGRAPH_HTTP": json.dumps(
                    {
                        "app": "/deps/air_agent/scripts/tracing_conversation_probe/native.py:app"
                    }
                ),
                "LANGGRAPH_API_VARIANT": "local",
                "LANGGRAPH_STORE_BACKEND": "postgres",
                "DB_MIGRATION_BY_CORE_API": "false",
                "LANGSERVE_GRAPHS": json.dumps(
                    {
                        "intelligent-tracing": "/deps/air_agent/scripts/tracing_conversation_probe/native.py:graph"
                    }
                ),
                "TENANT_ENFORCE_RUNTIME_IDENTITY": "false",
                "TENANT_BUSINESS_SNAPSHOT_ENFORCED": "false",
                "AGENT_IDENTITY_BACKEND": "local",
                "LANGCHAIN_TRACING_V2": "false",
                "LANGSMITH_TRACING": "false",
                "LANGFUSE_ENABLED": "false",
                "AGENT_OBSERVABILITY_ENABLED": "false",
                "N_WORKERS": "1",
                "N_JOBS_PER_WORKER": "2",
                "AGENT_MCP_CONNECTIONS_JSON": '{"schema_version":"agent.mcp-connections.v1","revision":1,"servers":[]}',
            }
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
            for filename in (".env.langgraph", ".env"):
                values = dotenv_values(ROOT / filename)
                key = values.get("LANGSMITH_API_KEY") or values.get("LANGCHAIN_API_KEY")
                if key:
                    env["LANGSMITH_API_KEY"] = key
                    break
            envfile = temp / "native.env"
            envfile.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
            envfile.chmod(0o600)
            args = [
                "run",
                "-d",
                "--name",
                NAMES[2],
                "--memory",
                "2G",
                "-p",
                f"127.0.0.1:{PORTS['native']}:8000",
                "--env-file",
                str(envfile),
            ]
            for folder in ("src", "scripts", "tests"):
                args += ["-v", f"{ROOT / folder}:/deps/air_agent/{folder}:ro"]
            args += ["air-agent/langgraph-api:tracing-merge-20261020"]
            cli(*args)
            created.append(NAMES[2])
            wait(f"http://127.0.0.1:{PORTS['native']}/ok")
            frontend = temp / "frontend"
            shutil.copytree(
                ROOT / "frontend",
                frontend,
                ignore=shutil.ignore_patterns("node_modules", ".next", ".env*", ".git"),
            )
            (frontend / "node_modules").symlink_to(
                ROOT / "frontend/node_modules", target_is_directory=True
            )
            host_env = {
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "AGENT_IDENTITY_BACKEND": "local",
                "ADMIN_API_URL": f"http://127.0.0.1:{PORTS['admin']}",
                "LANGGRAPH_API_URL": f"http://127.0.0.1:{PORTS['native']}",
                "INTERNAL_API_TOKEN": "conversation-probe",
                "AUTH_SECRET": "synthetic-secret",
                "NEXT_TELEMETRY_DISABLED": "1",
                "ARCHIVE_SCOPE_KEYS": '["synthetic-archive-key"]',
            }
            for label, args, cwd in [
                (
                    "admin",
                    [
                        str(ROOT / ".venv/bin/python"),
                        "-m",
                        "uvicorn",
                        "scripts.tracing_conversation_probe.admin:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(PORTS["admin"]),
                    ],
                    ROOT,
                ),
                (
                    "frontend",
                    [
                        "npm",
                        "run",
                        "dev",
                        "--",
                        "--webpack",
                        "--hostname",
                        "127.0.0.1",
                        "--port",
                        str(PORTS["frontend"]),
                    ],
                    frontend,
                ),
            ]:
                log = open(OUT / f"browser-{label}.log", "w")
                processes.append(
                    subprocess.Popen(
                        args,
                        cwd=cwd,
                        env=host_env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                )
            wait(f"http://127.0.0.1:{PORTS['admin']}/ok")
            wait(f"http://127.0.0.1:{PORTS['frontend']}/login")
            statefile.write_text(
                json.dumps(
                    {
                        "containers": NAMES,
                        "ports": PORTS,
                        "temp": str(temp),
                        "pids": [p.pid for p in processes],
                        "identity": "controlled",
                        "model": "scripted fixture",
                        "native": "official LangGraph API",
                        "formal_resources_used": False,
                    },
                    indent=2,
                )
            )
            print("ISOLATED_BROWSER_STACK_READY", flush=True)
            while not stopfile.exists():
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError("owned process exited")
                time.sleep(1)
        finally:
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
            for name in reversed(created):
                subprocess.run(["container", "stop", name], capture_output=True)
                subprocess.run(["container", "delete", name], capture_output=True)
            (OUT / "browser-stack-cleanup.json").write_text(
                json.dumps(
                    {
                        "owned_containers": created,
                        "removed": True,
                        "temp_removed_on_exit": str(temp),
                    },
                    indent=2,
                )
            )


if __name__ == "__main__":
    main()
