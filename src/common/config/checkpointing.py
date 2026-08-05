"""检查点基础设施.

本地化版本：使用 MySQL(checkpointer)替代 PostgreSQL。
PostgresSaver 改为 PyMySQLSaver,connect 协议换为 pymysql。
pg_store.py / multi_query_retriever / milvus_vector_store / metadata_query_helper /
vanna_sql_adapter 已随 Milvus 知识库下线一并删除。
"""
from __future__ import annotations

import threading
from typing import Any

from loguru import logger

from common.config import config

_checkpointer: Any | None = None
_checkpointer_lock = threading.Lock()


def _close_checkpointer(checkpointer: Any) -> None:
    """尽力关闭 checkpointer 持有的底层连接."""
    for attr in ("conn", "_conn"):
        conn = getattr(checkpointer, attr, None)
        close = getattr(conn, "close", None)
        if callable(close):
            close()
            return
    close = getattr(checkpointer, "close", None)
    if callable(close):
        checkpointer.close()


def _parse_mysql_uri(uri: str) -> dict[str, Any]:
    """解析 mysql://user:password@host:port/db 形式的连接串."""
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    if parsed.scheme not in ("mysql", "mysql+aiomysql", "mysql+pymysql"):
        raise ValueError(
            f"MYSQL_URI 协议必须为 mysql,实际为 {parsed.scheme!r}。完整串: {uri}"
        )
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": parsed.username or "root",
        "password": parsed.password or "",
        "database": (parsed.path or "/").lstrip("/") or None,
    }


def _create_checkpointer() -> Any:
    """创建 MySQL checkpointer(PyMySQLSaver),失败时不写入缓存."""
    import pymysql
    from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

    conn_params = _parse_mysql_uri(config.MysqlUri)
    if not conn_params["database"]:
        raise ValueError(
            "MySQL 数据库名不能为空,请检查 MysqlUri() 或 MYSQL_DB 配置"
        )
    pg_conn = pymysql.connect(
        host=conn_params["host"],
        port=conn_params["port"],
        user=conn_params["user"],
        password=conn_params["password"],
        database=conn_params["database"],
        autocommit=True,
        connect_timeout=20,
        ssl_disabled=True,
    )
    checkpointer = PyMySQLSaver(pg_conn)
    checkpointer.setup()
    logger.info(
        "MySQL checkpointer 初始化成功: db={}@{}:{}/{}",
        conn_params["user"],
        conn_params["host"],
        conn_params["port"],
        conn_params["database"],
    )
    return checkpointer


def get_checkpointer() -> Any:
    """获取 MySQL checkpointer 实例."""
    global _checkpointer

    if _checkpointer is not None:
        return _checkpointer

    with _checkpointer_lock:
        if _checkpointer is not None:
            return _checkpointer

        _checkpointer = _create_checkpointer()
        return _checkpointer


def reset_checkpointer_cache() -> None:
    """清空进程内 checkpointer 缓存,供测试或连接重建使用."""
    global _checkpointer

    with _checkpointer_lock:
        if _checkpointer is not None:
            try:
                _close_checkpointer(_checkpointer)
            except Exception as exc:
                logger.warning("关闭旧 MySQL checkpointer 失败: {}", exc)
        _checkpointer = None


def invalidate_checkpointer() -> None:
    """使当前 checkpointer 失效,下次获取时重新创建."""
    reset_checkpointer_cache()
