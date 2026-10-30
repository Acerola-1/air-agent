"""Own an isolated real Next.js login and actual Python SSO callback facade.

The central IAM response, dependency origin check and ticket exchange are
controlled test services. Java MVC tests separately cover the real IAM guard.
No formal database, cookie, account or process is used.
"""

# ruff: noqa: T201 -- Probe readiness is consumed by the runner.

import json
import os
import runpy
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs/testing/sso-session-recovery-20261023"
FRONTEND_PORT = 13126
API_PORT = 18126


def serve():
    """Expose only synthetic identity responses and the actual callback route."""
    runpy.run_path(str(ROOT / "tests/unit_tests/conftest.py"))
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "admin/api"))
    os.environ["DATABASE_URI"] = "postgresql+psycopg://unused:unused@127.0.0.1:1/unused"
    import uvicorn
    from app.routers import iam_remote as iam
    from fastapi import FastAPI, HTTPException, Request, Response

    app = FastAPI()
    app.dependency_overrides[iam.verify_write_origin] = lambda: None

    async def no_database():
        yield None

    app.dependency_overrides[iam.get_session] = no_database
    iam._iam_config = lambda: {
        "IAM_BASE_URL": f"http://127.0.0.1:{API_PORT}",
        "IAM_SSO_CLIENT_ID": "probe",
    }
    callback = f"http://127.0.0.1:{FRONTEND_PORT}/api/auth/iam/callback"
    iam._callback_url = lambda *args: callback
    state = {"mode": "expired", "exchanges": 0}

    async def exchange(*args):
        state["exchanges"] += 1
        raise HTTPException(
            status_code=503 if state["mode"] == "unavailable" else 401,
            detail="controlled refusal",
        )

    iam._sso_exchange = exchange
    app.include_router(iam.router)

    @app.get("/v1/sso/authorize")
    async def central(request: Request):
        params = request.query_params
        query = {"state": params["state"]}
        if state["mode"] == "expired":
            query["error"] = "login_required"
        elif state["mode"] == "wrong-state":
            query.update(error="login_required", state="wrong-state")
        else:
            query["ticket"] = "synthetic-ticket"
        return Response(
            status_code=303, headers={"Location": callback + "?" + urlencode(query)}
        )

    @app.post("/probe/mode/{mode}")
    async def mode(mode: str):
        if mode not in {"expired", "wrong-state", "unavailable", "exchange-failed"}:
            raise HTTPException(400)
        state["mode"] = mode
        return state

    @app.get("/probe/status")
    async def status():
        return state

    @app.get("/auth/me")
    async def me():
        return Response(status_code=401)

    uvicorn.run(app, host="127.0.0.1", port=API_PORT, access_log=False)


def main():
    """Run owned services until a stop file and reclaim all temporary files."""
    import httpx

    for port in (FRONTEND_PORT, API_PORT):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
    processes = []
    stop = OUT / "probe.stop"
    if stop.exists() or (OUT / "probe-resources.json").exists():
        raise RuntimeError("Refusing to reuse earlier evidence")
    with tempfile.TemporaryDirectory(prefix="aa-sso-recovery-") as directory:
        temp = Path(directory)
        try:
            frontend = temp / "frontend"
            shutil.copytree(
                ROOT / "frontend",
                frontend,
                ignore=shutil.ignore_patterns("node_modules", ".next", ".env*"),
            )
            (frontend / "node_modules").symlink_to(
                ROOT / "frontend/node_modules", target_is_directory=True
            )
            admin = temp / "admin-web"
            shutil.copytree(
                ROOT / "admin/web",
                admin,
                ignore=shutil.ignore_patterns("node_modules", ".next", ".env*"),
            )
            (admin / "node_modules").symlink_to(
                ROOT / "admin/web/node_modules", target_is_directory=True
            )
            env = {
                **os.environ,
                "ADMIN_API_URL": f"http://127.0.0.1:{API_PORT}",
                "AGENT_IDENTITY_BACKEND": "remote",
                "NEXT_TELEMETRY_DISABLED": "1",
            }
            for label, command, cwd in [
                ("api", [str(ROOT / ".venv/bin/python"), __file__, "--serve"], ROOT),
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
                        str(FRONTEND_PORT),
                    ],
                    frontend,
                ),
            ]:
                processes.append(
                    subprocess.Popen(
                        command,
                        cwd=cwd,
                        env=env,
                        stdout=open(OUT / f"probe-{label}.log", "w"),
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                )
            started = time.monotonic()
            while time.monotonic() - started < 90:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError("Owned service exited")
                try:
                    if (
                        httpx.get(
                            f"http://127.0.0.1:{FRONTEND_PORT}/login", timeout=2
                        ).status_code
                        == 200
                        and httpx.get(
                            f"http://127.0.0.1:{API_PORT}/probe/status", timeout=2
                        ).status_code
                        == 200
                    ):
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.3)
            else:
                raise RuntimeError("Owned readiness timeout")
            (OUT / "probe-resources.json").write_text(
                json.dumps(
                    {
                        "ports": [FRONTEND_PORT, API_PORT],
                        "pids": [p.pid for p in processes],
                        "temp": str(temp),
                        "central_identity": "controlled",
                        "callback": "actual Python router",
                        "formal_resources_used": False,
                    },
                    indent=2,
                )
            )
            print("SSO_PROBE_READY", flush=True)
            while not stop.exists():
                time.sleep(0.5)
        finally:
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
            (OUT / "probe-cleanup.json").write_text(
                json.dumps(
                    {"processes_stopped": True, "temp_removed_on_exit": str(temp)},
                    indent=2,
                )
            )


if __name__ == "__main__":
    serve() if "--serve" in sys.argv else main()
