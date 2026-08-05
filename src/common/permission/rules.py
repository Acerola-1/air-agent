"""权限检查的规则预分类.

采用纯规则设计：正则白名单快速放行 + 正则兜底判定。
语义层面的 need_check 判定已合并进权限槽位抽取（PermissionExtractedSlots.need_check），
避免预分类阶段额外一轮 LLM 调用。
"""

from __future__ import annotations

import re
from typing import Literal

# ── 正则白名单：符合的直接放行 ──────────────────────────────

# 聊天问候
_CHAT_GREETING_PATTERN = re.compile(
    r"^(你好|您好|hi|hello|hey|哈喽|嗨|早|在吗|在不在|"
    r"你是谁|你叫什么|你叫啥|介绍一下你自己|"
    r"你能做什么|你可以做什么|你会什么|怎么使用你|如何使用你|"
    r"谢谢|感谢|辛苦了|再见|拜拜|早上好|上午好|中午好|下午好|晚上好)[。！？!?\s]*$",
    re.IGNORECASE,
)

# 知识问答关键词（不涉及数据查询）
_KNOWLEDGE_PATTERN = re.compile(
    r"(什么是|是什[么麽]|什么意思|含义|定义|概念|原理|"
    r"怎么计算|如何计算|怎么算|计算方法|评估方法|评价方法|"
    r"为什么|原因是什么|区别|关系|影响|危害|来源|成因|"
    r"怎么办|如何治理|怎么治理|治理方案|建议|措施|管控建议|"
    r"政策|法规|法律|标准|规范|指南|预案|应急响应|科普|"
    r"介绍|解释|说明|解读|"
    r"帮我写|帮我生成|生成一份|写一份|撰写|起草|润色|总结|"
    r"报告模板|通知|通报|汇报材料|整改方案|工作方案)",
)

# 对话/质疑关键词
_DIALOGUE_PATTERN = re.compile(
    r"(我没有问|我不是问|我没有让你|你为什么|你怎么|"
    r"我是说|我的意思是|请回答|请解释|请说明|"
    r"刚才|之前|上面|上次|之前说的|"
    r"不对|错了|不是|不对吧|好像不对|"
    r"什么意思|什么意思啊|这是什么意思|"
    r"我不明白|我没懂|没听懂|再说一遍|"
    r"好的|知道了|了解了|明白了|清楚了|"
    r"谢谢|感谢|辛苦了|"
    r"再见|拜拜|下次再聊)",
)

# 纯数据指标词（单独出现时放行）
_DATA_METRIC_PATTERN = re.compile(
    r"^(AQI|aqi|PM2\.5|pm2\.5|PM10|pm10|O3|o3|NO2|no2|SO2|so2|CO|co|"
    r"空气质量指数|细颗粒物|可吸入颗粒物|臭氧|二氧化氮|二氧化硫|一氧化碳|"
    r"温度|湿度|风速|风向|气压|降水|"
    r"空气质量|污染|污染物)[。！？!?\s]*$",
    re.IGNORECASE,
)

# ── 正则模式定义（数据查询相关）─────────────────────────────

_TIME_PATTERN = re.compile(
    r"(昨[天日]|今[天日]|前[天日]|"
    r"上[个]?周|本周|下[个]?周|上[个]?月|本月|下[个]?月|去年|今年|明年|"
    r"本季度|上季度|第[一二三四1-4]季度|"
    r"\d{1,4}年|\d{1,2}月\d{1,2}[日号]?|"
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|"
    r"\d{1,2}[-/]\d{1,2}|"
    r"近\d+[个]?[小时天周月年]|最近\d+[个]?[小时天周月年]|"
    r"最近|近期|当前|现在|目前|"
    r"\d+[点时]|\d+:\d+|"
    r"同比|环比|同期|历史数据?|季度|年度|月度|周度|日均|小时|时段|期间)",
)

_LOCATION_PATTERN = re.compile(
    r"(国控站|省控站|市控站|标准站|乡镇站|微站|监测站|站点|"
    r"监测点|测点|站房|周边站点|附近站点|经纬度|"
    r"全省|全市|全县|各省|各市|各区县|各县区|各乡镇|"
    r"辖区|区域|我的区域|本地|当地|附近|"
    r"[一-龥]{2,12}(省|市|县|区|镇|乡|街道|新区|开发区|园区|流域|站))",
)

_DATA_QUERY_PATTERN = re.compile(
    r"(查|查询|检索|调取|看|看看|给我看|帮我看|"
    r"是多少|有多少|多少|几|第几|哪个最高|哪个最低|最高|最低|峰值|谷值|"
    r"排名|排行|排第|名次|对比|比较|同比|环比|趋势|变化|走势|"
    r"分析|统计|汇总|报表|日报|周报|月报|年报|清单|列表|明细|"
    r"数据|记录|浓度|均值|平均值|小时值|日均|月均|年均|累计|"
    r"超标|达标|优良|优良率|优良天数|污染天数|首要污染物|综合指数|"
    r"空气质量|污染)",
    re.IGNORECASE,
)

# ── 预分类入口 ────────────────────────────────────────────


async def classify_permission_need(
    question: str,
) -> Literal["no_check", "need_check", "uncertain"]:
    """权限需求预分类（纯规则，无 LLM 调用）.

    两层设计：
    1. 正则白名单：快速放行
    2. 正则兜底：模糊场景返回 uncertain，由后续槽位抽取的 need_check 定夺
    """
    normalized = question.strip()
    if not normalized:
        return "no_check"

    # 第一层：正则白名单
    if _CHAT_GREETING_PATTERN.search(normalized):
        return "no_check"

    if _DATA_METRIC_PATTERN.search(normalized):
        return "no_check"

    # 知识问答（不含时间和地点）
    has_time = bool(_TIME_PATTERN.search(normalized))
    has_location = bool(_LOCATION_PATTERN.search(normalized))
    if _KNOWLEDGE_PATTERN.search(normalized) and not has_time and not has_location:
        return "no_check"

    # 对话/质疑
    if _DIALOGUE_PATTERN.search(normalized) and not has_time:
        return "no_check"

    # 第二层：正则兜底
    return _rule_fallback(normalized)


def _rule_fallback(question: str) -> Literal["no_check", "need_check", "uncertain"]:
    """正则兜底."""
    has_time = bool(_TIME_PATTERN.search(question))
    has_location = bool(_LOCATION_PATTERN.search(question))
    has_data_query = bool(_DATA_QUERY_PATTERN.search(question))

    if has_data_query and (has_time or has_location):
        return "need_check"

    if has_time or has_location or has_data_query:
        return "uncertain"

    return "no_check"


def is_conversational(question: str) -> bool:
    """判断是否为纯问候/寒暄/自我介绍类对话轮次（非领域数据查询）.

    用于业务 Skill 语义路由前的快速门控：纯对话轮次不应触发 Skill 匹配，
    避免 "你好"、"你是谁"、"谢谢" 之类问题被 embedding 相似度误命中到数据分析 Skill。

    只使用整句锚定的问候白名单（^...$），因此不会误伤
    "在吗，帮我查保定空气质量" 这类带问候前缀的真实查询。
    """
    normalized = question.strip()
    if not normalized:
        return True
    return bool(_CHAT_GREETING_PATTERN.search(normalized))
