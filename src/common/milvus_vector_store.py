"""用于 Vanna 集成的 Milvus 向量存储辅助工具."""

from __future__ import annotations

from abc import ABC
from collections.abc import Mapping, Sequence
from typing import Protocol, cast, override, runtime_checkable

from vanna.milvus import Milvus_VectorStore


class EmbeddingVector(Protocol):
    """最小 embedding 向量协议，仅用于读取 shape."""

    @property
    def shape(self) -> Sequence[int]:
        """返回向量维度."""
        ...


@runtime_checkable
class EmbeddingFunction(Protocol):
    """Vanna embedding function 需要的最小接口."""

    def encode_documents(self, documents: Sequence[str]) -> Sequence[EmbeddingVector]:
        """将文档编码为向量."""
        ...

    def encode_queries(self, queries: Sequence[str]) -> object:
        """将查询字符串编码为向量."""
        ...


@runtime_checkable
class MilvusClient(Protocol):
    """Milvus client 在本适配器中使用的最小接口."""

    def search(
        self,
        *,
        collection_name: str,
        anns_field: str,
        data: object,
        limit: int,
        output_fields: Sequence[str],
        search_params: Mapping[str, object],
    ) -> Sequence[Sequence[Mapping[str, object]]]:
        """执行向量检索并返回分组命中结果."""
        ...


class Milvus_VectorStore_Extended(Milvus_VectorStore, ABC):
    """扩展 Vanna 的 Milvus 向量存储，支持按 collection 配置返回数量."""

    def __init__(  # pyright: ignore[reportMissingSuperCall]
        self,
        config: Mapping[str, object] | None = None,
    ) -> None:
        """初始化向量存储，并支持可配置的返回数量.

        跳过默认 embedding 初始化，避免启动时尝试下载 HuggingFace 模型。
        如果 config 中没有 embedding_function，则设为 None，后续调用时会抛出异常提示。
        """
        self.config: Mapping[str, object] = config if config is not None else {}

        # embedding_function: 优先从 config 获取，否则设为 None
        embedding_function = self.config.get("embedding_function")
        self.embedding_function: EmbeddingFunction | None = (
            embedding_function
            if isinstance(embedding_function, EmbeddingFunction)
            else None
        )
        if self.embedding_function is None:
            self._embedding_dim: int | None = None
        else:
            self._embedding_dim = self.embedding_function.encode_documents(["foo"])[0].shape[0]

        # 从 config 获取 milvus_client
        milvus_client = self.config.get("milvus_client")
        self.milvus_client: MilvusClient | None = (
            milvus_client if isinstance(milvus_client, MilvusClient) else None
        )

        # 可配置的返回结果数量
        self.n_results_similar_question: int = self._read_int(
            "n_results_similar_question",
            3,
        )
        self.n_results_related_ddl: int = self._read_int("n_results_related_ddl", 5)
        self.n_results_related_doc: int = self._read_int("n_results_related_doc", 10)

    def _read_int(self, key: str, default: int) -> int:
        value = self.config.get(key, default)
        return value if isinstance(value, int) else default

    def _require_embedding_function(self) -> EmbeddingFunction:
        if self.embedding_function is None:
            raise RuntimeError("Milvus embedding_function is not configured")
        return self.embedding_function

    def _require_milvus_client(self) -> MilvusClient:
        if self.milvus_client is None:
            raise RuntimeError("Milvus client is not configured")
        return self.milvus_client

    def _search(
        self,
        *,
        collection_name: str,
        question: str,
        limit: int,
        output_fields: Sequence[str],
    ) -> Sequence[Mapping[str, object]]:
        search_params: Mapping[str, object] = {
            "metric_type": "L2",
            "params": {"nprobe": 128},
        }
        embeddings = self._require_embedding_function().encode_queries([question])
        result = self._require_milvus_client().search(
            collection_name=collection_name,
            anns_field="vector",
            data=embeddings,
            limit=limit,
            output_fields=output_fields,
            search_params=search_params,
        )
        return result[0] if result else []

    def _entity_text(self, doc: Mapping[str, object], key: str) -> str:
        entity = doc.get("entity")
        if not isinstance(entity, Mapping):
            return ""
        entity = cast(Mapping[object, object], entity)
        value = entity.get(key)
        return str(value) if value is not None else ""

    @override
    def get_similar_question_sql(
        self,
        question: str,
        **_: object,
    ) -> list[dict[str, str]]:
        """从 `vannasql` collection 返回相似问题及其 SQL."""
        res = self._search(
            collection_name="vannasql",
            question=question,
            limit=self.n_results_similar_question,
            output_fields=["text", "sql"],
        )

        list_sql: list[dict[str, str]] = []
        for doc in res:
            row = {
                "question": self._entity_text(doc, "text"),
                "sql": self._entity_text(doc, "sql"),
            }
            list_sql.append(row)
        return list_sql

    @override
    def get_related_ddl(self, question: str, **_: object) -> list[str]:
        """从 `vannaddl` collection 返回相关 DDL 片段."""
        res = self._search(
            collection_name="vannaddl",
            question=question,
            limit=self.n_results_related_ddl,
            output_fields=["ddl"],
        )

        list_ddl: list[str] = []
        for doc in res:
            list_ddl.append(self._entity_text(doc, "ddl"))
        return list_ddl

    @override
    def get_related_documentation(self, question: str, **_: object) -> list[str]:
        """从 `vannadoc` collection 返回相关文档片段."""
        res = self._search(
            collection_name="vannadoc",
            question=question,
            limit=self.n_results_related_doc,
            output_fields=["doc"],
        )

        list_doc: list[str] = []
        for doc in res:
            list_doc.append(self._entity_text(doc, "doc"))
        return list_doc
