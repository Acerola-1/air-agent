"""同源HTTP应用装配，保留原有接口与生命周期."""

from application.business_policies import configure

configure()

from common.artifact_http import app as app  # noqa: E402
