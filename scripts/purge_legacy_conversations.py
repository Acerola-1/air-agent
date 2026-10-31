#!/usr/bin/env python3
"""按线程清理旧会话正文与 LangGraph 存储；默认 dry-run，可重入.

只删除会话/正文，不删除审计事实、IAM/账号、assistant、墓碑与证据目录。
必须在发布后、写入端停止且已完成备份的前提下以 owner/维护角色执行。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

LANGGRAPH_TABLES = (
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoints",
    "checkpoint_delete_queue",
    "run",
    "cron",
    "thread_ttl",
    "thread",
)

# 台账正文阶段；每条幂等，可单独重跑。
LEDGER_PHASES = (
    (
        "answer_contents",
        "DELETE FROM agent_answer_contents",
    ),
    (
        "content_refs",
        "UPDATE agent_content_refs SET content_snapshot = '', "
        "deleted_at = COALESCE(deleted_at, now())",
    ),
    (
        "feedback_comments",
        "UPDATE agent_feedback_comments SET comment_text = '', "
        "deleted_at = COALESCE(deleted_at, now())",
    ),
    (
        "feedback_notes",
        "UPDATE agent_feedback_case_notes SET note_text = '', "
        "deleted_at = COALESCE(deleted_at, now())",
    ),
    (
        "feedback_actions",
        "UPDATE agent_feedback_case_actions SET result_snapshot = NULL",
    ),
    ("knowledge_citations", "DELETE FROM knowledge_citations"),
    ("artifact_computations", "DELETE FROM artifact_computations"),
    ("tool_artifacts", "DELETE FROM tool_artifact_storage"),
    (
        "question_clusters",
        "UPDATE agent_question_clusters SET representative_intent_id = NULL, "
        "canonical_name = '', action = '', object = '', constraints = '{}'::jsonb",
    ),
    ("question_sources", "DELETE FROM agent_question_sources"),
    ("question_intents", "DELETE FROM agent_question_intents"),
    ("question_intent_contents", "DELETE FROM agent_question_intent_contents"),
    (
        "question_source_gaps",
        "UPDATE agent_question_source_gaps SET detail = NULL",
    ),
)

COUNT_TABLES = (
    "thread",
    "run",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_delete_queue",
    "thread_ttl",
    "cron",
    "thread_ownership",
    "store",
    "agent_answer_contents",
    "agent_answer_content_metadata",
    "agent_content_refs",
    "agent_feedback_comments",
    "agent_feedback_case_notes",
    "agent_feedback_case_actions",
    "knowledge_citations",
    "artifact_computations",
    "tool_artifact_storage",
    "agent_question_intent_contents",
    "agent_question_intents",
    "agent_question_sources",
    "agent_question_source_gaps",
    "agent_question_clusters",
    "agent_thread_deletions",
)


def _connect(dsn: str):
    import psycopg

    return psycopg.connect(dsn, autocommit=False)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s)", (f"public.{table}",)).fetchone()
    return bool(row and row[0])


def _scalar(conn, sql: str, params=None):
    row = conn.execute(sql, params or ()).fetchone()
    return row[0] if row else None


def _active_connections(conn) -> int:
    return int(
        _scalar(
            conn,
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()",
        )
        or 0
    )


def _run_status(conn) -> dict[str, int]:
    if not _table_exists(conn, "run"):
        return {}
    rows = conn.execute("SELECT status, count(*) FROM run GROUP BY status").fetchall()
    return {str(status): int(count) for status, count in rows}


def _preflight(conn, *, apply: bool, allow_active: bool) -> dict[str, object]:
    user = _scalar(conn, "SELECT current_user")
    if str(user) == "air_agent_runtime":
        raise SystemExit(
            "拒绝以运行角色 air_agent_runtime 执行清理；请使用 owner/维护角色"
        )
    statuses = _run_status(conn)
    non_terminal = {
        key: value
        for key, value in statuses.items()
        if key in {"pending", "queued", "running"}
    }
    active = _active_connections(conn)
    report = {
        "current_user": user,
        "run_status": statuses,
        "non_terminal_runs": non_terminal,
        "other_connections": active,
    }
    if apply and non_terminal:
        raise SystemExit(f"存在未终止运行，先取消并等待终态：{non_terminal}")
    if apply and active and not allow_active:
        raise SystemExit(
            f"检测到 {active} 个其他数据库连接；停止写入端后重试，"
            "或以 --allow-active-connections 显式确认"
        )
    return report


def _thread_ids(conn) -> list[str]:
    """枚举所有关联线程；以 text 形态去重后排序，批次可重放."""
    queries = []
    for table in ("thread", "run", "checkpoints", "checkpoint_writes"):
        if _table_exists(conn, table):
            queries.append(
                f"SELECT thread_id::text FROM {table} WHERE thread_id IS NOT NULL"
            )
    for table in ("thread_ttl", "cron", "checkpoint_delete_queue"):
        if _table_exists(conn, table):
            queries.append(
                f"SELECT thread_id::text FROM {table} WHERE thread_id IS NOT NULL"
            )
    if _table_exists(conn, "thread_ownership"):
        queries.append(
            "SELECT thread_id FROM thread_ownership "
            "WHERE thread_id ~ '^[0-9a-fA-F-]{36}$'"
        )
    if not queries:
        return []
    rows = conn.execute(" UNION ".join(queries)).fetchall()
    return sorted({str(row[0]) for row in rows if row[0]})


def _batch_key(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]


def _counts(conn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in COUNT_TABLES:
        if _table_exists(conn, table):
            counts[table] = int(_scalar(conn, f"SELECT count(*) FROM {table}") or 0)
    return counts


TOMBSTONE_COLUMNS = (
    "id",
    "thread_id",
    "tenant_id",
    "user_id",
    "requested_by",
    "state",
    "attempts",
    "last_error_class",
    "requested_at",
    "completed_at",
)


def _export_tombstones(conn, path: Path) -> int:
    """导出墓碑清单；保留表行，不改变删除语义."""
    if not _table_exists(conn, "agent_thread_deletions"):
        return 0
    columns = ", ".join(TOMBSTONE_COLUMNS)
    rows = conn.execute(
        f"SELECT {columns} FROM agent_thread_deletions ORDER BY requested_at"
    ).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(TOMBSTONE_COLUMNS)
        for row in rows:
            writer.writerow(
                [
                    value.isoformat() if hasattr(value, "isoformat") else value
                    for value in row
                ]
            )
    return len(rows)


def _purge_thread_batch(conn, ids: list[str]) -> None:
    if not ids:
        return
    with conn.transaction():
        for table in LANGGRAPH_TABLES:
            if not _table_exists(conn, table):
                continue
            conn.execute(
                f"DELETE FROM {table} WHERE thread_id = ANY(%s::uuid[])",
                (ids,),
            )
        if _table_exists(conn, "thread_ownership"):
            conn.execute(
                "DELETE FROM thread_ownership WHERE thread_id = ANY(%s::text[])",
                (ids,),
            )


def _purge_store(conn) -> None:
    if _table_exists(conn, "store"):
        with conn.transaction():
            conn.execute("DELETE FROM store")


PHASE_TABLES = {
    "answer_contents": "agent_answer_contents",
    "content_refs": "agent_content_refs",
    "feedback_comments": "agent_feedback_comments",
    "feedback_notes": "agent_feedback_case_notes",
    "feedback_actions": "agent_feedback_case_actions",
    "knowledge_citations": "knowledge_citations",
    "artifact_computations": "artifact_computations",
    "tool_artifacts": "tool_artifact_storage",
    "question_clusters": "agent_question_clusters",
    "question_sources": "agent_question_sources",
    "question_intents": "agent_question_intents",
    "question_intent_contents": "agent_question_intent_contents",
    "question_source_gaps": "agent_question_source_gaps",
}


def _purge_ledger_phase(conn, name: str, sql: str) -> None:
    if not _table_exists(conn, PHASE_TABLES[name]):
        return
    with conn.transaction():
        conn.execute(sql)


def _load_progress(path: Path) -> dict:
    if not path.is_file():
        return {
            "version": 1,
            "completed_batches": [],
            "completed_phases": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _save_progress(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def _delete_legacy_sqlite(path: Path, *, apply: bool, force: bool = False) -> str:
    """删除遗留 SQLite；存在 sidecar 时须显式 --delete-sqlite 确认无进程占用."""
    if not path.is_file():
        return "absent"
    sidecars = [path.with_suffix(path.suffix + suffix) for suffix in ("-wal", "-shm")]
    present = [item for item in sidecars if item.is_file()]
    if present and not force:
        return "skipped:" + ",".join(item.name for item in present)
    if not apply:
        return "present"
    for item in [path, *present]:
        item.unlink(missing_ok=True)
    return "deleted"


def _print_report(report: dict, counts: dict[str, int], threads: int, args) -> None:
    mode = "apply" if args.apply else "dry-run"
    sys.stdout.write(f"模式：{mode}；线程：{threads}；批次大小：{args.batch_size}\n")
    sys.stdout.write(
        "预检："
        + json.dumps(
            {
                "current_user": report["current_user"],
                "run_status": report["run_status"],
                "non_terminal_runs": report["non_terminal_runs"],
                "other_connections": report["other_connections"],
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    for table, count in counts.items():
        sys.stdout.write(f"  {table}: {count}\n")
    sqlite_state = _delete_legacy_sqlite(
        Path(args.sqlite), apply=False, force=args.delete_sqlite
    )
    sys.stdout.write(f"遗留 SQLite（{args.sqlite}）：{sqlite_state}\n")
    sys.stdout.write(
        "前端 localStorage：页面挂载时清理 air-agent-chat-mode 与 air-agent-artifacts:*；"
        "保留 air_agent_user_id。Langfuse 不在本次范围。\n"
    )


def main() -> int:
    """解析参数并按 dry-run 或 apply 执行."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.environ.get("PURGE_DATABASE_URL"))
    parser.add_argument(
        "--apply", action="store_true", help="实际执行删除；默认 dry-run"
    )
    parser.add_argument("--resume", action="store_true", help="从进度文件继续")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--export-tombstones", type=Path)
    parser.add_argument("--backup-file", type=Path)
    parser.add_argument(
        "--sqlite",
        default=str(Path(__file__).resolve().parents[1] / "db/checkpoints.db"),
    )
    parser.add_argument("--allow-active-connections", action="store_true")
    parser.add_argument(
        "--delete-sqlite",
        action="store_true",
        help="确认无进程占用后连同 -wal/-shm 删除遗留 SQLite",
    )
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("缺少 --dsn 或 PURGE_DATABASE_URL")
    if args.batch_size < 1:
        raise SystemExit("--batch-size 必须为正整数")
    progress_path = args.progress or (
        Path.home()
        / ".local/share/air-agent-backups"
        / f"purge-progress-{hashlib.sha256(args.dsn.encode()).hexdigest()[:12]}.json"
    )
    progress = (
        _load_progress(progress_path)
        if args.resume
        else {
            "version": 1,
            "started_at": datetime.now(UTC).isoformat(),
            "completed_batches": [],
            "completed_phases": [],
        }
    )

    if args.apply:
        if not args.backup_file or not args.backup_file.is_file():
            raise SystemExit("--apply 需要已完成的全库备份文件：--backup-file")
        if args.backup_file.stat().st_size <= 0:
            raise SystemExit("备份文件为空，拒绝执行")

    with _connect(args.dsn) as conn:
        report = _preflight(
            conn, apply=args.apply, allow_active=args.allow_active_connections
        )
        counts = _counts(conn)
        thread_ids = _thread_ids(conn)
        if args.export_tombstones:
            exported = _export_tombstones(conn, args.export_tombstones)
            sys.stdout.write(f"墓碑导出：{exported} 行 -> {args.export_tombstones}\n")
        _print_report(report, counts, len(thread_ids), args)
        batch_total = (len(thread_ids) + args.batch_size - 1) // args.batch_size
        sys.stdout.write(f"LangGraph 批次：{batch_total}\n")
        if not args.apply:
            return 0

        conn.commit()
        for index in range(batch_total):
            batch = thread_ids[index * args.batch_size : (index + 1) * args.batch_size]
            key = _batch_key(batch)
            if key in progress["completed_batches"]:
                continue
            _purge_thread_batch(conn, batch)
            progress["completed_batches"].append(key)
            _save_progress(progress_path, progress)
            sys.stdout.write(
                f"  批次 {index + 1}/{batch_total} 完成：{len(batch)} 线程\n"
            )

        if "store" not in progress["completed_phases"]:
            _purge_store(conn)
            conn.commit()
            progress["completed_phases"].append("store")
            _save_progress(progress_path, progress)

        for name, sql in LEDGER_PHASES:
            if name in progress["completed_phases"]:
                continue
            _purge_ledger_phase(conn, name, sql)
            conn.commit()
            progress["completed_phases"].append(name)
            _save_progress(progress_path, progress)
            sys.stdout.write(f"  正文阶段完成：{name}\n")

        sqlite_state = _delete_legacy_sqlite(
            Path(args.sqlite), apply=True, force=args.delete_sqlite
        )
        sys.stdout.write(f"遗留 SQLite（{args.sqlite}）：{sqlite_state}\n")
        sys.stdout.write(f"进度：{progress_path}\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
