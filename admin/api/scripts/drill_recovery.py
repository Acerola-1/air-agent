"""仅在新建隔离库演练身份迁移、无新写恢复和已有新写的保全/前向修复。"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import bcrypt
import psycopg
from psycopg import sql


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="/tmp/air-agent-identity-recovery.json")
    args = parser.parse_args()
    base = os.environ.get("TENANT_TEST_OWNER_URI", "")
    parsed = urlparse(base)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or not parsed.path.startswith(
        ("/air_agent_tenant_test", "/air_agent_review", "/air_agent_repair")
    ):
        raise SystemExit("必须提供本机专用 TENANT_TEST_OWNER_URI；不允许使用业务库")
    api_root = Path(__file__).resolve().parents[1]
    prefix = f"air_agent_drill_{uuid.uuid4().hex[:10]}"
    created: list[str] = []
    report: dict = {"schema": "0006", "mode": "isolated_pg", "checks": {}}

    def uri(name: str) -> str:
        return urlunparse(parsed._replace(path="/" + name))

    def run(command: list[str], *, env=None) -> bytes:
        result = subprocess.run(command, cwd=api_root, env=env, capture_output=True)
        if result.returncode:
            raise RuntimeError(f"演练子步骤失败：{command[0]}（exit {result.returncode}）")
        return result.stdout

    def create(suffix: str) -> str:
        name = prefix + "_" + suffix
        with psycopg.connect(uri("postgres"), autocommit=True) as conn:
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(name), sql.Identifier(parsed.username or "air_agent")
                )
            )
        created.append(name)
        return name

    def dump(name: str, path: Path) -> None:
        data = run(
            [
                "container",
                "exec",
                "air-agent-postgres",
                "pg_dump",
                "-U",
                parsed.username or "air_agent",
                "-d",
                name,
                "-Fc",
            ]
        )
        path.write_bytes(data)
        path.chmod(0o600)

    def restore(name: str, path: Path) -> None:
        result = subprocess.run(
            [
                "container",
                "exec",
                "-i",
                "air-agent-postgres",
                "pg_restore",
                "-U",
                parsed.username or "air_agent",
                "-d",
                name,
                "--no-owner",
                "--exit-on-error",
            ],
            input=path.read_bytes(),
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError("隔离库备份恢复失败")

    try:
        source = create("source")
        env = dict(os.environ, DATABASE_URI=uri(source))
        run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env)
        seed = """import asyncio
from app.db import OwnerSessionLocal
from scripts.seed import seed_permissions, seed_platform_roles
async def seed():
    async with OwnerSessionLocal() as s:
        p=await seed_permissions(s); await seed_platform_roles(s,p); await s.commit()
asyncio.run(seed())
"""
        run([sys.executable, "-c", seed], env=env)
        with psycopg.connect(uri(source)) as conn:
            conn.execute(
                "CREATE TABLE thread(thread_id uuid PRIMARY KEY,metadata jsonb DEFAULT '{}')"
            )
            conn.execute(
                "CREATE TABLE recovery_marker(id integer PRIMARY KEY, content text NOT NULL)"
            )
            conn.execute("INSERT INTO recovery_marker VALUES (1,'synthetic legacy body')")
        with tempfile.TemporaryDirectory(prefix="air-agent-recovery-") as tmp:
            folder = Path(tmp)
            backup = folder / "before.dump"
            dump(source, backup)
            before = create("before_restore")
            restore(before, backup)
            with psycopg.connect(uri(before)) as conn:
                report["checks"]["before_cutover_restore"] = (
                    conn.execute("SELECT content FROM recovery_marker WHERE id=1").fetchone()[0]
                    == "synthetic legacy body"
                )
                report["checks"]["restored_schema_head"] = (
                    conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0006"
                )
            password = secrets.token_urlsafe(24)
            json_id = str(uuid.uuid4())
            users = folder / "users.json"
            users.write_text(
                json.dumps(
                    {
                        json_id: {
                            "id": json_id,
                            "username": "drill_user",
                            "passwordHash": bcrypt.hashpw(
                                password.encode(), bcrypt.gensalt(rounds=4)
                            ).decode(),
                        }
                    }
                )
            )
            users.chmod(0o600)
            manifest = folder / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "tenants": [
                            {
                                "code": "drill-tenant",
                                "name": "隔离演练企业",
                                "create_if_missing": True,
                            }
                        ],
                        "users": [{"account": "drill_user", "source": "json", "json_id": json_id}],
                        "memberships": [
                            {
                                "account": "drill_user",
                                "tenant_code": "drill-tenant",
                                "role_codes": ["tenant_admin"],
                            }
                        ],
                        "threads": [],
                    }
                )
            )
            command = [
                sys.executable,
                "scripts/migrate_identity.py",
                "apply",
                "--manifest",
                str(manifest),
                "--json-users",
                str(users),
            ]
            first = json.loads(run(command, env=env))
            assert first["completed"]
            with psycopg.connect(uri(source)) as conn:
                user_id, stored = conn.execute(
                    "SELECT id,password_hash FROM users WHERE account='drill_user'"
                ).fetchone()
                report["checks"]["original_password_preserved"] = bcrypt.checkpw(
                    password.encode(), stored.encode()
                )
                tenant_id = conn.execute(
                    "SELECT id FROM tenants WHERE code='drill-tenant'"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO audit_logs(actor_account,action,tenant_id,platform_scope) "
                    "VALUES ('drill_user','post.switch',%s,false)",
                    (tenant_id,),
                )
                conn.execute("INSERT INTO recovery_marker VALUES (2,'synthetic post cutover body')")
            second = json.loads(run(command, env=env))
            report["checks"]["forward_repair_idempotent"] = second.get("already_applied") is True
            after = folder / "with_new_writes.dump"
            dump(source, after)
            restored = create("new_write_restore")
            restore(restored, after)
            with psycopg.connect(uri(restored)) as conn:
                current = conn.execute(
                    "SELECT id,password_hash FROM users WHERE account='drill_user'"
                ).fetchone()
                report["checks"]["new_writes_preserved"] = (
                    conn.execute("SELECT count(*) FROM recovery_marker").fetchone()[0] == 2
                    and conn.execute(
                        "SELECT count(*) FROM audit_logs WHERE action='post.switch'"
                    ).fetchone()[0]
                    == 1
                )
                report["checks"]["identity_preserved"] = (
                    current[0] == user_id and current[1] == stored
                )
                policies = conn.execute(
                    "SELECT relrowsecurity FROM pg_class WHERE relname IN ("
                    "'thread_ownership','tenant_memberships',"
                    "'tool_artifact_storage','artifact_computations') AND relkind='r'"
                ).fetchall()
                report["checks"]["rls_preserved"] = len(policies) == 4 and all(
                    r[0] for r in policies
                )
            assert all(report["checks"].values()), "恢复计数或身份校验失败"
            report["passed"] = True
    finally:
        with psycopg.connect(uri("postgres"), autocommit=True) as conn:
            for name in reversed(created):
                conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
