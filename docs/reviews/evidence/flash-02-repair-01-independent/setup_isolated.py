"""修复轮（02-repair-01）专用隔离环境：3 套全新空 PG 迁到 alembic head（含 0008）.

与协调者首次独立验收的 `../flash-02-independent/setup_isolated.py` 同构，但：
1) 迁移到 **head(0008)**，并把 0008 的新表/新列授权按本轮专用受限角色执行
   （0008 与 0007 一样暴露 `ledger_grant_statements(role_checker)`）；
2) 用当前解释器跑 alembic（admin/api 依赖在其独立 venv）。

**不修改** `flash-02-independent/` 下的首次提交证据；本脚本产物写到
`/tmp/air-agent-ivrp01-env.json` 与同目录的 `fresh-migrations-repair01.json`。
只用新建专用库，不触业务库 `air_agent`。

    admin/api/.venv/bin/python docs/reviews/evidence/flash-02-repair-01/setup_repair01_env.py
"""

import importlib.util
import json
import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg
from dotenv import load_dotenv
from psycopg import sql

load_dotenv(".env", override=True)
owner = os.environ["TENANT_TEST_OWNER_URI"]
runtime = os.environ["TENANT_TEST_RUNTIME_URI"]
u = urlparse(owner)
assert u.hostname in {"localhost", "127.0.0.1"} and u.path != "/air_agent"

suffix = uuid.uuid4().hex[:8]
names = [
    f"air_agent_ivrp01_ledger_{suffix}",
    f"air_agent_ivrp01_api_{suffix}",
    f"air_agent_ivrp01_rls_{suffix}",
]
worker = f"ivrp01_ledger_worker_{suffix}"
password = secrets.token_hex(20)


def db(uri: str, name: str) -> str:
    """把连接串的库名换成 `name`."""
    return urlunparse(urlparse(uri)._replace(path="/" + name))


with psycopg.connect(db(owner, "postgres"), autocommit=True) as c:
    c.execute(
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS").format(
            sql.Identifier(worker), sql.Literal(password)
        )
    )
    for name in names:
        c.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(u.username)
            )
        )

py = os.environ.get("LEDGER_SETUP_PYTHON") or sys.executable
for name in names:
    env = dict(os.environ, DATABASE_URI=db(owner, name))
    r = subprocess.run(
        [py, "-m", "alembic", "upgrade", "head"],
        cwd="admin/api",
        env=env,
        capture_output=True,
    )
    if r.returncode:
        raise RuntimeError("fresh migration failed " + r.stderr.decode()[-1500:])


def load(rev: str):
    """按文件名加载迁移模块（可覆写其 WORKER_ROLE）."""
    spec = importlib.util.spec_from_file_location(
        "rev", f"admin/api/migrations/versions/{rev}"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m7 = load("0007_agent_event_ledger.py")
m7.WORKER_ROLE = worker
m8 = load("0008_ledger_start_claim_and_associations.py")
m8.WORKER_ROLE = worker
with psycopg.connect(db(owner, names[0])) as c:
    statements = m7.ledger_grant_statements(
        lambda _: True
    ) + m8.ledger_grant_statements(lambda _: True)
    for stmt in statements:
        c.execute(stmt)

worker_uri = urlunparse(
    u._replace(
        netloc=worker + ":" + password + "@" + u.hostname + ":" + str(u.port),
        path="/" + names[0],
    )
)
out = {
    "names": names,
    "worker_role": worker,
    "env": {
        "LEDGER_TEST_OWNER_URI": db(owner, names[0]),
        "LEDGER_TEST_TENANT_ID": str(uuid.uuid4()),
        "LEDGER_TEST_USER_ID": str(uuid.uuid4()),
        "RUNTIME_DATABASE_URI": db(runtime, names[0]),
        "LEDGER_WORKER_DATABASE_URI": worker_uri,
        "DATABASE_URI": db(owner, names[1]),
        "ADMIN_RUNTIME_DATABASE_URI": db(runtime, names[1]),
        "TENANT_TEST_OWNER_URI": db(owner, names[2]),
        "TENANT_TEST_RUNTIME_URI": db(runtime, names[2]),
    },
}
p = Path("/tmp/air-agent-ivrp01-env.json")
p.write_text(json.dumps(out))
p.chmod(0o600)
Path(
    "docs/reviews/evidence/flash-02-repair-01-independent/fresh-migrations.json"
).write_text(
    json.dumps(
        {
            "databases": names,
            "head": "0008",
            "worker": "random_nonowner_test_role",
            "migration": "0001_to_0008",
            "notes": "3 套新空 PG；专用非 owner worker 已按 0007+0008 授权；未使用业务库",
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
sys.stdout.write(
    "3 套新空 PG 迁移到 head(0008)；专用非 owner worker 已按 0007+0008 授权；未使用业务库\n"
)
