"""Fenced code block 自动识别并封装 Artifact 事件的中间件.

当 Agent 或工具在文本消息中通过 fenced code block 直接写出
`` ```html `` / `` ```markdown `` / `` ```svg `` 源码时，
本中间件将其封装为通用 Artifact 事件并通过 runtime.stream_writer
流式推送到前端；同时用简短的占位摘要替换原始消息内容，
避免大段 HTML / Markdown / SVG 源码污染后续 LLM 上下文。

当工具显式调用 ``create_artifact`` 时，本中间件跳过对其结果的二次处理，
避免与工具内部的推送逻辑重复。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

# 前端 / history 恢复用的 artifact_ref 注释私有前缀（HTML 安全注释）
_AIR_AGENT_ARTIFACT_MARKER = "__AIR_AGENT_ARTIFACT__"
_ARTIFACT_REF_COMMENT_RE = re.compile(
    rf"<!--\s*{_AIR_AGENT_ARTIFACT_MARKER}\s+(?P<json>\{{.*?\}})\s*-->",
    re.DOTALL,
)

# 最小 content 长度：低于该值时不在 AIMessage 里额外塞 artifact_ref 注释
_ARTIFACT_REF_EMBED_MIN_LEN = 200

# fence 语言 → 对应 Artifact 的 content_type
_FENCE_CONTENT_TYPES: dict[str, str] = {
    "html": "html",
    "markdown": "markdown",
    "md": "markdown",
    "svg": "svg",
}

# 单个 fenced code block 最短内容长度（字符），避免误识别短代码示例
_MIN_CODE_BLOCK_LENGTH = 50

# 标题提取最长字符数
_TITLE_MAX_LENGTH = 60


def _compile_fence_pattern(lang: str) -> re.Pattern[str]:
    """为指定 fence 语言编译正则匹配模式.

    - 捕获分组:
      group(1) = 开 fence 行（完整，用于后续解析 artifact-title / artifact-open_in 属性）
      group(2) = fenced code block 内部的原始内容
    - 允许 fence 前后存在 0~3 个前导空格（与 GitHub/CommonMark 缩进代码块一致）
    - 开 fence 后允许存在属性，如 `` ```html artifact-title="报表A" ``
    - 结束 fence 要求单独一行
    """
    escaped = re.escape(lang)
    # 注意：不用 f-string 写正则，避免 {0,3} 被当作 tuple 字面量
    pattern = (
        "^( {0,3}```"
        + escaped
        + r"\b.*?)\n([\s\S]*?)\n {0,3}```\s*$"
    )
    return re.compile(pattern, re.MULTILINE | re.IGNORECASE)


# 解析开 fence 行中的 artifact-title="..." / artifact-open_in="..." 属性
_OPEN_FENCE_ATTR_RE = re.compile(
    r'artifact-(?P<key>title|open_in)\s*=\s*(?:"(?P<dq>[^"]+)"|\'(?P<sq>[^\']+)\'|(?P<bare>[^\s]+))',
    re.IGNORECASE,
)


def _parse_open_fence_attrs(open_fence_line: str) -> dict[str, str]:
    """从开 fence 行中提取 artifact-title / artifact-open_in.

    返回 dict，可能包含 key: title / open_in。缺失时为空 dict。
    """
    result: dict[str, str] = {}
    for m in _OPEN_FENCE_ATTR_RE.finditer(open_fence_line):
        key = m.group("key").lower()
        value = m.group("dq") or m.group("sq") or m.group("bare") or ""
        if value:
            result[key] = value
    return result


_FENCE_PATTERNS: dict[str, re.Pattern[str]] = {
    lang: _compile_fence_pattern(lang) for lang in _FENCE_CONTENT_TYPES
}

_TITLE_EXTRACTORS: dict[str, re.Pattern[str]] = {
    "html": re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE),
    "markdown": re.compile(r"^\s*#{1,6}\s+(.+)$", re.MULTILINE),
    "svg": re.compile(r"<desc[^>]*>([^<]+)</desc>", re.IGNORECASE),
}


