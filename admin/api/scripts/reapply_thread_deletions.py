"""备份恢复开放读取前，重施另存的永久删除清单；默认只校验预览。

清单必须来自可信运维备份通道，独立于历史数据库快照保存。owner连接来自
DATABASE_URI；--export导出当前清单，--manifest校验，--apply显式重施。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings


def export_manifest(conn):
    rows = conn.execute(
        "SELECT id,tenant_id,thread_id,user_id,requested_by,requested_at FROM a"
        "gent_thread_deletions ORDER BY tenant_id,thread_id"
    ).fetchall()
    records = []
    for row in rows:
        record = {
            k: (
                v.isoformat()
                if isinstance(v, dt.datetime)
                else str(v)
                if isinstance(v, uuid.UUID)
                else v
            )
            for k, v in row.items()
        }
        event = conn.execute(
            "SELECT event_id,user_id,membership_id,payload,occurred_at FROM age"
            "nt_events WHERE tenant_id=%s AND thread_id=%s AND event_type='reso"
            "urce.deleted' ORDER BY occurred_at DESC LIMIT 1",
            (row["tenant_id"], row["thread_id"]),
        ).fetchone()
        record["event"] = (
            {
                k: (
                    v.isoformat()
                    if isinstance(v, dt.datetime)
                    else str(v)
                    if isinstance(v, uuid.UUID)
                    else v
                )
                for k, v in event.items()
            }
            if event
            else None
        )
        records.append(record)
    return {"version": 1, "exported_at": dt.datetime.now(dt.UTC).isoformat(), "deletions": records}


def validate_manifest(manifest):
    if manifest.get("version") != 1 or not isinstance(manifest.get("deletions"), list):
        raise ValueError("删除清单版本或结构非法")
    for row in manifest["deletions"]:
        for key in ("id", "tenant_id", "user_id", "requested_by"):
            if row.get(key) is not None:
                uuid.UUID(row[key])
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", row.get("thread_id", "")):
            raise ValueError("线程标识非法")
        at = dt.datetime.fromisoformat(row["requested_at"])
        if at.tzinfo is None:
            raise ValueError("删除时间必须含时区")
        event = row.get("event")
        if event:
            for key in ("event_id", "user_id", "membership_id"):
                uuid.UUID(event[key])
            payload = event.get("payload") or {}
            if (
                set(payload) - {"resource_type", "resource_id", "deletion_state", "deleted_at"}
                or payload.get("resource_type") != "thread"
                or payload.get("resource_id") != row["thread_id"]
            ):
                raise ValueError("删除事实载荷非法")
    return manifest["deletions"]


def apply_manifest(conn, manifest):
    rows = validate_manifest(manifest)
    jobs = unresolved = 0
    for row in rows:
        conn.execute(
            "INSERT INTO agent_thread_deletions (id,tenant_id,thread_id,user_id"
            ",requested_by,state,attempts,requested_at) VALUES (%s,%s,%s,%s,%s,"
            "'requested',0,%s) ON CONFLICT(tenant_id,thread_id) DO NOTHING",
            tuple(
                row[k]
                for k in ("id", "tenant_id", "thread_id", "user_id", "requested_by", "requested_at")
            ),
        )
        for table in (
            "agent_feedback_current",
            "agent_feedback_operations",
            "agent_feedback_comments",
        ):
            if conn.execute("SELECT to_regclass(%s) AS name", (table,)).fetchone()["name"]:
                conn.execute(
                    f"DELETE FROM {table} WHERE tenant_id=%s AND thread_id=%s",
                    (row["tenant_id"], row["thread_id"]),
                )
        conn.execute(
            "UPDATE agent_content_refs SET deleted_at=COALESCE(deleted_at,now()"
            ") WHERE tenant_id=%s AND thread_id=%s",
            (row["tenant_id"], row["thread_id"]),
        )
        event = row.get("event")
        if not event or not row.get("user_id"):
            unresolved += 1
            continue
        payload = json.dumps(
            event["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        conn.execute(
            "INSERT INTO agent_events(event_id,tenant_id,user_id,membership_id,"
            "schema_version,event_type,source,occurred_at,received_at,thread_id"
            ",payload,payload_digest,created_at) VALUES (%s,%s,%s,%s,'1.0','res"
            "ource.deleted','server',%s,now(),%s,%s::jsonb,%s,now()) ON CONFLIC"
            "T(event_id) DO NOTHING",
            (
                event["event_id"],
                row["tenant_id"],
                event["user_id"],
                event["membership_id"],
                event["occurred_at"],
                row["thread_id"],
                payload,
                hashlib.sha256(payload.encode()).hexdigest(),
            ),
        )
        for consumer in ("deletion_cleanup", "external_trace_cleanup"):
            conn.execute(
                "INSERT INTO agent_outbox(tenant_id,event_id,consumer,status,av"
                "ailable_at,thread_id,created_at,updated_at) VALUES (%s,%s,%s,'"
                "pending',now(),%s,now(),now()) ON CONFLICT(tenant_id,event_id,"
                "consumer) DO UPDATE SET status='pending',lease_owner=NULL,leas"
                "e_deadline=NULL,available_at=now(),updated_at=now()",
                (row["tenant_id"], event["event_id"], consumer, row["thread_id"]),
            )
            conn.execute(
                "DELETE FROM agent_consumer_receipts WHERE tenant_id=%s AND eve"
                "nt_id=%s AND consumer=%s",
                (row["tenant_id"], event["event_id"], consumer),
            )
            jobs += 1
    return {
        "deletions_reapplied": len(rows),
        "cleanup_jobs": jobs,
        "unresolved_owner_or_event": unresolved,
        "external_cleanup_confirmed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--export", type=Path)
    group.add_argument("--manifest", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with psycopg.connect(
        get_settings().database_uri.replace("postgresql+psycopg://", "postgresql://"),
        row_factory=dict_row,
    ) as conn:
        if args.export:
            args.export.write_text(json.dumps(export_manifest(conn), indent=2) + "\n")
            args.export.chmod(0o600)
            print("删除清单已导出；请通过独立可信备份通道保存")
        else:
            manifest = json.loads(args.manifest.read_text())
            rows = validate_manifest(manifest)
            print(
                json.dumps(
                    apply_manifest(conn, manifest)
                    if args.apply
                    else {"dry_run": True, "deletions": len(rows)}
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
