"""Agent 包日志设置."""

from __future__ import annotations

import sys

from loguru import logger


class LoggingConfigurator:
    """只配置一次应用日志."""

    _configured = False

    @classmethod
    def configure(cls) -> None:
        """在尚未配置时初始化 loguru 输出."""
        if cls._configured:
            return

        logger.remove()
        logger.add(
            sys.stderr,
            format="<green>{time:YYYY-MM-DD HH:mm:ss ZZ}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level="INFO",
        )
        logger.add(
            "logs/graph_{time:YYYY-MM-DD}.log",
            rotation="00:00",
            retention="30 days",
            compression="zip",
            format="{time:YYYY-MM-DD HH:mm:ss ZZ} | {level: <8} | {name}:{function}:{line} - {message}",
            level="DEBUG",
            encoding="utf-8",
        )
        cls._configured = True
