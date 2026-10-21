"""Change 7：企业隔离的热门问题、主题与用户诉求分析。

本域只在后台管理进程/独立 worker 中运行，不在 Agent 图热路径执行，不访问
共享 RAG/Qdrant，也不导入根 `common.config` 触发模型/Embedding 预热。
"""
