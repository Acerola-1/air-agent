"""归档迁移、授权、保留期和处理配额回归."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from common.config import config
from common.storage import artifact_db as db


@pytest.fixture
def storage(tmp_path, monkeypatch):
    path = tmp_path / "archive.db"
    monkeypatch.delenv("DATABASE_URI", raising=False)
    monkeypatch.setattr(db, "_get_sqlite_path", lambda: str(path))
    monkeypatch.setattr(db, "_initialized", False)
    return path


async def save(**extra):
    return await db.save_tool_artifact(
        tool_call_id="c",
        tool_name="test",
        raw_payload="tail777" * 1000,
        user_id="u",
        thread_id="t",
        run_id="r",
        **extra,
    )


@pytest.mark.anyio
async def test_migration_ownership_and_delete(storage):
    connection = sqlite3.connect(storage)
    connection.execute(
        "CREATE TABLE tool_artifact_storage (artifact_id TEXT PRIMARY KEY,tool_call_id TEXT,tool_name TEXT,char_length INT,raw_payload TEXT,summary TEXT,created_at TEXT)"
    )
    connection.execute(
        "INSERT INTO tool_artifact_storage VALUES ('old','c','test',6,'secret','','2020-01-01')"
    )
    connection.commit()
    connection.close()
    artifact = await save()
    record = await db.get_tool_artifact(artifact, user_id="u", thread_id="t")
    assert record["char_length"] == 7000
    assert (
        29
        < (
            datetime.fromisoformat(record["expires_at"]) - datetime.now(UTC)
        ).total_seconds()
        / 86400
        < 31
    )
    assert await db.get_tool_artifact(artifact, user_id="other", thread_id="t") is None
    assert await db.get_tool_artifact(artifact, user_id="u", thread_id="other") is None
    assert await db.get_tool_artifact("old", user_id="u", thread_id="t") is None
    derived = await save(kind="derived", parent_artifact_id=artifact)
    await db.delete_thread_artifacts(user_id="u", thread_id="other")
    assert await db.get_tool_artifact(derived, user_id="u", thread_id="t")
    await db.delete_thread_artifacts(user_id="u", thread_id="t")
    await db.delete_thread_artifacts(user_id="u", thread_id="t")
    assert await db.get_tool_artifact(artifact, user_id="u", thread_id="t") is None
    assert await db.list_tool_artifacts(user_id="u", thread_id="t") == []


@pytest.mark.anyio
async def test_expiry_clears_payload(storage):
    artifact = await save()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await db._query(
        "UPDATE tool_artifact_storage SET expires_at=? WHERE artifact_id=?",
        (past, artifact),
    )
    with pytest.raises(db.ArtifactExpired):
        await db.get_tool_artifact(artifact, user_id="u", thread_id="t")
    await db.cleanup_expired_artifacts()
    rows = await db._query(
        "SELECT raw_payload,status FROM tool_artifact_storage WHERE artifact_id=?",
        (artifact,),
        fetch=True,
    )
    assert rows == [{"raw_payload": "", "status": "expired"}]


@pytest.mark.anyio
async def test_attempt_and_total_deadline(storage):
    artifact = await save()
    await db.claim_computation(artifact, "r", attempt=False)
    assert (await db.claim_computation(artifact, "r"))["attempt"] == 1
    assert (await db.claim_computation(artifact, "r"))["attempt"] == 2
    with pytest.raises(db.ComputationLimit):
        await db.claim_computation(artifact, "r")
    await db.claim_computation(artifact, "r2", attempt=False)
    await db._query(
        "UPDATE artifact_computations SET started_at=? WHERE run_id=?",
        ((datetime.now(UTC) - timedelta(seconds=100)).isoformat(), "r2"),
    )
    with pytest.raises(db.ComputationLimit):
        await db.claim_computation(artifact, "r2")


@pytest.mark.anyio
async def test_pg_failure_never_falls_back(storage, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URI", "postgresql://invalid:invalid@127.0.0.1:1/unavailable"
    )
    with pytest.raises(Exception):
        await save()
    assert not storage.exists()


@pytest.mark.anyio
async def test_zero_retention_and_required_owner(storage, monkeypatch):
    monkeypatch.setattr(config, "TOOL_ARTIFACT_RETENTION_DAYS", 0)
    artifact = await save()
    assert (await db.get_tool_artifact(artifact, user_id="u", thread_id="t"))[
        "expires_at"
    ] is None
    with pytest.raises(PermissionError):
        await db.save_tool_artifact(
            tool_call_id="c",
            tool_name="test",
            raw_payload="x",
            user_id="",
            thread_id="t",
            run_id="r",
        )


@pytest.mark.anyio
async def test_malformed_pg_uri_does_not_fall_back(storage, monkeypatch):
    monkeypatch.setenv("DATABASE_URI", "postgresql+psycopg://invalid")
    with pytest.raises(ValueError):
        await save()
    assert not storage.exists()
