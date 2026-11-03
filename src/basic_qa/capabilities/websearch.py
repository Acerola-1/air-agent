"""Basic QA 受人工审批约束的联网补充能力.

模型只通过 ``web_search`` 申请联网：工具本身不外呼、不改业务状态，只登记待审批
请求并返回占位回执。独立的 ``websearch_gate`` 节点负责审批、预算、外呼和回写
配对回执，因此恢复（resume）时只会重跑 gate，不会重跑已完成的工具批次。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command, interrupt
from loguru import logger

from common.config import config as app_config
from common.runtime_events import emit_event
from common.websearch import DeepSeekSearchError, search_web
from common.websearch.deepseek import (
    MAX_QUERY_CHARS,
    MAX_SNIPPET_CHARS,
    MAX_SUMMARY_CHARS,
)

WEB_SEARCH_TOOL = "web_search"
WEBSEARCH_BUDGET = 3
DEEPSEEK_PROVIDER = "DeepSeek"
DEEPSEEK_SOURCE = "deepseek-websearch"
EXTERNAL_COST_PER_CALL = "0.005"
EXTERNAL_COST_NONE = "0"
EXTERNAL_COST_CURRENCY = "CNY"
MAX_REASON_CHARS = 500
RESULT_COUNT = 5
CONTENT_EXCERPT_CHARS = 8000
CONTENT_SCORE_MIN = 1.5
MAX_CONTENT_EXCERPTS = 2
# 5 条摘要上限加上 2 段 8000 字摘录。超出后仍走统一的工具结果压缩。
MAX_MODEL_TEXT_CHARS = 26000

GENERIC_UNAVAILABLE = "部分数据暂时无法获取，无法进一步分析。"
_INVALID_QUERY = (
    f"联网搜索参数无效；请提供非空且不超过 {MAX_QUERY_CHARS} 字的 query，"
    f"以及非空且不超过 {MAX_REASON_CHARS} 字的 reason。"
)
_LINK_ONLY_LINE = re.compile(
    r"^(?:[-*+]\s*)?(?:\[[^\]]*\]\([^)]*\)\s*|https?://\S+\s*)+$"
)


def _pending_message_id(tool_call_id: str) -> str:
    """占位回执与 gate 最终回执共用的消息 ID，恢复时原地替换."""
    return f"websearch_pending_{tool_call_id}"


def _paired_message(
    pending: dict[str, Any],
    content: dict[str, Any],
    *,
    status: str | None = None,
    network_attempted: bool = False,
    message_id: str | None = None,
) -> ToolMessage:
    """构造与模型 tool_call 配对的回执；正文只含业务语言."""
    tool_call_id = str(pending.get("tool_call_id") or "")
    fields: dict[str, Any] = {}
    if status is not None:
        fields["status"] = status
    return ToolMessage(
        content=json.dumps(content, ensure_ascii=False),
        tool_call_id=tool_call_id,
        name=WEB_SEARCH_TOOL,
        id=message_id or _pending_message_id(tool_call_id),
        additional_kwargs={
            "websearch": True,
            "network_attempted": network_attempted,
            "ui_visible": False,
        },
        **fields,
    )


def _unavailable(pending: dict[str, Any], *, attempted: bool = False) -> ToolMessage:
    """统一的“数据不可得”回执；不暴露审批、额度或服务名."""
    return _paired_message(
        pending,
        {
            "status": "unavailable",
            "success": False,
            "error_code": "websearch_unavailable",
            "message": GENERIC_UNAVAILABLE,
        },
        network_attempted=attempted,
    )


def _pending_items(value: Any) -> list[dict[str, Any]]:
    """兼容单条旧检查点和排队后的列表."""
    if isinstance(value, dict) and value.get("tool_call_id"):
        return [value]
    if isinstance(value, list):
        return [
            item
            for item in value
            if isinstance(item, dict) and item.get("tool_call_id")
        ]
    return []


def _queue_pending(state: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    """把本次申请接在队尾。同一批的多次申请由执行器串行调用本函数."""
    return [*_pending_items(state.get("pending_websearch")), item]


def _websearch_enabled() -> bool:
    """缺少密钥时失败关闭：不登记审批，也不外呼."""
    return bool((app_config.DEEPSEEK_API_KEY or "").strip())


@tool(WEB_SEARCH_TOOL)
async def web_search(
    query: str,
    reason: str,
    runtime: ToolRuntime,
    config: RunnableConfig,
) -> Command | ToolMessage:
    """申请联网搜索补充 MCP 与知识库未取得的数据.

    仅在政策公告、新闻背景或现有工具无法取得所需数据（包括真实数字）时调用。
    query 不超过 128 个字符。首次调用会请求用户批准；一次批准覆盖本轮最多 3 次
    实际外呼。不要用它替代知识库法规检索，也不要在已能取得数据时调用。
    summary 是检索摘要；高相关结果可能另有 content_excerpt。精确数字优先依据
    content_excerpt。返回结果中的编号只能原样引用。
    """
    del config  # 工具本身不读取运行配置；身份与预算由 gate 处理。
    state = runtime.state or {}
    tool_call_id = runtime.tool_call_id or ""
    pending = {"tool_call_id": tool_call_id, "query": query, "reason": reason}

    value = (query or "").strip()
    explanation = (reason or "").strip()
    if (
        not value
        or len(value) > MAX_QUERY_CHARS
        or not explanation
        or len(explanation) > MAX_REASON_CHARS
    ):
        return _paired_message(
            pending,
            {
                "status": "invalid_query",
                "success": False,
                "error_code": "websearch_invalid_query",
                "message": _INVALID_QUERY,
            },
            status="error",
        )

    if not _websearch_enabled():
        logger.warning("basic-qa websearch disabled: missing DEEPSEEK_API_KEY")
        return _unavailable(pending)

    usable_state = _normalized_budget_state(state)
    if usable_state["denied"] or usable_state["used"] >= WEBSEARCH_BUDGET:
        return _unavailable(pending)

    return Command(
        update={
            "pending_websearch": _queue_pending(
                state,
                {
                    "tool_call_id": tool_call_id,
                    "query": value,
                    "reason": explanation,
                },
            ),
            "messages": [
                _paired_message(
                    pending,
                    {"status": "pending", "message": "联网搜索请求已登记。"},
                    message_id=_pending_message_id(tool_call_id),
                )
            ],
        }
    )


def _normalized_budget_state(state: dict[str, Any]) -> dict[str, Any]:
    """按当前轮 key 读取预算；新轮或旧检查点一律从零开始."""
    turn_key = str(state.get("bqa_turn_key") or "")
    current = str(state.get("websearch_turn_key") or "")
    if current != turn_key:
        return {"used": 0, "granted": False, "denied": False, "sources": []}
    used = state.get("websearch_used")
    return {
        "used": used if isinstance(used, int) and used >= 0 else 0,
        "granted": state.get("websearch_granted") is True,
        "denied": state.get("websearch_denied") is True,
        "sources": [
            item
            for item in (state.get("websearch_sources") or [])
            if isinstance(item, dict) and isinstance(item.get("n"), int)
        ],
    }


def _budget_updates(state: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any]:
    """把预算与来源显式写回，保证新轮重置和恢复不重置."""
    return {
        "websearch_turn_key": str(state.get("bqa_turn_key") or ""),
        "websearch_used": budget["used"],
        "websearch_granted": budget["granted"],
        "websearch_denied": budget["denied"],
        "websearch_sources": budget["sources"],
    }


def _finite_score(value: Any) -> float | None:
    """读取排序分；布尔值在 Python 里也是 int，不能当成 0 或 1."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _query_terms(query: str) -> list[str]:
    """抽出可在正文中定位的词，去掉站点、语言和日期运算符."""
    terms = re.findall(r'"([^"]+)"', query)
    plain = re.sub(r'"[^"]*"', " ", query)
    plain = re.sub(r"(?:-?site:|lang:|after:|before:)\S+", " ", plain)
    for token in plain.split():
        if token.startswith("-") or len(token) < 2:
            continue
        terms.append(token)
    ordered: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            ordered.append(term)
    return ordered


