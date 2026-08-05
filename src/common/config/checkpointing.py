"""检查点基础设施(SQLite 版).

使用官方 `langgraph-checkpoint-sqlite` 的 `AsyncSqliteSaver`(基于 aiosqlite),
继承 `BaseAsyncSqliteSaver → SqliteSaver → BaseCheckpointSaver`,自带完整
async 接口(aget_tuple / alist / aput / aput_writes / adelete_thread /
adelete_for_runs / acopy_thread / aprune)。

构造约束:
- `AsyncSqliteSaver.__init__` 调 `asyncio.get_running_loop()`,必须在 running
  event loop 里构造。业务图 `graph.compile(checkpointer=...)` 和 `get_checkpointer`
  都是 sync 上下文,没有 running loop,直接 `AsyncSqliteSaver(...)` 会 RuntimeError。
- 解决: 启动一个后台 daemon 线程跑 `loop.run_forever`,主线程 sync 上下文
  用 `asyncio.run_coroutine_threadsafe(...)` + `future.result()` 跨 loop
  同步等构造完成。构造出的实例 `self.loop` 指向后台 loop,后续 FastAPI
  在主 loop 调 `await cp.aput(...)` 时,`AsyncSqliteSaver` 内部用
  `run_coroutine_threadsafe(..., self.loop)` 自动转发(同官方模式)。

业务图 `graph.compile(checkpointer=get_checkpointer())` 拿 sync BaseCheckpointSaver
实例(isinstance 通过);FastAPI lifespan 拿同一实例,调 aget_state / astream /
aput 等 async 方法。

之前的 MySQL / 跨 loop async hack 已全部清除。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from loguru import logger

from common.config import config

logger = logging.getLogger(__name__)


# ==================== 后台 event loop(供 AsyncSqliteSaver 构造) ====================

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _ensure_background_loop() -> asyncio.AbstractEventLoop:
    """启动一个 daemon 线程,常驻跑 event loop."""
    global _loop, _loop_thread
    if _loop is not None and _loop.is_running():
        return _loop

    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop

        new_loop = asyncio.new_event_loop()

        def _runner() -> None:
            asyncio.set_event_loop(new_loop)
            new_loop.run_forever()

        thread = threading.Thread(
            target=_runner,
            name="sqlite-checkpointer-loop",
            daemon=True,
        )
        thread.start()
        _loop = new_loop
        _loop_thread = thread
        return new_loop


def _run_async_blocking(coro: Any, timeout: float = 30.0) -> Any:
    """在后台 loop 同步跑一个 coroutine,返回结果.不污染主线程 event loop."""
    loop = _ensure_background_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


# ==================== saver 单例 ====================

_saver: Any | None = None
_saver_lock = threading.Lock()


def _create_checkpointer() -> Any:
    """在后台 event loop 上构造 AsyncSqliteSaver + 跑 setup()."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    # 确保父目录存在
    parent = Path(config.SQLITE_PATH).parent
    if str(parent) and str(parent) != ".":
        parent.mkdir(parents=True, exist_ok=True)

    async def _create() -> Any:
        cm = AsyncSqliteSaver.from_conn_string(config.SQLITE_PATH)
        saver = await cm.__aenter__()
        await saver.setup()
        # 保持 cm 在 saver 上, 避免被 GC
        saver._cm = cm  # type: ignore[attr-defined]
        return saver

    return _run_async_blocking(_create())


def get_checkpointer() -> Any:
    """同步获取 AsyncSqliteSaver 实例(缓存).

    业务图编译路径 `graph.compile(checkpointer=get_checkpointer())` 拿 sync 实例;
    FastAPI 路径 拿同一实例, 调 aget_state / astream / aput 等 async 方法,
    AsyncSqliteSaver 内部用 `asyncio.run_coroutine_threadsafe` 跨 loop 调度。
    """
    global _saver
    if _saver is not None:
        return _saver

    with _saver_lock:
        if _saver is not None:
            return _saver

        _saver = _create_checkpointer()
        logger.info("SQLite checkpointer 初始化成功: path=%s", config.SQLITE_PATH)
        return _saver


def reset_checkpointer_cache() -> None:
    """清空进程内 checkpointer 缓存,供测试或连接重建使用."""
    global _saver
    with _saver_lock:
        _saver = None
