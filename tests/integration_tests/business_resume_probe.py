"""显式运行的隔离官方API验收；不读取正式凭据、不写正式库。

运行：.venv/bin/python tests/integration_tests/business_resume_probe.py <新证据目录>
创建空PG/Redis/API，使用合成IAM/模型；真实受理、签票、认领、暂停、恢复均走HTTP。
SQL只用于空库迁移/身份引用外键接线及最终只读检查，禁止伪造请求或运行状态。
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
from fastapi import FastAPI, Request
from psycopg import sql
from uvicorn import Config, Server

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO / "admin/api")]
PREFIX = "aa-codex-bizfinal"
PORTS = {"pg": 25433, "redis": 26380, "api": 22025, "mock": 29111, "admin": 28101}
BRIDGE = "192.168.64.1"
ISSUER = "synthetic-iam"
PASSWORD = secrets.token_hex(20)
RUNTIME_PASSWORD = secrets.token_hex(20)
KEY = secrets.token_hex(32)
INTERNAL = secrets.token_hex(24)
IDS = {key: str(uuid.uuid4()) for key in ("user", "tenant", "membership", "session")}
PERMS = [
    f"agent:business:{name}:use"
    for name in ("basic-qa", "data-analysis", "intelligent-tracing")
]
STATE = {"active": True, "revision": 1, "permissions": list(PERMS), "model_calls": 0}
CREATED: list[str] = []
SERVERS: list[Server] = []
EVIDENCE: dict = {
    "started_at": datetime.now(UTC).isoformat(),
    "checks": [],
    "controlled_iam_llm": True,
}
MOCK = FastAPI()


def revisions():
    return dict(
        user_auth_revision=1,
        tenant_auth_revision=1,
        membership_revision=STATE["revision"],
        session_ctx_version=1,
    )


def identity(audience):
    now = datetime.now(UTC)
    future = (now + timedelta(hours=1)).isoformat()
    return {
        "schema_version": "iam.identity.v1",
        "active": STATE["active"],
        "issuer": ISSUER,
        "audience": audience,
        "principal": {"type": "human", "id": IDS["user"], "realm": "workforce"},
        "user": {
            "id": IDS["user"],
            "account": "fixture",
            "display_name": "受控验收",
            "status": "active",
        },
        "current_tenant": {
            "id": IDS["tenant"],
            "code": "fixture",
            "name": "受控企业",
            "status": "active",
        },
        "membership": {
            "id": IDS["membership"],
            "tenant_id": IDS["tenant"],
            "status": "active",
            "revision": STATE["revision"],
            "department_id": None,
        },
        "session": {
            "id": IDS["session"],
            "ctx_version": 1,
            "created_at": now.isoformat(),
            "expires_at": future,
            "absolute_expires_at": future,
        },
        "revisions": revisions(),
        "context_version": 1,
        "permissions": list(STATE["permissions"]),
        "platform_admin": False,
        "context_usable": True,
        "needs_tenant_selection": False,
        "platform_roles": [],
        "tenant_roles": [],
        "evaluated_at": now.isoformat(),
        "execution_context": {
            "verification_ref": str(
                uuid.uuid5(uuid.UUID(IDS["session"]), str(STATE["revision"]))
            ),
            "session_id": IDS["session"],
            "audience": audience,
            "revisions": revisions(),
            "valid_until": future,
        },
    }


@MOCK.post("/v1/auth/login")
def login():
    return {"access_token": "synthetic-subject-token"}


@MOCK.get("/v1/auth/tenants")
def tenants():
    return [
        {
            "tenant_id": IDS["tenant"],
            "code": "fixture",
            "name": "受控企业",
            "status": "active",
            "member_status": "active",
        }
    ]


@MOCK.post("/v1/internal/identity/introspect")
async def introspect(request: Request):
    body = await request.json()
    if ec := body.get("execution_context"):
        return {
            "active": STATE["active"],
            "context_matches": bool(
                STATE["active"] and ec.get("expected_revisions") == revisions()
            ),
            "revisions": revisions(),
        }
    return identity(body["audience"])


@MOCK.post("/v1/internal/runtime-tickets")
async def ticket(request: Request):
    from app.security import RuntimeTicket, sign_runtime_ticket

    body = await request.json()
    now = int(time.time())
    claims = RuntimeTicket(
        session_id=IDS["session"],
        user_id=IDS["user"],
        tenant_id=IDS["tenant"],
        membership_id=IDS["membership"],
        audience=body["audience"],
        issued_at=now,
        expires_at=now + 60,
        session_ctx_version=1,
        user_auth_revision=1,
        tenant_auth_revision=1,
        membership_revision=STATE["revision"],
        kid="fixture",
    )
    return {"ticket": sign_runtime_ticket(claims, [("fixture", KEY)]), "expires_in": 60}


@MOCK.get("/auth")
def license_check():
    return {
        "organization_config": {
            "langgraph_enterprise_enabled": True,
            "langgraph_deploy_own_cloud_enabled": True,
        },
        "tenant_config": {},
    }


@MOCK.post("/v1/metadata/submit")
def metadata():
    return {"ok": True}


@MOCK.post("/v1/chat/completions")
def completion():
    STATE["model_calls"] += 1
    return {
        "id": "controlled-answer",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "controlled",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "受控恢复验证完成。"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
    }


def command(args, *, env=None, cwd=None):
    result = subprocess.run(args, env=env, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        diagnostic = result.stderr[-5000:]
        for secret in (PASSWORD, RUNTIME_PASSWORD, KEY, INTERNAL):
            diagnostic = diagnostic.replace(secret, "<redacted>")
        raise RuntimeError(diagnostic)
    return result.stdout


def container_ip(name):
    data = json.loads(command(["container", "inspect", name]))[0]
    networks = data["status"]["networks"]
    rows = networks.values() if isinstance(networks, dict) else networks
    return next(
        row["ipv4Address"].split("/")[0] for row in rows if row.get("ipv4Address")
    )


def start_container(kind, args):
    name = f"{PREFIX}-{kind}"
    # Never delete a pre-existing name. Only names successfully created here enter cleanup.
    command(["container", "run", "-d", "--name", name, *args])
    CREATED.append(name)
    return name


def wait_http(url):
    for _ in range(120):
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"health deadline: {url}")


def serve(app, port, host="127.0.0.1"):
    server = Server(
        Config(app, host=host, port=port, log_level="error", access_log=False)
    )
    SERVERS.append(server)
    threading.Thread(target=server.run, daemon=True).start()


def prepare_database(dsn, env, evidence):
    for _ in range(80):
        try:
            with psycopg.connect(dsn):
                break
        except psycopg.Error:
            time.sleep(0.25)
    with psycopg.connect(dsn) as conn:
        for role in (
            "air_agent_worker",
            "air_agent_ledger_worker",
            "air_agent_maintenance",
        ):
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        conn.execute(
            sql.SQL("CREATE ROLE air_agent_runtime LOGIN PASSWORD {}").format(
                sql.Literal(RUNTIME_PASSWORD)
            )
        )
    migration = command(
        [str(REPO / ".venv/bin/python"), "-m", "alembic", "upgrade", "head"],
        cwd=REPO / "admin/api",
        env=env,
    )
    (evidence / "migration.log").write_text(migration)
    # Empty fixture schema only: apply the existing I4 reference-FK model without
    # copying production rows, dumps, shadows or editing any historical migration.
    old = (
        "membership_roles",
        "user_roles",
        "role_permissions",
        "auth_sessions",
        "tenant_memberships",
        "roles",
        "permissions",
        "users",
        "tenants",
    )
    targets = {
        "users": "iam_reference_users",
        "tenants": "iam_reference_tenants",
        "tenant_memberships": "iam_reference_memberships",
    }
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT c.conrelid::regclass::text,c.conname,c.confrelid::regclass::text,pg_get_constraintdef(c.oid) FROM pg_constraint c WHERE c.contype='f' AND c.confrelid IN (SELECT oid FROM pg_class WHERE relname=ANY(%s))",
            (list(old),),
        )
        for table, name, target, definition in cur.fetchall():
            if table in old:
                continue
            assert target in targets, (table, target)
            cur.execute(
                sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                    sql.Identifier(table), sql.Identifier(name)
                )
            )
            replacement = re.sub(
                r"REFERENCES (?:public\.)?" + re.escape(target) + r"\b",
                "REFERENCES " + targets[target],
                definition,
            )
            assert replacement != definition
            cur.execute(
                sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} ").format(
                    sql.Identifier(table), sql.Identifier("fixture_" + name)
                )
                + sql.SQL(replacement)
            )
        cur.execute(
            "SELECT c.relname,p.polname FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid WHERE c.relname=ANY(%s)",
            (list(old),),
        )
        for table, name in cur.fetchall():
            cur.execute(
                sql.SQL("DROP POLICY {} ON {}").format(
                    sql.Identifier(name), sql.Identifier(table)
                )
            )
        for table in old:
            cur.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(table)))
        cur.execute(
            "UPDATE i4_separation_state SET mode='remote',state='ready' WHERE singleton=1"
        )
        cur.execute(
            sql.SQL("ALTER ROLE air_agent_runtime LOGIN PASSWORD {}").format(
                sql.Literal(RUNTIME_PASSWORD)
            )
        )
        cur.execute(
            "SELECT rolname,rolsuper,rolbypassrls FROM pg_roles WHERE rolname='air_agent_runtime'"
        )
        EVIDENCE["runtime_role"] = cur.fetchone()


def check_response(response, expected=200):
    if response.status_code != expected:
        raise AssertionError(
            f"{response.request.url.path}: expected {expected}, got {response.status_code}; {response.text[:500]}"
        )
    return response.json() if response.content else None


def run_checks(admin, native):
    check_response(
        admin.post(
            "/auth/login", json={"account": "fixture", "password": "synthetic-password"}
        )
    )
    admin.headers.update(
        {
            "x-tenant-id": IDS["tenant"],
            "x-tenant-context-version": "1",
            "x-internal-token": INTERNAL,
        }
    )

    def refresh_ticket():
        result = check_response(
            admin.post(
                "/auth/runtime-ticket",
                json={"target_tenant_id": IDS["tenant"], "expected_ctx_version": 1},
            )
        )
        native.headers["authorization"] = "Bearer " + result["ticket"]

    refresh_ticket()
    thread = str(uuid.uuid4())
    check_response(
        native.post(
            "/threads",
            json={
                "thread_id": thread,
                "if_exists": "raise",
                "metadata": {
                    "tenant_id": IDS["tenant"],
                    "user_id": IDS["user"],
                    "membership_id": IDS["membership"],
                    "session_id": IDS["session"],
                },
            },
        )
    )
    confirmed = check_response(
        native.post(
            "/internal/threads/claim-confirmation",
            json={"thread_id": thread},
            headers={"x-internal-token": INTERNAL},
        )
    )
    assert confirmed["confirmed"] is True
    selection = {
        "skill": {"mode": "auto"},
        "mcp": {"mode": "allowlist", "tool_ids": []},
    }
    catalog = check_response(
        native.get("/capabilities", params={"graph_id": "basic-qa"})
    )
    selection["catalog_version"] = catalog["catalog_version"]
    question = "请直接回复恢复验证完成，不需要查询数据。"
    accepted = check_response(
        admin.post(
            "/agent/requests",
            json={
                "thread_id": thread,
                "submission_key": str(uuid.uuid4()),
                "graph_id": "basic-qa",
                "content": question,
                "capability_selection": selection,
            },
        )
    )

    def payload(admission, *, resume=False):
        result = {
            "assistant_id": "basic-qa",
            "metadata": {
                "ledger_request_id": admission["request_id"],
                "ledger_attempt_id": admission["attempt_id"],
                "ledger_submission_id": admission["submission_id"],
                "ledger_graph_id": "basic-qa",
                "ledger_business_code": "basic-qa",
                "ledger_operation_kind": "resume" if resume else "initial",
            },
        }
        if resume:
            result["input"] = (
                None  # static interrupt resumes from the persisted next node
            )
        else:
            result.update(
                input={"messages": [{"role": "user", "content": question}]},
                interrupt_before=["call_model"],
            )
        return result

    check_response(native.post(f"/threads/{thread}/runs/wait", json=payload(accepted)))
    snapshot = check_response(native.get(f"/threads/{thread}/state"))
    thread_info = check_response(native.get(f"/threads/{thread}"))
    assert snapshot["next"] == ["call_model"] and thread_info["status"] == "interrupted"
    checkpoint = (
        snapshot.get("checkpoint_id") or snapshot["checkpoint"]["checkpoint_id"]
    )
    assert checkpoint and STATE["model_calls"] == 0
    paused_runs = check_response(native.get(f"/threads/{thread}/runs"))
    assert paused_runs
    check_response(
        admin.post(
            "/agent/runs/admission-confirmed",
            json={
                "request_id": accepted["request_id"],
                "attempt_id": accepted["attempt_id"],
                "submission_id": accepted["submission_id"],
                "outcome": "confirmed",
                "platform_run_id": paused_runs[0]["run_id"],
            },
        )
    )
    # Actual platform read -> existing internal state-observation HTTP API, no SQL state edits.
    check_response(
        admin.post(
            f"/agent/runs/{accepted['attempt_id']}/state",
            json={
                "state": "waiting_input",
                "evidence_source": "platform_read",
                "platform_status": thread_info["status"],
            },
        )
    )
    EVIDENCE["checks"].append(
        {
            "name": "real_interrupt",
            "checkpoint": checkpoint,
            "next": snapshot["next"],
            "model_calls": 0,
        }
    )

    resume_body = {
        "thread_id": thread,
        "submission_key": str(uuid.uuid4()),
        "graph_id": "basic-qa",
        "operation_kind": "resume",
        "source_attempt_id": accepted["attempt_id"],
        "content": "",
    }
    altered = copy.deepcopy(resume_body)
    altered["capability_selection"] = {
        "skill": {"mode": "auto"},
        "mcp": {"mode": "auto"},
    }
    check_response(admin.post("/agent/requests", json=altered), 409)
    EVIDENCE["checks"].append({"name": "admission_selection_tamper", "status": 409})
    altered = {**resume_body, "graph_id": "data-analysis"}
    check_response(admin.post("/agent/requests", json=altered), 409)
    EVIDENCE["checks"].append(
        {"name": "admission_graph_tamper_both_permitted", "status": 409}
    )
    resumed = check_response(admin.post("/agent/requests", json=resume_body))
    assert resumed["attempt_id"] == accepted["attempt_id"]
    legitimate = payload(resumed, resume=True)
    for label, updates in (
        ("native_graph_tamper_both_permitted", {"assistant_id": "data-analysis"}),
        ("native_selection_tamper", {"capability_mcp_mode": "auto"}),
        ("native_business_tamper", {"ledger_business_code": "data-analysis"}),
    ):
        changed = copy.deepcopy(legitimate)
        for key, value in updates.items():
            if key == "assistant_id":
                changed[key] = value
            else:
                changed["metadata"][key] = value
        check_response(native.post(f"/threads/{thread}/runs", json=changed), 409)
        assert STATE["model_calls"] == 0
        EVIDENCE["checks"].append({"name": label, "status": 409, "model_delta": 0})
    STATE["permissions"].remove(PERMS[0])
    STATE["revision"] += 1
    refresh_ticket()
    check_response(native.post(f"/threads/{thread}/runs", json=legitimate), 403)
    EVIDENCE["checks"].append(
        {"name": "business_revoked", "status": 403, "model_delta": STATE["model_calls"]}
    )
    STATE["permissions"] = list(PERMS)
    STATE["revision"] += 1
    refresh_ticket()
    STATE["active"] = False
    check_response(native.post(f"/threads/{thread}/runs", json=legitimate), 401)
    EVIDENCE["checks"].append(
        {"name": "session_revoked", "status": 401, "model_delta": STATE["model_calls"]}
    )
    STATE["active"] = True
    refresh_ticket()
    check_response(native.post(f"/threads/{thread}/runs/wait", json=legitimate))
    final = check_response(native.get(f"/threads/{thread}/state"))
    runs = check_response(native.get(f"/threads/{thread}/runs"))
    assert runs[0]["status"] == "success" and not final["next"]
    assert STATE["model_calls"] == 1
    assert any(
        "受控恢复验证完成" in str(m.get("content", ""))
        for m in final["values"]["messages"]
    )
    EVIDENCE["checks"].append(
        {"name": "legal_resume", "run_status": "success", "model_delta": 1}
    )
    # Read-only verification: two platform runs map to one logical request/attempt.
    check_response(
        admin.post(
            "/agent/runs/admission-confirmed",
            json={
                "request_id": resumed["request_id"],
                "attempt_id": resumed["attempt_id"],
                "submission_id": resumed["submission_id"],
                "outcome": "confirmed",
                "platform_run_id": runs[0]["run_id"],
            },
        )
    )
    with psycopg.connect(os.environ["DATABASE_URI"]) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        counts = {
            table: conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
            for table in (
                "agent_requests",
                "agent_run_attempts",
                "agent_submissions",
                "agent_run_resumes",
            )
        }
    assert counts == {
        "agent_requests": 1,
        "agent_run_attempts": 1,
        "agent_submissions": 2,
        "agent_run_resumes": 1,
    }, counts
    EVIDENCE["ledger_counts"] = counts


def main():
    evidence = Path(sys.argv[1]).resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    stage = "create_empty_resources"
    try:
        pg = start_container(
            "pg",
            [
                "-p",
                f"127.0.0.1:{PORTS['pg']}:5432",
                "-e",
                "POSTGRES_USER=air_agent",
                "-e",
                f"POSTGRES_PASSWORD={PASSWORD}",
                "-e",
                "POSTGRES_DB=air_agent_business",
                "-e",
                "PGDATA=/var/lib/postgresql/data/pgdata",
                "postgres:17-alpine",
            ],
        )
        redis = start_container(
            "redis", ["-p", f"127.0.0.1:{PORTS['redis']}:6379", "redis:7-alpine"]
        )
        pg_ip, redis_ip = container_ip(pg), container_ip(redis)
        dsn = f"postgresql://air_agent:{PASSWORD}@127.0.0.1:{PORTS['pg']}/air_agent_business"
        runtime = f"postgresql://air_agent_runtime:{RUNTIME_PASSWORD}@127.0.0.1:{PORTS['pg']}/air_agent_business"
        env = dict(
            os.environ,
            DATABASE_URI=dsn,
            ADMIN_RUNTIME_DATABASE_URI=runtime,
            RUNTIME_DATABASE_URI=runtime,
            AGENT_IDENTITY_BACKEND="remote",
            IAM_BASE_URL=f"http://{BRIDGE}:{PORTS['mock']}",
            IAM_ISSUER=ISSUER,
            IAM_INTERNAL_CLIENT_ID="fixture",
            IAM_INTERNAL_CLIENT_SECRET="synthetic",
            IAM_SSO_CLIENT_ID="fixture",
            IAM_SSO_CLIENT_SECRET="synthetic",
            IAM_RUNTIME_AUDIENCE="langgraph-runtime",
            RUNTIME_TICKET_KEYS=json.dumps({"fixture": KEY}),
            INTERNAL_API_TOKEN=INTERNAL,
            AGENT_CAPABILITY_SELECTION_ENABLED="true",
            TENANT_ENFORCE_RUNTIME_IDENTITY="true",
            TENANT_BUSINESS_SNAPSHOT_ENFORCED="true",
            LANGFUSE_ENABLED="false",
            SILICONFLOW_API_KEY="synthetic",
            SERPER_API_KEY="synthetic",
            MAIN_LLM_API_KEY="synthetic",
            MAIN_LLM_MODEL="controlled",
            MAIN_LLM_BASE_URL=f"http://127.0.0.1:{PORTS['mock']}/v1",
            POSTGRES_USER="air_agent",
            POSTGRES_PASSWORD=PASSWORD,
            PYTHONPATH=f"{REPO / 'src'}:{REPO / 'admin/api'}",
            AGENT_MCP_CONNECTIONS_JSON='{"schema_version":"agent.mcp-connections.v1","revision":1,"servers":[]}',
        )
        stage = "migrate_empty_database"
        prepare_database(dsn, env, evidence)
        os.environ.update(env)
        serve(
            MOCK, PORTS["mock"], BRIDGE
        )  # private host bridge only, never LAN interfaces
        from app.main import app
        from app.services import remote_identity

        original_error = remote_identity.raise_remote_error

        def record_dependency_error(exc):
            # Diagnostics only: retain the original failure mapping, never bypass it.
            EVIDENCE["last_dependency_error"] = {
                "type": type(exc).__name__,
                "message": str(exc).splitlines()[0][:300],
            }
            return original_error(exc)

        remote_identity.raise_remote_error = record_dependency_error
        from app.routers import ledger as ledger_router

        original_ledger_error = ledger_router._error

        def record_ledger_error(exc):
            cause = exc.__cause__
            if cause is not None:
                EVIDENCE["last_ledger_cause"] = {
                    "type": type(cause).__name__,
                    "message": str(cause).splitlines()[0][:300],
                }
            return original_ledger_error(exc)

        ledger_router._error = record_ledger_error

        serve(app, PORTS["admin"])
        wait_http(f"http://127.0.0.1:{PORTS['admin']}/health")
        container_env = {
            **{
                key: env[key]
                for key in (
                    "AGENT_IDENTITY_BACKEND",
                    "IAM_ISSUER",
                    "IAM_INTERNAL_CLIENT_ID",
                    "IAM_INTERNAL_CLIENT_SECRET",
                    "IAM_RUNTIME_AUDIENCE",
                    "RUNTIME_TICKET_KEYS",
                    "INTERNAL_API_TOKEN",
                    "AGENT_MCP_CONNECTIONS_JSON",
                    "TENANT_ENFORCE_RUNTIME_IDENTITY",
                    "TENANT_BUSINESS_SNAPSHOT_ENFORCED",
                    "LANGFUSE_ENABLED",
                )
            },
            "DATABASE_URI": f"postgresql://air_agent:{PASSWORD}@{pg_ip}:5432/air_agent_business",
            "POSTGRES_URI": f"postgresql://air_agent:{PASSWORD}@{pg_ip}:5432/air_agent_business",
            "RUNTIME_DATABASE_URI": f"postgresql://air_agent_runtime:{RUNTIME_PASSWORD}@{pg_ip}:5432/air_agent_business",
            "REDIS_URI": f"redis://{redis_ip}:6379",
            "IAM_BASE_URL": f"http://{BRIDGE}:{PORTS['mock']}",
            "LANGSERVE_GRAPHS": json.dumps(
                {
                    g: f"/deps/air_agent/src/{g.replace('-', '_')}/graph.py:graph"
                    for g in ("basic-qa", "data-analysis", "intelligent-tracing")
                }
            ),
            "LANGGRAPH_AUTH_TYPE": "custom",
            "LANGGRAPH_AUTH": '{"path":"/deps/air_agent/src/common/identity/langgraph_auth.py:auth","disable_studio_auth":true}',
            "LANGGRAPH_HTTP": '{"app":"/deps/air_agent/src/common/artifact_http.py:app"}',
            "MAIN_LLM_BASE_URL": f"http://{BRIDGE}:{PORTS['mock']}/v1",
            "MAIN_LLM_MODEL": "controlled",
            "MAIN_LLM_API_KEY": "synthetic",
            "SILICONFLOW_API_KEY": "synthetic",
            "SERPER_API_KEY": "synthetic",
            "LANGCHAIN_TRACING_V2": "false",
            "LANGSMITH_API_KEY": "lsv2_pt_synthetic_mock_key_000000000000_1234567890",
            "LANGSMITH_AUTH_ENDPOINT": f"http://{BRIDGE}:{PORTS['mock']}",
            "LANGSMITH_CONTROL_PLANE_API_KEY": "lsv2_pt_synthetic_mock_key_000000000000_1234567890",
        }
        args = [
            "-p",
            f"127.0.0.1:{PORTS['api']}:8000",
            "-v",
            f"{REPO / 'src'}:/deps/air_agent/src",
        ]
        for key, value in container_env.items():
            args.extend(["-e", f"{key}={value}"])
        stage = "official_api_boot"
        start_container("api", [*args, "air-agent/langgraph-api:latest"])
        wait_http(f"http://127.0.0.1:{PORTS['api']}/ok")
        stage = "real_http_chain"
        with (
            httpx.Client(
                base_url=f"http://127.0.0.1:{PORTS['admin']}",
                timeout=60,
                headers={"origin": "http://127.0.0.1:3000"},
            ) as admin,
            httpx.Client(
                base_url=f"http://127.0.0.1:{PORTS['api']}", timeout=60
            ) as native,
        ):
            run_checks(admin, native)
        EVIDENCE["passed"] = True
    except Exception as exc:
        EVIDENCE.update(passed=False, failed_stage=stage, error_type=type(exc).__name__)
        raise
    finally:
        EVIDENCE["resources_created"] = list(CREATED)
        cleanup = {}
        for server in SERVERS:
            server.should_exit = True
        for name in reversed(CREATED):
            if name.endswith("-api"):
                log = subprocess.run(
                    ["container", "logs", "-n", "200", name],
                    capture_output=True,
                    text=True,
                )
                (evidence / "official-api.log").write_text(log.stdout + log.stderr)
            cleanup[name] = (
                subprocess.run(
                    ["container", "delete", "--force", name], capture_output=True
                ).returncode
                == 0
            )
        EVIDENCE.update(cleanup=cleanup, ended_at=datetime.now(UTC).isoformat())
        (evidence / "result.json").write_text(
            json.dumps(EVIDENCE, ensure_ascii=False, indent=2) + "\n"
        )
        sys.stdout.write(json.dumps(EVIDENCE, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
