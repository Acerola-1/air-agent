"""Basic QA 能力解析的异常契约；不依赖加载、目录或执行流程."""


class BasicQARuntimeError(RuntimeError):
    """会话能力与可信运行、当前材料或工具范围不匹配."""
