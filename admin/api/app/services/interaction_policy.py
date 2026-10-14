"""部署显式启用和采集起点；默认关闭，不为历史伪造零值。"""

import datetime as dt

from app.config import get_settings


def enabled_at():
    value = get_settings().agent_interactions_enabled_at
    if value is None or value.tzinfo is None or value > dt.datetime.now(dt.UTC):
        return None
    return value


def enabled():
    return get_settings().agent_interactions_enabled and enabled_at() is not None
