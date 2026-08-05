"""数据分析菜单到 Skill 的确定性映射."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DataAnalysisSkillMapping:
    """单个数据分析 Skill 的菜单映射配置."""

    title: str
    skill_name: str
    menu_names: tuple[str, ...]


DATA_ANALYSIS_SKILL_MAPPINGS: tuple[DataAnalysisSkillMapping, ...] = (
    DataAnalysisSkillMapping(
        title="小时播报",
        skill_name="broadcast-hour",
        menu_names=("CityHourBroadcast",),
    ),
    DataAnalysisSkillMapping(
        title="浓度排名",
        skill_name="concentration-ranking",
        menu_names=("concentrationranking",),
    ),
    DataAnalysisSkillMapping(
        title="监测数据",
        skill_name="monitoring-data",
        menu_names=("cityMonitoringData",),
    ),
    DataAnalysisSkillMapping(
        title="单指标对比分析",
        skill_name="single-indicator-comparison",
        menu_names=("SingleIndexMultiCity",),
    ),
    DataAnalysisSkillMapping(
        title="多指标对比分析",
        skill_name="multi-indicator-comparison",
        menu_names=("SingleCityMultiIndex",),
    ),
    DataAnalysisSkillMapping(
        title="空气质量日历",
        skill_name="air-quality-calendar",
        menu_names=(
            "datastatistics_urbanaircalendar_day",
            "datastatistics_urbanaircalendar_month",
            "datastatistics_urbanaircalendar_year",
        ),
    ),
    DataAnalysisSkillMapping(
        title="综合指数占比分析",
        skill_name="integrated-index-ratio",
        menu_names=("proportionComprehensive",),
    ),
    DataAnalysisSkillMapping(
        title="首要污染物占比分析",
        skill_name="primary-pollutant-ratio",
        menu_names=("ProportionPrimaryPollutants",),
    ),
    DataAnalysisSkillMapping(
        title="空气质量等级分析",
        skill_name="air-quality-level-analysis",
        menu_names=("airQualityGrade",),
    ),
    DataAnalysisSkillMapping(
        title="城市边界",
        skill_name="city-boundary",
        menu_names=("CityBoundaryDisplay",),
    ),
    DataAnalysisSkillMapping(
        title="浓度比值分析",
        skill_name="concentration-ratio",
        menu_names=("PM25AndPM10Ratio",),
    ),
    DataAnalysisSkillMapping(
        title="浓度箱线图",
        skill_name="concentration-boxplot",
        menu_names=("ConcentrationBoxDiagram",),
    ),
    DataAnalysisSkillMapping(
        title="区域距平图",
        skill_name="regional-anomaly-map",
        menu_names=("anomalyMap",),
    ),
    DataAnalysisSkillMapping(
        title="区域特征雷达",
        skill_name="regional-feature-radar",
        menu_names=("RegionalRadarMap",),
    ),
    DataAnalysisSkillMapping(
        title="时间特征雷达",
        skill_name="temporal-feature-radar",
        menu_names=("TimeRadarCart",),
    ),
    DataAnalysisSkillMapping(
        title="污染相关性分析",
        skill_name="pollutant-correlation",
        menu_names=("CorrelationAnalysisDiagram",),
    ),
    DataAnalysisSkillMapping(
        title="24小时均值对比分析",
        skill_name="rolling-24h-average-comparison",
        menu_names=("MeanMap",),
    ),
    DataAnalysisSkillMapping(
        title="区域排名分析",
        skill_name="regional-ranking",
        menu_names=("statistics_region",),
    ),
    DataAnalysisSkillMapping(
        title="月考核排名",
        skill_name="monthly-assessment-ranking",
        menu_names=("CityMonthlyAssess",),
    ),
    DataAnalysisSkillMapping(
        title="空气质量计算器",
        skill_name="air-quality-calculator",
        menu_names=("AirQualityCalculator",),
    ),
    DataAnalysisSkillMapping(
        title="AQI 达标控制",
        skill_name="aqi-attainment-control",
        menu_names=("AQIDailyComplianceControl",),
    ),
    DataAnalysisSkillMapping(
        title="今日目标分析",
        skill_name="today-target-analysis",
        menu_names=("todayTargetAnalysis",),
    ),
    DataAnalysisSkillMapping(
        title="城市考核目标控制",
        skill_name="city-assessment-target-control",
        menu_names=("UrbanobjectiveControl",),
    ),
    DataAnalysisSkillMapping(
        title="城市沙尘判定",
        skill_name="city-dust-assessment",
        menu_names=("analysisOfUrbanDust",),
    ),
)


MENU_SKILL_INDEX: dict[str, DataAnalysisSkillMapping] = {
    menu_name.strip().lower(): mapping
    for mapping in DATA_ANALYSIS_SKILL_MAPPINGS
    for menu_name in mapping.menu_names
}


def match_menu_skill(menu_name: str | None) -> DataAnalysisSkillMapping | None:
    """按前端菜单英文名匹配数据分析 Skill."""
    normalized = (menu_name or "").strip().lower()
    if not normalized or normalized in {"none", "null", "default"}:
        return None
    return MENU_SKILL_INDEX.get(normalized)
