"""超大工具返回结果专属数据库存储服务.

用于持久化超限的原始 MCP 工具输出数据，避免 Checkpointer 数据库膨胀。
自适应适配生产 PostgreSQL（若 psycopg 可用且配置 DATABASE_URI）与本地 SQLite（aiosqlite）。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from common.config import config

_TABLE_NAME = "tool_artifact_storage"
_initialized = False
_init_lock = asyncio.Lock()


def _is_pg_configured() -> bool:
    """判断是否配置并支持 PostgreSQL."""
    db_uri = os.getenv("DATABASE_URI", "")
    if not (db_uri.startswith("postgresql://") or db_uri.startswith("postgres://")):
        return False
    try:
        import psycopg  # noqa: F401

        return True
    except Exception:
        return False


def _get_sqlite_path() -> str:
    """获取 SQLite 数据库文件绝对路径并确保父目录存在."""
    sqlite_path = getattr(config, "SQLITE_PATH", "db/checkpoints.db")
    path_obj = Path(sqlite_path)
    if not path_obj.is_absolute():
        # 项目根目录
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        path_obj = project_root / sqlite_path
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    return str(path_obj)


async def init_artifact_storage_table() -> None:
    """初始化专属存储表（幂等执行）."""
    global _initialized
    if _initialized:
        return

    async with _init_lock:
        if _initialized:
            return

        if _is_pg_configured():
            import psycopg

            db_uri = os.getenv("DATABASE_URI", "")
            try:
                async with await psycopg.AsyncConnection.connect(db_uri) as aconn:
                    async with aconn.cursor() as cur:
                        await cur.execute(
                            f"""
                            CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                                artifact_id VARCHAR(64) PRIMARY KEY,
                                tool_call_id VARCHAR(64) NOT NULL,
                                tool_name VARCHAR(128) NOT NULL,
                                char_length INT NOT NULL,
                                raw_payload TEXT NOT NULL,
                                summary TEXT,
                                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                            );
                            CREATE INDEX IF NOT EXISTS idx_tool_artifact_call_id ON {_TABLE_NAME}(tool_call_id);
                            """
                        )
                    await aconn.commit()
                _initialized = True
                logger.info("专属工具载荷表已在 PostgreSQL 中就绪: table={}", _TABLE_NAME)
                return
            except Exception as e:
                logger.warning(
                    "PostgreSQL 初始化工具载荷表失败，将回退至 SQLite: error={}",
                    e,
                )

        # 回退至 SQLite
        import aiosqlite

        db_path = _get_sqlite_path()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
                    artifact_id TEXT PRIMARY KEY,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    char_length INTEGER NOT NULL,
                    raw_payload TEXT NOT NULL,
                    summary TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            await db.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_tool_artifact_call_id ON {_TABLE_NAME}(tool_call_id);
                """
            )
            await db.commit()
        _initialized = True
        logger.info("专属工具载荷表已在 SQLite 中就绪: path={}", db_path)


async def save_tool_artifact(
    *,
    tool_call_id: str,
    tool_name: str,
    raw_payload: str,
    summary: str = "",
) -> str:
    """异步将超大工具原始数据存入专属数据库表.

    Args:
        tool_call_id: 工具调用 ID
        tool_name: 工具名称
        raw_payload: 原始返回文本（如超长 JSON）
        summary: Subagent 生成的高浓度提炼结论（可选）

    Returns:
        生成的全局唯一 artifact_id
    """
    await init_artifact_storage_table()

    artifact_id = f"art_{uuid.uuid4().hex}"
    char_length = len(raw_payload)
    now_iso = datetime.now(UTC).isoformat()

    if _is_pg_configured():
        import psycopg

        db_uri = os.getenv("DATABASE_URI", "")
        try:
            async with await psycopg.AsyncConnection.connect(db_uri) as aconn:
                async with aconn.cursor() as cur:
                    await cur.execute(
                        f"""
                        INSERT INTO {_TABLE_NAME} (
                            artifact_id, tool_call_id, tool_name, char_length, raw_payload, summary, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                        """,
                        (
                            artifact_id,
                            tool_call_id,
                            tool_name,
                            char_length,
                            raw_payload,
                            summary,
                        ),
                    )
                await aconn.commit()
            logger.info(
                "超大工具载荷已成功存入 PostgreSQL: id={}, tool={}, length={}",
                artifact_id,
                tool_name,
                char_length,
            )
            return artifact_id
        except Exception as e:
            logger.warning(
                "写入 PostgreSQL 失败，尝试回退写入 SQLite: id={}, error={}",
                artifact_id,
                e,
            )

    # 写入 SQLite
    import aiosqlite

    db_path = _get_sqlite_path()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            f"""
            INSERT INTO {_TABLE_NAME} (
                artifact_id, tool_call_id, tool_name, char_length, raw_payload, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                tool_call_id,
                tool_name,
                char_length,
                raw_payload,
                summary,
                now_iso,
            ),
        )
        await db.commit()

    logger.info(
        "超大工具载荷已成功存入 SQLite: id={}, tool={}, length={}",
        artifact_id,
        tool_name,
        char_length,
    )
    return artifact_id


async def get_tool_artifact(artifact_id: str) -> dict[str, Any] | None:
    """根据 artifact_id 查询原始数据记录."""
    await init_artifact_storage_table()

    if _is_pg_configured():
        import psycopg

        db_uri = os.getenv("DATABASE_URI", "")
        try:
            async with await psycopg.AsyncConnection.connect(db_uri) as aconn:
                async with aconn.cursor() as cur:
                    await cur.execute(
                        f"""
                        SELECT artifact_id, tool_call_id, tool_name, char_length, raw_payload, summary, created_at
                        FROM {_TABLE_NAME} WHERE artifact_id = %s
                        """,
                        (artifact_id,),
                    )
                    row = await cur.fetchone()
                    if row:
                        return {
                            "artifact_id": row[0],
                            "tool_call_id": row[1],
                            "tool_name": row[2],
                            "char_length": row[3],
                            "raw_payload": row[4],
                            "summary": row[5],
                            "created_at": str(row[6]),
                        }
        except Exception as e:
            logger.warning("从 PostgreSQL 读取载荷失败，尝试 SQLite: {}", e)

    import aiosqlite

    db_path = _get_sqlite_path()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            f"""
            SELECT artifact_id, tool_call_id, tool_name, char_length, raw_payload, summary, created_at
            FROM {_TABLE_NAME} WHERE artifact_id = ?
            """,
            (artifact_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "artifact_id": row[0],
                    "tool_call_id": row[1],
                    "tool_name": row[2],
                    "char_length": row[3],
                    "raw_payload": row[4],
                    "summary": row[5],
                    "created_at": str(row[6]),
                }
    return None
