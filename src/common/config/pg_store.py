"""用于知识检索的 PostgreSQL 父文档存储."""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Sequence, Tuple

import psycopg2
from langchain_core.stores import BaseStore
from loguru import logger

from common.config import config

# =========================================================================
# 连接辅助函数
# =========================================================================


def _make_connection() -> Any:
    """创建并返回 psycopg2 连接."""
    return psycopg2.connect(
        host=config.POSTGRES_HOST,
        port=config.POSTGRES_PORT,
        user=config.POSTGRES_USER,
        password=config.POSTGRES_PASSWORD,
        dbname=config.POSTGRES_DB,
    )


def connect_pg() -> Any:
    """公共连接入口（兼容旧代码）."""
    return _make_connection()


# =========================================================================
# PGStore：实现 LangChain BaseStore 接口
# =========================================================================


class PGStore(BaseStore[str, str]):
    """将父文档存储在 PostgreSQL 中，供知识检索使用.

    实现 LangChain BaseStore 接口，用于 ParentDocumentRetriever 的父文档持久化。
    """

    def __init__(self) -> None:
        """初始化 PostgreSQL 连接.

        注意：连接会在实例生命周期内保持打开。
        长时间运行的服务可考虑使用连接池。
        """
        self.conn = _make_connection()

    # ------------------------------------------------------------------
    # BaseStore 标准接口实现
    # ------------------------------------------------------------------

    def mget(self, keys: Sequence[str]) -> List[str | None]:
        """按 ID 批量获取父文档.

        参数:
            keys: 父文档 ID 列表。

        返回:
            与 keys 顺序一致的内容列表，缺失项为 None。
        """
        if not keys:
            return []

        # 用参数化查询防止 SQL 注入
        placeholders = ",".join(["%s"] * len(keys))
        query = f"SELECT parent_id, content FROM ipp_parent_docs WHERE parent_id IN ({placeholders})"

        with self.conn.cursor() as cur:
            cur.execute(query, tuple(keys))
            rows = cur.fetchall()

        # 构建 id → content 映射，再按 keys 顺序返回，保证与入参顺序一致
        result_map = {row[0]: row[1] for row in rows}
        return [result_map.get(k) for k in keys]

    def mset(self, key_value_pairs: Sequence[Tuple[str, str]]) -> None:
        """批量插入或更新父文档.

        参数:
            key_value_pairs: (parent_id, content) 元组序列。
        """
        if not key_value_pairs:
            return

        upsert_sql = """
            INSERT INTO ipp_parent_docs (parent_id, content)
            VALUES (%s, %s)
            ON CONFLICT (parent_id) DO UPDATE SET content = EXCLUDED.content
        """
        with self.conn.cursor() as cur:
            for key, value in key_value_pairs:
                cur.execute(upsert_sql, (key, value))
        self.conn.commit()

    def mdelete(self, keys: Sequence[str]) -> None:  # type: ignore[override]
        """批量删除父文档.

        参数:
            keys: 待删除的父文档 ID 列表。
        """
        if not keys:
            return

        placeholders = ",".join(["%s"] * len(keys))
        query = f"DELETE FROM ipp_parent_docs WHERE parent_id IN ({placeholders})"

        with self.conn.cursor() as cur:
            cur.execute(query, tuple(keys))
        self.conn.commit()

    def yield_keys(self, *, prefix: str | None = None) -> Iterator[str]:  # type: ignore[override]
        """迭代所有父文档 ID，可按前缀过滤.

        参数:
            prefix: 可选的 ID 前缀过滤条件。
        """
        params: Tuple[str, ...]
        if prefix:
            query = "SELECT parent_id FROM ipp_parent_docs WHERE parent_id LIKE %s"
            params = (f"{prefix}%",)
        else:
            query = "SELECT parent_id FROM ipp_parent_docs"
            params = ()

        with self.conn.cursor() as cur:
            cur.execute(query, params)
            for row in cur.fetchall():
                yield row[0]

    # ------------------------------------------------------------------
    # 单条操作（便利方法，供业务代码直接调用）
    # ------------------------------------------------------------------

    def get(self, key: str) -> str | None:
        """按 ID 获取单个父文档.

        参数:
            key: 父文档 ID。

        返回:
            文档内容字符串；未找到时返回 None。
        """
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT content FROM ipp_parent_docs WHERE parent_id = %s", (key,)
            )
            result = cur.fetchone()
        return result[0] if result else None

    def set(self, key: str, value: str) -> None:
        """插入或更新单个父文档.

        参数:
            key: 父文档 ID。
            value: 文档内容。
        """
        # 复用 mset 避免逻辑重复
        self.mset([(key, value)])

    def close(self) -> None:
        """关闭数据库连接."""
        try:
            self.conn.close()
        except Exception as e:
            logger.warning(f"关闭 PGStore 连接时出现异常: {e}")


# =========================================================================
# 批量导入辅助函数
# =========================================================================


def insert_into_pg(parent_docs: List[Dict[str, Any]]) -> None:
    """将父文档批量插入 PostgreSQL.

    参数:
        parent_docs: 包含 id/title/content 字段的字典列表。
    """
    conn = _make_connection()
    insert_sql = """
        INSERT INTO ipp_parent_docs (parent_id, title, content)
        VALUES (%s, %s, %s)
    """
    try:
        with conn.cursor() as cur:
            for doc in parent_docs:
                cur.execute(insert_sql, (doc["id"], doc["title"], doc["content"]))
        conn.commit()
        logger.info(f"成功写入 {len(parent_docs)} 条父文档到 PostgreSQL")
    except Exception as e:
        conn.rollback()
        logger.error(f"批量写入父文档失败，已回滚: {e}", exc_info=True)
        raise
    finally:
        conn.close()
