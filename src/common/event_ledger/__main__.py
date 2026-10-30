"""台账运维 CLI：投递 worker、补偿校准、重放与历史归属报告。.

用法（宿主机或容器内，需相应连接配置）：

    PYTHONPATH=src python -m common.event_ledger worker
    PYTHONPATH=src python -m common.event_ledger replay --tenant <uuid> --dry-run
    PYTHONPATH=src python -m common.event_ledger replay --tenant <uuid> --apply
    PYTHONPATH=src python -m common.event_ledger reconcile --tenant <uuid>
    PYTHONPATH=src python -m common.event_ledger history-report

设计约束：
- 重放默认 dry-run，必须显式 `--apply`；范围（租户/窗口/消费版本）必填；
- 重放只恢复业务投影，不调用模型/MCP，遵守删除标记；
- 历史报告为**只读**，不补造反馈/Token/耗时，未知归属不计入企业报表；
- 所有命令输出非秘密标识与计数，不打印正文。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any


def emit(payload: Any) -> None:  # noqa: ANN401
    """输出非秘密结果（CLI 唯一输出出口）."""
    print(  # noqa: T201 - CLI 需要标准输出
        payload
        if isinstance(payload, str)
        else json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _scope(tenant_id: str, user_id: str = "") -> Any:  # noqa: ANN401
    """构造租户台账作用域（缺省 user 用 tenant 占位，仅用于只读查询）."""
    from common.event_ledger.store import LedgerScope

    return LedgerScope(
        tenant_id=tenant_id,
        user_id=user_id or tenant_id,
        membership_id=user_id or tenant_id,
    )


async def _cmd_worker(args: argparse.Namespace) -> int:
    from common.event_ledger.worker import OutboxWorker, WorkerDisabled

    try:
        worker = OutboxWorker(
            batch_size=args.batch,
            max_attempts=args.max_attempts,
            lease_seconds=args.lease_seconds,
        )
    except WorkerDisabled as exc:
        emit({"enabled": False, "reason": str(exc)})
        return 2
    if args.once:
        emit(await worker.run_once())
        return 0
    await worker.serve(poll_seconds=args.poll)
    return 0


async def _cmd_replay(args: argparse.Namespace) -> int:
    from common.event_ledger.worker import WorkerDisabled, replay

    try:
        result = await replay(
            tenant_id=args.tenant,
            consumer=args.consumer,
            dry_run=not args.apply,
            consumer_version=args.consumer_version,
        )
    except WorkerDisabled as exc:
        emit({"enabled": False, "reason": str(exc)})
        return 2
    emit(result)
    return 0


async def _cmd_reconcile(args: argparse.Namespace) -> int:
    """有界、可续跑的服务级只读校准.

    凭据是**服务级只读校准票据**（rs-v1，`LEDGER_RECONCILE_TICKET` 或 `--ticket-file`），
    不绑定任何用户会话：原成员被撤销后校准仍可用，但只能按票据租户（可再按线程清单）
    只读运行状态，不恢复业务权限、不读消息、不查询平台私有表、不重执行模型/MCP。
    单次运行受 `--max-batches` 限制并写校准运行记录；`--cursor` 续跑。
    """
    import datetime as dt

    from common.event_ledger.reconcile import HttpRunStatusProbe, Reconciler

    scope = _scope(args.tenant, args.user)
    ticket = args.ticket or os.environ.get("LEDGER_RECONCILE_TICKET", "")
    if not ticket and args.ticket_file:
        try:
            ticket = Path(args.ticket_file).read_text().strip()
        except OSError as exc:
            emit(
                {
                    "status": "blocked",
                    "reason": "read_only_calibration_ticket_unreadable",
                    "detail": type(exc).__name__,
                }
            )
            return 2
    if not ticket:
        emit(
            {
                "status": "blocked",
                "reason": "missing_read_only_calibration_credential",
                "required_env": "LEDGER_RECONCILE_TICKET",
                "note": (
                    "服务级只读校准凭据由 admin 内部端点 /agent/reconcile/ticket 按票据租户签发"
                    "（rs-v1，与用户运行票据分离的密钥/受众）。未配置时校准只报告阻塞，"
                    "不改变任何状态，也不把 no_platform_run_id 当作闭合。"
                ),
            }
        )
        return 2
    progress, _, attempt_id = (args.cursor or "").partition("|")
    cursor = dt.datetime.fromisoformat(progress) if progress else None
    if attempt_id:
        attempt_id = str(uuid.UUID(attempt_id))
    reconciler = Reconciler(
        stalled_seconds=args.stalled_seconds, batch_limit=args.batch, probe=None
    )
    probe = HttpRunStatusProbe(args.base_url, ticket, timeout_seconds=args.timeout)
    reconciler.probe = probe
    outcomes = await reconciler.reconcile_scope(
        scope,
        cursor=cursor,
        cursor_attempt_id=attempt_id or None,
        max_batches=args.max_batches,
        actor=args.actor,
        thread_id=args.thread or None,
    )
    emit(
        {
            "status": "ok",
            "batches": reconciler.last_batches,
            "max_batches": args.max_batches,
            "cursor": args.cursor,
            "next_cursor": reconciler.next_cursor,
            "thread": args.thread or None,
            "outcomes": [o.as_dict() for o in outcomes],
            "counts": {
                "calibrated": sum(1 for o in outcomes if o.action == "calibrated"),
                "kept_unknown": sum(1 for o in outcomes if o.action == "kept_unknown"),
                "released": sum(
                    1 for o in outcomes if o.action == "admission_released"
                ),
            },
        }
    )
    return 0


async def _cmd_history_report(args: argparse.Namespace) -> int:
    """历史归属只读报告：可关联/未知归属计数，不补造任何缺失事实."""
    import psycopg
    from psycopg.rows import dict_row

    uri = os.environ.get("LEDGER_REPORT_OWNER_URI") or os.environ.get(
        "DATABASE_URI", ""
    )
    if not uri:
        emit({"error": "需要 LEDGER_REPORT_OWNER_URI/DATABASE_URI（只读）"})
        return 2
    async with await psycopg.AsyncConnection.connect(uri, row_factory=dict_row) as conn:
        cursor = await conn.execute(
            """
                SELECT
                  (SELECT count(*) FROM thread_ownership WHERE source = 'native') AS owned_native,
                  (SELECT count(*) FROM thread_ownership WHERE source = 'native_pending') AS pending,
                  (SELECT count(*) FROM thread_ownership WHERE source = 'migrated') AS migrated,
                  (SELECT count(*) FROM thread_ownership WHERE source IS NULL) AS unknown_source,
                  (SELECT count(*) FROM agent_requests) AS ledger_requests,
                  (SELECT count(*) FROM agent_run_attempts) AS ledger_attempts,
                  (SELECT count(*) FROM agent_events) AS ledger_events
                """
        )
        rows = await cursor.fetchone()
    report = {
        "read_only": True,
        "ownership": {
            "native": rows["owned_native"],
            "native_pending": rows["pending"],
            "migrated": rows["migrated"],
            "unknown_source": rows["unknown_source"],
        },
        "ledger": {
            "requests": rows["ledger_requests"],
            "attempts": rows["ledger_attempts"],
            "events": rows["ledger_events"],
        },
        "notes": [
            "历史运行未关联台账时不补造归属、反馈或耗时；未知归属不进入企业报表。",
            "native_pending 行保持不可读（未确认创建事实）。",
        ],
    }
    emit(report)
    return 0


def _add_scope(target: argparse.ArgumentParser) -> None:
    """租户作用域参数（所有子命令共用；缺省 user 以 tenant 占位，仅只读查询）."""
    target.add_argument("--tenant", default="")
    target.add_argument("--user", default="")


def build_parser() -> argparse.ArgumentParser:
    """命令行入口（worker / replay / reconcile / history-report）."""
    parser = argparse.ArgumentParser(prog="event_ledger", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    worker = sub.add_parser("worker", help="投递 worker（单进程，有界批）")
    _add_scope(worker)
    worker.add_argument("--batch", type=int, default=50)
    worker.add_argument("--max-batches", type=int, default=0, help="0 表示常驻")
    worker.set_defaults(func=_cmd_worker)

    replay = sub.add_parser("replay", help="从保留事实重建消费投影（有界）")
    _add_scope(replay)
    replay.add_argument("--consumer", default="run_projection")
    replay.add_argument("--consumer-version", type=int, default=2)
    replay.add_argument("--since", default="")
    replay.add_argument("--until", default="")
    replay.add_argument("--apply", action="store_true")
    replay.add_argument("--limit", type=int, default=1000)
    replay.set_defaults(func=_cmd_replay)

    reconcile = sub.add_parser("reconcile", help="有界服务级只读校准（可续跑）")
    _add_scope(reconcile)
    reconcile.add_argument(
        "--base-url", default=os.environ.get("LANGGRAPH_API_URL", "")
    )
    reconcile.add_argument("--ticket", default="")
    reconcile.add_argument("--ticket-file", default="")
    reconcile.add_argument("--stalled-seconds", type=int, default=60)
    reconcile.add_argument("--batch", type=int, default=100)
    reconcile.add_argument("--max-batches", type=int, default=1)
    reconcile.add_argument(
        "--cursor", default="", help="上次返回的 cursor_after（续跑）"
    )
    reconcile.add_argument("--timeout", type=float, default=10.0)
    reconcile.add_argument("--actor", default="service:ledger-reconcile")
    reconcile.add_argument(
        "--thread",
        default="",
        help="只核对指定会话（与票据线程清单一致，避免批次被积压占满）",
    )
    reconcile.set_defaults(func=_cmd_reconcile)

    report = sub.add_parser("history-report", help="历史归属只读报告")
    report.add_argument("--tenant", default="")
    report.set_defaults(func=_cmd_history_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：返回进程退出码."""
    args = build_parser().parse_args(argv)
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
