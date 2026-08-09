"""Artifact 自动识别 + 显式工具 单元测试.

- 覆盖 ArtifactMiddleware: fenced code block 扫描 → Artifact 事件推送 + 占位摘要替换
- 覆盖 create_artifact 工具: 显式参数校验 + 自定义 stream 事件推送
"""

from __future__ import annotations

import textwrap
from typing import Any

from langchain_core.messages import AIMessage

from common.artifacts.create_artifact_tool import (
    build_create_artifact_tool,
    create_artifact_tool_fn,
)
from common.middleware.artifact_middleware import ArtifactMiddleware

# 报表内容刻意写得长一点，避免被 _MIN_CODE_BLOCK_LENGTH 阈值过滤
_LONG_HTML = textwrap.dedent(
    """\
    <!doctype html>
    <html lang="zh-CN">
    <head>
      <meta charset="utf-8" />
      <title>北京 2026 月度 PM2.5 趋势</title>
    </head>
    <body>
      <h1>北京 2026 月度 PM2.5 趋势</h1>
      <p>数据来源：空气质量历史数据，单位 μg/m³，按月聚合平均值。</p>
      <table>
        <thead><tr><th>月份</th><th>PM2.5 月均</th><th>等级</th></tr></thead>
        <tbody>
          <tr><td>1月</td><td>58</td><td>良</td></tr>
          <tr><td>2月</td><td>45</td><td>良</td></tr>
          <tr><td>3月</td><td>62</td><td>良</td></tr>
          <tr><td>4月</td><td>71</td><td>轻度污染</td></tr>
          <tr><td>5月</td><td>54</td><td>良</td></tr>
          <tr><td>6月</td><td>41</td><td>良</td></tr>
        </tbody>
      </table>
    </body>
    </html>
    """,
)

_LONG_MD = textwrap.dedent(
    """\
    # 周报 - 北京市空气质量（2026-W25）

    ## 总体评价
    本周 AQI 均值 65，整体**良**，较上周下降 8%。

    ## 关键指标
    - PM2.5 周均：42 μg/m³（二级良）
    - PM10 周均：78 μg/m³（二级良）
    - O₃-8h 日最大第 90 百分位：145 μg/m³（二级良）

    ## 区县对比
    1. 门头沟区 → 优（AQI 42）
    2. 密云区 → 优（AQI 46）
    3. 朝阳区 → 良（AQI 68）
    4. 通州区 → 良（AQI 72）
    5. 大兴区 → 轻度污染（AQI 103）

    ## 下周展望
    受冷高压影响，扩散条件总体有利，预计以良为主，周四可能出现轻度污染过程。
    """,
)

_LONG_SVG = textwrap.dedent(
    """\
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 260" width="420" height="260">
      <defs>
        <linearGradient id="grad1" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="#60a5fa" stop-opacity="0.9"/>
          <stop offset="100%" stop-color="#34d399" stop-opacity="0.9"/>
        </linearGradient>
      </defs>
      <rect x="20" y="20" width="380" height="220" rx="10" fill="#f8fafc" stroke="#cbd5e1"/>
      <polyline
        points="40,200 80,170 120,180 160,130 200,150 240,110 280,120 320,90 360,100"
        fill="none" stroke="url(#grad1)" stroke-width="3" stroke-linejoin="round"
      />
      <text x="30" y="250" font-size="12" fill="#64748b">1月</text>
      <text x="350" y="250" font-size="12" fill="#64748b">9月</text>
      <text x="20" y="40" font-size="14" fill="#334155" font-weight="700">北京 2026 · PM2.5 月度趋势</text>
    </svg>
    """,
)


_LONG_PYTHON = textwrap.dedent(
    """\
    # 计算 30 天的滑动平均浓度序列，长度跨过 MIN_CODE_BLOCK_LENGTH 阈值
    import statistics

    def moving_average(values: list[float], window: int = 7) -> list[float]:
        if not values or window <= 0:
            return []
        out: list[float] = []
        for i in range(len(values)):
            start = max(0, i - window + 1)
            chunk = values[start : i + 1]
            out.append(round(statistics.fmean(chunk), 3))
        return out

    if __name__ == "__main__":
        print(moving_average([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0], window=3))
    """,
)


