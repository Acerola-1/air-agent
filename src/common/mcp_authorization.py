"""企业 MCP 授权解析与凭据提供（任务 7.1/7.2）.

核心规则（specs/tenant-mcp-authorization）：
- 只有**已核验协议**（binding.protocol_version 非 `unverified`）且状态 active 的
  绑定可以发起企业 MCP 调用；无绑定、失效、凭据缺失、身份库故障一律拒绝，
  绝不回退全局/匿名凭据。
- 真实凭据只在发送边界由 `CredentialProvider` 从受保护引用解析
  （`env:VAR` 形式），不进入模型 schema、state、日志或 Trace。
- 调用主体字段（`user_id`/`userId` 等）来自服务端绑定的 external_subject，
  按已核验的**字段映射**覆盖；业务对象参数不动。字段映射未核验时为空——
  这正与"未核验协议默认禁用"闭环。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from common.identity.db import fetch_mcp_binding


class McpAuthorizationError(Exception):
    """企业 MCP 授权不可用（拒绝发起调用；无匿名兜底）."""


UNVERIFIED_PROTOCOL = "unverified"

# ------------------------------------------------------------------ 已核验协议登记（R07）
#
# **白名单语义**：只有登记在此的协议可被解析执行；`unverified`、任意未登记
# 字符串（评审反例 `never-verified-protocol`）一律拒绝——修复前只排除字面量
# `unverified`，任何改名都能绕过门禁拿到默认 Bearer。
# 真实统一平台协议核验完成（任务 7.6）之前不得向本表添加条目；
# `v1-fixture` 仅本地受控 fixture 可用（fixture_only），生产环境即使写入绑定
# 也保持禁用（企业 MCP 安全禁用状态）。


@dataclass(frozen=True)
class ProtocolProfile:
    """已核验协议的执行画像：凭据提供者 + 已核验主体字段映射."""

    protocol_version: str
    provider: str  # 目前仅 "bearer-env"（env: 引用的 Bearer 凭据）
    subject_fields: dict[str, str]
    fixture_only: bool = False


VERIFIED_PROTOCOLS: dict[str, ProtocolProfile] = {
    "v1-fixture": ProtocolProfile(
        protocol_version="v1-fixture",
        provider="bearer-env",
        subject_fields={"user_id": "external_subject"},
        fixture_only=True,
    ),
}


def fixture_mode_enabled() -> bool:
    """本地 fixture 协议只允许显式声明的测试环境使用（默认关闭）."""
    return os.environ.get("TENANT_MCP_FIXTURE_MODE", "").lower() == "true"


def resolve_protocol_profile(protocol_version: str) -> ProtocolProfile | None:
    """未登记/仅 fixture/环境不符 → None（调用方拒绝）."""
    profile = VERIFIED_PROTOCOLS.get(protocol_version)
    if profile is None:
        return None
    if profile.fixture_only and not fixture_mode_enabled():
        return None
    return profile


@dataclass(frozen=True)
class ScopeBinding:
    """已验证身份在某一 MCP 服务上的授权绑定."""

    tenant_id: str
    membership_id: str
    server_key: str
    external_subject: str
    credential_ref: str
    protocol_version: str
    auth_revision: int

    @property
    def cache_scope(self) -> tuple[str, str, str, str, str, str, int]:
        """按成员、凭据引用、协议及修订隔离发现对象，避免同主体绑定复用."""
        return (
            self.tenant_id,
            self.membership_id,
            self.external_subject,
            self.server_key,
            self.credential_ref,
            self.protocol_version,
            self.auth_revision,
        )

    def redacted(self) -> dict[str, Any]:
        """脱敏描述（可进日志/Trace，不含凭据）."""
        return {
            "tenant_id": self.tenant_id,
            "server_key": self.server_key,
            "external_subject": self.external_subject,
            "protocol_version": self.protocol_version,
            "auth_revision": self.auth_revision,
        }


class CredentialProvider:
    """credential_ref → 发送边界凭据.

    当前受支持引用形式：`env:<VAR>`。真实值只在这里出现；解析失败抛授权错误
    （不回退）。
    """

    def auth_headers(self, binding: ScopeBinding) -> dict[str, str]:
        """返回该绑定发送时所需的认证 headers."""
        raise NotImplementedError

    def resolve_secret(self, credential_ref: str) -> str:
        """把凭据引用解析为真实值（仅发送边界调用）."""
        if not credential_ref:
            raise McpAuthorizationError("凭据引用缺失")
        if not credential_ref.startswith("env:"):
            # 其他后端（secret manager 等）不属于本 change；明确拒绝而非忽略
            raise McpAuthorizationError("不支持的凭据引用类型")
        name = credential_ref[len("env:") :]
        value = os.environ.get(name, "")
        if not value:
            raise McpAuthorizationError("凭据不可用")
        return value


class BearerEnvCredentialProvider(CredentialProvider):
    """`env:VAR` → `Authorization: Bearer <value>` 的最小实现."""

    def auth_headers(self, binding: ScopeBinding) -> dict[str, str]:
        """以 Bearer 头承载 env 凭据."""
        return {
            "Authorization": f"Bearer {self.resolve_secret(binding.credential_ref)}"
        }


DEFAULT_PROVIDER = BearerEnvCredentialProvider()


async def resolve_binding(
    *,
    tenant_id: str,
    membership_id: str,
    server_key: str,
    provider: CredentialProvider | None = None,
    require_credential: bool = True,
) -> ScopeBinding:
    """解析并校验绑定；任何不满足即抛 `McpAuthorizationError`（失败关闭）."""
    try:
        row = await fetch_mcp_binding(tenant_id, membership_id, server_key)
    except Exception as exc:  # 身份/授权库故障同样拒绝，不回退
        raise McpAuthorizationError("授权服务不可用") from exc
    if row is None:
        raise McpAuthorizationError("企业未配置该服务的授权绑定")
    if row["status"] != "active":
        raise McpAuthorizationError("授权绑定已停用")
    # R07：白名单门禁。`unverified` 与任何未登记协议名都拒绝；真实协议核验
    # （7.6）完成前登记表不含生产条目，企业 MCP 保持安全禁用。
    profile = resolve_protocol_profile(str(row["protocol_version"]))
    if profile is None:
        if str(row["protocol_version"]) == UNVERIFIED_PROTOCOL:
            raise McpAuthorizationError(
                "统一业务平台的企业认证协议尚未核验，企业 MCP 调用保持禁用"
            )
        raise McpAuthorizationError(
            "该企业 MCP 协议未登记核验，拒绝发起调用（含默认凭据兜底）"
        )
    if profile.provider != "bearer-env":
        # provider 登记未实现同样拒绝，不回退默认 Bearer
        raise McpAuthorizationError("协议登记的凭据提供者不可用，拒绝发起调用")
    binding = ScopeBinding(
        tenant_id=str(row["tenant_id"]),
        membership_id=str(row["membership_id"] or membership_id),
        server_key=server_key,
        external_subject=str(row["external_subject"]),
        credential_ref=str(row["credential_ref"]),
        protocol_version=str(row["protocol_version"]),
        auth_revision=int(row["auth_revision"]),
    )
    if require_credential:
        try:
            (provider or DEFAULT_PROVIDER).resolve_secret(binding.credential_ref)
        except McpAuthorizationError:
            raise
        except Exception as exc:
            raise McpAuthorizationError("凭据不可用") from exc
    return binding


def verified_subject_fields(protocol_version: str) -> dict[str, str]:
    """已核验协议下的主体参数映射（工具参数名 → 绑定字段）.

    字段清单**必须来自核验记录**（docs/mcp-auth-contract.md 与
    VERIFIED_PROTOCOLS 登记表），不做模糊匹配。协议未登记/未核验返回空
    映射——配合 resolve_binding 的白名单拒绝，双保险。
    """
    profile = resolve_protocol_profile(protocol_version)
    if profile is None:
        return {}
    return dict(profile.subject_fields)
