"""权限修正文案模板."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RegionDict = dict[str, Any]

MONTH_YEAR_GRANULARITIES = {"month", "month_count", "year", "year_count"}


@dataclass(frozen=True)
class CorrectionTemplateResult:
    """确定性文案模板匹配结果."""

    text: str
    template_id: str
    fallback_reason: str = ""


def _name(region: RegionDict | None, default: str = "目标区域") -> str:
    if not isinstance(region, dict):
        return default
    value = region.get("name") or region.get("region_name")
    return str(value or default)


def _level(region: RegionDict | None) -> str:
    if not isinstance(region, dict):
        return ""
    return str(region.get("level") or "")


def _generic_region_text(
    requested: RegionDict | None,
    replacement: RegionDict | None,
) -> str:
    req_name = _name(requested)
    rep_name = _name(replacement, "")
    rep_level = _level(replacement)
    level_map = {"province": "省级", "city": "地市", "district": "区县"}
    level_text = level_map.get(rep_level, "")
    if level_text and rep_name:
        return (
            f"因数据权限限制，您无法查看{req_name}的数据，"
            f"已为您展示{level_text}{rep_name}的数据。"
        )
    return "因数据权限限制，已为您调整查询区域。"


def build_region_replacement_correction_text(
    *,
    region_context: RegionDict | None,
    replacement: RegionDict,
    time_granularity: str = "",
) -> CorrectionTemplateResult:
    """按权限矩阵生成行政区替换文案."""
    context = region_context or {}
    user = context.get("user_region") if isinstance(context, dict) else {}
    requested = context.get("requested_region") if isinstance(context, dict) else {}
    if not isinstance(user, dict):
        user = {}
    if not isinstance(requested, dict):
        requested = {}

    req_name = _name(requested)
    req_level = _level(requested)
    rep_name = _name(replacement, "")
    user_level = _level(user)
    user_name = _name(user, "")
    same_province = bool(context.get("same_province"))
    same_city = bool(context.get("same_city"))

    if time_granularity in MONTH_YEAR_GRANULARITIES:
        return CorrectionTemplateResult(
            text=(
                f"月/年统计数据不支持跨行政区查看，已为您切换至有权限的{rep_name}数据。"
            ),
            template_id="month_year_region_replacement",
        )

    if user_level == "province":
        if req_level == "district" and same_province:
            return CorrectionTemplateResult(
                text=(
                    "因数据权限限制，省级用户无法查看区县明细数据，"
                    f"已为您展示{req_name}所属{rep_name}的汇总数据。"
                ),
                template_id="province_same_province_district_rollup",
            )
        if req_level in {"city", "district"} and not same_province:
            return CorrectionTemplateResult(
                text=(
                    "因数据权限限制，省级用户无法查看外省地市/区县数据，"
                    f"已为您展示{req_name}所属{rep_name}的省级汇总数据。"
                    "如需查看明细，可查询最近7天的数据。"
                ),
                template_id="province_external_rollup",
            )

    if user_level == "city":
        if req_level == "district" and same_province and not same_city:
            owner = user_name or rep_name
            return CorrectionTemplateResult(
                text=(
                    "因数据权限限制，地市用户无法查看其他市区县数据，"
                    f"已为您展示您所在{owner}的{rep_name}数据。"
                ),
                template_id="city_same_province_other_city_district",
            )
        if not same_province:
            return CorrectionTemplateResult(
                text=(
                    "因数据权限限制，您无法查看外省数据，"
                    f"已为您展示您所在城市{rep_name}的数据。"
                ),
                template_id="city_external_replacement",
            )

    if user_level == "district":
        if req_level == "province":
            return CorrectionTemplateResult(
                text=(
                    "因数据权限限制，区县用户无法查看省级汇总数据，"
                    f"已为您展示您所在{rep_name}的明细数据。"
                    "如需查看省级汇总，可查询最近7天的数据。"
                ),
                template_id="district_province_summary_replacement",
            )
        if not same_city:
            return CorrectionTemplateResult(
                text=(
                    "因数据权限限制，您无法查看外市数据，"
                    f"已为您展示您所在{rep_name}的数据。"
                ),
                template_id="district_external_city_replacement",
            )

    return CorrectionTemplateResult(
        text=_generic_region_text(requested, replacement),
        template_id="fallback_region_replacement",
        fallback_reason=(
            f"unmatched_matrix:user_level={user_level},requested_level={req_level},"
            f"same_province={same_province},same_city={same_city}"
        ),
    )


def build_time_truncation_correction_text(
    *,
    legal_span: list[str],
    requested_region: RegionDict | None = None,
    metric_names: list[str] | None = None,
) -> str:
    """生成时间截断文案."""
    if not legal_span or len(legal_span) < 2:
        return "查询日期范围部分超出豁免窗口，已截断至可访问时间范围。"

    region_name = _name(requested_region, "")
    metrics = "、".join(metric_names or [])
    target = ""
    if region_name and metrics:
        target = f"{region_name}{metrics}"
    elif region_name:
        target = region_name
    elif metrics:
        target = metrics

    suffix = f"的{target}数据" if target else "的数据"
    return (
        "查询日期范围部分超出豁免窗口，"
        f"已截断至 {legal_span[0]} 至 {legal_span[1]}{suffix}。"
    )
