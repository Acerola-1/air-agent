"""Owned empty PostgreSQL resources: binding and reference-label RLS only."""

# Bootstrap the repo import path; this is a standalone CLI report.
# ruff: noqa: E402, T201

import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.conninfo import make_conninfo

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / "src"))
from common.identity.ticket_binding import _load_binding_sync


def main():
    """Create, validate and remove only the empty resources owned by this run."""
    owner = dotenv_values(root / ".env")["DATABASE_URI"]
    suffix = uuid.uuid4().hex[:12]
    database = "aa_lf_session_" + suffix
    role = "aa_lf_reader_" + suffix
    password = uuid.uuid4().hex + uuid.uuid4().hex
    created_db = created_role = False
    result = {
        "database": database,
        "role": role,
        "scope": "self-owned empty resources",
        "checks": {},
    }
    admin = psycopg.connect(owner, dbname="postgres", autocommit=True)
    try:
        admin.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
        created_role = True
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        created_db = True
        user_a, user_b, user_c = [uuid.uuid4() for _ in range(3)]
        with psycopg.connect(owner, dbname=database) as setup:
            setup.execute(
                "CREATE TABLE agent_iam_ticket_bindings(ticket_hash text PRIMARY KEY,user_id uuid NOT NULL,entry_expires_at timestamptz NOT NULL)"
            )
            setup.execute(
                "CREATE TABLE iam_reference_users(id uuid PRIMARY KEY,account varchar(64) NOT NULL)"
            )
            for table, condition in [
                (
                    "agent_iam_ticket_bindings",
                    "ticket_hash = current_setting('air_agent.iam_ticket_hash',true)",
                ),
                (
                    "iam_reference_users",
                    "id::text = current_setting('air_agent.user_id',true)",
                ),
            ]:
                setup.execute(
                    sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(
                        sql.Identifier(table)
                    )
                )
                setup.execute(
                    sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(
                        sql.Identifier(table)
                    )
                )
                setup.execute(
                    sql.SQL(
                        "CREATE POLICY owned_scope ON {} TO {} USING ("
                        + condition
                        + ") WITH CHECK ("
                        + condition
                        + ")"
                    ).format(sql.Identifier(table), sql.Identifier(role))
                )
                setup.execute(
                    sql.SQL("GRANT SELECT ON {} TO {}").format(
                        sql.Identifier(table), sql.Identifier(role)
                    )
                )
            setup.execute(
                "INSERT INTO iam_reference_users VALUES(%s,%s),(%s,%s)",
                (user_a, "alice-fixture", user_b, "bob-fixture"),
            )
            expires = datetime.now(UTC) + timedelta(minutes=5)
            setup.execute(
                "INSERT INTO agent_iam_ticket_bindings VALUES(%s,%s,%s),(%s,%s,%s),(%s,%s,%s),(%s,%s,%s)",
                (
                    "ticket-a",
                    user_a,
                    expires,
                    "ticket-b",
                    user_b,
                    expires,
                    "missing-reference",
                    user_c,
                    expires,
                    "expired",
                    user_a,
                    datetime.now(UTC) - timedelta(seconds=2),
                ),
            )
        runtime = make_conninfo(owner, dbname=database, user=role, password=password)
        os.environ["RUNTIME_DATABASE_URI"] = runtime
        assert _load_binding_sync("ticket-a")["account"] == "alice-fixture"
        assert _load_binding_sync("ticket-b")["account"] == "bob-fixture"
        assert _load_binding_sync("missing-reference")["account"] == ""
        assert _load_binding_sync("missing-ticket") is None
        assert _load_binding_sync("expired") is None
        result["checks"].update(
            bound_account_a=True,
            bound_account_b=True,
            missing_reference_omits_label=True,
            missing_ticket_denied=True,
            expired_ticket_denied=True,
        )
        with psycopg.connect(runtime) as request:
            flags = request.execute(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            assert flags == (False, False)
            request.execute(
                "SELECT set_config('air_agent.user_id',%s,true)", (str(user_a),)
            )
            own = request.execute(
                "SELECT account FROM iam_reference_users WHERE id=%s", (user_a,)
            ).fetchall()
            other = request.execute(
                "SELECT account FROM iam_reference_users WHERE id=%s", (user_b,)
            ).fetchall()
            assert own == [("alice-fixture",)] and not other
            result["checks"].update(
                non_owner_non_bypass_role=True,
                positive_own_reference=True,
                denied_other_reference_rows=len(other),
            )
    finally:
        if created_db:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )
        if created_role:
            admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        admin.close()
        result["resources_removed"] = True
    output = Path(__file__).resolve().parent / "reruns" / ("rls-" + suffix)
    output.mkdir(parents=True)
    (output / "isolated-reference-rls.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
