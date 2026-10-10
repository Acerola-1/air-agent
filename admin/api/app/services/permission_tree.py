"""把扁平权限列表组装成树。

两处用途：
- 侧边栏菜单：只传当前用户拥有的权限码（`allowed`），得到 TA 可见的菜单；
- 角色授权界面：不传 `allowed`，得到完整权限树供勾选。

父级被过滤掉而子级允许时，子级会被提升为根节点——避免出现"有权限却渲染不出来"
的隐形菜单。种子脚本授权时已经补了祖先，这里是兜底。
"""

from __future__ import annotations

from app.models import Permission
from app.schemas import PermissionNode


def build_tree(
    permissions: list[Permission], allowed: set[str] | None = None
) -> list[PermissionNode]:
    nodes: dict[object, PermissionNode] = {
        p.id: PermissionNode(**PermissionNode.of(p).model_dump(exclude={"children"}))
        for p in permissions
        if allowed is None or p.code in allowed
    }

    roots: list[PermissionNode] = []
    for permission in permissions:
        node = nodes.get(permission.id)
        if node is None:
            continue
        parent = nodes.get(permission.parent_id) if permission.parent_id else None
        if parent is None:
            roots.append(node)
        else:
            parent.children.append(node)

    def sort_key(node: PermissionNode) -> tuple[int, str]:
        return (node.sort, node.code)

    def sort_nodes(items: list[PermissionNode]) -> None:
        items.sort(key=sort_key)
        for item in items:
            sort_nodes(item.children)

    sort_nodes(roots)
    return roots
