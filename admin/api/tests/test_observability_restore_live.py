"""全新临时PG内的真实pg_dump/pg_restore删除重施演练；不接触业务库。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from tests.test_event_ledger_api import ledger_env as ledger_env
from tests.test_observability_api import fixture

pytest.importorskip("common.observability")
from common.observability import store

from scripts.reapply_thread_deletions import apply_manifest, export_manifest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("AIR_AGENT_OBSERVABILITY_RESTORE_TESTS") != "1",
        reason="显式开启完全独立临时PG恢复演练",
    ),
]


async def test_restore_reapplies_tombstone_before_runtime_reads(ledger_env, monkeypatch):
    container = "aa-takeover35-ephemeral-pg"
    owner = os.environ["DATABASE_URI"]
    parsed = urlsplit(owner)
    assert parsed.username == "aa35_owner" and parsed.path.startswith("/aa35_")
    raw = json.loads(subprocess.check_output(["container", "inspect", container], text=True))[0]
    ips = {
        n["ipv4Address"].split("/")[0] for n in raw["status"]["networks"] if n.get("ipv4Address")
    }
    assert parsed.hostname in ips, "只能操作已核实的独立临时容器"
    c, d, ctx = await fixture(ledger_env)
    name = "aa35_restored_" + uuid.uuid4().hex[:10]

    def target(uri):
        p = urlsplit(uri)
        return urlunsplit((p.scheme, p.netloc, "/" + name, p.query, p.fragment))

    created = False
    root = Path(__file__).resolve().parents[3]
    try:
        feedback = await c.put(
            "/agent/feedback/answers/" + d["answer"]["answer_id"],
            json={
                "rating": "negative",
                "reason_code": "incomplete",
                "comment": "仅限临时库恢复探针",
                "operation_id": "restore-" + uuid.uuid4().hex,
                "expected_revision": 0,
            },
        )
        assert feedback.status_code == 200, feedback.text
        await store.record_model_observation(
            ctx,
            observation_id=str(uuid.uuid4()),
            logical_call_id=str(uuid.uuid4()),
            role="main",
            model="controlled",
            provider="controlled",
            status="succeeded",
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            measurement_status="measured",
        )
        dump = subprocess.run(
            [
                "container",
                "exec",
                container,
                "pg_dump",
                "-U",
                "aa35_owner",
                "-d",
                parsed.path[1:],
                "--format=c",
                "--no-owner",
                "--no-privileges",
            ],
            capture_output=True,
        )
        assert dump.returncode == 0
        deleted = await c.post("/agent/threads/deletion", json={"thread_id": ctx.thread_id})
        assert deleted.status_code == 200, deleted.text
        with psycopg.connect(owner, row_factory=dict_row) as conn:
            manifest = export_manifest(conn)
        manifest["deletions"] = [
            row
            for row in manifest["deletions"]
            if row["tenant_id"] == ctx.tenant_id and row["thread_id"] == ctx.thread_id
        ]
        assert len(manifest["deletions"]) == 1 and manifest["deletions"][0]["event"]
        with psycopg.connect(owner, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            created = True
        restored = subprocess.run(
            [
                "container",
                "exec",
                "-i",
                container,
                "pg_restore",
                "-U",
                "aa35_owner",
                "-d",
                name,
                "--no-owner",
                "--no-privileges",
            ],
            input=dump.stdout,
            capture_output=True,
        )
        assert restored.returncode == 0, restored.stderr[-300:].decode(errors="replace")
        env = {**os.environ, "DATABASE_URI": target(owner)}
        permissions = subprocess.run(
            [sys.executable, "scripts/restore_ledger_permissions.py"],
            cwd=root / "admin/api",
            env=env,
            capture_output=True,
            text=True,
        )
        assert permissions.returncode == 0, permissions.stderr[-400:]
        with psycopg.connect(target(owner), row_factory=dict_row) as conn:
            before = conn.execute(
                "SELECT count(*) AS n FROM agent_feedback_comments WHERE tenant"
                "_id=%s AND thread_id=%s",
                (ctx.tenant_id, ctx.thread_id),
            ).fetchone()["n"]
            assert before == 1
            applied = apply_manifest(conn, manifest)
        with psycopg.connect(target(os.environ["RUNTIME_DATABASE_URI"])) as conn:
            conn.execute("SELECT set_config('air_agent.tenant_id',%s,true)", (ctx.tenant_id,))
            conn.execute("SELECT set_config('air_agent.user_id',%s,true)", (ctx.user_id,))
            visible = {
                table: conn.execute(
                    sql.SQL("SELECT count(*) FROM {} WHERE tenant_id=%s").format(
                        sql.Identifier(table)
                    ),
                    (ctx.tenant_id,),
                ).fetchone()[0]
                for table in [
                    "agent_feedback_current",
                    "agent_feedback_operations",
                    "agent_feedback_comments",
                    "agent_model_observations",
                ]
            }
            assert all(n == 0 for n in visible.values()), visible
        await store.close_pool()
        monkeypatch.setenv("RUNTIME_DATABASE_URI", target(os.environ["RUNTIME_DATABASE_URI"]))
        with pytest.raises(store.ThreadDeleted):
            await store.record_model_observation(
                ctx,
                observation_id=str(uuid.uuid4()),
                logical_call_id=str(uuid.uuid4()),
                role="main",
                model="controlled",
                provider="controlled",
                status="started",
            )
        proof = {
            "backup": "真实pg_dump/pg_restore，恢复时无ACL",
            "restored_old_comments_before_reapply": before,
            "permissions_reapplied": True,
            "runtime_visible_after_reapply": visible,
            "late_write_rejected": True,
            "manifest_result": applied,
            "external_cleanup": "独立任务待验证；不冒充已删除",
        }
        (root / "docs/reviews/evidence/takeover-03-05/restore-deletion.json").write_text(
            json.dumps(proof, indent=2) + "\n"
        )
    finally:
        await c.aclose()
        await store.close_pool()
        if created:
            with psycopg.connect(owner, autocommit=True) as conn:
                conn.execute(sql.SQL("DROP DATABASE {} WITH(FORCE)").format(sql.Identifier(name)))
