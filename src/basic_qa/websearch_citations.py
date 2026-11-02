"""Basic QA 联网来源编号与回答引用后处理.

编号由服务端在 gate 中分配；本模块只做两件事：

- 过滤正文：保留已分配的 ``[web:n]``，移除未知编号；
- 追加文末来源：只列出正文实际引用过的来源，格式为标题/站点/URL/日期。

流式输出使用 ``push``/``flush``，避免把跨增量切开的 ``[web:`` 前缀当正文发布。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

WEB_CITE_RE = re.compile(r"\[web:(\d+)\]")
# 只可能出现在增量尾部的未完成标记；普通正文不会以 "[web" 结尾。
_PARTIAL_RE = re.compile(r"\[web:?\d*$")
_TITLE_ESCAPE_RE = re.compile(r"([\[\]])")
# 模型自写的来源段落：标题行（加粗或 Markdown 标题）才算，正文提到不拦。
_REFERENCE_HEADING_RE = re.compile(
    r"(?m)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*)?互联网来源(?:\*\*)?[ \t]*$"
)
_TRAILING_SEPARATOR_RE = re.compile(r"(?m)^[ \t]*---[ \t]*$")


def _known_numbers(sources: Iterable[Any]) -> dict[int, dict[str, Any]]:
    known: dict[int, dict[str, Any]] = {}
    for item in sources or ():
        if not isinstance(item, dict):
            continue
        number = item.get("n")
        if isinstance(number, int) and number > 0 and number not in known:
            known[number] = item
    return known


def _link(title: str, url: str) -> str:
    safe_title = _TITLE_ESCAPE_RE.sub(r"\\\1", title or url)
    return f"[{safe_title}](<{url}>)"


class WebAnswerCitations:
    """一次终答的引用过滤器；只记录本次正文实际使用的编号."""

    def __init__(self, sources: Iterable[Any] = ()) -> None:
        """绑定本轮已分配的来源编号."""
        self._sources = _known_numbers(sources)
        self._used: set[int] = set()
        self._pending = ""
        self._suppress_references = False

    @property
    def used_numbers(self) -> tuple[int, ...]:
        """正文实际引用过的来源编号（升序）."""
        return tuple(sorted(self._used))

    def _replace(self, text: str) -> str:
        def substitute(match: re.Match[str]) -> str:
            number = int(match.group(1))
            if number not in self._sources:
                return ""
            self._used.add(number)
            return match.group(0)

        return WEB_CITE_RE.sub(substitute, text)

    @staticmethod
    def _cut_model_references(text: str) -> tuple[str, bool]:
        """在模型的来源标题处截断，返回 (正文, 是否命中)."""
        heading = _REFERENCE_HEADING_RE.search(text)
        if heading is None:
            return text, False
        body = text[: heading.start()].rstrip()
        body = _TRAILING_SEPARATOR_RE.sub("", body).rstrip()
        return body, True

    def _consume(self, data: str) -> str:
        """发布一段正文；一旦进入模型自写的来源段，后续内容不再公开."""
        if self._suppress_references:
            return ""
        data, hit = self._cut_model_references(data)
        if hit:
            self._suppress_references = True
        return self._replace(data)

    def push(self, text: str) -> str:
        """处理一段流式增量，返回可安全发布的文本."""
        data = self._pending + text
        self._pending = ""
        partial = _PARTIAL_RE.search(data)
        if partial:
            self._pending = data[partial.start() :]
            data = data[: partial.start()]
        return self._consume(data)

    def flush(self) -> str:
        """结束流式输出时释放尾部缓存."""
        data = self._pending
        self._pending = ""
        return self._consume(data)

    def render(self, text: str) -> str:
        """对完整正文做一次幂等过滤（不改变已记录的引用集合）."""
        body, hit = self._cut_model_references(text or "")
        if hit:
            self._suppress_references = True
        return self._replace(body)

    def _render_references(self, numbers: set[int]) -> str:
        if not numbers:
            return ""
        lines = ["", "", "---", "", "**互联网来源**", ""]
        # 用无序列表并显式写编号：Markdown 有序列表会按出现顺序重新编号，
        # 导致 [web:2]/[web:5] 与列表编号不一致。
        for number in sorted(numbers):
            source = self._sources[number]
            title = str(source.get("title") or source.get("url") or "").strip()
            url = str(source.get("url") or "").strip()
            meta = " · ".join(
                str(value).strip()
                for value in (source.get("site"), source.get("date"))
                if value
            )
            suffix = f" — {meta}" if meta else ""
            lines.append(f"- **[{number}]** {_link(title, url)}{suffix}")
        return "\n".join(lines) + "\n"

    def references(self) -> str:
        """正文引用了来源时追加文末列表；未引用则返回空串."""
        return self._render_references(self._used)

    def references_for(self, text: str) -> str:
        """只按最终正文里仍存在的角标生成列表（防止策略改写后编号悬空）."""
        numbers = {
            int(match.group(1))
            for match in WEB_CITE_RE.finditer(text or "")
            if int(match.group(1)) in self._sources
        }
        return self._render_references(numbers)
