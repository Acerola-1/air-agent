"""需求洞察域错误（路由按 code 映射 HTTP 状态，不泄露原文/内部对象）。"""

from __future__ import annotations


class InsightsError(Exception):
    """本域可安全展示的错误；message 不包含问题原文、向量或凭据。"""

    status_code = 400

    def __init__(self, message: str, *, code: str = "insights_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class InsightsForbidden(InsightsError):
    status_code = 403


class InsightsNotFound(InsightsError):
    status_code = 404


class InsightsConflict(InsightsError):
    status_code = 409


class InsightsPayloadInvalid(InsightsError):
    status_code = 422


class InsightsUnavailable(InsightsError):
    status_code = 503


def forbidden(message: str, *, code: str = "permission_denied") -> InsightsForbidden:
    return InsightsForbidden(message, code=code)
