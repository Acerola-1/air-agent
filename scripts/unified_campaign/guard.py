"""Refuse formal resources and disabled execution ranges."""

from __future__ import annotations

from urllib.parse import urlparse

PROTECTED_PORTS = frozenset({15432, 19132})
PROTECTED_DATABASES = frozenset({"air_agent_business", "iam_identity"})
ENABLED_SMOKE = frozenset({("P", "S")})


class CampaignGuardError(ValueError):
    """Raised when a target or profile is outside the allowed range."""


def _port_and_database(value: str) -> tuple[int | None, str | None]:
    text = value.strip()
    if "://" not in text:
        text = (
            f"//{text}"
            if text.startswith("127.0.0.1") or text.startswith("localhost")
            else text
        )
    parsed = urlparse(text)
    port = parsed.port
    database = parsed.path.lstrip("/") or None
    if database and "?" in database:
        database = database.split("?", 1)[0]
    return port, database


def assert_target_allowed(value: str) -> None:
    """Reject formal ports and formal database names in one endpoint or DSN."""
    port, database = _port_and_database(value)
    if port in PROTECTED_PORTS:
        raise CampaignGuardError(f"拒绝正式端口 {port}")
    if database in PROTECTED_DATABASES:
        raise CampaignGuardError(f"拒绝正式库名 {database}")


def assert_targets_allowed(values: list[str]) -> None:
    """Reject every formal target in a list."""
    for value in values:
        if value:
            assert_target_allowed(value)


def assert_execution_allowed(
    *,
    layer: str,
    profile: str,
    allow_real_models: bool,
    allow_load: bool,
    enable_real_models: bool,
    enable_load: bool,
) -> None:
    """Allow only P/S unless the operator explicitly enables a later range.

    The endpoint file and the process flag must both allow an expansion.
    Defaults stay closed so a plan document cannot start load or a real model.
    """
    if allow_real_models and not enable_real_models:
        raise CampaignGuardError("端点要求真实模型，但本次进程未显式开启")
    if allow_load and not enable_load:
        raise CampaignGuardError("端点要求压测，但本次进程未显式开启")
    if (layer, profile) not in ENABLED_SMOKE:
        if not (enable_load and profile in {"R", "F"} and allow_load):
            raise CampaignGuardError(f"默认只允许 P/S，拒绝 {layer}/{profile}")
        if layer == "E" and not (enable_real_models and allow_real_models):
            raise CampaignGuardError("E 层需要同时显式开启真实模型")
    if layer == "E" and not (enable_real_models and allow_real_models):
        raise CampaignGuardError("E 层需要同时显式开启真实模型")