class ArtifactMiddleware(AgentMiddleware):
    """Fenced code block 扫描 → Artifact 事件推送 + 上下文压缩中间件."""

    @staticmethod
    def _make_artifact_id_and_hash(content: str) -> tuple[str, str]:
        """生成 artifact_id + 内容 SHA-256，供幂等恢复用.

        artifact_id 格式: ``art_{uuid12}``，同一内容每次扫描都是新的 artifact_id，
        避免"多次调用产生的同一内容 Artifact 被错误合并"。
        """
        artifact_id = f"art_{uuid.uuid4().hex[:12]}"
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return artifact_id, content_sha256

    @staticmethod
    def _embed_artifact_ref_comment(text: str, artifact_ref: dict[str, Any]) -> str:
        """在占位文本末尾追加 artifact_ref HTML 注释，供前端/历史恢复.

        采用私有前缀 marker，前端用对应正则精确提取；
        注释在 Markdown/HTML 渲染时都会被过滤，不会展示给用户。
        """
        try:
            payload = json.dumps(artifact_ref, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return text
        comment = f"<!-- {_AIR_AGENT_ARTIFACT_MARKER} {payload} -->"
        # 把注释贴在占位块的末尾，避免污染正常阅读
        if text.endswith("\n"):
            return text + comment + "\n"
        return text + "\n" + comment + "\n"

    @staticmethod
    def extract_artifact_refs(text: str) -> list[dict[str, Any]]:
        """从 AIMessage/ToolMessage 字符串中提取所有嵌入的 artifact_ref.

        对外暴露的静态方法，前端 hydrateFromHistory 逻辑的后端参考实现
        （前端用 TS 写对应的正则 + JSON.parse）。
        """
        if not isinstance(text, str):
            return []
        out: list[dict[str, Any]] = []
        for m in _ARTIFACT_REF_COMMENT_RE.finditer(text):
            raw = m.group("json")
            try:
                ref = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(ref, dict) and ref.get("artifact_id"):
                out.append(ref)
        # ToolMessage JSON 中可能在 artifact_ref 字段存
        return out

    def _extract_title(
        self, *, content_type: str, raw: str, fallback_title: str
    ) -> str:
        """从源码首段内容中提取标题，失败时返回兜底标题."""
        pattern = _TITLE_EXTRACTORS.get(content_type)
        if pattern is not None:
            match = pattern.search(raw)
            if match:
                title = match.group(1).strip()
                if title:
                    return title[:_TITLE_MAX_LENGTH]

        for line in raw.splitlines():
            stripped = line.strip()
            if stripped and len(stripped) >= 4:
                return stripped[:_TITLE_MAX_LENGTH]

        return fallback_title[:_TITLE_MAX_LENGTH]

    def _build_artifact_event(
        self,
        *,
        title: str,
        content_type: str,
        content: str,
        open_in: str = "canvas_window",
        auto_detected: bool = False,
        artifact_id: str | None = None,
        content_sha256: str | None = None,
    ) -> dict[str, Any]:
        """构造通用 Artifact 事件（前端 store.add 使用的结构）.

        补充 artifact_id / content_sha256 供前端幂等恢复与 persist 使用。
        """
        if artifact_id is None or content_sha256 is None:
            _id, _hash = self._make_artifact_id_and_hash(content)
            artifact_id = artifact_id or _id
            content_sha256 = content_sha256 or _hash
        return {
            "type": "artifact",
            "artifact_id": artifact_id,
            "content_sha256": content_sha256,
            "title": title,
            "content_type": content_type,
            "content": content,
            "open_in": open_in,
            "auto_detected": auto_detected,
        }

    def _scan_and_replace_code_blocks(
        self,
        text: str,
        *,
        default_open_in: str = "canvas_window",
    ) -> str:
        """扫描文本中的 fenced code block，替换为摘要占位并嵌入 artifact_ref 注释.

        返回替换后的文本，供调用方写回消息上下文。
        artifact_ref 通过 HTML 注释嵌入文本中，供前端从历史消息恢复。
        """
        if not isinstance(text, str) or len(text) < _MIN_CODE_BLOCK_LENGTH:
            return text

        replaced = text
        touched: bool = False

        for lang, pattern in _FENCE_PATTERNS.items():
            content_type = _FENCE_CONTENT_TYPES[lang]
            for match in list(pattern.finditer(text)):
                # 新 pattern 中: group(1)=开fence行, group(2)=原始内容
                open_fence_line = match.group(1)
                raw = match.group(2)
                if len(raw) < _MIN_CODE_BLOCK_LENGTH:
                    continue

                # 优先从开 fence 行读取 artifact-title / artifact-open_in 属性
                fence_attrs = _parse_open_fence_attrs(open_fence_line)
                raw_title = fence_attrs.get("title")
                override_open_in = fence_attrs.get("open_in")

                title = (
                    raw_title
                    if raw_title
                    else self._extract_title(
                        content_type=content_type,
                        raw=raw,
                        fallback_title=f"{content_type} Artifact",
                    )
                )
                open_in = override_open_in or default_open_in

                artifact_id, content_sha256 = self._make_artifact_id_and_hash(raw)
                artifact_ref = {
                    "artifact_id": artifact_id,
                    "content_sha256": content_sha256,
                    "title": title,
                    "content_type": content_type,
                    "content": raw,
                    "open_in": open_in,
                    "auto_detected": True,
                }

                placeholder = (
                    f"\n> *[已在画布中生成 Artifact：{title}（{content_type}），"
                    "用户可在画布窗口中查看完整内容]*\n"
                )
                if len(raw) >= _ARTIFACT_REF_EMBED_MIN_LEN:
                    placeholder = self._embed_artifact_ref_comment(placeholder, artifact_ref)
                replaced = replaced.replace(match.group(0), placeholder)
                touched = True

        return replaced if touched else text

    def _result_to_text(self, result: Any) -> tuple[str, Any]:
        """尝试从 ToolMessage / dict / str 结果中提取纯文本片段用于扫描.

        返回 (text, payload_kind)：
        - payload_kind == "str": 结果是普通字符串，写回时直接替换整个字符串
        - payload_kind == "toolmessage": ToolMessage.content 是字符串，写回时 model_copy
        - payload_kind == "dict-text": dict["text"] 是字符串，写回时改 dict.text
        - payload_kind == "toolmessage-parts": ToolMessage.content 是 list，跳过压缩
        - payload_kind == None: 不支持扫描，直接原样返回
        """
        if isinstance(result, ToolMessage):
            if isinstance(result.content, str):
                return result.content, "toolmessage"
            return "", "toolmessage-parts"
        if isinstance(result, dict) and isinstance(result.get("text"), str):
            return result["text"], "dict-text"
        if isinstance(result, str):
            return result, "str"
        return "", None

    def _write_back_result(self, result: Any, *, kind: Any, new_text: str) -> Any:
        """根据 kind 将替换后的文本写回原始 result 形态."""
        if kind == "toolmessage":
            return result.model_copy(update={"content": new_text})
        if kind == "dict-text":
            return {**result, "text": new_text}
        if kind == "str":
            return new_text
        return result

    def _handle_create_artifact_result(
        self,
        request: ToolCallRequest,
        result: Any,
    ) -> Any:
        """处理 create_artifact 工具返回值：压缩 ToolMessage 并保留 artifact_ref.

        create_artifact_tool_fn 返回结构化 dict（被 StructuredTool 序列化为
        ToolMessage.content JSON 字符串），其中 ``artifact_ref`` / ``artifact``
        字段携带完整 payload（含 artifact_id + 完整 content）。
        本方法提取该 payload，把 ToolMessage.content 压缩为"摘要 + artifact_ref 内嵌"——
        artifact_ref 保留完整 payload 便于 checkpointer 持久化后前端从历史恢复。

        注意: 不再通过 stream_writer 推送 custom channel 事件（v2 协议不支持），
        前端改为从 messages channel 中读取 ToolMessage.content 的 artifact_ref.
        """
        import json as _json

        # 从 ToolMessage / dict / str 中提取 JSON payload
        text, kind = self._result_to_text(result)
        payload: dict[str, Any] | None = None
        if text:
            try:
                payload = _json.loads(text)
            except (ValueError, TypeError):
                payload = None

        if not isinstance(payload, dict):
            return result
        # artifact_ref 是新字段，artifact 是旧字段；任一存在就处理
        artifact = payload.get("artifact_ref") or payload.get("artifact")
        if not isinstance(artifact, dict):
            return result

        summary = (
            payload.get("data", {}).get("summary")
            or f"已在画布中生成 Artifact：{artifact.get('title', '?')}"
        )

        # 压缩后的 ToolMessage：summary 简短 + artifact_ref 内嵌保留完整恢复依据
        compressed = _json.dumps(
            {
                "code": payload.get("code", 200),
                "success": payload.get("success", True),
                "summary": summary,
                # artifact_ref 始终保留（含完整 content），供前端 hydrate 恢复
                "artifact_ref": artifact,
                "artifact_id": artifact.get("artifact_id"),
            },
            ensure_ascii=False,
        )
        return self._write_back_result(result, kind=kind, new_text=compressed)

    def _handle_tool_result(
        self,
        request: ToolCallRequest,
        result: Any,
    ) -> Any:
        """拦截 ToolMessage：扫描 fenced code block → 压缩内容并嵌入 artifact_ref.

        create_artifact 工具结果在此处提取 artifact payload 并压缩 ToolMessage.
        其他工具结果中的 fenced code block 会被扫描并替换为摘要占位.
        """
        tool_name = str(request.tool_call.get("name") or "")
        if tool_name == "create_artifact":
            return self._handle_create_artifact_result(request, result)

        text, kind = self._result_to_text(result)
        if kind is None or len(text) < _MIN_CODE_BLOCK_LENGTH:
            return result

        new_text = self._scan_and_replace_code_blocks(text)
        if new_text is text:
            return result
        return self._write_back_result(result, kind=kind, new_text=new_text)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        """同步工具调用拦截."""
        result = handler(request)
        return self._handle_tool_result(request, result)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> ToolMessage | Any:
        """异步工具调用拦截."""
        result = await handler(request)
        return self._handle_tool_result(request, result)

    # ─── 公开的辅助函数：供 LangGraph 业务节点在 AIMessage / 任意文本上复用 ───

    def scan_text(
        self,
        text: str,
        *,
        default_open_in: str = "canvas_window",
    ) -> str:
        """对任意文本字符串执行 fenced code block 扫描与压缩.

        LangGraph 显式节点流（无中间件链）的 call_model 节点中可直接调用：
        传入 AIMessage.content，返回压缩后的 content 写回状态，
        artifact_ref 通过 HTML 注释嵌入文本供前端历史恢复.
        """
        return self._scan_and_replace_code_blocks(
            text,
            default_open_in=default_open_in,
        )
