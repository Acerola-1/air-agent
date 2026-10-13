"""终态/受理校准：对有界间隔无进展的尝试做只读平台核对.

原则（spec: 可恢复的终态补偿）：
- 只查询**确定范围**的平台运行状态，不读取全量消息、不恢复业务权限、不重执行工具；.
- 区分「服务不可用」「平台对象缺失」「权限撤销」「实际终态」；
- 仅因时间流逝或查不到对象**不得**自动判定成功/取消/超时 → `status_unknown`；
- 超时必须有实际 deadline/平台超时证据（平台 `timeout` 状态），长时长本身不算超时。
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Protocol

from loguru import logger

from common.event_ledger.lifecycle import business_state_from_native
from common.event_ledger.store import (
    LedgerBackendUnavailable,
    LedgerScope,
    attach_platform_run,
    attempts_needing_reconcile,
    observe_state,
    record_reconcile_run,
    record_resume,
)

# 平台 4xx 语义 → 校准结论（不猜测成功）
MISSING_OBJECT_HINTS = ("not found", "no matching", "404")


class PlatformProbe(Protocol):
    """最小授权平台状态接口（受授权只读；仅返回状态字符串或抛错."""

    async def run_status(self, *, thread_id: str, run_id: str) -> str | None:
        """平台状态字符串或 None（None 表示无状态，不猜测）."""
        ...

    async def locate_run(
        self, *, thread_id: str, attempt_id: str, submission_id: str | None = None
    ) -> str | None:
        """按可信 metadata 定位既有 run（无映射时收敛受理歧义）；找不到返回 None."""
        ...


@dataclass(frozen=True)
class ReconcileOutcome:
    """校准结果（尝试 ID、动作、状态、类别化说明）."""

    attempt_id: str
    action: str
    state: str | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        """返回可审计的类别化错误字符串."""
        return {
            "attempt_id": self.attempt_id,
            "action": self.action,
            "state": self.state,
            "detail": self.detail,
        }


class PlatformUnavailable(Exception):
    """平台接口不可用（网络/5xx）：保持待确认，有界重试."""


class PlatformObjectMissing(Exception):
    """平台对象缺失：不判取消/成功，保持 status_unknown 并记录."""


class PlatformPermissionRevoked(Exception):
    """校准身份已被撤销：使用受限服务权限；不恢复原成员业务权限."""


def classify_probe_error(exc: Exception) -> str:
    """把探测错误归类为可审计的类别字符串（不外泄细节."""
    if isinstance(exc, PlatformUnavailable):
        return "platform_unavailable"
    if isinstance(exc, PlatformObjectMissing):
        return "object_missing"
    if isinstance(exc, PlatformPermissionRevoked):
        return "permission_revoked"
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return "permission_revoked"
    if status == 404:
        return "object_missing"
    if status is not None and int(status) >= 500:
        return "platform_unavailable"
    return "unknown_error"


class Reconciler:
    """有界批量校准；不持有正文，不写业务答案，只修状态与待确认标记."""

    def __init__(
        self,
        *,
        stalled_seconds: int = 60,
        batch_limit: int = 100,
        probe: PlatformProbe | None = None,
        release_after_seconds: int = 300,
    ) -> None:
        """有界校准器：超时阈值、批量上限、释放宽限期与最小授权探测接口.

        `release_after_seconds` 仅兼容旧配置，已停止依据时间/空列表释放启动权。
        创建歧义必须靠可信运行映射收敛，未知时保留裁决，避免二次业务执行。
        """
        self.stalled_seconds = stalled_seconds
        self.batch_limit = batch_limit
        self.probe = probe
        self.release_after_seconds = release_after_seconds
        self.next_cursor: str | None = None
        self.cursor_attempt_id: str | None = None
        self.last_batches = 0

    async def reconcile_scope(
        self,
        scope: LedgerScope,
        *,
        thread_id_of: Any = None,
        cursor: dt.datetime | None = None,
        cursor_attempt_id: str | None = None,
        max_batches: int = 1,
        actor: str = "service:ledger-reconcile",
        thread_id: str | None = None,
    ) -> list[ReconcileOutcome]:
        """按租户有界校准，可**续跑**：每批处理 `batch_limit` 条，游标向后推进.

        F05：有界执行路径——`max_batches` 限制单次运行的批次数（默认 1），返回的批次
        游标可直接作为下次 `cursor`，避免一次运行无界扫描；每次运行都落一条校准运行
        记录（`agent_ledger_reconcile_runs`）供审计与续跑。
        """
        outcomes: list[ReconcileOutcome] = []
        batch_cursor = cursor
        batch_attempt_id = cursor_attempt_id
        self.next_cursor = None
        self.last_batches = 0
        total_candidates = 0
        batches = 0
        for _ in range(max(1, max_batches)):
            try:
                candidates = await attempts_needing_reconcile(
                    scope,
                    stalled_seconds=self.stalled_seconds,
                    limit=self.batch_limit,
                    after_progress_at=batch_cursor,
                    after_attempt_id=batch_attempt_id,
                    thread_id=thread_id,
                )
            except LedgerBackendUnavailable:
                outcomes.append(
                    ReconcileOutcome("", "skipped", None, "backend_unavailable")
                )
                await self._record_run(
                    scope,
                    actor=actor,
                    batches=batches,
                    candidates=total_candidates,
                    outcomes=outcomes,
                    cursor=cursor,
                    cursor_after=batch_cursor,
                    scope_value=thread_id,
                    status="failed",
                    detail="backend_unavailable",
                )
                return outcomes
            if not candidates:
                break
            batches += 1
            self.last_batches = batches
            total_candidates += len(candidates)
            batch_outcomes = await self._reconcile_batch(scope, candidates)
            outcomes.extend(batch_outcomes)
            last_progress = candidates[-1].get("last_progress_at")
            if last_progress is None:
                break
            batch_cursor = last_progress
            batch_attempt_id = str(candidates[-1]["attempt_id"])
            self.cursor_attempt_id = batch_attempt_id
            self.next_cursor = f"{last_progress.isoformat()}|{batch_attempt_id}"
            if len(candidates) < self.batch_limit:
                break
        await self._record_run(
            scope,
            actor=actor,
            batches=batches,
            candidates=total_candidates,
            outcomes=outcomes,
            cursor=cursor,
            cursor_after=batch_cursor,
            scope_value=thread_id,
            status="bounded_stop" if batches >= max(1, max_batches) else "completed",
        )
        return outcomes

    async def _record_run(
        self,
        scope: LedgerScope,
        *,
        actor: str,
        batches: int,
        candidates: int,
        outcomes: list[ReconcileOutcome],
        cursor: dt.datetime | None,
        cursor_after: dt.datetime | None,
        status: str,
        scope_value: str | None = None,
        detail: str | None = None,
    ) -> None:
        """写校准运行记录；失败只记日志（不把审计写入变成校准失败）."""
        try:
            await record_reconcile_run(
                scope,
                actor=actor,
                batches=batches,
                candidates=candidates,
                calibrated=sum(1 for o in outcomes if o.action == "calibrated"),
                kept_unknown=sum(1 for o in outcomes if o.action == "kept_unknown"),
                released=sum(1 for o in outcomes if o.action == "admission_released"),
                status=status,
                cursor=cursor,
                cursor_after=cursor_after,
                scope_value=scope_value,
                detail=detail
                or (
                    f"cursor_attempt_id={self.cursor_attempt_id}"
                    if self.cursor_attempt_id
                    else None
                ),
            )
        except LedgerBackendUnavailable:
            logger.bind(event_ledger=True).warning(
                "reconcile run record unavailable (actor={})", actor
            )

    async def _reconcile_batch(
        self, scope: LedgerScope, candidates: list[dict[str, Any]]
    ) -> list[ReconcileOutcome]:
        """处理一批候选（原有逐条逻辑，含删除/终态/定位/释放分支）."""
        outcomes: list[ReconcileOutcome] = []
        now = dt.datetime.now(dt.UTC)
        for row in candidates:
            attempt_id = str(row["attempt_id"])
            submission_id = row.get("pending_submission_id")
            run_id = None if submission_id else row.get("platform_run_id")
            thread_id = str(row.get("thread_id") or "")
            if not run_id and self.probe is not None:
                # 受理歧义：先按**可信 metadata**（服务端注入的 ledger_attempt_id）
                # 定位"已受理但响应丢失"的既有运行；找到则收敛映射，找不到才保持待确认。
                try:
                    located = await self.probe.locate_run(
                        thread_id=thread_id,
                        attempt_id=attempt_id,
                        submission_id=submission_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    outcomes.append(
                        ReconcileOutcome(
                            attempt_id,
                            "kept_unknown",
                            row.get("state"),
                            classify_probe_error(exc),
                        )
                    )
                    continue
                if located:
                    attached = await attach_platform_run(
                        scope,
                        attempt_id=attempt_id,
                        platform_run_id=located,
                        evidence="metadata_match",
                        submission_id=submission_id,
                    )
                    if (
                        attached.get("reason") == "mapping_conflict"
                        and row.get("pending_operation_kind") == "resume"
                    ):
                        resumed = await record_resume(
                            scope,
                            attempt_id=attempt_id,
                            platform_run_id=located,
                            submission_id=submission_id,
                        )
                        attached = {
                            "attached": resumed.get("recorded"),
                            "state": "running",
                        }
                    if (
                        attached.get("attached")
                        or attached.get("reason") == "already_attached"
                    ):
                        outcomes.append(
                            ReconcileOutcome(
                                attempt_id,
                                "admission_recovered",
                                str(attached.get("state") or "running"),
                                f"run_id={located}",
                            )
                        )
                        run_id = located
                    else:
                        outcomes.append(
                            ReconcileOutcome(
                                attempt_id,
                                "kept_unknown",
                                row.get("state"),
                                str(attached.get("reason") or "attach_failed"),
                            )
                        )
                        continue
                else:
                    # 空列表/超时都不能证明创建没有提交：平台过滤、分页、延迟
                    # 可见性及在途请求仍可能隐藏运行。保留裁决，绝不据此重执行业务。
                    outcomes.append(
                        ReconcileOutcome(
                            attempt_id,
                            "kept_unknown",
                            row.get("state"),
                            "no_platform_run_id",
                        )
                    )
                    continue
            if not run_id:
                # 受理歧义且无平台标识：保持待确认（不猜测）
                outcomes.append(
                    ReconcileOutcome(
                        attempt_id,
                        "kept_unknown",
                        row.get("state"),
                        "no_platform_run_id",
                    )
                )
                continue
            if self.probe is None:
                outcomes.append(
                    ReconcileOutcome(
                        attempt_id, "skipped", row.get("state"), "no_probe_configured"
                    )
                )
                continue
            thread_id = str(row.get("thread_id") or "")
            try:
                status = await self.probe.run_status(
                    thread_id=thread_id, run_id=str(run_id)
                )
            except Exception as exc:  # noqa: BLE001
                category = classify_probe_error(exc)
                outcomes.append(
                    ReconcileOutcome(
                        attempt_id, "kept_unknown", row.get("state"), category
                    )
                )
                continue
            if status is None:
                outcomes.append(
                    ReconcileOutcome(
                        attempt_id, "kept_unknown", row.get("state"), "no_status"
                    )
                )
                continue
            state = business_state_from_native(status)
            result = await observe_state(
                scope,
                attempt_id=attempt_id,
                state=state,
                evidence_source="platform_read",
                platform_status=status,
                cancel_confirmed=(state == "cancelled"),
            )
            logger.bind(event_ledger=True).info(
                "reconciled attempt={} status={} state={} applied={}",
                attempt_id,
                status,
                state,
                result.get("applied"),
            )
            outcomes.append(
                ReconcileOutcome(
                    attempt_id, "calibrated", state, f"platform_status={status}"
                )
            )
        _ = now
        return outcomes


class HttpRunStatusProbe:
    """原生平台状态读取（最小授权只读）：只取单个 run 的 status.

    rs-v1 只调用状态投影网关；普通用户票据沿用本人原生读取。
    票据种类仅用于选择地址，授权始终由服务端验签，不能靠客户端解码授权。

    - 不读取消息、不恢复业务权限、不重执行任何业务操作；
    - 401/403 → PlatformPermissionRevoked；404 → PlatformObjectMissing；5xx/网络 → PlatformUnavailable。
    """

    def __init__(
        self, base_url: str, ticket: str, *, timeout_seconds: float = 10.0
    ) -> None:
        """记录原生地址与只读票据（票据不落日志）."""
        self.base_url = base_url.rstrip("/")
        self._ticket = ticket
        self.timeout_seconds = timeout_seconds
        try:
            encoded = ticket.split(".")[0]
            payload = json.loads(
                base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            )
            self._service = payload.get("p", {}).get("typ") == "rs-v1"
        except (ValueError, TypeError, AttributeError):
            self._service = False

    def _runs_url(self, thread_id: str) -> str:
        prefix = "/internal/ledger" if self._service else ""
        return f"{self.base_url}{prefix}/threads/{thread_id}/runs"

    async def run_status(self, *, thread_id: str, run_id: str) -> str | None:
        """读取 run 的权威 status；缺失返回 None（不猜测）."""
        import httpx

        if not thread_id or not run_id:
            raise PlatformObjectMissing("缺少 thread_id/run_id")
        url = f"{self._runs_url(thread_id)}/{run_id}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                res = await client.get(
                    url, headers={"authorization": f"Bearer {self._ticket}"}
                )
        except Exception as exc:  # noqa: BLE001 - 网络层故障归为不可用
            raise PlatformUnavailable(type(exc).__name__) from exc
        if res.status_code in (401, 403):
            raise PlatformPermissionRevoked(f"http_{res.status_code}")
        if res.status_code == 404:
            raise PlatformObjectMissing("run_not_found")
        if res.status_code >= 500:
            raise PlatformUnavailable(f"http_{res.status_code}")
        if res.status_code != 200:
            raise PlatformUnavailable(f"http_{res.status_code}")
        payload = res.json()
        status = payload.get("status")
        return str(status) if status is not None else None

    async def locate_run(
        self, *, thread_id: str, attempt_id: str, submission_id: str | None = None
    ) -> str | None:
        """有界遍历并按操作关联定位；缺失、分页未尽或多匹配都不是重发证据."""
        import httpx

        if not thread_id or not attempt_id:
            return None
        matches: set[str] = set()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                for page in range(20):
                    res = await client.get(
                        self._runs_url(thread_id),
                        params={"limit": 50, "offset": page * 50},
                        headers={"authorization": f"Bearer {self._ticket}"},
                    )
                    if res.status_code in (401, 403):
                        raise PlatformPermissionRevoked(f"http_{res.status_code}")
                    if res.status_code == 404:
                        raise PlatformObjectMissing("runs_not_found")
                    if res.status_code != 200:
                        raise PlatformUnavailable(f"http_{res.status_code}")
                    items = res.json()
                    if not isinstance(items, list):
                        raise PlatformUnavailable("invalid_run_list")
                    for item in items:
                        if not isinstance(item, dict):
                            raise PlatformUnavailable("invalid_run_entry")
                        metadata = item.get("metadata") or {}
                        if not isinstance(metadata, dict):
                            raise PlatformUnavailable("invalid_run_metadata")
                        if str(
                            metadata.get("ledger_attempt_id") or ""
                        ) == attempt_id and (
                            submission_id is None
                            or metadata.get("ledger_submission_id") == submission_id
                        ):
                            if item.get("run_id"):
                                matches.add(str(item["run_id"]))
                    if len(matches) > 1:
                        raise PlatformUnavailable("ambiguous_submission_runs")
                    if len(items) < 50:
                        return next(iter(matches), None)
        except (httpx.HTTPError, ValueError) as exc:
            raise PlatformUnavailable(type(exc).__name__) from exc
        raise PlatformUnavailable("run_lookup_page_bound_reached")