def test_middleware_scan_single_fenced_html():
    """识别 ```html fenced code block，生成一个 Artifact 事件 + 占位摘要替换."""
    mw = ArtifactMiddleware()
    text = (
        "以下是 2026 年北京市 PM2.5 月度趋势的简单 HTML 报表：\n"
        '```html artifact-title="北京PM2.5月度趋势" artifact-open_in="canvas_window"\n'
        f"{_LONG_HTML}\n"
        "```\n"
        "祝好。\n"
    )

    events: list[dict[str, Any]] = []
    replaced = mw.scan_text(
        text,
        stream_writer=events.append,
        default_open_in="canvas_window",
    )

    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "artifact"
    assert ev["content_type"] == "html"
    assert ev["title"] == "北京PM2.5月度趋势"
    assert ev["open_in"] == "canvas_window"
    assert ev["auto_detected"] is True
    assert "北京 2026 月度 PM2.5 趋势" in ev["content"]

    # 原文被替换为摘要占位，不再包含 fenced 代码块本身
    assert "```" not in replaced
    assert "北京PM2.5月度趋势" in replaced
    assert "已在画布中生成 Artifact" in replaced


def test_middleware_scan_preserves_inline_code():
    """文本中的 inline `code` 不影响真正的 fenced code block 识别."""
    mw = ArtifactMiddleware()
    text = (
        "先看看 `markdown` 语法：\n"
        "```md\n"
        f"{_LONG_MD}\n"
        "```\n"
        "结束。\n"
    )
    events: list[dict[str, Any]] = []
    replaced = mw.scan_text(text, stream_writer=events.append, default_open_in="inline_below")
    assert len(events) == 1
    # fence 语言是 md，但 ArtifactMiddleware 内部统一规范化为 content_type = markdown
    assert events[0]["content_type"] == "markdown"
    assert events[0]["open_in"] == "inline_below"
    assert "下周展望" in events[0]["content"]
    # inline 反引号原样保留
    assert "先看看 `markdown` 语法：" in replaced


def test_middleware_scan_multiple_code_blocks_ignores_unknown_language():
    """svg + python 共存时，python 不支持 → 保留原 fenced，仅 svg 出 Artifact."""
    mw = ArtifactMiddleware()
    text = (
        "一份 SVG 图：\n"
        '```svg artifact-title="SVG 趋势图"\n'
        f"{_LONG_SVG}\n"
        "```\n"
        "再附一段计算代码（不要作为 Artifact 渲染）：\n"
        "```python\n"
        f"{_LONG_PYTHON}\n"
        "```\n"
        "最后。\n"
    )
    events: list[dict[str, Any]] = []
    replaced = mw.scan_text(text, stream_writer=events.append)
    assert len(events) == 1
    assert events[0]["content_type"] == "svg"
    # python 块保留原 fenced 内容
    assert "```python" in replaced
    assert "moving_average" in replaced


def test_middleware_scan_message_list():
    """scan_text 被 call_model 节点直接作用在 AIMessage.content 上时的写回语义."""
    mw = ArtifactMiddleware()
    content = (
        "报表如下：\n"
        '```markdown artifact-title="周报" artifact-open_in="canvas_window"\n'
        f"{_LONG_MD}\n"
        "```\n"
        "以上。\n"
    )
    original = AIMessage(content=content)

    events: list[dict[str, Any]] = []
    scanned = mw.scan_text(
        original.content if isinstance(original.content, str) else str(original.content),
        stream_writer=events.append,
        default_open_in="canvas_window",
    )
    assert len(events) == 1
    assert events[0]["type"] == "artifact"
    assert "```markdown" not in scanned


def test_create_artifact_tool_validation_failure_returns_400():
    """create_artifact 显式工具: content_type 非法 → 返回 code=400，不抛异常."""
    result = create_artifact_tool_fn(
        title="x",
        content_type="???",
        content="y",
        open_in="canvas_window",
    )
    assert isinstance(result, dict)
    assert result.get("code") == 400
    assert result.get("success") is False


def test_create_artifact_tool_success_path():
    """create_artifact 显式工具成功路径: 返回含 artifact payload 的结构化结果.

    工具函数本身不再推 stream_writer 事件（由拦截层 _handle_tool_result 统一推送），
    此处验证返回值中 artifact 字段完整携带 title/content_type/content/open_in。
    """
    result = create_artifact_tool_fn(
        title="报表A",
        content_type="html",
        content=_LONG_HTML,
        open_in="canvas_window",
    )
    assert isinstance(result, dict)
    assert result.get("code") == 200
    assert result.get("success") is True
    # artifact payload 完整
    artifact = result.get("artifact")
    assert isinstance(artifact, dict)
    assert artifact["title"] == "报表A"
    assert artifact["content_type"] == "html"
    assert artifact["open_in"] == "canvas_window"
    assert artifact["auto_detected"] is False
    assert artifact["content"] == _LONG_HTML
    # summary 不含源码
    assert "报表A" in result["data"]["summary"]


def test_build_create_artifact_tool_has_correct_metadata():
    """构造出的 StructuredTool 含正确的 tool name，方便前端和 LLM 识别."""
    tool = build_create_artifact_tool()
    assert tool.name == "create_artifact"
    assert "画布" in tool.description or "渲染" in tool.description
