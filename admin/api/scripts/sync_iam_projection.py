"""I2 同 UUID 业务引用投影同步（dry-run 默认；写入需 --apply + 显式库）.

从 IAM 管理 API 拉取用户/企业/成员，把与 IAM **同 UUID** 的最小引用写入
iam_reference_* 投影表。只服务业务 FK/RLS/展示——不迁移口令、会话、角色或
权限；同名不同 UUID / 未知归属只报告，绝不自动合并（A10 负例）。

正式存量导入属于正式切换前置，本轮只对隔离夹具实证。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def _iam_get(base: str, path: str, bearer: str) -> dict:
    req = urllib.request.Request(
        f"{base.rstrip('/')}{path}", headers={"Authorization": f"Bearer {bearer}"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def _pages(base: str, path: str, bearer: str) -> list[dict]:
    """完整展开服务端游标；拒绝截断或不可访问企业被当作空集合。"""
    import urllib.parse

    items = []
    seen = set()
    cursor = None
    while True:
        query = {"limit": 200}
        if cursor:
            query["cursor"] = cursor
        data = _iam_get(base, path + "?" + urllib.parse.urlencode(query), bearer)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ValueError("IAM 分页响应非法")
        items.extend(data["items"])
        cursor = data.get("next_cursor")
        if not cursor:
            return items
        if cursor in seen:
            raise ValueError("IAM 分页游标循环")
        seen.add(cursor)


def fetch_tenants(base: str, bearer: str) -> list[dict]:
    return _pages(base, "/v1/admin/tenants", bearer)


def fetch_members(base: str, bearer: str, tenant_id: str) -> list[dict]:
    me = _iam_get(base, "/v1/auth/me", bearer)
    if (me.get("current_tenant") or {}).get("id") != tenant_id:
        raise ValueError("当前会话不能导出其他企业；清单不完整，未写入")
    members = _pages(base, "/v1/admin/members", bearer)
    if any(item.get("tenant_id") != tenant_id for item in members):
        raise ValueError("成员接口仅当前企业可见；本次清单不完整，不能同步")
    return members


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iam-base", default=os.environ.get("IAM_BASE_URL", ""))
    parser.add_argument("--iam-token", default=os.environ.get("IAM_ADMIN_TOKEN", ""))
    parser.add_argument("--database-uri", default=os.environ.get("DATABASE_URI", ""))
    parser.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    args = parser.parse_args()
    if not args.iam_base or not args.iam_token or not args.database_uri:
        print("缺少 --iam-base / --iam-token / --database-uri", file=sys.stderr)
        return 2

    tenants = fetch_tenants(args.iam_base, args.iam_token)
    plan: list[dict] = []
    for tenant in tenants:
        plan.append(
            {
                "kind": "tenant",
                "id": tenant["id"],
                "code": tenant["code"],
                "name": tenant["name"],
                "status": tenant["status"],
                "auth_revision": tenant.get("auth_revision", 1),
            }
        )
        for member in fetch_members(args.iam_base, args.iam_token, tenant["id"]):
            user = member.get("user") or {}
            plan.append(
                {
                    "kind": "user",
                    "id": user.get("id"),
                    "account": user.get("account"),
                    "status": user.get("status"),
                    "auth_revision": user.get("auth_revision", 0),
                }
            )
            plan.append(
                {
                    "kind": "membership",
                    "id": member.get("id"),
                    "tenant_id": tenant["id"],
                    "user_id": user.get("id"),
                    "status": member.get("status"),
                    "revision": member.get("revision", 1),
                }
            )

    # 差异报告：同 account 不同 id（绝不自动合并，正式切换受阻项）
    accounts: dict[str, set[str]] = {}
    for item in plan:
        if item["kind"] == "user" and item.get("account") and item.get("id"):
            import re

            accounts.setdefault(item["account"], set()).add(item["id"])
            alias = re.sub(r"[^a-zA-Z0-9_-]", "_", item["account"])[:32]
            if len(alias) >= 3:
                accounts.setdefault(alias, set()).add(item["id"])
    conflicts = {a: ids for a, ids in accounts.items() if len(ids) > 1}
    import psycopg

    with psycopg.connect(args.database_uri) as check_conn:
        check_conn.execute("SET TRANSACTION READ ONLY")
        for table in ("users", "iam_reference_users"):
            if check_conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]:
                for local_id, local_account in check_conn.execute(
                    f"SELECT id::text, account FROM {table}"
                ):
                    if local_account in accounts:
                        accounts[local_account].add(local_id)
        conflicts = {account: ids for account, ids in accounts.items() if len(ids) > 1}
    print(f"plan items: {len(plan)}; tenants: {sum(1 for i in plan if i['kind'] == 'tenant')}")
    if conflicts:
        print("NAME-UUID CONFLICTS (block formal cutover, no auto-merge):")
        for account, ids in sorted(conflicts.items()):
            print(f"  {account}: {sorted(ids)}")

    if conflicts:
        print("同步已阻止：存在同名不同 UUID；未写入。", file=sys.stderr)
        return 2

    if not args.apply:
        print("dry-run：未写入。加 --apply 写入隔离库。")
        return 0

    import psycopg

    with psycopg.connect(args.database_uri) as conn:
        for item in plan:
            if item["kind"] == "tenant":
                conn.execute(
                    "INSERT INTO iam_reference_tenants (id, code, name, status, auth_revision)"
                    " VALUES (%s, %s, %s, %s, %s)"
                    " ON CONFLICT (id) DO UPDATE SET code=EXCLUDED.code, name=EXCLUDED.name,"
                    " status=EXCLUDED.status, auth_revision=EXCLUDED.auth_revision,"
                    " synced_at=now()",
                    (item["id"], item["code"], item["name"], item["status"], item["auth_revision"]),
                )
            elif item["kind"] == "user" and item.get("id"):
                conn.execute(
                    "INSERT INTO iam_reference_users (id, account, status, auth_revision)"
                    " VALUES (%s, %s, %s, %s)"
                    " ON CONFLICT (id) DO UPDATE SET account=EXCLUDED.account,"
                    " status=EXCLUDED.status, auth_revision=EXCLUDED.auth_revision,"
                    " synced_at=now()",
                    (item["id"], item["account"], item["status"], item["auth_revision"]),
                )
            elif item["kind"] == "membership" and item.get("id"):
                conn.execute(
                    "INSERT INTO iam_reference_memberships"
                    " (id, tenant_id, user_id, status, revision)"
                    " VALUES (%s, %s, %s, %s, %s)"
                    " ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status,"
                    " revision=EXCLUDED.revision, synced_at=now()",
                    (
                        item["id"],
                        item["tenant_id"],
                        item["user_id"],
                        item["status"],
                        item["revision"],
                    ),
                )
        conn.commit()
    print("applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
