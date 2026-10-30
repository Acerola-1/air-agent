"""恢复本任务合成旧库，验证成员绑定和删除期间的真实 MCP 拒绝."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
OWNED = ROOT / ".local/graph-module-ownership/20261029-apply"
NAME = "graph-ownership-send-20261029-pg"
PORT = 27641


def main():
    """只创建和回收本次成功创建的容器，不输出配置或合成凭据."""
    evidence, fixture = (Path(value).resolve() for value in sys.argv[1:3])
    if not evidence.is_relative_to(OWNED) or not fixture.is_relative_to(OWNED):
        raise ValueError("only this task's evidence directories are accepted")
    evidence.mkdir(exist_ok=False)
    bootstrap = json.loads((fixture / "fixture-bootstrap.json").read_text())
    password = secrets.token_hex(20)
    owner_uri = f"postgresql://air_agent:{password}@127.0.0.1:{PORT}/air_agent_business"
    runtime_uri = f"postgresql://air_agent_runtime:{bootstrap['runtime_password']}@127.0.0.1:{PORT}/air_agent_business"
    created = False
    result = {"controlled_fixture": True, "postgres_port": PORT, "container": NAME}
    try:
        subprocess.run(
            [
                "container",
                "run",
                "-d",
                "--name",
                NAME,
                "-p",
                f"127.0.0.1:{PORT}:5432",
                "-e",
                "POSTGRES_USER=air_agent",
                "-e",
                f"POSTGRES_PASSWORD={password}",
                "-e",
                "POSTGRES_DB=air_agent_business",
                "-e",
                "PGDATA=/var/lib/postgresql/data/pgdata",
                "postgres:17-alpine",
            ],
            check=True,
            capture_output=True,
        )
        created = True
        for _ in range(100):
            try:
                with psycopg.connect(owner_uri) as conn:
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
                        psycopg.sql.SQL(
                            "CREATE ROLE air_agent_runtime LOGIN PASSWORD {}"
                        ).format(psycopg.sql.Literal(bootstrap["runtime_password"]))
                    )
                break
            except psycopg.OperationalError:
                time.sleep(0.3)
        else:
            raise RuntimeError("owned PostgreSQL did not start")
        with (fixture / "checkpoint-fixture.dump").open("rb") as dump:
            restored = subprocess.run(
                [
                    "container",
                    "exec",
                    "-i",
                    NAME,
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
        (evidence / "restore.log").write_bytes(restored.stderr)
        if restored.returncode:
            raise RuntimeError("owned fixture restore failed")
        with psycopg.connect(owner_uri, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT s.*, r.thread_id FROM agent_capability_selections s JOIN agent_requests r ON r.id=s.request_id AND r.tenant_id=s.tenant_id WHERE s.graph_id='basic-qa' ORDER BY s.created_at DESC LIMIT 1"
            ).fetchone()
            assert row
        private = evidence / "runtime-fixture.json"
        private.write_text(
            json.dumps({"owner_uri": owner_uri, "selection": row}, default=str)
        )
        private.chmod(0o600)
        env = dict(
            os.environ,
            RUNTIME_DATABASE_URI=runtime_uri,
            OWNERSHIP_SEND_PG_FIXTURE=str(private),
        )
        with (evidence / "pytest.txt").open("w") as log:
            checked = subprocess.run(
                [
                    str(ROOT / ".venv/bin/python"),
                    "-m",
                    "pytest",
                    "tests/integration_tests/test_graph_ownership_send_postgres.py",
                    "-q",
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        result["passed"] = checked.returncode == 0
    finally:
        if created:
            subprocess.run(["container", "stop", NAME], capture_output=True)
            removed = subprocess.run(["container", "delete", NAME], capture_output=True)
            result["cleanup"] = removed.returncode == 0
        (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    sys.stdout.write(json.dumps(result) + "\n")
    if not result.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