def _readable_content(content: str) -> str:
    """去掉纯链接行，避免摘录停在导航上."""
    lines: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or _LINK_ONLY_LINE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _excerpt(content: str, query: str, limit: int) -> tuple[str, bool]:
    """在查询词附近截取正文；没有命中时保留清洗后的开头."""
    cleaned = _readable_content(content)
    if not cleaned:
        return "", False
    truncated = len(cleaned) > limit
    if not truncated:
        return cleaned, False
    lowered = cleaned.lower()
    hits: list[int] = []
    for term in _query_terms(query):
        found = lowered.find(term.lower())
        if found >= 0:
            hits.append(found)
    start = 0
    if hits:
        hit = min(hits)
        start = max(0, hit - 200)
        newline = cleaned.rfind("\n", start, hit)
        if newline >= start:
            start = newline + 1
    return cleaned[start : start + limit], True


def _public_result(number: int, result: Any) -> dict[str, Any]:
    """模型可见字段。长文本稍后按预算裁剪，来源列表不走这里."""
    item: dict[str, Any] = {
        "n": number,
        "title": str(getattr(result, "title", "") or ""),
        "url": str(getattr(result, "url", "") or ""),
        "site": str(getattr(result, "site", "") or ""),
        "date": str(getattr(result, "date", "") or ""),
    }
    score = _finite_score(getattr(result, "rerank_score", None))
    if score is not None:
        item["rerank_score"] = score
    authority = getattr(result, "authority", None)
    if isinstance(authority, int) and not isinstance(authority, bool):
        item["authority"] = authority
    summary = str(getattr(result, "summary", "") or "").strip()[:MAX_SUMMARY_CHARS]
    snippet = str(getattr(result, "snippet", "") or "").strip()[:MAX_SNIPPET_CHARS]
    if summary:
        item["summary"] = summary
    elif snippet:
        item["snippet"] = snippet
    return item


