"""从自然语言问题中提取结构化元数据过滤条件."""

import json
from typing import Dict

from langchain_core.prompts import PromptTemplate
from langchain_ollama import OllamaLLM
from loguru import logger

from common.config import config


class MetadataQueryHelper:
    """通过 LLM prompt 生成元数据过滤条件（issuer/date/type/id/tags）."""

    def __init__(self):
        """初始化 LLM 链和允许的元数据字段."""
        self.llm = OllamaLLM(
            base_url=config.OLLAMA_BASE_URL,
            model=config.OLLAMA_CHAT_MODEL,
            temperature=0.2,
        )
        self.allowed_fields = {
            "issuer",
            "issue_date",
            "document_type",
            "doc_id",
            "tags",
        }

        self.prompt = PromptTemplate.from_template(
            """
你是一个结构化信息分析器，请从用户的问题中提取用于文档过滤的元数据（以 JSON 格式返回）。
仅返回 JSON，不要添加说明或解释。字段如下（可为空）：
- issuer: 发布单位，如 “中国环境监测总站”
- issue_date: 发布时间，格式如 “2020-05-12”
- document_type: 文种，如 “通知”、“通报”、“规范”
- doc_id: 文号，如 “总站气〔2021〕17号”
- tags: 关键词，如 ["空气质量", "监测", "政策"]

用户问题：
\"\"\"{question}\"\"\"
请只返回一个 JSON 格式的结构。
"""
        )
        self.chain = self.prompt | self.llm

    def extract_metadata_filter(self, question: str) -> Dict[str, str]:
        """返回从问题中提取并清洗后的元数据过滤条件."""
        try:
            raw = self.chain.invoke({"question": question}).strip()
            logger.debug("Extracted raw metadata: %s", raw)
            data = json.loads(raw)

            clean_data = {
                k: v for k, v in data.items() if k in self.allowed_fields and v
            }

            if "tags" in clean_data and isinstance(clean_data["tags"], list):
                clean_data["tags"] = ", ".join(clean_data["tags"])

            return clean_data

        except Exception as e:
            logger.warning(f"元数据解析失败: {e}")
            return {}
