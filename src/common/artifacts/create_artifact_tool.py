"""create_artifact 工具：Agent 自主生成 Artifact 画布的显式入口.

当 Agent 需要生成独立画布内容（报表、页面、文档等）时，
可显式调用本工具，返回结构化 Artifact payload（含完整内容），
由调用方消费；同时避免 HTML / Markdown / SVG 大段源码
重新进入 LLM 上下文。

（注：原先由中间件拦截层负责的 stream_writer 事件推送与
fenced code block 自动识别能力已随中间件移除，待重新设计。）
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

ArtifactContentType = Literal[
    "html", "markdown", "svg", "text", "iframe_url", "image_url"
]
ArtifactOpenIn = Literal["canvas_window", "inline_below", "modal", "sidebar"]

_CONTENT_TYPES: set[str] = {
    "html",
    "markdown",
    "svg",
    "text",
    "iframe_url",
    "image_url",
}
_OPEN_IN_MODES: set[str] = {"canvas_window", "inline_below", "modal", "sidebar"}


class CreateArtifactInput(BaseModel):
    """create_artifact 工具的参数 schema."""

    title: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="画布标题，显示在 Tab / 窗口标题栏。",
    )
    content_type: ArtifactContentType = Field(
        ...,
        description=(
            "内容类型：html=完整HTML页面（iframe沙箱渲染），"
            "markdown=Markdown文档（react-markdown渲染），"
            "svg=矢量图，text=纯文本，"
            "iframe_url=直接嵌入的URL，image_url=图片URL。"
        ),
    )
    content: str = Field(
        ...,
        description="原始内容字符串。对应 content_type：HTML源码 / MD正文 / SVG源码 / 纯文本 / URL。",
    )
    open_in: ArtifactOpenIn = Field(
        default="canvas_window",
        description=(
            "画布打开方式：canvas_window=独立画布窗口（默认），"
            "inline_below=嵌入对话消息下方，modal=模态弹窗，sidebar=右侧边栏。"
        ),
    )


def create_artifact_tool_fn(
    title: str,
    content_type: str,
    content: str,
    open_in: str = "canvas_window",
    **_: Any,
) -> dict[str, Any]:
    """create_artifact 工具执行函数.

    仅做参数校验并返回结构化结果（含 artifact_ref + artifact_id），
    供调用方消费或持久化恢复。

    参数:
        title: 画布标题.
        content_type: 内容类型（html/markdown/svg/text/iframe_url/image_url）.
        content: 原始内容字符串.
        open_in: 画布打开方式（canvas_window/inline_below/modal/sidebar）.

    返回:
        含 artifact_ref（完整 payload + artifact_id）的结构化结果，
        供调用方消费，同时保留 artifact_ref 到 ToolMessage
        作为 checkpointer 持久化后的恢复依据。
    """
    # 1) 参数校验
    try:
        validated = CreateArtifactInput(
            title=title,
            content_type=content_type,  # type: ignore[arg-type]
            content=content,
            open_in=open_in,  # type: ignore[arg-type]
        )
    except ValidationError as exc:
        errs = "; ".join(e.get("msg", str(e)) for e in exc.errors())
        return {
            "code": 400,
            "success": False,
            "message": (
                f"create_artifact 参数校验失败：{errs}。"
                f"支持的 content_type={sorted(_CONTENT_TYPES)}，"
                f"支持的 open_in={sorted(_OPEN_IN_MODES)}。"
            ),
        }

    # 2) 生成 artifact_id：同一内容的幂等恢复用 UUID + 内容哈希
    artifact_id = f"art_{uuid.uuid4().hex[:12]}"
    content_sha256 = hashlib.sha256(validated.content.encode("utf-8")).hexdigest()

    # 3) artifact_ref：checkpointer 持久化 + 前端恢复的完整依据
    #    （保留完整 content，因为 64KB 以内对大多数报表/Markdown 完全够用）
    artifact_ref = {
        "artifact_id": artifact_id,
        "content_sha256": content_sha256,
        "title": validated.title,
        "content_type": validated.content_type,
        "content": validated.content,
        "open_in": validated.open_in,
        # 显式工具调用 = 非自动检测
        "auto_detected": False,
    }

    summary = (
        f"已在画布中生成 [{validated.title}] "
        f"（{validated.content_type}，{validated.open_in} 模式）。"
        "该 Artifact 已直接发送给用户端渲染，回答中无需重复输出源码。"
    )

    return {
        "code": 200,
        "success": True,
        "artifact_id": artifact_id,
        "content_sha256": content_sha256,
        # artifact_ref 是持久化恢复与前端消费的双重依据
        "artifact": artifact_ref,
        "artifact_ref": artifact_ref,
        "data": {"summary": summary, "artifact_id": artifact_id},
    }


def build_create_artifact_tool() -> Any:
    """将 create_artifact 包装为 StructuredTool 实例.

    调用方在组装业务工具列表时直接 append 返回值即可。
    """
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(
        func=create_artifact_tool_fn,
        name="create_artifact",
        description=(
            "当需要在独立画布窗口中渲染内容时调用。"
            "支持输出 HTML 页面（含内联 CSS/SVG/CDN JS，如 ECharts/Chart.js 图表）、"
            "Markdown 文档、SVG 图形、或直接嵌入的 URL / 图片。"
            "系统会自动在用户端打开画布并渲染，调用后回答中无需再写代码块重复展示。"
        ),
        args_schema=CreateArtifactInput,
    )
