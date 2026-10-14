"""观测采集器：有界队列 / 批次 / flush / 有界重试 / 丢弃可见.

设计约束（design §5）：
- 队列默认最多 1000 条、每批最多 100、等待 flush 最多 2 秒、重试最多 3 次
  且指数退避；全部可用环境变量下调，不可上调（无界后台任务禁止）。
- 丢弃/积压计数可见（`stats()`），不静默。
- 写入失败不改变业务答案；不把失败采集声明为已落库。
- 关闭开关立即生效：队列为空即停止，不产生新的外部调用。

线程模型：LangGraph 的 LLM 回调可能来自无事件循环的工作线程（sync 回调），
也可能来自运行节点的事件循环。采集器因此运行**专用后台线程 + 自有事件循环**，
入队线程安全、非阻塞（queue.Queue + put_nowait，队列满立即丢弃并计数）。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from common.observability.context import ObservationContext
from common.observability.store import (
    ObservabilityBackendUnavailable,
    ThreadDeleted,
    observability_enabled,
)


def _env_int(name: str, default: int, maximum: int) -> int:
    """读取有界配置：只允许下调，不允许超过上限."""
    raw = os.environ.get(name, "")
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return max(0, min(value, maximum))


MAX_QUEUE = _env_int("AGENT_OBSERVABILITY_MAX_QUEUE", 1000, 1000)
MAX_BATCH = _env_int("AGENT_OBSERVABILITY_MAX_BATCH", 100, 100)


def _flush_seconds() -> float:
    try:
        value = float(
            os.environ.get("AGENT_OBSERVABILITY_FLUSH_WAIT_SECONDS", "2") or 2
        )
    except ValueError:
        return 2.0
    return min(max(value, 0.0), 2.0) if math.isfinite(value) else 2.0


FLUSH_WAIT_SECONDS = _flush_seconds()
MAX_RETRIES = _env_int("AGENT_OBSERVABILITY_MAX_RETRIES", 3, 3)


@dataclass
class WorkItem:
    """一条待落库的观测工作项（写入函数 + 参数；retries 由 worker 推进）."""

    kind: str
    ctx: ObservationContext | None
    fn: Any  # zero-arg async callable -> dict
    enqueued_at: float = field(default_factory=time.monotonic)
    retries: int = 0


@dataclass
class CollectorStats:
    """队列运行统计（丢弃/积压/重试可见）."""

    enqueued: int = 0
    written: int = 0
    dropped: int = 0
    failed: int = 0
    retries: int = 0
    queue_high_watermark: int = 0
    last_drop_reason: str = ""


class ObservabilityCollector:
    """进程级单例：专用线程 + 自有事件循环消费有界队列."""

    def __init__(self) -> None:
        """初始化有界队列并启动后台写线程."""
        self._queue: queue.Queue[WorkItem | None] = queue.Queue(
            maxsize=max(1, MAX_QUEUE)
        )
        self._stats = CollectorStats()
        self._stats_lock = threading.Lock()
        self._closed = threading.Event()
        self._accepting = True
        self._active = 0
        self._thread: threading.Thread | None = None
        self._start_worker()

    # ---------------------------------------------------------------- 入队

    def enqueue(self, item: WorkItem) -> bool:
        """非阻塞入队；队列满/关闭/停用立即丢弃并计数（不阻塞业务）."""
        if (
            not self._accepting
            or self._closed.is_set()
            or not observability_enabled()
            or MAX_QUEUE == 0
            or MAX_BATCH == 0
        ):
            with self._stats_lock:
                self._stats.dropped += 1
                self._stats.last_drop_reason = "disabled_or_closed"
            return False
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._stats_lock:
                self._stats.dropped += 1
                self._stats.last_drop_reason = "queue_full"
            return False
        with self._stats_lock:
            self._stats.enqueued += 1
            self._stats.queue_high_watermark = max(
                self._stats.queue_high_watermark, self._queue.qsize()
            )
        return True

    # ---------------------------------------------------------------- worker

    def _start_worker(self) -> None:
        def _run_loop(collector: ObservabilityCollector) -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(collector._worker())
            except Exception:  # noqa: BLE001 - worker 崩溃只记日志
                logger.bind(observability=True).warning(
                    "observability worker exited unexpectedly"
                )
            finally:
                try:
                    from common.observability.store import close_pool

                    loop.run_until_complete(close_pool())
                    loop.close()
                except Exception:  # noqa: BLE001
                    pass

        self._thread = threading.Thread(
            target=_run_loop, args=(self,), name="obs-collector", daemon=True
        )
        self._thread.start()

    async def _worker(self) -> None:
        while not (self._closed.is_set() and self._queue.empty()):
            try:
                item = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if item is None:
                continue
            batch: list[WorkItem] = [item]
            while len(batch) < MAX_BATCH:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is not None:
                    batch.append(nxt)
            await self._write_batch(batch)

    async def _write_batch(self, batch: list[WorkItem]) -> None:
        for work in batch:
            with self._stats_lock:
                self._active += 1
            try:
                for attempt in range(MAX_RETRIES + 1):
                    if self._closed.is_set() or not observability_enabled():
                        with self._stats_lock:
                            self._stats.dropped += 1
                            self._stats.last_drop_reason = (
                                "closed_or_disabled_midflight"
                            )
                        break
                    try:
                        await asyncio.wait_for(work.fn(), timeout=2.0)
                        with self._stats_lock:
                            self._stats.written += 1
                        break
                    except ThreadDeleted:
                        with self._stats_lock:
                            self._stats.dropped += 1
                            self._stats.last_drop_reason = "thread_deleted"
                        break
                    except (ObservabilityBackendUnavailable, TimeoutError):
                        if attempt == MAX_RETRIES:
                            with self._stats_lock:
                                self._stats.failed += 1
                            break
                        with self._stats_lock:
                            self._stats.retries += 1
                        await asyncio.sleep(min(0.25 * (2**attempt), 2.0))
                    except Exception as exc:
                        with self._stats_lock:
                            self._stats.failed += 1
                        logger.warning(
                            "observability write failed: kind={} category={}",
                            work.kind,
                            type(exc).__name__,
                        )
                        break
            finally:
                with self._stats_lock:
                    self._active -= 1
                self._queue.task_done()

    # ---------------------------------------------------------------- flush

    def flush(self, timeout: float | None = None) -> dict[str, int | str]:
        """有界 flush：等待队列清空，最多 FLUSH_WAIT_SECONDS；返回统计快照.

        重试退避中的工作项不在队列里：预算内追加一轮等待，避免把退避中的
        重试误报为已处理。
        """
        deadline = time.monotonic() + min(
            timeout if timeout is not None else FLUSH_WAIT_SECONDS, FLUSH_WAIT_SECONDS
        )
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                break
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        return self.stats()

    def stats(self) -> dict[str, int | str]:
        """统计快照（丢弃/积压/写入/失败可见）."""
        with self._stats_lock:
            return {
                "enqueued": self._stats.enqueued,
                "written": self._stats.written,
                "dropped": self._stats.dropped,
                "failed": self._stats.failed,
                "retries": self._stats.retries,
                "queue_size": self._queue.qsize(),
                "in_flight": self._active,
                "pending": self._queue.unfinished_tasks,
                "queue_high_watermark": self._stats.queue_high_watermark,
                "last_drop_reason": self._stats.last_drop_reason,
            }

    async def close(self) -> None:
        """先停止接收并限时排空；预算后取消剩余工作且保留丢弃计数."""
        self._accepting = False
        await asyncio.to_thread(self.flush)
        self._closed.set()
        if self._thread is not None and self._thread.is_alive():
            await asyncio.to_thread(self._thread.join, 0.35)
        if self._thread is not None and not self._thread.is_alive():
            self._thread = None


_COLLECTOR: ObservabilityCollector | None = None
_COLLECTOR_LOCK = threading.Lock()


def get_collector() -> ObservabilityCollector:
    """进程级单例（线程安全初始化）."""
    global _COLLECTOR
    if _COLLECTOR is None:
        with _COLLECTOR_LOCK:
            if _COLLECTOR is None:
                _COLLECTOR = ObservabilityCollector()
    return _COLLECTOR


def reset_collector_for_tests() -> None:
    """测试辅助：替换单例（daemon 线程随进程结束，不 join）."""
    global _COLLECTOR
    with _COLLECTOR_LOCK:
        if _COLLECTOR is not None:
            _COLLECTOR._closed.set()
        _COLLECTOR = None


def utcnow() -> dt.datetime:
    """当前 UTC 时间（观测时间戳统一来源）."""
    return dt.datetime.now(dt.UTC)


def collector_health() -> dict[str, Any]:
    """仅供内部运维端点读取，不因健康查询创建采集线程."""
    collector = _COLLECTOR
    return {
        "enabled": observability_enabled(),
        "state": "initialized" if collector is not None else "not_initialized",
        "limits": {
            "queue": MAX_QUEUE,
            "batch": MAX_BATCH,
            "flush_seconds": FLUSH_WAIT_SECONDS,
            "retries": MAX_RETRIES,
        },
        "stats": collector.stats() if collector is not None else None,
    }


def _shutdown_collector() -> None:
    """解释器正常退出时限时排空，不新建线程、不延长业务关停预算."""
    collector = _COLLECTOR
    if collector is None:
        return
    collector._accepting = False
    collector.flush(timeout=FLUSH_WAIT_SECONDS)
    collector._closed.set()


__import__("atexit").register(_shutdown_collector)
