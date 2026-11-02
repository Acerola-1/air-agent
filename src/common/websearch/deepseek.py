"""DeepSeek 联网搜索客户端.

只服务 Basic QA 的受控联网补充能力：调用方必须先完成人工审批，本模块只负责
一次实际外呼。安全与成本约束：

- 密钥只从部署配置读取，任何日志/异常都不回显明文；
- 固定超时、禁止跟随重定向、忽略进程代理环境（``trust_env=False``）；
- 只记录结果条数、耗时与错误类别，不记录查询全文与响应原文；
- 响应字段做边界校验，畸形或超长内容按不可得处理，不向上抛原始报文。

``summary`` 是给模型阅读的检索摘要，``snippet`` 是短摘录，``content`` 只在
``need_content`` 为真时可能非空。三者分开保存，正文不会被塞进摘要字段。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from loguru import logger

from common.config import config

DEFAULT_RESULT_COUNT = 5
MAX_RESULT_COUNT = 5
MAX_QUERY_CHARS = 128
MAX_TITLE_CHARS = 200
MAX_SNIPPET_CHARS = 500
# 文档写的是约 600–800 字；实测政策页面的 summary 大约 1500 字，1000 会裁掉后半段。
MAX_SUMMARY_CHARS = 2000
MAX_CONTENT_CHARS = 20000
MAX_SITE_CHARS = 120
MAX_DATE_CHARS = 40
SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
# 打开 need_content 后，同一次搜索会等待页面正文，10 秒容易把可用结果变成超时。
TIMEOUT_SECONDS = 20.0

_ERROR_MISSING_KEY = "missing_key"
_ERROR_TIMEOUT = "timeout"
_ERROR_TRANSPORT = "transport_error"
_ERROR_HTTP = "http_error"
_ERROR_PROTOCOL = "protocol_error"


class DeepSeekSearchError(RuntimeError):
    """一次搜索未取得可用结果；``category`` 只用于内部记录与计数."""

    def __init__(self, category: str, *, status: int | None = None) -> None:
        """保存固定的错误类别，不携带查询、密钥或响应正文."""
        super().__init__(category)
        self.category = category
        self.status = status


@dataclass(frozen=True)
class SearchResult:
    """一条经过边界校验的网页结果；url 为唯一标识.

    ``content`` 是页面正文的有界副本，可能为空。是否交给模型由调用方决定。
    """

    title: str
    url: str
    site: str
    date: str
    snippet: str
    summary: str
    content: str
    rerank_score: float | None
    authority: int | None


def _text(value: Any, limit: int) -> str:
    """把任意字段收敛成有上限的纯文本；非字符串视为缺失."""
    if not isinstance(value, str):
        return ""
    cleaned = "".join(char for char in value if char in "\n\t" or ord(char) >= 32)
    return cleaned.strip()[:limit]


def _http_url(value: Any) -> str:
    """只接受 http/https 绝对地址，拒绝 data/脚本等伪协议."""
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate or len(candidate) > 2048:
        return ""
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return candidate


def _score(value: Any) -> float | None:
    """只接受官方 0～3 的排序分；越界或布尔值视为缺失."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    if score != score or score < 0 or score > 3:
        return None
    return score


def _authority(value: Any) -> int | None:
    """权威档位为 -1～4 的整数；-1 表示未知，不把布尔值当成 0 或 1."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < -1 or value > 4:
        return None
    return value


def _items(payload: Any) -> list[Any]:
    """兼容 results/data 两种常见形态；未知结构按协议错误处理."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise DeepSeekSearchError(_ERROR_PROTOCOL)


def _parse_result(item: Any) -> SearchResult | None:
    """解析单条结果；缺 url 或完全空白的条目按不可得丢弃."""
    if not isinstance(item, dict):
        return None
    url = _http_url(item.get("url") or item.get("link"))
    if not url:
        return None
    title = _text(item.get("title"), MAX_TITLE_CHARS)
    snippet = _text(item.get("snippet"), MAX_SNIPPET_CHARS)
    summary = _text(item.get("summary"), MAX_SUMMARY_CHARS)
    content = _text(item.get("content"), MAX_CONTENT_CHARS)
    if not title and not snippet and not summary and not content:
        return None
    site = _text(
        item.get("site_name") or item.get("site") or item.get("source"),
        MAX_SITE_CHARS,
    )
    date = _text(
        item.get("date")
        or item.get("published_at")
        or item.get("publish_time")
        or item.get("time"),
        MAX_DATE_CHARS,
    )
    return SearchResult(
        title=title,
        url=url,
        site=site,
        date=date,
        snippet=snippet,
        summary=summary,
        content=content,
        rerank_score=_score(item.get("rerank_score")),
        authority=_authority(item.get("authority")),
    )


def _valid_query(query: str) -> str:
    """校验并返回去空白后的查询词；空值或超过官方 128 字不外呼."""
    value = (query or "").strip()
    if not value or len(value) > MAX_QUERY_CHARS:
        raise ValueError("invalid_query")
    return value


async def search_web(
    query: str, *, count: int = DEFAULT_RESULT_COUNT
) -> list[SearchResult]:
    """执行一次 DeepSeek 搜索；调用方负责审批与预算计数.

    Raises:
        ValueError: 查询为空或超过 128 字（不发起外呼）。
        DeepSeekSearchError: 缺密钥、超时、传输/HTTP/协议错误。
    """
    value = _valid_query(query)
    limit = max(1, min(int(count), MAX_RESULT_COUNT))
    api_key = (config.DEEPSEEK_API_KEY or "").strip()
    if not api_key:
        raise DeepSeekSearchError(_ERROR_MISSING_KEY)

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(
                SEARCH_URL,
                json={"query": value, "count": limit, "need_content": True},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
    except (TimeoutError, httpx.TimeoutException):
        logger.warning(
            "deepseek websearch failed category={} elapsed_ms={}",
            _ERROR_TIMEOUT,
            int((time.perf_counter() - started) * 1000),
        )
        raise DeepSeekSearchError(_ERROR_TIMEOUT) from None
    except Exception as exc:  # noqa: BLE001 - 只暴露固定类别
        logger.warning(
            "deepseek websearch failed category={} class={} elapsed_ms={}",
            _ERROR_TRANSPORT,
            type(exc).__name__,
            int((time.perf_counter() - started) * 1000),
        )
        raise DeepSeekSearchError(_ERROR_TRANSPORT) from None

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if response.status_code != 200:
        logger.warning(
            "deepseek websearch failed category={} status={} elapsed_ms={}",
            _ERROR_HTTP,
            response.status_code,
            elapsed_ms,
        )
        raise DeepSeekSearchError(_ERROR_HTTP, status=response.status_code)

    try:
        payload = response.json()
        items = _items(payload)
    except DeepSeekSearchError:
        logger.warning(
            "deepseek websearch failed category={} elapsed_ms={}",
            _ERROR_PROTOCOL,
            elapsed_ms,
        )
        raise
    except Exception:  # noqa: BLE001 - 畸形 JSON 不向上抛原文
        logger.warning(
            "deepseek websearch failed category={} elapsed_ms={}",
            _ERROR_PROTOCOL,
            elapsed_ms,
        )
        raise DeepSeekSearchError(_ERROR_PROTOCOL) from None

    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in items:
        parsed = _parse_result(item)
        if parsed is None or parsed.url in seen:
            continue
        seen.add(parsed.url)
        results.append(parsed)
        if len(results) >= limit:
            break

    logger.info(
        "deepseek websearch completed results={} elapsed_ms={}",
        len(results),
        elapsed_ms,
    )
    return results
