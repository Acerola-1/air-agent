"""受保护 MCP 服务连接配置（change simplify-agent-business-authorization 任务 3.1）.

连接是**平台部署配置**，不是人员授权：

- 来源只有进程环境（`AGENT_MCP_CONNECTIONS_JSON`）或受保护文件
  （`AGENT_MCP_CONNECTIONS_FILE`）；浏览器、模型、运行时 metadata 都不能指定
  地址、凭据或工具范围。
- 支持两种明确声明的接入方式：
  - `shared_readonly`：共享只读数据连接，不伪造 Bearer/user_id/租户范围；
  - `tenant`：企业连接，必须声明 `tenant_id`，且只允许真实认证方式。
- 凭据只保存引用（`env:VAR`），真实值仅在发送边界解析；配置/日志/Trace/
  checkpoint 不出现秘密。
- `approved_query_tools` 是服务侧审核通过的查询工具白名单；业务注册表再按
  业务显式登记取交集，未分类的新工具不会自动开放。

配置非法时抛 `MCPConnectionConfigError`，调用方失败关闭（不降级到匿名或
旧环境变量地址）。文件/环境都未配置时返回空目录（不代表任何连接可用）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

CONNECTIONS_SCHEMA_VERSION = "agent.mcp-connections.v1"

_SERVER_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ALLOWED_TOP_LEVEL = {"schema_version", "revision", "servers", "notes"}
_ALLOWED_SERVER_KEYS = {
    "server_key",
    "url",
    "scope",
    "tenant_id",
    "auth_mode",
    "credential_ref",
    "enabled",
    "approved_query_tools",
    "display_name",
}
_SCOPES = {"shared_readonly", "tenant"}
_AUTH_MODES = {"none", "bearer_env"}


class MCPConnectionConfigError(RuntimeError):
    """连接配置缺失、非法或歧义：必须失败关闭，不回退旧绑定/匿名地址."""


@dataclass(frozen=True)
class MCPServerConnection:
    """一个已校验的服务连接（仅非秘密字段）."""

    server_key: str
    url: str
    scope: str
    tenant_id: str | None
    auth_mode: str
    credential_ref: str | None
    enabled: bool
    approved_query_tools: frozenset[str]
    display_name: str
    revision: int
    config_revision: int

    @property
    def is_shared_readonly(self) -> bool:
        """是否为显式共享只读连接（不声称企业数据隔离）."""
        return self.scope == "shared_readonly"

    @property
    def credential_digest(self) -> str:
        """认证引用摘要（不解析秘密；用于缓存隔离与轮换检测）."""
        raw = f"{self.auth_mode}|{self.credential_ref or ''}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def allows_tool(self, tool_name: str) -> bool:
        """只有显式审核通过的查询工具可以执行."""
        return tool_name in self.approved_query_tools

    def redacted(self) -> dict[str, Any]:
        """脱敏描述（可进入日志/普通事件）."""
        return {
            "server_key": self.server_key,
            "scope": self.scope,
            "tenant_id": self.tenant_id,
            "auth_mode": self.auth_mode,
            "enabled": self.enabled,
            "revision": self.revision,
            "approved_tool_count": len(self.approved_query_tools),
        }


@dataclass(frozen=True)
class MCPConnectionConfig:
    """一次加载的连接配置快照."""

    schema_version: str
    revision: int
    servers: tuple[MCPServerConnection, ...]

    def resolve(
        self, server_key: str, tenant_id: str | None
    ) -> MCPServerConnection | None:
        """解析当前企业可用的唯一连接；歧义配置在加载时已拒绝."""
        candidates = [
            item
            for item in self.servers
            if item.enabled
            and item.server_key == server_key
            and (
                item.scope == "shared_readonly"
                or (
                    item.scope == "tenant"
                    and str(item.tenant_id) == str(tenant_id or "")
                )
            )
        ]
        if len(candidates) > 1:
            raise MCPConnectionConfigError("同一企业/服务存在多个启用连接，配置歧义")
        return candidates[0] if candidates else None

    def available_for_tenant(self, tenant_id: str | None) -> list[MCPServerConnection]:
        """当前企业可见的启用连接（共享 + 本企业专属）."""
        return [
            item
            for item in self.servers
            if item.enabled
            and (
                item.scope == "shared_readonly"
                or (
                    item.scope == "tenant"
                    and str(item.tenant_id) == str(tenant_id or "")
                )
            )
        ]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MCPConnectionConfigError(message)


def _validate_url(raw: Any) -> str:
    _require(isinstance(raw, str) and bool(raw.strip()), "连接 URL 必须是非空字符串")
    text = raw.strip()
    parsed = urlparse(text)
    _require(parsed.scheme in {"http", "https"}, "连接 URL 仅支持 http/https")
    _require(bool(parsed.netloc), "连接 URL 缺少主机名")
    _require(
        parsed.username is None and parsed.password is None, "连接 URL 不能内嵌凭据"
    )
    _require(not parsed.fragment, "连接 URL 不能包含 fragment")
    _require(not parsed.query, "连接 URL 不能包含查询串（防止令牌进入地址）")
    return text


def _validate_server(raw: Any, *, config_revision: int) -> MCPServerConnection:
    _require(isinstance(raw, dict), "servers 条目必须是对象")
    unknown = set(raw) - _ALLOWED_SERVER_KEYS
    _require(not unknown, f"连接配置包含未知字段: {sorted(unknown)}")
    server_key = raw.get("server_key")
    _require(
        isinstance(server_key, str) and bool(_SERVER_KEY_PATTERN.fullmatch(server_key)),
        "server_key 非法",
    )
    url = _validate_url(raw.get("url"))
    scope = raw.get("scope")
    _require(scope in _SCOPES, "scope 必须是 shared_readonly 或 tenant")
    tenant_id = raw.get("tenant_id")
    if scope == "shared_readonly":
        _require(tenant_id in (None, ""), "共享只读连接不能声明 tenant_id")
        tenant_id = None
    else:
        _require(
            isinstance(tenant_id, str) and bool(tenant_id), "企业连接必须声明 tenant_id"
        )
        try:
            tenant_id = str(uuid.UUID(tenant_id))
        except (TypeError, ValueError) as exc:
            raise MCPConnectionConfigError("tenant_id 必须是规范 UUID") from exc
    auth_mode = raw.get("auth_mode")
    _require(auth_mode in _AUTH_MODES, "auth_mode 必须是 none 或 bearer_env")
    credential_ref = raw.get("credential_ref")
    if auth_mode == "none":
        _require(credential_ref in (None, ""), "auth_mode=none 不能声明 credential_ref")
        credential_ref = None
    else:
        _require(
            isinstance(credential_ref, str) and credential_ref.startswith("env:"),
            "bearer_env 必须使用 env:VAR 凭据引用",
        )
        _require(len(credential_ref) > len("env:"), "凭据引用缺少变量名")
        credential_ref = credential_ref.strip()
    if scope == "tenant":
        # 企业连接不允许匿名：没有真实认证就不是企业连接。
        _require(auth_mode == "bearer_env", "企业连接必须使用真实认证方式")
    enabled = raw.get("enabled")
    _require(isinstance(enabled, bool), "enabled 必须是布尔")
    tools = raw.get("approved_query_tools")
    _require(isinstance(tools, list), "approved_query_tools 必须是数组")
    cleaned: list[str] = []
    for name in tools:
        _require(
            isinstance(name, str) and bool(name.strip()) and len(name) <= 200,
            "approved_query_tools 含非法工具名",
        )
        _require(name not in cleaned, "approved_query_tools 不能重复")
        cleaned.append(name)
    _require(not enabled or bool(cleaned), "启用的连接必须显式登记至少一个查询工具")
    display_name = raw.get("display_name") or server_key
    _require(
        isinstance(display_name, str) and bool(display_name.strip()),
        "display_name 非法",
    )
    return MCPServerConnection(
        server_key=server_key,
        url=url,
        scope=str(scope),
        tenant_id=tenant_id,
        auth_mode=str(auth_mode),
        credential_ref=credential_ref,
        enabled=enabled,
        approved_query_tools=frozenset(cleaned),
        display_name=display_name.strip(),
        revision=config_revision,
        config_revision=config_revision,
    )


def parse_connections(payload: Any) -> MCPConnectionConfig:
    """严格解析并校验连接配置（纯函数，便于测试）."""
    _require(isinstance(payload, dict), "连接配置必须是 JSON 对象")
    unknown = set(payload) - _ALLOWED_TOP_LEVEL
    _require(not unknown, f"连接配置包含未知顶层字段: {sorted(unknown)}")
    _require(
        payload.get("schema_version") == CONNECTIONS_SCHEMA_VERSION,
        f"schema_version 必须 = {CONNECTIONS_SCHEMA_VERSION}",
    )
    revision = payload.get("revision")
    _require(
        isinstance(revision, int) and not isinstance(revision, bool) and revision >= 1,
        "revision 必须是正整数",
    )
    servers_raw = payload.get("servers")
    _require(isinstance(servers_raw, list), "servers 必须是数组")
    servers = tuple(
        _validate_server(item, config_revision=revision) for item in servers_raw
    )
    keys = [(item.server_key, item.scope, item.tenant_id) for item in servers]
    _require(
        len(keys) == len(set(keys)), "连接配置存在重复的 (server_key, scope, tenant_id)"
    )
    shared_keys = {
        item.server_key for item in servers if item.scope == "shared_readonly"
    }
    tenant_keys = {item.server_key for item in servers if item.scope == "tenant"}
    _require(
        not (shared_keys & tenant_keys),
        "同一 server_key 不能同时声明共享只读与企业连接（避免认证降级歧义）",
    )
    return MCPConnectionConfig(
        schema_version=CONNECTIONS_SCHEMA_VERSION,
        revision=revision,
        servers=servers,
    )


_CACHE: tuple[tuple[str, str], MCPConnectionConfig] | None = None


def _source() -> tuple[str, str]:
    """返回 (来源标识, 原始文本)；两者都没有时返回空配置来源."""
    inline = os.environ.get("AGENT_MCP_CONNECTIONS_JSON", "")
    file_path = os.environ.get("AGENT_MCP_CONNECTIONS_FILE", "")
    if inline and file_path:
        raise MCPConnectionConfigError(
            "AGENT_MCP_CONNECTIONS_JSON 与 AGENT_MCP_CONNECTIONS_FILE 不能同时设置"
        )
    if inline:
        return "env", inline
    if file_path:
        path = Path(file_path).expanduser()
        try:
            return "file", path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MCPConnectionConfigError("连接配置文件不可读") from exc
    return (
        "empty",
        '{"schema_version":"'
        + CONNECTIONS_SCHEMA_VERSION
        + '","revision":1,"servers":[]}',
    )


def load_connections() -> MCPConnectionConfig:
    """加载当前连接配置（按来源内容摘要缓存；变更立即生效）."""
    global _CACHE
    source, raw = _source()
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    key = (source, digest)
    if _CACHE is not None and _CACHE[0] == key:
        return _CACHE[1]
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise MCPConnectionConfigError("连接配置不是合法 JSON") from exc
    config = parse_connections(payload)
    _CACHE = (key, config)
    return config


def reset_connections_cache() -> None:
    """测试/配置热切换辅助：清空解析缓存."""
    global _CACHE
    _CACHE = None


def connection_for(
    server_key: str, tenant_id: str | None
) -> MCPServerConnection | None:
    """解析当前企业可用连接；未配置返回 None（调用方拒绝，不回退 env URL）."""
    return load_connections().resolve(server_key, tenant_id)


def connection_scope_key(
    *,
    tenant_id: str,
    business_code: str,
    business_policy_version: str,
    connection: MCPServerConnection,
) -> tuple[str, str, str, str, int, str]:
    """发现/连接缓存键：企业 + 业务 + 业务策略 + 服务 + 连接修订 + 认证引用摘要.

    不含成员/人员许可——人员许可不跨请求缓存，由每次发送的业务守卫复查。
    """
    return (
        str(tenant_id),
        str(business_code),
        str(business_policy_version),
        connection.server_key,
        int(connection.config_revision),
        connection.credential_digest,
    )
