"""确定性清洗、脱敏、槽位与规则拆分（模型之前的第一道边界）.

原则（design D3）：

- 先最小化再外发：手机号/邮箱/账号/坐标只替换为占位符；证件号、银行卡、
  显式口令/令牌视为不可分析敏感内容，整条拒绝外发。
- 保留业务语义：动作、对象、否定、城市、日期、污染物写入 slots；标准问题
  可抽象城市/日期，但参数不删除。
- 规则拆分只处理明确的多动作/并列句；模糊追问留给受限模型或未归类，
  不猜测上下文。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

ACTIONS = ("query", "trend", "cause", "forecast", "advice", "other")
TIME_KINDS = ("point", "range", "future", "unknown")

_SENSITIVE_LABEL = re.compile(
    r"(密码|口令|password|passwd|pwd|秘钥|私钥|secret|token|api[_-]?key|access[_-]?key)"
    r"\s*(?:是|为|:|：|=)\s*\S+",
    re.IGNORECASE,
)
_TOKEN_SHAPE = re.compile(
    r"(?:sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
    r"|Bearer\s+[A-Za-z0-9._~+/=-]{16,}|[0-9a-fA-F]{32,})"
)
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_BANK_CARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ACCOUNT = re.compile(r"(账号|账户|用户名)\s*[:：]?\s*[A-Za-z0-9_.@-]{2,64}")
_COORDINATE = re.compile(r"(经度|纬度|坐标)\s*[:：]?\s*-?\d{1,3}\.\d{3,}")

_CITY_SUFFIX = re.compile(r"([\u4e00-\u9fff]{2,6}?)(?:市|地区|区|县)")
_CITY_NAMES = (
    "北京",
    "上海",
    "广州",
    "深圳",
    "杭州",
    "南京",
    "成都",
    "重庆",
    "武汉",
    "西安",
    "天津",
    "苏州",
    "郑州",
    "长沙",
    "青岛",
    "沈阳",
    "大连",
    "厦门",
    "福州",
    "合肥",
    "昆明",
    "哈尔滨",
    "济南",
    "佛山",
    "东莞",
    "无锡",
    "宁波",
    "石家庄",
    "太原",
    "南昌",
    "贵阳",
    "南宁",
    "兰州",
    "乌鲁木齐",
    "呼和浩特",
    "银川",
    "西宁",
    "拉萨",
    "海口",
    "三亚",
)
_POLLUTANT = re.compile(
    r"(PM2\.5|PM10|O3|NO2|SO2|CO|AQI|臭氧|二氧化硫|二氧化氮|一氧化碳|颗粒物)", re.IGNORECASE
)
_DATE_ABSOLUTE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?")
_DATE_MONTH_DAY = re.compile(r"(\d{1,2})月(\d{1,2})日")
_DATE_WORD = re.compile(
    r"(今天|今日|昨天|昨日|明天|明日|前天|后天|本周|这周|上周|近7天|近七天|最近7天"
    r"|未来三天|未来一周|未来7天|今年|去年)"
)

_CAUSE = re.compile(r"(为什么|为何|原因|成因|导致|来源|怎么造成|什么造成)")
_FORECAST = re.compile(r"(预测|预报|会改善|会变好|会变差|未来三天|未来一周|未来7天)")
_FUTURE_WORD = re.compile(r"(明天|明日|后天|未来)")
_ADVICE = re.compile(r"(建议|怎么办|如何防护|应该|注意什么|要不要|怎么应对|如何避免)")
_QUERY = re.compile(
    r"(查|查询|多少|是什么|怎么样|如何|有没有|看看|显示|AQI|PM2\.5|空气质量|数据|情况|趋势|走势)",
    re.IGNORECASE,
)
_INJECTION = re.compile(
    r"(忽略|无视|绕过)(以上|之前|上述|所有)?[^，,。；;]{0,40}(指令|提示词|规则)[，,。；;]?"
)
_NEGATION = re.compile(r"(不想|不要|不用|无需|没有|没|不)(查|看|知道|需要|要)")
_RANGE = re.compile(r"(近7天|近七天|最近7天|本周|这周|上周|未来一周|未来7天)")

_SPLIT = re.compile(r"[；;。，,]")
_PRONOUN = re.compile(r"^(那|这|它|这个|那个|还有|呢|那么)")


@dataclass(frozen=True)
class IntentDraft:
    text: str
    summary: str
    action: str
    object: str
    time_kind: str
    negation: bool
    slots: dict[str, Any]
    confidence: float
    split_index: int = 0
    source: str = "rule"


@dataclass(frozen=True)
class NormalizationResult:
    intents: tuple[IntentDraft, ...]
    redactions: tuple[str, ...]
    policy_version: str
    rejected_reason: str | None = None
    review_reason: str | None = None


def normalize_text(raw: str) -> str:
    """NFKC + 控制字符/空白收敛；保留换行语义为空格。"""
    text = unicodedata.normalize("NFKC", raw or "")
    text = "".join(ch if ch.isprintable() or ch in "\n\t" else " " for ch in text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_action(text: str) -> str:
    if _CAUSE.search(text):
        return "cause"
    if _ADVICE.search(text):
        return "advice"
    # “查说明天”仍是查询；只有显式预测词，或无查询动词的未来问句才是预测。
    if _FORECAST.search(text) or (_FUTURE_WORD.search(text) and not _QUERY.search(text)):
        return "forecast"
    if re.search(r"(趋势|走势)", text) or (_RANGE.search(text) and _QUERY.search(text)):
        return "trend"
    if _QUERY.search(text):
        return "query"
    return "other"


def detect_object(text: str, action: str) -> str:
    if action == "cause":
        return "pollution_source"
    if action == "advice":
        return "health_advice"
    if action == "forecast":
        return "air_quality_forecast"
    return "air_quality"


def detect_time_kind(text: str, action: str) -> str:
    if action == "forecast" or re.search(r"(明天|明日|后天|未来|预测|预报)", text):
        return "future"
    if _RANGE.search(text):
        return "range"
    if _DATE_ABSOLUTE.search(text) or _DATE_MONTH_DAY.search(text) or _DATE_WORD.search(text):
        return "point"
    return "unknown"


def extract_slots(text: str) -> dict[str, Any]:
    city: str | None = None
    suffix = _CITY_SUFFIX.search(text)
    if suffix and suffix.group(1) not in ("空气", "质量", "数据"):
        city = suffix.group(1)
    if city is None:
        for name in _CITY_NAMES:
            if name in text:
                city = name
                break
    pollutant = _POLLUTANT.search(text)
    date_text: str | None = None
    absolute = _DATE_ABSOLUTE.search(text)
    month_day = _DATE_MONTH_DAY.search(text)
    word = _DATE_WORD.search(text)
    if absolute:
        date_text = f"{absolute.group(1)}-{int(absolute.group(2)):02d}-{int(absolute.group(3)):02d}"
    elif month_day:
        date_text = f"{int(month_day.group(1)):02d}-{int(month_day.group(2)):02d}"
    elif word:
        date_text = word.group(1)
    station = re.search(r"([\u4e00-\u9fff]{2,12})(?:站|监测点)", text)
    return {
        "city": city,
        "city_code": None,
        "station": station.group(1) if station else None,
        "pollutant": pollutant.group(1).upper() if pollutant else None,
        "date": date_text,
    }


def action_label(action: str) -> str:
    return {
        "query": "查询",
        "trend": "趋势",
        "cause": "成因",
        "forecast": "预测",
        "advice": "建议",
        "other": "其他",
    }.get(action, "其他")


def object_label(obj: str) -> str:
    return {
        "air_quality": "空气质量",
        "pollution_source": "污染成因",
        "air_quality_forecast": "空气质量预测",
        "health_advice": "健康建议",
    }.get(obj, "空气质量")


def summarize(text: str, action: str, obj: str, slots: dict[str, Any]) -> str:
    """脱敏后的短摘要；不使用原句中的未知长片段。"""
    parts = [action_label(action), object_label(obj)]
    if slots.get("city"):
        parts.append(str(slots["city"]))
    if slots.get("date"):
        parts.append(str(slots["date"]))
    if slots.get("pollutant"):
        parts.append(str(slots["pollutant"]))
    return "·".join(parts)[:120]


def redact(text: str) -> tuple[str, list[str], bool]:
    """返回 (脱敏文本, 替换类别, 是否含不可分析秘密)。"""
    redactions: list[str] = []
    sensitive = False
    if _SENSITIVE_LABEL.search(text) or _TOKEN_SHAPE.search(text):
        sensitive = True
        redactions.append("credential")
    if _ID_CARD.search(text):
        sensitive = True
        redactions.append("id_card")
        text = _ID_CARD.sub("[证件号]", text)
    if _BANK_CARD.search(text):
        sensitive = True
        redactions.append("bank_card")
        text = _BANK_CARD.sub("[卡号]", text)
    if _PHONE.search(text):
        redactions.append("phone")
        text = _PHONE.sub("[手机号]", text)
    if _EMAIL.search(text):
        redactions.append("email")
        text = _EMAIL.sub("[邮箱]", text)
    if _ACCOUNT.search(text):
        redactions.append("account")
        text = _ACCOUNT.sub(r"\1[账号]", text)
    if _COORDINATE.search(text):
        redactions.append("coordinate")
        text = _COORDINATE.sub(r"\1[坐标]", text)
    if _TOKEN_SHAPE.search(text):
        text = _TOKEN_SHAPE.sub("[凭据]", text)
    if _SENSITIVE_LABEL.search(text):
        text = _SENSITIVE_LABEL.sub("[敏感信息已移除]", text)
    return text, redactions, sensitive


def needs_context(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) <= 16 and _PRONOUN.search(stripped):
        return True
    return bool(
        re.fullmatch(r"(那|这|它|那么)?\s*(明天|今天|昨天|后天|呢|怎么样|如何)?\s*[?？]?", stripped)
    )


def merge_context(text: str, context_texts: list[str], max_chars: int) -> str:
    """只拼接最近受控上下文；总长受限，不扫描整个 thread。"""
    if not needs_context(text) or not context_texts:
        return text
    merged = ""
    for previous in context_texts[-2:]:
        candidate = f"{previous} {text}".strip()
        if len(candidate) <= max_chars:
            merged = candidate
        else:
            break
    return merged or text


def rule_split(text: str, max_intents: int) -> tuple[list[str], bool]:
    """按明确标点/并列词拆段；无动作的短段并入前段。"""
    raw_parts = [part.strip() for part in _SPLIT.split(text) if part and part.strip()]
    if len(raw_parts) <= 1:
        return [text], False
    segments: list[str] = []
    for part in raw_parts:
        action = detect_action(part)
        if segments and action == "other" and len(part) < 20:
            segments[-1] = f"{segments[-1]}，{part}"
        else:
            segments.append(part)
    if len(segments) <= 1:
        return ([segments[0]] if segments else [text]), False
    truncated = len(segments) > max_intents
    return segments[:max_intents], truncated


def normalize_question(
    raw: str,
    *,
    context_texts: list[str] | None = None,
    max_input_chars: int = 4000,
    max_intents: int = 5,
    max_context_chars: int = 8000,
    policy_version: str = "qdi-normalizer-v1",
) -> NormalizationResult:
    """完整标准化入口；失败原因供 source gap/未归类记录，不含原文。"""
    text = normalize_text(raw)
    if not text:
        return NormalizationResult((), (), policy_version, rejected_reason="empty")
    if len(text) > max_input_chars:
        return NormalizationResult((), (), policy_version, rejected_reason="too_long")
    safe_context: list[str] = []
    for context_text in context_texts or []:
        context_clean, _context_redactions, context_sensitive = redact(normalize_text(context_text))
        if context_sensitive:
            continue
        safe_context.append(context_clean)
    text = merge_context(text, safe_context, max_context_chars)
    cleaned, redactions, sensitive = redact(text)
    if len(cleaned) > max_input_chars:
        return NormalizationResult(tuple(), tuple(redactions), policy_version, "too_long")
    if sensitive:
        return NormalizationResult(
            tuple(), tuple(redactions), policy_version, rejected_reason="sensitive_content"
        )
    cleaned = _INJECTION.sub("", cleaned).strip()
    if not cleaned:
        return NormalizationResult(tuple(), tuple(redactions), policy_version, "empty")
    original = normalize_text(raw)
    if needs_context(original):
        if not context_texts:
            draft = IntentDraft(
                text=original,
                summary="未解析追问",
                action="other",
                object="air_quality",
                time_kind="unknown",
                negation=False,
                slots=extract_slots(original),
                confidence=0.3,
            )
            return NormalizationResult(
                (draft,), tuple(redactions), policy_version, review_reason="unresolved_context"
            )
        previous = safe_context[-1]
        action = detect_action(previous)
        obj = detect_object(previous, action)
        slots = extract_slots(previous)
        for key, value in extract_slots(original).items():
            if value is not None:
                slots[key] = value
        time_kind = detect_time_kind(original, action)
        draft = IntentDraft(
            text=merge_context(cleaned, context_texts, max_context_chars),
            summary=summarize(cleaned, action, obj, slots),
            action=action,
            object=obj,
            time_kind=time_kind,
            negation=bool(_NEGATION.search(original)),
            slots=slots,
            confidence=0.75,
        )
        return NormalizationResult((draft,), tuple(redactions), policy_version)
    segments, truncated = rule_split(cleaned, max_intents)
    intents: list[IntentDraft] = []
    carry_slots: dict[str, Any] = {}
    for index, segment in enumerate(segments):
        action = detect_action(segment)
        obj = detect_object(segment, action)
        time_kind = detect_time_kind(segment, action)
        slots = extract_slots(segment)
        for key, value in carry_slots.items():
            if slots.get(key) is None:
                slots[key] = value
        for key, value in slots.items():
            if value is not None:
                carry_slots[key] = value
        negation = bool(_NEGATION.search(segment))
        confidence = 0.62 if action == "other" else 0.80
        intents.append(
            IntentDraft(
                text=segment,
                summary=summarize(segment, action, obj, slots),
                action=action,
                object=obj,
                time_kind=time_kind,
                negation=negation,
                slots=slots,
                confidence=confidence,
                split_index=index,
                source="rule",
            )
        )
    review_reason = "too_many_intents" if truncated else None
    if needs_context(cleaned) and intents and intents[0].action == "other":
        review_reason = review_reason or "unresolved_context"
    return NormalizationResult(
        tuple(intents),
        tuple(sorted(set(redactions))),
        policy_version,
        review_reason=review_reason,
    )
