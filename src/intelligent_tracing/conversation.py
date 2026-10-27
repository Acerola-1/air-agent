"""Composable, server-authored conversation facts; no arbitrary model prose."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from intelligent_tracing.policy import AnalysisPolicy
from intelligent_tracing.schemas import TurnDecision

PLAN_TOOL = "submit_tracing_turn"
ANALYSIS_TOOLS = frozenset(
    {
        "freeze_tracing_request",
        "task",
        "get_analysis_policy_summary",
        "build_validated_report",
        "publish_tracing_canvas",
        "knowledge_search",
    }
)
LABELS = {
    "target": "站点代码或名称",
    "region_name": "行政区（如郑州市）",
    "start": "开始时间",
    "end": "结束时间",
    "timezone": "时间与时区",
    "neighbor_radius_km": "调查半径（1–50公里）",
    "pollutant": "污染物",
    "baseline_start": "基线开始时间",
    "baseline_end": "基线结束时间",
    "time_window": "起止时间（须对齐整点，且不超过31天）",
    "baseline_window": "基线窗口（须在分析开始前，且起止完整）",
}


@dataclass(frozen=True)
class ConversationCatalog:
    """Versioned facts derived from the configured graph."""

    version: str
    entries: dict[str, str]


def capability_catalog(
    policy: AnalysisPolicy, tools: frozenset[str]
) -> ConversationCatalog:
    """Describe configured support, never infer that upstream data is healthy."""
    entries = {
        "greeting": "你好！我可以帮助你了解和使用污染过程分析。",
        "thanks": "不客气，有需要可以继续告诉我。",
        "boundary": "当前不提供轨迹或PSCF分析，也不能确认具体企业、排口、源贡献率或来源概率。",
        "workflow": "你可以提供站点、所在城市、污染物、起止时间和调查半径。我会先确认条件，再根据可用证据分析。",
        "next_step": "你可以先问我能分析哪些内容，或告诉我想分析的站点与时段。",
        "knowledge_guidance": "具体法规、标准条款及限值需要可核验的知识依据；你可以使用知识问答查询，不应把无依据的解释当作评价结论。",
        "report_guidance": "已有分析请以已发布报告中的数据、引用和限制为准；如需调整范围，可以明确告诉我需要变更的条件。",
    }
    if {"task", "freeze_tracing_request", "build_validated_report"} <= tools:
        names = [
            policy.require_supported(p).display_name
            for p in sorted(policy.enabled_pollutants)
        ]
        entries.update(
            {
                "overview": "我可以辅助复盘站点污染过程，结合周边站点、空间分布和气象条件寻找关联线索，并说明证据与限制。实际结果取决于数据是否可用。",
                "pollutants": "当前支持的污染物包括：" + "、".join(names) + "。",
                "station": "可以分析站点浓度变化、异常时段与数据覆盖，并对比周边站点。",
                "spatial": "可以结合网格数据观察热点与区域连片现象；空间同步本身不证明来源。",
                "weather": "可以分析风场、滞后及静稳条件等关联；相关线索不等于因果或来源确认。",
                "report": "分析结论经证据校验后发布报告；证据不足时会明确说明可判断与不可判断的部分。",
            }
        )
    if "publish_tracing_canvas" in tools:
        entries["canvas"] = "已确认的分析报告可以在画布中展示。"
    if "knowledge_search" in tools:
        entries["knowledge"] = (
            "涉及具体法规与限值时，需要检索并引用可核验的知识依据；知识依据与本次监测证据分开使用。"
        )
    version = hashlib.sha256(
        json.dumps(
            [policy.version, sorted(tools), entries], ensure_ascii=False
        ).encode()
    ).hexdigest()
    return ConversationCatalog(version=version, entries=entries)


def render_conversation(decision: TurnDecision, catalog: ConversationCatalog) -> str:
    """Render only registered facts, or a precise server-selected clarification."""
    if decision.route == "conversation":
        if decision.catalog_version != catalog.version or not decision.blocks:
            raise ValueError("conversation catalog mismatch")
        blocks = [catalog.entries[key] for key in decision.blocks]
        if decision.tone == "concise" and "greeting" in decision.blocks:
            blocks[decision.blocks.index("greeting")] = "你好！"
        return "\n\n".join(blocks)
    if decision.reason == "station_ambiguous":
        return (
            "站点名称对应多个候选，请确认站点代码：\n\n"
            + "\n".join(f"- {item}" for item in decision.candidates)
            + "\n\n尚未开始污染观测或气象取数。"
        )
    if decision.reason == "station_unavailable":
        return "站点目录暂时不可用，尚未开始分析，请稍后重试。"
    if decision.reason == "station_invalid":
        return (
            "当前授权目录无法唯一确认该站点，请核对站点代码或所在城市。尚未开始分析。"
        )
    if decision.route == "clarify":
        parts = []
        if decision.missing:
            parts.append(
                "请补充或确认："
                + "、".join(LABELS.get(k, k) for k in decision.missing)
                + "。"
            )
        if decision.invalid:
            parts.append(
                "请核对存在冲突或格式不明确的条件："
                + "、".join(LABELS.get(k, k) for k in decision.invalid)
                + "。"
            )
        return "\n\n".join(
            parts
            + [
                "尚未开始分析取数。未明确给出的污染物、时区及基线，分别沿用PM2.5、北京时间及分析前七天；已有明确条件会保留。"
            ]
        )
    return "我可以介绍污染分析的能力与使用方式。如果需要开始分析，请明确站点和分析条件；当前未启动数据查询。"