def _attach_excerpts(
    query: str, numbered: list[dict[str, Any]], results: list[Any]
) -> None:
    """只给排序分达标的前两条附正文摘录，再把摘要和摘录压进总字数."""
    candidates: list[tuple[int, float, int, str]] = []
    seen_numbers: set[int] = set()
    for index, (item, result) in enumerate(zip(numbered, results, strict=False)):
        number = item.get("n")
        score = item.get("rerank_score")
        if (
            not isinstance(number, int)
            or number in seen_numbers
            or not isinstance(score, (int, float))
            or float(score) < CONTENT_SCORE_MIN
        ):
            continue
        content = getattr(result, "content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        seen_numbers.add(number)
        candidates.append((index, float(score), number, content))
    candidates.sort(key=lambda row: (-row[1], row[0]))
    excerpts: dict[int, tuple[str, bool]] = {}
    for _, _, number, content in candidates[:MAX_CONTENT_EXCERPTS]:
        excerpt, truncated = _excerpt(content, query, CONTENT_EXCERPT_CHARS)
        if excerpt:
            excerpts[number] = (excerpt, truncated)
    for item in numbered:
        number = item.get("n")
        if isinstance(number, int) and number in excerpts:
            excerpt, truncated = excerpts[number]
            item["content_excerpt"] = excerpt
            item["content_truncated"] = truncated
    _fit_model_text(numbered)


def _reading_key(item: dict[str, Any]) -> str:
    if item.get("summary"):
        return "summary"
    if item.get("snippet"):
        return "snippet"
    return ""


def _fit_model_text(items: list[dict[str, Any]]) -> None:
    """摘要优先；正文摘录只使用剩余字数，同一编号的重复行保持一致."""
    order: list[int] = []
    reading: dict[int, tuple[str, str]] = {}
    excerpts: dict[int, tuple[str, bool]] = {}
    for item in items:
        number = item.get("n")
        if not isinstance(number, int) or number in reading:
            continue
        order.append(number)
        key = _reading_key(item)
        reading[number] = (key, str(item.get(key) or "") if key else "")
        excerpt = item.get("content_excerpt")
        if isinstance(excerpt, str) and excerpt:
            excerpts[number] = (excerpt, item.get("content_truncated") is True)
        else:
            excerpts[number] = ("", False)

    remaining = MAX_MODEL_TEXT_CHARS - sum(len(text) for _, text in reading.values())
    if remaining < 0:
        for number in reversed(order):
            key, text = reading[number]
            if not text:
                continue
            overflow = -remaining
            if len(text) <= overflow:
                reading[number] = (key, "")
                remaining += len(text)
            else:
                reading[number] = (key, text[: len(text) - overflow])
                remaining = 0
            if remaining >= 0:
                break

    kept: dict[int, tuple[str, bool]] = {}
    for number in order:
        excerpt, was_truncated = excerpts[number]
        if not excerpt or remaining <= 0:
            kept[number] = ("", False)
            continue
        if len(excerpt) > remaining:
            kept[number] = (excerpt[:remaining], True)
            remaining = 0
        else:
            kept[number] = (excerpt, was_truncated)
            remaining -= len(excerpt)

    for item in items:
        number = item.get("n")
        if not isinstance(number, int) or number not in reading:
            item.pop("content_excerpt", None)
            item.pop("content_truncated", None)
            continue
        key, text = reading[number]
        item.pop("summary", None)
        item.pop("snippet", None)
        if key and text:
            item[key] = text
        excerpt, truncated = kept[number]
        if excerpt:
            item["content_excerpt"] = excerpt
            item["content_truncated"] = truncated
        else:
            item.pop("content_excerpt", None)
            item.pop("content_truncated", None)


def _number_results(
    sources: list[dict[str, Any]],
    results: list[Any],
    retrieved_at: str,
    query: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按唯一 URL 分配连续编号；重复 URL 复用已有编号.

    持久来源只保留角标所需的标题、站点、URL 和日期。摘要与正文只出现在
    当次工具回执里。
    """
    by_url = {str(item.get("url") or ""): item for item in sources}
    next_number = (
        max(
            (item["n"] for item in sources if isinstance(item.get("n"), int)), default=0
        )
        + 1
    )
    numbered: list[dict[str, Any]] = []
    updated = list(sources)
    for result in results:
        url = str(getattr(result, "url", "") or "")
        existing = by_url.get(url)
        if existing is not None:
            number = int(existing["n"])
        else:
            number = next_number
            next_number += 1
            entry = {
                "n": number,
                "title": str(getattr(result, "title", "") or ""),
                "url": url,
                "site": str(getattr(result, "site", "") or ""),
                "date": str(getattr(result, "date", "") or ""),
                "retrieved_at": retrieved_at,
            }
            updated.append(entry)
            by_url[url] = entry
        numbered.append(_public_result(number, result))
    _attach_excerpts(query, numbered, results)
    return updated, numbered


async def websearch_gate(
    state: dict[str, Any], config: RunnableConfig
) -> dict[str, Any]:
    """审批并执行队首的一次联网申请；其余申请留在队列里继续处理."""
    queue = _pending_items(state.get("pending_websearch"))
    if not queue:
        return {}
    pending = queue[0]
    rest = queue[1:] or None

    budget = _normalized_budget_state(state)
    updates = _budget_updates(state, budget)

    if budget["denied"]:
        return {
            **updates,
            "pending_websearch": rest,
            "messages": [_unavailable(pending)],
        }

    if budget["used"] >= WEBSEARCH_BUDGET:
        return {
            **updates,
            "pending_websearch": rest,
            "messages": [_unavailable(pending)],
        }

    if not budget["granted"]:
        remaining = WEBSEARCH_BUDGET - budget["used"]
        emit_event(
            config,
            "websearch_approval_required",
            provider=DEEPSEEK_PROVIDER,
            remaining=remaining,
        )
        decision = interrupt(
            {
                "type": "websearch_approval",
                "provider": DEEPSEEK_PROVIDER,
                "query": str(pending.get("query") or ""),
                "reason": str(pending.get("reason") or ""),
                "remaining": remaining,
                "used": budget["used"],
            }
        )
        approved = isinstance(decision, dict) and decision.get("approved") is True
        emit_event(
            config,
            "websearch_approval",
            provider=DEEPSEEK_PROVIDER,
            decision="approved" if approved else "denied",
            remaining=remaining,
        )
        if not approved:
            return {
                **updates,
                "websearch_denied": True,
                "pending_websearch": rest,
                "messages": [_unavailable(pending)],
            }
        budget["granted"] = True
        updates = _budget_updates(state, budget)

    used = budget["used"] + 1
    remaining = WEBSEARCH_BUDGET - used
    retrieved_at = datetime.now(UTC).isoformat()
    query = str(pending.get("query") or "")

    # 每次实际外呼都计数：失败、超时与空结果同样占用 3 次硬上限，不自动重试。
    emit_event(
        config,
        "websearch_call",
        source=DEEPSEEK_SOURCE,
        provider=DEEPSEEK_PROVIDER,
        status="attempted",
        used=used,
        remaining=remaining,
    )

    outcome = "ok"
    results: list[Any] = []
    try:
        results = await search_web(query, count=RESULT_COUNT)
    except DeepSeekSearchError as exc:
        outcome = exc.category
        logger.warning("basic-qa websearch failed category={}", exc.category)
    except ValueError:
        outcome = "invalid_query"
    except Exception as exc:  # noqa: BLE001 - 外部失败只用通用话术
        outcome = "transport_error"
        logger.warning("basic-qa websearch failed class={}", type(exc).__name__)

    final_budget = {**budget, "used": used, "granted": True}
    final_updates = _budget_updates(state, final_budget)
    billable = outcome == "ok" and bool(results)
    emit_event(
        config,
        "websearch_external_cost",
        source=DEEPSEEK_SOURCE,
        amount=EXTERNAL_COST_PER_CALL if billable else EXTERNAL_COST_NONE,
        currency=EXTERNAL_COST_CURRENCY,
        measurement_status="measured",
        usage_unit="call",
        used=used,
        remaining=remaining,
    )

    if not billable:
        emit_event(
            config,
            "websearch_call",
            source=DEEPSEEK_SOURCE,
            provider=DEEPSEEK_PROVIDER,
            status="failed" if outcome != "ok" else "empty",
            used=used,
            remaining=remaining,
            results=len(results),
        )
        logger.info(
            "basic-qa websearch completed outcome={} results={} used={}",
            outcome,
            len(results),
            used,
        )
        return {
            **final_updates,
            "pending_websearch": rest,
            "messages": [_unavailable(pending, attempted=True)],
        }

    sources, numbered = _number_results(budget["sources"], results, retrieved_at, query)
    final_budget["sources"] = sources
    final_updates = _budget_updates(state, final_budget)
    emit_event(
        config,
        "websearch_call",
        source=DEEPSEEK_SOURCE,
        provider=DEEPSEEK_PROVIDER,
        status="ok",
        used=used,
        remaining=remaining,
        results=len(numbered),
    )
    logger.info(
        "basic-qa websearch completed outcome=ok results={} used={} remaining={}",
        len(numbered),
        used,
        remaining,
    )
    return {
        **final_updates,
        "pending_websearch": rest,
        "messages": [
            _paired_message(
                pending,
                {
                    "status": "ok",
                    "success": True,
                    "provider": "deepseek",
                    "retrieved_at": retrieved_at,
                    "used": used,
                    "remaining": remaining,
                    "results": numbered,
                    "notice": (
                        "summary 是检索摘要。rerank_score 不低于 1.5 的前两条"
                        "可能带 content_excerpt，content_truncated 为真表示正文被截断。"
                        "精确数字优先依据 content_excerpt；只有 summary 时要说明依据是检索摘要。"
                        "authority 为 -1 表示来源分量未知。"
                        "引用时使用结果中的 n 写作 [web:n]，只能引用本结果中出现的编号。"
                    ),
                },
                network_attempted=True,
            )
        ],
    }
