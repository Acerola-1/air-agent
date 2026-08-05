"""政策、知识和问答检索的多路检索工具."""

import logging

from langchain_community.document_transformers import LongContextReorder
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings, OllamaLLM
from pymilvus import MilvusClient

from common.config import config
from common.config.pg_store import PGStore

logger = logging.getLogger(__name__)


class _MilvusClientVectorStore:
    """基于 `pymilvus.MilvusClient` 的最小向量存储包装器."""

    def __init__(
        self,
        client: MilvusClient,
        collection_name: str,
        *,
        anns_field: str = "vector",
        output_fields: list[str],
    ):
        """创建一个薄封装，将 Milvus `search` 结果转为 LangChain Document."""
        self._client = client
        self._collection_name = collection_name
        self._anns_field = anns_field
        self._output_fields = output_fields

    def similarity_search_by_vector(self, embedding, k: int = 4):
        """使用单个 embedding 检索 collection，并返回 top-k Document."""
        results = self._client.search(
            collection_name=self._collection_name,
            data=[embedding],
            anns_field=self._anns_field,
            limit=k,
            output_fields=self._output_fields,
        )
        docs = []
        for hit in results[0]:
            entity = hit.get("entity", {})
            page_content = entity.get("text", "")
            metadata = dict(entity)
            metadata["pk"] = hit.get("pk")
            metadata["distance"] = hit.get("distance")
            docs.append(Document(page_content=page_content, metadata=metadata))
        return docs


class MultiQueryRetriever:
    """使用 embedding、Milvus 和 PostgreSQL 检索政策、知识和问答内容."""

    def __init__(self):
        """初始化 LLM、embedding 模型、Milvus 存储和父文档存储."""
        self.llm = OllamaLLM(
            base_url=config.OLLAMA_BASE_URL,
            model=config.OLLAMA_CHAT_MODEL,
            temperature=0.2,
        )
        self.embedder1 = OllamaEmbeddings(
            model=config.OLLAMA_EMBEDDING_MODEL1, base_url=config.OLLAMA_BASE_URL
        )
        self.embedder2 = OllamaEmbeddings(
            model=config.OLLAMA_EMBEDDING_MODEL2, base_url=config.OLLAMA_BASE_URL
        )
        self._milvus_client = MilvusClient(
            uri=config.MILVUS_URI, db_name=config.MILVUS_DB_NAME
        )
        existing_collections = set(self._milvus_client.list_collections())  # type: ignore[arg-type]
        required = {config.POLICY_COLLECTION, config.KNOWLEDGE_COLLECTION}
        missing = sorted(required - existing_collections)
        if missing:
            raise RuntimeError(
                f"Milvus collections missing in db={config.MILVUS_DB_NAME}: {missing}. "
                f"Existing: {sorted(existing_collections)}"
            )

        self.policy_vectorstore = _MilvusClientVectorStore(
            self._milvus_client,
            config.POLICY_COLLECTION,
            output_fields=["text", "title"],
        )
        self.knowledge_vectorstore = _MilvusClientVectorStore(
            self._milvus_client,
            config.KNOWLEDGE_COLLECTION,
            output_fields=["text", "parent_id"],
        )
        self.QA_vectorstore = (
            _MilvusClientVectorStore(
                self._milvus_client,
                config.QA_COLLECTION,
                output_fields=["text"],
            )
            if config.QA_COLLECTION in existing_collections
            else None
        )
        self.pg_store = PGStore()

    def knowledge_search(self, query, top_k=2):
        """按查询检索父级知识文档."""
        query_embedding = self.embedder1.embed_query("为信息检索优化的问句: " + query)

        search_results = self.knowledge_vectorstore.similarity_search_by_vector(
            embedding=query_embedding, k=top_k
        )

        if not search_results:
            logger.info("No knowledge documents found.")
            return []

        parent_ids = list(set(doc.metadata["parent_id"] for doc in search_results))
        parent_docs_contents = self.pg_store.mget(parent_ids)

        doc_list = [
            Document(page_content=content, metadata={"parent_id": pid})
            for pid, content in zip(parent_ids, parent_docs_contents)
            if content is not None
        ]

        reorderer = LongContextReorder()
        reordered_docs = reorderer.transform_documents(doc_list)

        return [doc.page_content for doc in reordered_docs]

    def qa_search(self, query, top_k=2):
        """按查询检索问答文档；未启用时返回空列表."""
        query_embedding = self.embedder2.embed_query("为信息检索优化的问句: " + query)
        if self.QA_vectorstore is None:
            return []
        search_results = self.QA_vectorstore.similarity_search_by_vector(
            embedding=query_embedding, k=top_k
        )
        if not search_results:
            logger.info("No QA documents found.")
            return []
        return search_results
