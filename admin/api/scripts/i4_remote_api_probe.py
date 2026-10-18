#!/usr/bin/env python3
"""Exercise Agent's real remote login/session routes against the isolated I4 targets."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

AUTHOR = Path(
    "/Users/acerola/Dev/Java/air_agent_iam/docs/reviews/evidence/run/i4-luna-20261018/author"
)
ENV_FILE = Path(os.environ.get("I4_PRIVATE_ENV", "/private/tmp/i4-luna-20261018.private.env"))
VALUES = {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}
DB_NAME = "i4_luna_agent_target"


def configure() -> tuple[dict[str, str], dict[str, str]]:
    owner_url = make_url(VALUES["DATABASE_URI"]).set(database=DB_NAME)
    runtime_url = make_url(VALUES["ADMIN_RUNTIME_DATABASE_URI"]).set(database=DB_NAME)
    os.environ.update(
        {
            "DATABASE_URI": owner_url.render_as_string(hide_password=False),
            "ADMIN_RUNTIME_DATABASE_URI": runtime_url.render_as_string(hide_password=False),
            "AGENT_IDENTITY_BACKEND": "remote",
            "IAM_BASE_URL": "http://127.0.0.1:50400",
            "IAM_ISSUER": "air-agent-iam",
            "IAM_INTERNAL_CLIENT_ID": VALUES["IAM_INTERNAL_RUNTIME_CLIENT_ID"],
            "IAM_INTERNAL_CLIENT_SECRET": VALUES["IAM_INTERNAL_RUNTIME_CLIENT_SECRET"],
            "IAM_RUNTIME_AUDIENCE": "langgraph-runtime",
        }
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    return (
        {"database": DB_NAME, "runtime_role": runtime_url.username or ""},
        {
            "owner_dsn": owner_url.render_as_string(hide_password=False),
            "runtime_dsn": runtime_url.render_as_string(hide_password=False),
        },
    )


def read_session_state(owner_dsn: str, handle_hash: str):
    """Read through the table's session-key RLS policy using the same scoped key."""
    import psycopg

    with psycopg.connect(owner_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('air_agent.iam_handle_hash', %s, true)", (handle_hash,))
        cur.execute(
            "SELECT id::text, user_id::text, revoked_at IS NOT NULL, subject_token IS NOT NULL "
            "FROM agent_iam_sessions WHERE handle_hash=%s",
            (handle_hash,),
        )
        return cur.fetchone()


def main() -> None:
    target, db = configure()
    ids = json.loads((AUTHOR / "agent-fixture-ids.json").read_text(encoding="utf-8"))
    from fastapi.testclient import TestClient

    from app.main import app

    evidence: dict[str, object] = {"target": target}
    with TestClient(app, raise_server_exceptions=False) as client:
        login_response = client.post(
            "/auth/login",
            json={"account": VALUES["I4_MEMBER_ACCOUNT"], "password": VALUES["I4_MEMBER_PASSWORD"]},
        )
        if login_response.status_code != 200:
            raise RuntimeError(f"remote Agent login failed with HTTP {login_response.status_code}")
        login_body = login_response.json()
        evidence["remote_login_http"] = login_response.status_code
        evidence["cookie_names"] = sorted(client.cookies.keys())
        evidence["login_user_id"] = str(login_body["user"]["id"])
        evidence["login_user_created_at"] = login_body["user"].get("created_at")
        evidence["login_user_has_password_hash"] = "password_hash" in login_body["user"]
        evidence["login_tenant_count"] = len(login_body.get("tenants") or [])

        current = login_body.get("current_tenant")
        if not current or str(current.get("id")) != ids["tenant_id"]:
            switch_response = client.post(
                "/auth/switch-tenant",
                json={
                    "tenant_id": ids["tenant_id"],
                    "expected_ctx_version": login_body["context_version"],
                },
            )
            if switch_response.status_code != 200:
                raise RuntimeError(
                    f"remote tenant switch failed with HTTP {switch_response.status_code}"
                )
            login_body = switch_response.json()
            evidence["remote_tenant_switch_http"] = switch_response.status_code

        me_response = client.get("/auth/me")
        if me_response.status_code != 200:
            raise RuntimeError(f"remote Agent me failed with HTTP {me_response.status_code}")
        me = me_response.json()
        evidence["remote_me_http"] = me_response.status_code
        evidence["resolved_user_id"] = str(me["user"]["id"])
        evidence["resolved_tenant_id"] = str(me["current_tenant"]["id"])
        evidence["resolved_membership_id"] = str(me["membership"]["id"])
        evidence["resolved_context_version"] = me["context_version"]
        if evidence["resolved_user_id"] != ids["user_id"]:
            raise RuntimeError("resolved user UUID differs from the IAM-authoritative fixture")
        if evidence["resolved_tenant_id"] != ids["tenant_id"]:
            raise RuntimeError("resolved tenant UUID differs from the IAM-authoritative fixture")
        if evidence["resolved_membership_id"] != ids["membership_id"]:
            raise RuntimeError(
                "resolved membership UUID differs from the IAM-authoritative fixture"
            )

        cookie = client.cookies.get("air_agent_iam_handle")
        if not cookie:
            raise RuntimeError("remote Agent login did not set the application session cookie")
        handle_hash = hashlib.sha256(cookie.encode()).hexdigest()
        before_logout = read_session_state(db["runtime_dsn"], handle_hash)
        evidence["session_row_before_logout_found"] = before_logout is not None
        evidence["session_row_before_logout_active"] = bool(before_logout and not before_logout[2])
        evidence["session_row_before_logout_has_subject_token"] = bool(
            before_logout and before_logout[3]
        )

        logout_response = client.post("/auth/logout")
        if logout_response.status_code != 204:
            raise RuntimeError(
                f"remote Agent logout failed with HTTP {logout_response.status_code}"
            )
        evidence["remote_logout_http"] = logout_response.status_code
        stale_response = client.get("/auth/me")
        evidence["me_after_logout_http"] = stale_response.status_code
        if stale_response.status_code != 401:
            raise RuntimeError("Agent application session remained usable after logout")
        session_row = read_session_state(db["runtime_dsn"], handle_hash)
        evidence["session_row_after_logout_found"] = session_row is not None
        evidence["session_row_after_logout_revoked"] = bool(session_row and session_row[2])
        evidence["session_row_after_logout_has_subject_token"] = bool(
            session_row and session_row[3]
        )
        (AUTHOR / "agent-remote-api-debug.json").write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
        )
        os.chmod(AUTHOR / "agent-remote-api-debug.json", 0o600)

    if (
        session_row is None
        or session_row[1] != ids["user_id"]
        or session_row[2] is not True
        or session_row[3] is not False
    ):
        raise RuntimeError("Agent IAM session was not revoked and cleared after logout")
    evidence["persisted_agent_session_id"] = session_row[0]
    evidence["persisted_agent_session_revoked"] = session_row[2]
    evidence["persisted_agent_subject_token_cleared"] = not session_row[3]
    evidence["subject_token_written_to_report"] = False

    out = AUTHOR / "agent-remote-api-probes.json"
    out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    os.chmod(out, 0o600)
    print("AGENT_REMOTE_API_PROBES_PASSED")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - never print response bodies or credentials
        print(f"AGENT_REMOTE_API_PROBES_FAILED {type(exc).__name__}: {exc}")
        raise SystemExit(2) from None
