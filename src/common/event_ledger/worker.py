"""事务 outbox 投递工作进程：租约、退避、死信、消费幂等、重放、投影重建.

- 领取走**受限调度角色**（`LEDGER_WORKER_DATABASE_URI`，非 BYPASSRLS、无正文列
  权限）+ GUC `air_agent.ledger_dispatch='on'`；领取只取任务 ID/tenant/租约字段。
- 处理再进入**对应租户事务**（`RUNTIME_DATABASE_URI` + 事务本地 GUC），
  消费 receipt 与投影更新同事务（至少一次交付 + 消费幂等）。
- `SKIP LOCKED` 支持多 worker 并发；租约过期可被安全重领（消费幂等兜底）。
- 毒事件超预算进死信；不阻塞整个租户。
- 关停：停止领取并有界等待在途工作。
- 重放只恢复业务投影，**不**调用模型/MCP，遵守删除标记。

未配置 worker 连接时明确"未启用"，不静默降级到运行角色跨租户领取。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from loguru import logger
from psycopg.rows import dict_row

RUNTIME_URI_ENV = "RUNTIME_DATABASE_URI"
WORKER_URI_ENV = "LEDGER_WORKER_DATABASE_URI"

DEFAULT_BATCH = 100
DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_LEASE_SECONDS = 60
DEFAULT_BACKOFF_BASE = 2
DEFAULT_BACKOFF_CAP = 300


class WorkerDisabled(Exception):
    """未配置受限调度连接：worker 未启用（不降级为运行角色跨租户领取."""


def worker_enabled() -> bool:
    """调度角色连接是否已配置（未配置即 worker 未启用）."""
    return bool(os.environ.get(WORKER_URI_ENV, ""))


def _runtime_uri() -> str:
    uri = os.environ.get(RUNTIME_URI_ENV, "")
    if not uri:
        raise RuntimeError(f"{RUNTIME_URI_ENV} not configured")
    return uri


def _worker_uri() -> str:
    uri = os.environ.get(WORKER_URI_ENV, "")
    if not uri:
        raise WorkerDisabled(f"{WORKER_URI_ENV} not configured")
    return uri


@dataclass(frozen=True)
class OutboxTask:
    """一个待投递任务的最小字段（不含载荷与正文）."""

    outbox_id: int
    tenant_id: str
    event_id: str
    consumer: str
    attempt_count: int
    event_type: str
    source: str
    # 租约"代次"覆盖处理与确认——过期旧持有者不得先提交副作用再假确认成功
    generation: int = 0
    consumer_version: int = 1
    thread_id: str | None = None


class StaleLease(Exception):
    """租约已被他人接管或过期：**立即放弃**，不得提交任何副作用."""


class LedgerCleanupUnavailable(Exception):
    """删除清理无法完成（0 行或权限不足）：**不得假装 done**."""


def _backoff_seconds(attempt_count: int, *, base: int = DEFAULT_BACKOFF_BASE) -> float:
    """有限指数退避 + 抖动（有界，避免同批同时重试."""
    import random

    raw = base ** max(0, attempt_count - 1)
    capped = min(float(raw), float(DEFAULT_BACKOFF_CAP))
    return capped * (0.5 + random.random())


class OutboxWorker:
    """单进程 worker；`run_once` 便于测试与单批运维."""

    def __init__(
        self,
        *,
        owner: str | None = None,
        tenant_id: str | None = None,
        batch_size: int = DEFAULT_BATCH,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        consumers: tuple[str, ...] = (
            "run_projection",
            "deletion_cleanup",
            "observability_projection",
            "external_trace_cleanup",
        ),
    ) -> None:
        """构造 worker；`owner` 为租约持有者标识."""
        if not worker_enabled():
            raise WorkerDisabled(
                "未配置 LEDGER_WORKER_DATABASE_URI：投递 worker 未启用"
            )
        self.owner = (
            owner or f"{os.uname().nodename}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self.tenant_id = str(uuid.UUID(tenant_id)) if tenant_id else None
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.consumers = consumers
        self._stopping = False

    # ------------------------------------------------------------- 领取（跨租户）

    async def _claim(self) -> list[OutboxTask]:
        """跨租户领取：只取任务 ID/租户/租约字段（看不到 payload/正文."""
        async with await psycopg.AsyncConnection.connect(
            _worker_uri(), row_factory=dict_row
        ) as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('air_agent.ledger_dispatch', 'on', true)"
                )
                rows = await (
                    await conn.execute(
                        """
                        SELECT o.id, o.tenant_id::text AS tenant_id, o.event_id::text AS event_id,
                               o.consumer, o.attempt_count, o.consumer_version,
                               COALESCE(o.thread_id,e.thread_id) AS thread_id,
                               e.event_type, e.source
                        FROM agent_outbox o
                        JOIN agent_events e
                          ON e.event_id = o.event_id AND e.tenant_id = o.tenant_id
                        WHERE o.status IN ('pending', 'leased')
                          AND (%(tenant)s::uuid IS NULL OR o.tenant_id=%(tenant)s::uuid)
                          AND o.available_at <= now()
                          AND o.consumer = ANY(%(consumers)s)
                          AND (o.lease_deadline IS NULL OR o.lease_deadline < now())
                        ORDER BY o.available_at ASC
                        FOR UPDATE OF o SKIP LOCKED
                        LIMIT %(limit)s
                        """,
                        {
                            "consumers": list(self.consumers),
                            "limit": self.batch_size,
                            "tenant": self.tenant_id,
                        },
                    )
                ).fetchall()
                if not rows:
                    return []
                ids = [r["id"] for r in rows]
                await conn.execute(
                    "UPDATE agent_outbox SET status = 'leased', lease_owner = %(owner)s,"
                    " lease_deadline = now() + make_interval(secs => %(lease)s),"
                    " updated_at = now() WHERE id = ANY(%(ids)s)",
                    {"owner": self.owner, "lease": self.lease_seconds, "ids": ids},
                )
                # 代次台账：记录本次领取（generation = 该 outbox 行的下一个序号）
                for row in rows:
                    await conn.execute(
                        "INSERT INTO agent_lease_generations"
                        " (tenant_id, outbox_id, generation, owner, acquired_at)"
                        " VALUES (%(t)s, %(o)s, COALESCE((SELECT max(generation)"
                        "   FROM agent_lease_generations WHERE outbox_id = %(o)s), 0) + 1,"
                        "  %(owner)s, now())",
                        {
                            "t": str(row["tenant_id"]),
                            "o": int(row["id"]),
                            "owner": self.owner,
                        },
                    )
        generations: dict[int, int] = {}
        async with await psycopg.AsyncConnection.connect(
            _worker_uri(), row_factory=dict_row
        ) as conn:
            await conn.execute(
                "SELECT set_config('air_agent.ledger_dispatch', 'on', true)"
            )
            for row in rows:
                gen = await (
                    await conn.execute(
                        "SELECT COALESCE(max(generation), 0) AS g FROM agent_lease_generations"
                        " WHERE outbox_id = %(o)s",
                        {"o": int(row["id"])},
                    )
                ).fetchone()
                generations[int(row["id"])] = int(gen["g"] or 0)

        return [
            OutboxTask(
                outbox_id=int(r["id"]),
                tenant_id=str(r["tenant_id"]),
                event_id=str(r["event_id"]),
                consumer=str(r["consumer"]),
                attempt_count=int(r["attempt_count"]),
                event_type=str(r["event_type"]),
                source=str(r["source"]),
                generation=generations.get(int(r["id"]), 0),
                consumer_version=int(r.get("consumer_version") or 1),
                thread_id=str(r["thread_id"]) if r.get("thread_id") else None,
            )
            for r in rows
        ]

    # --------------------------------------------------------- 处理（租户事务）

    async def _handle(self, task: OutboxTask) -> None:
        """在对应租户事务内消费：**代次校验 → receipt 占位 → 副作用**，同一事务.

        顺序是幂等的关键：
        1. `FOR UPDATE` 锁住 outbox 行并校验租约仍归本 worker 且未过期（代次）；
           过期/被接管 → 抛 `StaleLease`，**不执行任何副作用**；
        2. 先插入消费 receipt（唯一键冲突即"已处理过"）→ 已处理则直接返回，
           **不重复执行副作用**；
        3. 副作用（投影/删除清理）与 receipt 同事务提交；任一步失败整笔回滚。
        """
        stale = False
        async with await psycopg.AsyncConnection.connect(
            _runtime_uri(), row_factory=dict_row
        ) as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('air_agent.tenant_id', %(t)s, true)",
                    {"t": task.tenant_id},
                )
                if task.consumer in {"deletion_cleanup", "external_trace_cleanup"}:
                    await self._deletion_scope_guc(conn, task)
                if task.consumer == "observability_projection":
                    await conn.execute(
                        "SELECT set_config('air_agent.agent_operations','on',true)"
                    )
                # 1) 代次/租约校验（同一事务内先做，保证不会先副作用后假确认）
                lease = await (
                    await conn.execute(
                        "SELECT status, lease_owner, lease_deadline,"
                        " (lease_deadline IS NOT NULL AND lease_deadline < now()) AS expired"
                        " FROM agent_outbox WHERE id = %(o)s AND tenant_id = %(t)s"
                        " FOR UPDATE",
                        {"o": task.outbox_id, "t": task.tenant_id},
                    )
                ).fetchone()
                if (
                    lease is None
                    or str(lease["lease_owner"] or "") != self.owner
                    or bool(lease["expired"])
                    or str(lease["status"]) != "leased"
                ):
                    raise StaleLease("lease lost or expired")
                # 2) receipt 幂等裁决**在副作用之前**
                applied_cur = await conn.execute(
                    "INSERT INTO agent_consumer_receipts"
                    " (tenant_id, consumer, consumer_version, event_id, applied_at)"
                    " VALUES (%(t)s, %(c)s, %(v)s, %(e)s, now())"
                    " ON CONFLICT (tenant_id, consumer, consumer_version, event_id)"
                    "   DO NOTHING RETURNING id",
                    {
                        "t": task.tenant_id,
                        "c": task.consumer,
                        "v": task.consumer_version,
                        "e": task.event_id,
                    },
                )
                applied = await applied_cur.fetchone()
                if applied is None:
                    return  # 该事件（该消费版本）已应用过：副作用不重复执行
                # 3) 副作用
                if task.consumer == "deletion_cleanup":
                    await self._apply_deletion(conn, task)
                elif task.consumer == "external_trace_cleanup":
                    from common.observability.external_cleanup import (
                        cleanup_thread_traces,
                    )

                    await cleanup_thread_traces(conn, task)
                elif task.consumer == "observability_projection":
                    await self._apply_observability_projection(conn, task)
                else:
                    await self._apply_run_projection(conn, task)
                await self._bump_projection(conn, task)
        _ = stale

    async def _deletion_scope_guc(self, conn: Any, task: OutboxTask) -> None:  # noqa: ANN401
        """删除清理的**受限作用域**：按删除标记的归属人设置 user GUC.

        最小权限 worker 不绕过 owner：只用 (tenant, user) 作用域访问该线程的
        事件与原文；拿不到归属人时**无法清理**（由调用方判定为失败，不假装完成）。
        """
        row = await (
            await conn.execute(
                "SELECT user_id::text AS user_id FROM agent_thread_deletions"
                " WHERE tenant_id = %(t)s AND thread_id = %(th)s LIMIT 1",
                {"t": task.tenant_id, "th": task.thread_id or ""},
            )
        ).fetchone()
        if row is None or not row["user_id"]:
            raise LedgerCleanupUnavailable("deletion owner unknown")
        await conn.execute(
            "SELECT set_config('air_agent.user_id', %(u)s, true)",
            {"u": str(row["user_id"])},
        )

    async def _apply_observability_projection(
        self, conn: Any, task: OutboxTask
    ) -> None:
        await conn.execute(
            "INSERT INTO agent_observability_projection (tenant_id,consumer,con"
            "sumer_version,metric_version,last_event_at,projected_at,updated_at"
            ") VALUES (%(t)s,%(c)s,%(v)s,'obs-v1',(SELECT occurred_at FROM agen"
            "t_events WHERE tenant_id=%(t)s AND event_id=%(e)s),now(),now()) ON"
            " CONFLICT(tenant_id,consumer) DO UPDATE SET last_event_at=GREATEST"
            "(agent_observability_projection.last_event_at,EXCLUDED.last_event_"
            "at),projected_at=now(),updated_at=now()",
            {
                "t": task.tenant_id,
                "c": task.consumer,
                "v": task.consumer_version,
                "e": task.event_id,
            },
        )

    async def _apply_run_projection(self, conn: Any, task: OutboxTask) -> None:  # noqa: ANN401
        """运行投影：只更新口径状态，不改写业务事实（事实由写入路径维护）."""
        await conn.execute(
            "SELECT set_config('air_agent.agent_operations', 'on', true)"
        )

    async def _apply_deletion(self, conn: Any, task: OutboxTask) -> None:  # noqa: ANN401
        """删除清理：原文失效 + 标记完成。**0 行不得当成功**（抛错进入重试/死信）."""
        if not task.thread_id:
            raise LedgerCleanupUnavailable("event has no thread scope")
        thread_id = task.thread_id
        await conn.execute("SELECT set_config('air_agent.deletion_cleanup','on',true)")
        for table in (
            "agent_feedback_case_notes",
            "agent_feedback_case_actions",
            "agent_feedback_cases",
            "agent_answer_contents",
            "agent_feedback_current",
            "agent_feedback_comments",
            "agent_feedback_operations",
        ):
            exists = await (
                await conn.execute("SELECT to_regclass(%s)", (table,))
            ).fetchone()
            if exists and exists["to_regclass"]:
                await conn.execute(
                    f"DELETE FROM {table} WHERE tenant_id=%s AND thread_id=%s",
                    (task.tenant_id, thread_id),
                )
        observations = await (
            await conn.execute(
                "SELECT to_regclass('agent_observation_trace_links') AS table_name"
            )
        ).fetchone()
        if observations and observations["table_name"]:
            await conn.execute(
                "INSERT INTO agent_outbox (tenant_id,event_id,consumer,status,a"
                "vailable_at,created_at,updated_at,thread_id) "
                "VALUES (%s,%s,'external_trace_cleanup','pending',now(),now(),now(),%s) "
                "ON CONFLICT (tenant_id,event_id,consumer) DO NOTHING",
                (task.tenant_id, task.event_id, thread_id),
            )
        deleted = await (
            await conn.execute(
                "UPDATE agent_content_refs SET deleted_at = now()"
                " WHERE tenant_id = %(t)s AND thread_id = %(th)s AND deleted_at IS NULL"
                " RETURNING id",
                {"t": task.tenant_id, "th": thread_id},
            )
        ).fetchall()
        marker = await (
            await conn.execute(
                "UPDATE agent_thread_deletions SET state = 'completed', attempts = attempts + 1,"
                " completed_at = now() WHERE tenant_id = %(t)s AND thread_id = %(th)s"
                " AND state <> 'completed' RETURNING id",
                {"t": task.tenant_id, "th": thread_id},
            )
        ).fetchone()
        if marker is None and not deleted:
            # 标记已完成后重投：幂等短路（视为已处理），不算失败
            done = await (
                await conn.execute(
                    "SELECT 1 FROM agent_thread_deletions WHERE tenant_id = %(t)s"
                    " AND thread_id = %(th)s AND state = 'completed'",
                    {"t": task.tenant_id, "th": thread_id},
                )
            ).fetchone()
            if done is None:
                raise LedgerCleanupUnavailable("deletion marker not updated (0 rows)")

    async def _bump_projection(self, conn: Any, task: OutboxTask) -> None:  # noqa: ANN401
        await conn.execute(
            "INSERT INTO agent_projection_state"
            " (tenant_id, consumer, consumer_version, metric_version, last_event_at,"
            "  projected_at, updated_at)"
            " VALUES (%(t)s, %(c)s, %(v)s, 'v1',"
            "  (SELECT occurred_at FROM agent_events WHERE event_id = %(e)s AND tenant_id = %(t)s),"
            "  now(), now())"
            " ON CONFLICT (tenant_id, consumer) DO UPDATE SET"
            "  consumer_version = EXCLUDED.consumer_version,"
            "  last_event_at = GREATEST(agent_projection_state.last_event_at,"
            "    EXCLUDED.last_event_at), projected_at = now(), updated_at = now()",
            {
                "t": task.tenant_id,
                "c": task.consumer,
                "v": task.consumer_version,
                "e": task.event_id,
            },
        )

    async def _finish(
        self, task: OutboxTask, *, error_class: str | None = None
    ) -> dict[str, Any]:
        """确认/退还租约；**租约或代次不符时拒绝确认**（返回 confirmed=False）."""
        async with await psycopg.AsyncConnection.connect(
            _worker_uri(), row_factory=dict_row
        ) as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('air_agent.ledger_dispatch', 'on', true)"
                )
                attempts = task.attempt_count + 1
                if error_class is None:
                    # 确认前校验租约仍归本 worker 且代次未前进；否则拒绝"假确认"
                    current = await (
                        await conn.execute(
                            "SELECT lease_owner,"
                            " COALESCE((SELECT max(generation) FROM agent_lease_generations"
                            "   WHERE outbox_id = %(id)s), 0) AS gen"
                            " FROM agent_outbox WHERE id = %(id)s",
                            {"id": task.outbox_id},
                        )
                    ).fetchone()
                    if (
                        current is None
                        or str(current["lease_owner"] or "") != self.owner
                        or int(current["gen"] or 0) != task.generation
                    ):
                        # 陈旧持有者：不确认（不得先副作用后假确认成功）
                        return {
                            "confirmed": False,
                            "reason": "lease_generation_moved",
                        }
                    await conn.execute(
                        "UPDATE agent_lease_generations SET released_at = now()"
                        " WHERE outbox_id = %(id)s AND generation = %(gen)s"
                        " AND released_at IS NULL",
                        {"id": task.outbox_id, "gen": task.generation},
                    )
                    await conn.execute(
                        "UPDATE agent_outbox SET status = 'done', attempt_count = %(n)s,"
                        " lease_owner = NULL, lease_deadline = NULL, error_class = NULL,"
                        " updated_at = now() WHERE id = ANY(%(ids)s) AND lease_owner = %(owner)s",
                        {"n": attempts, "ids": [task.outbox_id], "owner": self.owner},
                    )
                    return {"confirmed": True}
                if attempts >= self.max_attempts:
                    await conn.execute(
                        "UPDATE agent_outbox SET status = 'dead_letter', attempt_count = %(n)s,"
                        " error_class = %(err)s, lease_owner = NULL, lease_deadline = NULL,"
                        " updated_at = now() WHERE id = ANY(%(ids)s) AND lease_owner = %(owner)s",
                        {
                            "n": attempts,
                            "err": error_class,
                            "ids": [task.outbox_id],
                            "owner": self.owner,
                        },
                    )
                    return {"confirmed": True, "dead_letter": True}
                # 退还并在退避后重试
                await conn.execute(
                    "UPDATE agent_outbox SET status = 'pending', attempt_count = %(n)s,"
                    " error_class = %(err)s, lease_owner = NULL, lease_deadline = NULL,"
                    " available_at = now() + make_interval(secs => %(delay)s), updated_at = now()"
                    " WHERE id = ANY(%(ids)s) AND lease_owner = %(owner)s",
                    {
                        "n": attempts,
                        "err": error_class,
                        "delay": _backoff_seconds(attempts),
                        "ids": [task.outbox_id],
                        "owner": self.owner,
                    },
                )
                return {"confirmed": True, "retry_scheduled": True}

    # ------------------------------------------------------------------ 主循环

    async def run_once(self) -> dict[str, int]:
        """处理一批；返回 {claimed, done, retried, dead_lettered}."""
        tasks = await self._claim()
        stats = {"claimed": len(tasks), "done": 0, "retried": 0, "dead_lettered": 0}
        for task in tasks:
            if self._stopping:
                # 在途停止：归还租约，等下次（不丢事实）
                await self._finish(task, error_class="worker_stopping")
                stats["retried"] += 1
                continue
            try:
                await self._handle(task)
            except Exception as exc:  # noqa: BLE001 - 错误类别只记类型名（脱敏）
                error_class = type(exc).__name__
                logger.bind(event_ledger=True).warning(
                    "outbox task failed: consumer={} class={}",
                    task.consumer,
                    error_class,
                )
                await self._finish(task, error_class=error_class)
                if task.attempt_count + 1 >= self.max_attempts:
                    stats["dead_lettered"] += 1
                else:
                    stats["retried"] += 1
            else:
                await self._finish(task)
                stats["done"] += 1
        return stats

    async def serve(self, *, poll_seconds: float = 1.0) -> None:
        """有界 lifespan：停止时不再领取并有界等待在途."""
        logger.bind(event_ledger=True).info("outbox worker started: {}", self.owner)
        try:
            while not self._stopping:
                stats = await self.run_once()
                if stats["claimed"] == 0:
                    await asyncio.sleep(poll_seconds)
        finally:
            logger.bind(event_ledger=True).info("outbox worker stopped: {}", self.owner)

    def request_stop(self) -> None:
        """请求停止：停止领取新任务，在途批次归还租约."""
        self._stopping = True


# ------------------------------------------------------------------ 重放/重建


async def replay(
    *,
    tenant_id: str,
    consumer: str = "run_projection",
    consumer_version: int = 1,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    dry_run: bool = True,
    actor: str = "ops",
    limit: int = 1000,
) -> dict[str, Any]:
    """从**保留事实**重建投影：包含已 done 的事件，版本真实生效.

    语义（与"只重试失败任务"不同）：
    - 数据源是 `agent_events`（保留事实），窗口按 `occurred_at`，租户显式限定；
    - 只挑"在目标 `consumer_version` 下尚无消费 receipt"的事件——因此对同一窗口
      请求 v2 会把已 done 的事实也纳入重建，并把 outbox 行的 `consumer_version`
      提升到 v2 后重新投递；
    - 遵守删除规则：**存在删除标记**（含清理已完成）的线程不参与重建；
    - 不调用模型/MCP、不新建运行；
    - dry-run 只统计并写入运维审计；apply 才真正重新排队。
    """
    if not worker_enabled():
        raise WorkerDisabled("未配置 LEDGER_WORKER_DATABASE_URI：重放未启用")
    since = since or (dt.datetime.now(dt.UTC) - dt.timedelta(days=7))
    until = until or dt.datetime.now(dt.UTC)
    async with await psycopg.AsyncConnection.connect(
        _worker_uri(), row_factory=dict_row
    ) as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('air_agent.ledger_dispatch', 'on', true)"
            )
            rows = await (
                await conn.execute(
                    """
                    SELECT o.id,
                           o.event_id::text AS event_id,
                           o.consumer,
                           o.status AS outbox_status,
                           o.consumer_version AS outbox_version
                    FROM agent_events e
                    JOIN agent_outbox o
                      ON o.event_id = e.event_id AND o.tenant_id = e.tenant_id
                    WHERE e.tenant_id = %(t)s
                      AND o.consumer = %(c)s
                      AND e.occurred_at >= %(since)s AND e.occurred_at < %(until)s
                      -- 删除规则优先：**已被删除的线程一律不重建**（含清理已完成），
                      -- 否则版本重建会把已删会话的派生内容复活。
                      AND NOT EXISTS (
                        SELECT 1 FROM agent_thread_deletions d
                        WHERE d.tenant_id = e.tenant_id AND d.thread_id = e.thread_id)
                      -- 目标消费版本已有 receipt 的（已应用）不再重建
                      AND NOT EXISTS (
                        SELECT 1 FROM agent_consumer_receipts r
                        WHERE r.tenant_id = e.tenant_id AND r.consumer = %(c)s
                          AND r.consumer_version = %(v)s AND r.event_id = e.event_id)
                    ORDER BY e.occurred_at ASC, o.id ASC
                    LIMIT %(limit)s
                    """,
                    {
                        "t": tenant_id,
                        "c": consumer,
                        "v": consumer_version,
                        "since": since,
                        "until": until,
                        "limit": limit,
                    },
                )
            ).fetchall()
            if not dry_run and rows:
                ids = [int(r["id"]) for r in rows]
                await conn.execute(
                    "UPDATE agent_outbox SET status = 'pending', available_at = now(),"
                    " lease_owner = NULL, lease_deadline = NULL, attempt_count = 0,"
                    " error_class = NULL, consumer_version = %(v)s, updated_at = now()"
                    " WHERE id = ANY(%(ids)s)",
                    {"v": consumer_version, "ids": ids},
                )
            # 运维审计（dry-run 也留痕）
            await conn.execute(
                "INSERT INTO agent_ledger_ops_audit"
                " (tenant_id, operation, actor, dry_run, affected, scope, detail)"
                " VALUES (%(t)s, 'replay', %(a)s, %(d)s, %(n)s, %(scope)s::json"
                "b, %(detail)s::jsonb)",
                {
                    "t": tenant_id,
                    "a": actor,
                    "d": dry_run,
                    "n": len(rows),
                    "scope": json.dumps(
                        {
                            "consumer": consumer,
                            "consumer_version": consumer_version,
                            "since": since.isoformat(),
                            "until": until.isoformat(),
                        }
                    ),
                    "detail": json.dumps(
                        {
                            "completed_facts_included": sum(
                                1 for r in rows if str(r["outbox_status"]) == "done"
                            ),
                            "cursor": int(rows[-1]["id"]) if rows else None,
                        }
                    ),
                },
            )
    return {
        "dry_run": dry_run,
        "would_replay": len(rows),
        "replayed": 0 if dry_run else len(rows),
        "consumer": consumer,
        "consumer_version": consumer_version,
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "cursor": int(rows[-1]["id"]) if rows else None,
        "audited": True,
    }
