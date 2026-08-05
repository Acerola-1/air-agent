"""问题实体归一化：将具体的时间、地点、城市等替换为占位符，用于语义路由匹配."""

from __future__ import annotations

import re


class QuestionNormalizer:
    """将用户问题中的具体实体（时间、地点、城市等）替换为占位符，提高语义路由的泛化能力.

    替换规则按优先级执行，先匹配长的、具体的模式，避免冲突。
    所有替换都是基于正则的纯文本替换，不影响原始问题的语义结构。
    """

    # ========== 时间相关模式（优先级高，先匹配）==========

    # 完整日期区间：2026年5月1日至5月8日
    _DATE_RANGE = re.compile(
        r"\d{4}年\d{1,2}月\d{1,2}[日号]\s*[至到]\s*\d{4}年\d{1,2}月\d{1,2}[日号]|"
        r"\d{4}年\d{1,2}月\d{1,2}[日号]\s*[至到]\s*\d{1,2}月\d{1,2}[日号]|"
        r"\d{4}年\d{1,2}月\d{1,2}[日号]\s*[至到]\s*\d{1,2}[日号]"
    )

    # 完整日期：2026年5月1日、2026年3月15日
    _DATE_FULL = re.compile(
        r"\d{4}年(?:\d{1,2}月)?(?:\d{1,2}[日号])?"
    )

    # ISO格式日期：2026-05-27
    _DATE_ISO = re.compile(
        r"\d{4}-\d{2}-\d{2}"
    )

    # 季度：2026年第一季度、第一季度
    _QUARTER = re.compile(
        r"\d{4}年第?[一二三四1234]季度|"
        r"第[一二三四1234]季度"
    )

    # 半年：上半年、下半年
    _HALF_YEAR = re.compile(
        r"[上下]半年"
    )

    # 月份：2026年4月、本月、上个月、下个月
    _MONTH_PERIOD = re.compile(
        r"\d{4}年\d{1,2}月|"
        r"(?:本|上|下|这|前|昨|今|明|去)个?月"
    )

    # 年份：2026年、去年、今年、明年
    _YEAR = re.compile(
        r"\d{4}年|"
        r"(?:去|今|明|前)年"
    )

    # 周/天：上周、本周、过去一周、最近7天、近三天
    _WEEK_DAY = re.compile(
        r"过去?[一-九\d]+?[周天日]|"
        r"最近\d+?[周天日]|"
        r"近[一-九\d]+?[周天日]|"
        r"[上下]周|"
        r"(?:本|上|下|这)周|"
        r"(?:昨|今|明)天|"
        r"(?:前|后)天"
    )

    # 具体小时：15时、14时
    _HOUR = re.compile(
        r"\d{1,2}时"
    )

    # 时间修饰词：今年以来、本月、今日、实时、当日、昨天、前天、明天
    _TIME_MODIFIERS = re.compile(
        r"今年以来|"
        r"本月|"
        r"今日|"
        r"当日|"
        r"实时|"
        r"昨天|"
        r"前天|"
        r"明天"
    )

    # ========== 地点相关模式 ==========

    # 国控站点
    _STATION_TYPE = re.compile(
        r"国控站点"
    )

    # 省市区县镇街道：南浔区、杭州市、浙江省
    # 排除：
    #   - "某" 开头的（某市、某省等占位符）
    #   - "xx" 或 "XX" 开头的（xx市等占位符）
    #   - 数字开头的（如"168重点城市"中的"城市"）
    _REGION_ADMIN = re.compile(
        r"(?<![某xX\d])[一-龥]{2,10}(?:省|市|区|县|镇|乡|街道)"
    )

    # 城市简称/特定名称（在行政区划之后，避免重复）
    _CITY_NAME = re.compile(
        r"(?:洛阳|北京|上海|广州|深圳|杭州|南京|武汉|成都|西安|重庆|天津|苏州|"
        r"郑州|长沙|青岛|宁波|无锡|佛山|东莞|厦门|济南|合肥|福州|石家庄|"
        r"太原|呼和浩特|沈阳|大连|长春|哈尔滨|南昌|昆明|南宁|贵阳|乌鲁木齐|"
        r"兰州|银川|西宁|拉萨|海口)"
    )

    # ========== 其他需要归一化的模式 ==========

    # 浓度单位
    _UNIT = re.compile(
        r"μg/m³|mg/m³"
    )

    # 固定专有名词（不应被替换的地理/区域概念，保持原样）
    _FIXED_GEO_TERMS: tuple[str, ...] = (
        "珠三角",
        "长三角",
        "74重点城市",
        "省级行政区",
        "东北地区",
        "成渝地区",
        "其他省会城市和计划单列市",
        "苏皖鲁豫",
        "339城市",
        "汾渭平原",
        "中原地区",
        "168重点城市",
        "长江中游城市",
        "省会城市",
        "京津冀及周边",
        "京津冀",
        "粤港澳大湾区",
        "长江经济带",
        "黄河流域",
        "全国",
        "全省",
        "全市",
    )

    @classmethod
    def normalize(cls, text: str) -> str:
        """将问题中的具体实体替换为占位符，用于语义路由匹配.

        Args:
            text: 原始用户问题

        Returns:
            归一化后的问题文本
        """
        if not text or not isinstance(text, str):
            return text

        # 先保护固定专有名词（用占位符替换，最后再恢复）
        protected: list[tuple[str, str]] = []
        for i, term in enumerate(cls._FIXED_GEO_TERMS):
            if term in text:
                placeholder = f"\x00GEO{i}\x00"
                text = text.replace(term, placeholder)
                protected.append((placeholder, term))

        # 按优先级顺序执行替换
        # 1. 完整日期区间（最长，先匹配）
        text = cls._DATE_RANGE.sub("<日期区间>", text)
        # 2. 完整日期
        text = cls._DATE_FULL.sub("<日期>", text)
        # 3. ISO格式日期
        text = cls._DATE_ISO.sub("<日期>", text)
        # 4. 季度/半年（在月份之前）
        text = cls._QUARTER.sub("<季度>", text)
        text = cls._HALF_YEAR.sub("<半年>", text)
        # 5. 月份
        text = cls._MONTH_PERIOD.sub("<月份>", text)
        # 6. 年份
        text = cls._YEAR.sub("<年份>", text)
        # 7. 周/天
        text = cls._WEEK_DAY.sub("<时间段>", text)
        # 8. 小时
        text = cls._HOUR.sub("<小时>", text)
        # 9. 时间修饰词
        text = cls._TIME_MODIFIERS.sub("<时间>", text)

        # 10. 地点相关
        text = cls._STATION_TYPE.sub("<站点类型>", text)
        # 11. 行政区划名称
        text = cls._REGION_ADMIN.sub("<地区>", text)
        # 12. 已知城市名
        text = cls._CITY_NAME.sub("<城市>", text)

        # 13. 其他
        text = cls._UNIT.sub("<单位>", text)

        # 恢复被保护的固定专有名词
        for placeholder, original in protected:
            text = text.replace(placeholder, original)

        return text

    @classmethod
    def normalize_for_embedding(cls, text: str) -> str:
        """为语义路由 embedding 做更激进的归一化.

        除了替换实体外，还会标准化一些常见表述，使语义更聚焦。
        """
        text = cls.normalize(text)

        # 清理多余空格
        text = re.sub(r"\s+", " ", text).strip()

        return text
