"""Readable account labels come from the binding owner's IAM reference."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from common.identity.ticket_binding import _load_binding_sync


@pytest.mark.parametrize("account", ["trusted-account", None])
def test_binding_account_lookup_respects_reference_user_scope(monkeypatch, account):
    row = {
        "user_id": "bound-user",
        "entry_expires_at": datetime.now(UTC) + timedelta(minutes=1),
    }
    conn = MagicMock()
    conn.__enter__.return_value = conn
    queries = []

    def execute(sql, params):
        queries.append((sql, params))
        result = (
            row
            if "agent_iam_ticket_bindings" in sql
            else {"account": account}
            if "iam_reference_users" in sql and account
            else None
        )
        return SimpleNamespace(fetchone=lambda: result)

    conn.execute.side_effect = execute
    monkeypatch.setenv(
        "RUNTIME_DATABASE_URI", "postgresql://fixture.invalid/self_owned"
    )
    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: conn)
    result = _load_binding_sync("digest")
    assert result["account"] == (account or "")
    assert queries[0][1] == ("digest",)
    assert "set_config('air_agent.user_id'" in queries[2][0]
    assert queries[2][1] == ("bound-user",)
    assert queries[3] == (
        "SELECT account FROM iam_reference_users WHERE id = %s",
        ("bound-user",),
    )


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_expired_binding_never_reads_an_account(monkeypatch, missing):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.return_value = (
        None
        if missing
        else {
            "user_id": "bound-user",
            "entry_expires_at": datetime.now(UTC) - timedelta(seconds=1),
        }
    )
    monkeypatch.setenv(
        "RUNTIME_DATABASE_URI", "postgresql://fixture.invalid/self_owned"
    )
    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: conn)
    assert _load_binding_sync("digest") is None
    assert conn.execute.call_count == 2
