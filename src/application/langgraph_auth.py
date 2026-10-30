"""原生平台认证装配；真实助手图解析及受理顺序保留在公共认证层."""

from application.business_policies import configure

configure()

from common.identity.langgraph_auth import auth as auth  # noqa: E402
