"""Non-authorizing permission labels used to render the Agent navigation tree."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from app.schemas import PermissionNode

DIRECTORY, MENU, BUTTON = 1, 2, 3

# (code, name, type, parent_code, path, icon, sort, grantable_to_tenants)
# The catalog describes presentation only. Every API action checks IAM permissions server-side.
PERMISSION_TREE: list[tuple[str, str, int, str | None, str | None, str | None, int, bool]] = [
    ("system", "系统管理", DIRECTORY, None, None, "settings", 10, True),
    ("system:user", "平台账号", MENU, "system", "/system/users", "users", 10, False),
    ("user:read", "查看账号", BUTTON, "system:user", None, None, 10, False),
    ("user:create", "新建账号", BUTTON, "system:user", None, None, 20, False),
    ("user:update", "编辑账号", BUTTON, "system:user", None, None, 30, False),
    ("user:delete", "删除账号", BUTTON, "system:user", None, None, 40, False),
    ("user:assign_role", "分配平台角色", BUTTON, "system:user", None, None, 50, False),
    ("user:reset_password", "重置密码", BUTTON, "system:user", None, None, 60, False),
    ("member:link", "授权关联成员", BUTTON, "system:user", None, None, 70, False),
    ("system:tenant", "企业列表", MENU, "system", "/platform/tenants", "building", 15, False),
    ("tenant:read", "查看企业", BUTTON, "system:tenant", None, None, 10, False),
    ("tenant:create", "创建企业", BUTTON, "system:tenant", None, None, 20, False),
    ("tenant:update", "停用/修改企业", BUTTON, "system:tenant", None, None, 30, False),
    ("system:role", "角色管理", MENU, "system", "/system/roles", "shield", 20, True),
    ("role:read", "查看角色", BUTTON, "system:role", None, None, 10, True),
    ("role:create", "新建角色", BUTTON, "system:role", None, None, 20, True),
    ("role:update", "编辑角色", BUTTON, "system:role", None, None, 30, True),
    ("role:delete", "删除角色", BUTTON, "system:role", None, None, 40, True),
    ("role:assign_permission", "分配权限", BUTTON, "system:role", None, None, 50, True),
    ("system:permission", "权限定义", MENU, "system", "/system/permissions", "key", 30, False),
    ("permission:read", "查看权限定义", BUTTON, "system:permission", None, None, 10, False),
    ("system:audit", "审计日志", MENU, "system", "/system/audit", "history", 40, True),
    ("audit:read", "查看审计", BUTTON, "system:audit", None, None, 10, True),
    ("enterprise", "企业管理", DIRECTORY, None, None, "building", 15, True),
    ("enterprise:member", "成员管理", MENU, "enterprise", "/enterprise/members", "users", 10, True),
    ("member:read", "查看成员", BUTTON, "enterprise:member", None, None, 10, True),
    ("member:manage", "成员管理", BUTTON, "enterprise:member", None, None, 20, True),
    ("member:remove", "移出成员", BUTTON, "enterprise:member", None, None, 30, True),
    (
        "enterprise:department",
        "部门管理",
        MENU,
        "enterprise",
        "/enterprise/departments",
        "sitemap",
        20,
        True,
    ),
    ("department:read", "查看部门", BUTTON, "enterprise:department", None, None, 10, True),
    ("department:manage", "管理部门", BUTTON, "enterprise:department", None, None, 20, True),
    ("enterprise:role", "企业角色", MENU, "enterprise", "/enterprise/roles", "shield", 30, True),
    ("enterprise:mcp", "MCP 授权", MENU, "enterprise", "/enterprise/mcp", "api", 40, True),
    ("mcp:binding:read", "查看授权状态", BUTTON, "enterprise:mcp", None, None, 10, True),
    ("mcp:binding:manage", "管理授权绑定", BUTTON, "enterprise:mcp", None, None, 20, False),
    ("business", "业务管理", DIRECTORY, None, None, "database", 20, True),
    ("business:region", "行政区划", MENU, "business", "/business/regions", "map", 10, True),
    ("region:read", "查看区划", BUTTON, "business:region", None, None, 10, True),
    ("region:manage", "维护区划", BUTTON, "business:region", None, None, 20, False),
    ("business:followed", "用户关注城市", MENU, "business", "/business/followed", "star", 20, True),
    ("followed:read", "查看关注", BUTTON, "business:followed", None, None, 10, True),
    ("followed:manage", "维护关注", BUTTON, "business:followed", None, None, 20, True),
    ("business:runs", "运行台账", MENU, "business", "/business/runs", "activity", 30, True),
    ("agent:operations:read", "查看企业运行明细", BUTTON, "business:runs", None, None, 10, True),
    ("agent:metrics:read", "查看运行统计", BUTTON, "business:runs", None, None, 20, True),
    ("agent:content:read", "读取问题原文", BUTTON, "business:runs", None, None, 30, True),
    ("agent:replay:manage", "重放与清理运维", BUTTON, "business:runs", None, None, 40, False),
    ("agent:thread:delete", "删除任意会话", BUTTON, "business:runs", None, None, 50, False),
    ("agent:pricing:manage", "配置模型价格与重算", BUTTON, "business:runs", None, None, 60, True),
]


def build_display_tree(permission_codes: Iterable[str]) -> list[PermissionNode]:
    """Build display-only nodes from a trusted permission set and its ancestors."""
    definitions = {row[0]: row for row in PERMISSION_TREE}
    visible = set(permission_codes) & definitions.keys()
    pending = list(visible)
    while pending:
        parent = definitions[pending.pop()][3]
        if parent and parent not in visible:
            visible.add(parent)
            pending.append(parent)

    nodes: dict[str, PermissionNode] = {}
    parents: dict[str, str | None] = {}
    for code in visible:
        _, name, kind, parent, path, icon, sort, _grantable = definitions[code]
        nodes[code] = PermissionNode(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"air-agent-permission:{code}"),
            code=code,
            name=name,
            type=kind,
            path=path,
            icon=icon,
            sort=sort,
            is_active=True,
            children=[],
        )
        parents[code] = parent

    roots: list[PermissionNode] = []
    for code, node in nodes.items():
        parent = nodes.get(parents[code] or "")
        (parent.children if parent else roots).append(node)

    def sort_nodes(items: list[PermissionNode]) -> None:
        items.sort(key=lambda node: (node.sort, node.code))
        for item in items:
            sort_nodes(item.children)

    sort_nodes(roots)
    return roots
