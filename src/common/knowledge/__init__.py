"""Shared, permission-preserving knowledge retrieval for all public graphs."""

from common.knowledge.client import search_knowledge
from common.knowledge.contracts import SCHEMA_VERSION
from common.knowledge.tool import TOOL_NAME, build_knowledge_tool, knowledge_search_tool

__all__ = [
    "SCHEMA_VERSION",
    "TOOL_NAME",
    "build_knowledge_tool",
    "knowledge_search_tool",
    "search_knowledge",
]
