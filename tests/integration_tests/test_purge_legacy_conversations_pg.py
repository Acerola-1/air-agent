"""隔离临时库上的旧会话清理脚本测试；无 PURGE_TEST_DSN 时跳过."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest

psycopg = pytest.importorskip("psycopg")

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/purge_legacy_conversations.py"

SCHEMA = """
CREATE TABLE thread (thread_id uuid PRIMARY KEY, status text);
CREATE TABLE run (run_id uuid PRIMARY KEY, thread_id uuid, status text);
CREATE TABLE checkpoints (thread_id uuid, checkpoint_id uuid);
CREATE TABLE checkpoint_blobs (thread_id uuid, channel text);
CREATE TABLE checkpoint_writes (thread_id uuid, checkpoint_id uuid);
CREATE TABLE checkpoint_delete_queue (thread_id uuid);
CREATE TABLE thread_ttl (thread_id uuid);
CREATE TABLE cron (thread_id uuid);
CREATE TABLE thread_ownership (thread_id text, tenant_id uuid, user_id uuid);
CREATE TABLE store (prefix text, key text, value jsonb);
CREATE TABLE agent_answer_contents (
    id uuid PRIMARY KEY, tenant_id uuid NOT NULL, content_text text, summary text,
    UNIQUE (id, tenant_id)
);
CREATE TABLE agent_answer_content_metadata (
    id uuid, tenant_id uuid NOT NULL,
    FOREIGN KEY (id, tenant_id) REFERENCES agent_answer_contents(id, tenant_id)
        ON DELETE CASCADE
);
CREATE TABLE agent_content_refs (
    id uuid PRIMARY KEY, content_snapshot text, deleted_at timestamptz
);
CREATE TABLE agent_feedback_comments (
    id uuid PRIMARY KEY, comment_text text NOT NULL, deleted_at timestamptz
);
CREATE TABLE agent_feedback_case_notes (
    id uuid PRIMARY KEY, note_text text NOT NULL, deleted_at timestamptz
);
CREATE TABLE agent_feedback_case_actions (
    id uuid PRIMARY KEY, result_snapshot jsonb
);
CREATE TABLE knowledge_citations (citation_id text PRIMARY KEY, excerpt text);
CREATE TABLE artifact_computations (artifact_id text, run_id text);
CREATE TABLE tool_artifact_storage (
    artifact_id text PRIMARY KEY, raw_payload text NOT NULL
);
CREATE TABLE agent_question_sources (id uuid PRIMARY KEY);
CREATE TABLE agent_question_intents (
    id uuid PRIMARY KEY, source_id uuid NOT NULL,
    FOREIGN KEY (source_id) REFERENCES agent_question_sources(id) ON DELETE CASCADE
);
CREATE TABLE agent_question_intent_contents (
    intent_id uuid PRIMARY KEY, normalized_text text,
    FOREIGN KEY (intent_id) REFERENCES agent_question_intents(id) ON DELETE CASCADE
);
CREATE TABLE agent_question_source_gaps (id uuid PRIMARY KEY, detail text);
CREATE TABLE agent_question_clusters (
    id uuid PRIMARY KEY, representative_intent_id uuid,
    canonical_name text NOT NULL, action text NOT NULL, object text NOT NULL,
    constraints jsonb NOT NULL
);
CREATE TABLE agent_thread_deletions (
    id uuid PRIMARY KEY, tenant_id uuid, thread_id text, requested_by uuid,
    state text, attempts integer, last_error_class text,
    requested_at timestamptz, completed_at timestamptz, user_id uuid
);
CREATE TABLE agent_requests (id uuid PRIMARY KEY);
CREATE TABLE agent_events (event_id uuid PRIMARY KEY);
CREATE TABLE agent_outbox (id bigserial PRIMARY KEY);
CREATE TABLE agent_answers (id uuid PRIMARY KEY);
CREATE TABLE assistant (assistant_id uuid PRIMARY KEY);
CREATE TABLE iam_reference_users (id uuid PRIMARY KEY);
"""

PRESERVED = {
    "agent_requests": 3,
    "agent_events": 3,
    "agent_outbox": 3,
    "agent_answers": 3,
    "assistant": 1,
    "iam_reference_users": 2,
    "agent_thread_deletions": 3,
}


def _admin_dsn() -> str:
    dsn = os.environ.get("PURGE_TEST_DSN")
    if not dsn:
        pytest.skip("PURGE_TEST_DSN 未设置")
    return dsn


@pytest.fixture
def temp_database():
    admin = _admin_dsn()
    name = f"purge_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    parsed = urlparse(admin)
    dsn = urlunparse(parsed._replace(path=f"/{name}"))
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            _seed(conn)
        yield dsn
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _seed(conn) -> None:
    thread_ids = [uuid.uuid4() for _ in range(3)]
    for thread_id in thread_ids:
        conn.execute("INSERT INTO thread VALUES (%s, 'idle')", (thread_id,))
        conn.execute(
            "INSERT INTO run VALUES (%s, %s, 'success')", (uuid.uuid4(), thread_id)
        )
        conn.execute(
            "INSERT INTO checkpoints VALUES (%s, %s)", (thread_id, uuid.uuid4())
        )
        conn.execute(
            "INSERT INTO checkpoint_blobs VALUES (%s, 'messages')", (thread_id,)
        )
        conn.execute(
            "INSERT INTO checkpoint_writes VALUES (%s, %s)",
            (thread_id, uuid.uuid4()),
        )
        conn.execute("INSERT INTO thread_ttl VALUES (%s)", (thread_id,))
        conn.execute(
            "INSERT INTO thread_ownership VALUES (%s, %s, %s)",
            (str(thread_id), uuid.uuid4(), uuid.uuid4()),
        )
        conn.execute(
            "INSERT INTO agent_thread_deletions "
            "(id, tenant_id, thread_id, requested_by, state, attempts, requested_at, user_id) "
            "VALUES (%s, %s, %s, %s, 'completed', 1, now(), %s)",
            (uuid.uuid4(), uuid.uuid4(), str(thread_id), uuid.uuid4(), uuid.uuid4()),
        )
    # 无归属线程与无法解析的归属记录
    orphan = uuid.uuid4()
    conn.execute("INSERT INTO thread VALUES (%s, 'idle')", (orphan,))
    conn.execute("INSERT INTO checkpoint_delete_queue VALUES (%s)", (orphan,))
    conn.execute(
        "INSERT INTO thread_ownership VALUES ('not-a-uuid', %s, %s)",
        (
            uuid.uuid4(),
            uuid.uuid4(),
        ),
    )
    conn.execute("INSERT INTO cron VALUES (%s)", (uuid.uuid4(),))
    conn.execute("INSERT INTO store VALUES ('user', 'k1', '{}'::jsonb)")
    conn.execute("INSERT INTO store VALUES ('user', 'k2', '{}'::jsonb)")

    for index in range(2):
        content_id = uuid.uuid4()
        tenant = uuid.uuid4()
        conn.execute(
            "INSERT INTO agent_answer_contents VALUES (%s, %s, '回答正文', '摘要')",
            (content_id, tenant),
        )
        conn.execute(
            "INSERT INTO agent_answer_content_metadata VALUES (%s, %s)",
            (content_id, tenant),
        )
        conn.execute(
            "INSERT INTO agent_content_refs VALUES (%s, '原文快照', NULL)",
            (uuid.uuid4(),),
        )
        conn.execute(
            "INSERT INTO agent_feedback_comments VALUES (%s, '用户意见', NULL)",
            (uuid.uuid4(),),
        )
        conn.execute(
            "INSERT INTO agent_feedback_case_notes VALUES (%s, '处理说明', NULL)",
            (uuid.uuid4(),),
        )
        conn.execute(
            "INSERT INTO agent_feedback_case_actions VALUES (%s, %s::jsonb)",
            (uuid.uuid4(), '{"result": "ok"}'),
        )
        conn.execute(
            "INSERT INTO knowledge_citations VALUES (%s, '引用原文')",
            (f"cit-{index}",),
        )
        conn.execute(
            "INSERT INTO artifact_computations VALUES ('art-1', %s)", (f"run-{index}",)
        )
        conn.execute(
            "INSERT INTO tool_artifact_storage VALUES (%s, '工具原文')",
            (f"art-{index}",),
        )
        conn.execute(
            "INSERT INTO agent_question_source_gaps VALUES (%s, '细节')",
            (uuid.uuid4(),),
        )
    source = uuid.uuid4()
    intent = uuid.uuid4()
    conn.execute("INSERT INTO agent_question_sources VALUES (%s)", (source,))
    conn.execute("INSERT INTO agent_question_intents VALUES (%s, %s)", (intent, source))
    conn.execute(
        "INSERT INTO agent_question_intent_contents VALUES (%s, '归一化文本')",
        (intent,),
    )
    conn.execute(
        "INSERT INTO agent_question_clusters VALUES (%s, %s, '主题', '查', '对象', '{}'::jsonb)",
        (uuid.uuid4(), intent),
    )

    for index in range(3):
        conn.execute("INSERT INTO agent_requests VALUES (%s)", (uuid.uuid4(),))
        conn.execute("INSERT INTO agent_events VALUES (%s)", (uuid.uuid4(),))
        conn.execute("INSERT INTO agent_outbox DEFAULT VALUES")
        conn.execute("INSERT INTO agent_answers VALUES (%s)", (uuid.uuid4(),))
    conn.execute("INSERT INTO assistant VALUES (%s)", (uuid.uuid4(),))
    for _ in range(2):
        conn.execute("INSERT INTO iam_reference_users VALUES (%s)", (uuid.uuid4(),))


def _count(conn, table: str) -> int:
    return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def _run(
    dsn: str, tmp_path: Path, *, apply: bool, resume: bool = False
) -> subprocess.CompletedProcess:
    backup = tmp_path / "backup.dump"
    if not backup.exists():
        backup.write_bytes(b"backup")
    args = [
        sys.executable,
        str(SCRIPT),
        "--dsn",
        dsn,
        "--backup-file",
        str(backup),
        "--allow-active-connections",
        "--progress",
        str(tmp_path / "progress.json"),
        "--sqlite",
        str(tmp_path / "no-such.db"),
    ]
    if apply:
        args.append("--apply")
    if resume:
        args.append("--resume")
    return subprocess.run(args, capture_output=True, text=True, check=False)


def test_dry_run_then_apply_is_reentrant(temp_database, tmp_path):
    dsn = temp_database
    with psycopg.connect(dsn, autocommit=True) as conn:
        before = {table: _count(conn, table) for table in PRESERVED}

    dry = _run(dsn, tmp_path, apply=False)
    assert dry.returncode == 0, dry.stderr
    assert "dry-run" in dry.stdout
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert _count(conn, "thread") == 4
        assert _count(conn, "store") == 2

    applied = _run(dsn, tmp_path, apply=True)
    assert applied.returncode == 0, applied.stderr
    with psycopg.connect(dsn, autocommit=True) as conn:
        for table in (
            "thread",
            "run",
            "checkpoints",
            "checkpoint_blobs",
            "checkpoint_writes",
            "checkpoint_delete_queue",
            "thread_ttl",
            "cron",
            "store",
            "agent_answer_contents",
            "agent_answer_content_metadata",
            "knowledge_citations",
            "artifact_computations",
            "tool_artifact_storage",
            "agent_question_sources",
            "agent_question_intents",
            "agent_question_intent_contents",
        ):
            assert _count(conn, table) == 0, table
        refs = conn.execute(
            "SELECT count(*) FROM agent_content_refs WHERE content_snapshot <> '' "
            "OR deleted_at IS NULL"
        ).fetchone()[0]
        assert refs == 0
        comments = conn.execute(
            "SELECT count(*) FROM agent_feedback_comments "
            "WHERE comment_text <> '' OR deleted_at IS NULL"
        ).fetchone()[0]
        assert comments == 0
        notes = conn.execute(
            "SELECT count(*) FROM agent_feedback_case_notes "
            "WHERE note_text <> '' OR deleted_at IS NULL"
        ).fetchone()[0]
        assert notes == 0
        actions = conn.execute(
            "SELECT count(*) FROM agent_feedback_case_actions "
            "WHERE result_snapshot IS NOT NULL"
        ).fetchone()[0]
        assert actions == 0
        cluster = conn.execute(
            "SELECT canonical_name, representative_intent_id FROM agent_question_clusters"
        ).fetchone()
        assert cluster == ("", None)
        gaps = conn.execute(
            "SELECT count(*) FROM agent_question_source_gaps WHERE detail IS NOT NULL"
        ).fetchone()[0]
        assert gaps == 0
        ownership = conn.execute("SELECT thread_id FROM thread_ownership").fetchall()
        assert ownership == [("not-a-uuid",)]
        after = {table: _count(conn, table) for table in PRESERVED}
        assert after == before

    resumed = _run(dsn, tmp_path, apply=True, resume=True)
    assert resumed.returncode == 0, resumed.stderr
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert _count(conn, "thread") == 0
        assert _count(conn, "knowledge_citations") == 0
