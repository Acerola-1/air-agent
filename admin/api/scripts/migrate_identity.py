"""历史身份与企业归属迁移工具（任务 8.1–8.3）.

子命令：
- `preflight --manifest m.json [--json-users path]`：只读预检。验证旧 JSON 账号、
  PG 账号、企业/成员/线程/归档/审计归属；输出计数与冲突类别报告（不含密码哈希、
  不含对话原文），零写入。
- `apply --manifest m.json [--json-users path]`：按 manifest 显式归属执行迁移。
  台账（migration_ledger）记录清单指纹与实体映射；幂等可续跑；硬冲突整体停止；
  未能映射的资源进入 quarantined（保持不可访问，不默认归属）。
- `report --manifest m.json`：打印台账与计数核对。

规则（spec tenant-identity-migration）：
- 不按同名自动合并身份：同名不同 UUID 必须 manifest 显式给出 canonical；
- 平台/企业授权由 manifest 决定，绝不自动扩散旧超级角色；
- 旧审计无法确定租户时保留历史域（tenant_id 不动，不并入任何企业列表）；
- 历史消息不迁移；线程仅受限更新 metadata 的归属键与 thread_ownership。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import String, bindparam, text  # noqa: E402

from app.db import OwnerSessionLocal  # noqa: E402
from app.services.tenant_seed import ensure_tenant_admin_role  # noqa: E402

CONFLICT_SAME_NAME_DIFF_ID = "same_account_conflict"
CONFLICT_PG_USER_MISSING = "pg_user_missing"
CONFLICT_TENANT_MISSING = "tenant_missing"
CONFLICT_HASH_IMPORT = "password_hash_incompatible"
CONFLICT_THREAD_NOT_IN_PG = "thread_not_found"
QUARANTINE_UNMAPPED_JSON = "json_user_unmapped"
QUARANTINE_UNOWNED_THREAD = "thread_unowned"


def load_manifest(path: str) -> dict[str, Any]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    _validate_manifest(manifest)
    return manifest


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("version") != 1:
        raise ValueError("manifest.version 必须为 1")
    for key in ("tenants", "users", "memberships", "threads"):
        if not isinstance(manifest.get(key), list):
            raise ValueError(f"manifest.{key} 必须是数组")
    for user in manifest["users"]:
        source = user.get("source")
        if source not in ("pg", "json"):
            raise ValueError(f"用户 {user.get('account')} source 必须是 pg 或 json")
        if source == "json" and not user.get("json_id"):
            raise ValueError(f"json 来源用户 {user.get('account')} 必须提供 json_id")
        if source == "pg" and not user.get("pg_user_id"):
            raise ValueError(f"pg 来源用户 {user.get('account')} 必须提供 pg_user_id")
        # R06：同名不同 UUID 的显式决议字段
        canonical = user.get("canonical")
        if canonical is not None and canonical not in ("pg", "json"):
            raise ValueError(f"用户 {user.get('account')} canonical 必须是 pg 或 json")
        if canonical == "pg" and not user.get("resolve_pg_user_id"):
            raise ValueError(
                f"用户 {user.get('account')} canonical=pg 必须显式提供 resolve_pg_user_id"
            )
    accounts = [u["account"] for u in manifest["users"]]
    if len(accounts) != len(set(accounts)):
        raise ValueError("manifest.users 存在重复 account")


def manifest_fingerprint(manifest: dict[str, Any]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_json_users(path: str) -> dict[str, dict[str, Any]]:
    file = Path(path)
    if not file.exists():
        return {}
    data = json.loads(file.read_text(encoding="utf-8"))
    return {str(user["id"]): user for user in data.values()}


class MigrationRunner:
    """preflight/apply 共享的只读校验 + 受控写入."""

    def __init__(
        self,
        manifest: dict[str, Any],
        json_users: dict[str, dict[str, Any]],
        platform_metadata_patch: Any = None,
    ) -> None:
        self.manifest = manifest
        self.json_users = json_users
        self.fingerprint = manifest_fingerprint(manifest)
        self.conflicts: list[dict[str, str]] = []
        self.counts: dict[str, int] = {}
        # R06：线程 metadata 只经"受限受支持 API"更新（mt-v1 票据 + PATCH
        # /threads/{id}）。测试可注入替身；默认实现见 _platform_metadata_patch。
        self._patch_override = platform_metadata_patch

    # ------------------------------------------------------------ 只读预检

    async def preflight(self) -> dict[str, Any]:
        self.conflicts = []
        async with OwnerSessionLocal() as session:
            pg_users: dict[str, str] = {
                row["account"]: str(row["id"])
                for row in (await session.execute(text("SELECT id, account FROM users"))).mappings()
            }
            tenants = {
                row["code"]
                for row in (await session.execute(text("SELECT code FROM tenants"))).mappings()
            }
            thread_ids = {
                str(row["thread_id"])
                for row in (await session.execute(text("SELECT thread_id FROM thread"))).mappings()
            }
            json_ids = set(self.json_users)
            user_mappings = dict(
                (
                    await session.execute(
                        text(
                            "SELECT source_key, target_key FROM migration_ledger"
                            " WHERE manifest_fingerprint=:f AND entity_type='user' AND "
                            "status='applied'"
                        ),
                        {"f": self.fingerprint},
                    )
                ).all()
            )

            for user in self.manifest["users"]:
                account = user["account"]
                source = user["source"]
                if source == "pg" and account not in pg_users:
                    self.conflicts.append({"type": CONFLICT_PG_USER_MISSING, "key": account})
                elif source == "pg" and str(user.get("pg_user_id")) != pg_users[account]:
                    self.conflicts.append({"type": CONFLICT_SAME_NAME_DIFF_ID, "key": account})
                if source == "json":
                    if user.get("json_id") not in json_ids:
                        self.conflicts.append({"type": CONFLICT_SAME_NAME_DIFF_ID, "key": account})
                    # R06：同名不同 UUID 必须显式 canonical 决议，preflight 不得 clean
                    existing_pg_id = pg_users.get(account)
                    if existing_pg_id is not None:
                        canonical = user.get("canonical")
                        mapped_id = user_mappings.get(str(user.get("json_id")))
                        if mapped_id is not None and str(mapped_id) == existing_pg_id:
                            pass  # 同一清单已提交的来源映射，允许续传未完成的实体。
                        elif canonical != "pg":
                            self.conflicts.append(
                                {
                                    "type": CONFLICT_SAME_NAME_DIFF_ID,
                                    "key": account,
                                    "note": (
                                        "同名 PG 账号与 JSON 身份 UUID 不同，"
                                        "必须显式提供 canonical=pg + resolve_pg_user_id"
                                    ),
                                }
                            )
                        elif str(user.get("resolve_pg_user_id")) != str(existing_pg_id):
                            self.conflicts.append(
                                {
                                    "type": CONFLICT_SAME_NAME_DIFF_ID,
                                    "key": account,
                                    "note": "resolve_pg_user_id 与实际 PG 身份不一致",
                                }
                            )
                    stored_hash = str(
                        self.json_users.get(str(user.get("json_id")), {}).get("passwordHash", "")
                    )
                    if stored_hash and not self._bcrypt_parseable(stored_hash):
                        self.conflicts.append({"type": CONFLICT_HASH_IMPORT, "key": account})

            for tenant in self.manifest["tenants"]:
                if tenant.get("create_if_missing") is False and tenant["code"] not in tenants:
                    self.conflicts.append({"type": CONFLICT_TENANT_MISSING, "key": tenant["code"]})
            for membership in self.manifest["memberships"]:
                known_users = {u["account"] for u in self.manifest["users"]}
                if membership["account"] not in known_users:
                    self.conflicts.append(
                        {"type": CONFLICT_PG_USER_MISSING, "key": membership["account"]}
                    )
                tenant_ok = membership["tenant_code"] in tenants or any(
                    t["code"] == membership["tenant_code"] and t.get("create_if_missing", False)
                    for t in self.manifest["tenants"]
                )
                if not tenant_ok:
                    self.conflicts.append(
                        {"type": CONFLICT_TENANT_MISSING, "key": membership["tenant_code"]}
                    )
            for thread in self.manifest["threads"]:
                if thread["thread_id"] not in thread_ids:
                    self.conflicts.append(
                        {"type": CONFLICT_THREAD_NOT_IN_PG, "key": thread["thread_id"]}
                    )

            mapped_json = {
                str(u.get("json_id")) for u in self.manifest["users"] if u.get("json_id")
            }
            unmapped_json = json_ids - mapped_json
            owned_threads = {
                str(row["thread_id"])
                for row in (
                    await session.execute(text("SELECT thread_id FROM thread_ownership"))
                ).mappings()
            }
            all_threads = {
                str(row["thread_id"])
                for row in (await session.execute(text("SELECT thread_id FROM thread"))).mappings()
            }
            manifest_threads = {t["thread_id"] for t in self.manifest["threads"]}
            unattributed = all_threads - owned_threads - manifest_threads

            async def _count(sql: str) -> int:
                return int(await session.scalar(text(sql)) or 0)

            self.counts = {
                "json_users_total": len(json_ids),
                "json_users_unmapped": len(unmapped_json),
                "pg_users_total": len(pg_users),
                "threads_total": len(all_threads),
                "threads_manifest": len(manifest_threads),
                "threads_unattributed": len(unattributed),
                "artifacts_total": await _count("SELECT count(*) FROM tool_artifact_storage"),
                "artifacts_unattributed": await _count(
                    "SELECT count(*) FROM tool_artifact_storage"
                    " WHERE tenant_id IS NULL OR tenant_id=''"
                ),
                "audits_legacy": await _count(
                    "SELECT count(*) FROM audit_logs WHERE tenant_id IS NULL"
                ),
            }
        return {
            "manifest_fingerprint": self.fingerprint,
            "counts": self.counts,
            "conflicts": self.conflicts,
            "clean": not self.conflicts,
        }

    @staticmethod
    def _bcrypt_parseable(stored_hash: str) -> bool:
        import bcrypt

        try:
            bcrypt.checkpw(b"probe", stored_hash.encode("ascii"))
            return True
        except ValueError:
            return False

    async def _platform_metadata_patch(self, thread_id: str, tenant_id: str, user_id: str) -> int:
        """走原生平台 PATCH /threads/{id}（受支持 API），携带 mt-v1 受限票据.

        失败关闭语义：无 RUNTIME_TICKET_KEYS 配置直接报错停止——绝不回退为
        直接 UPDATE 平台私有表。
        """
        if self._patch_override is not None:
            return int(await self._patch_override(thread_id, tenant_id, user_id))
        import httpx

        from app.config import get_settings
        from app.security import sign_migration_ticket

        keys = get_settings().runtime_ticket_key_pairs
        if not keys:
            raise SystemExit(
                "RUNTIME_TICKET_KEYS 未配置：无法签发迁移票据，停止（不触碰平台私有表）"
            )
        base = os.environ.get("LANGGRAPH_MIGRATION_BASE_URL", "http://127.0.0.1:2024")
        audience = os.environ.get("RUNTIME_TICKET_MIGRATION_AUDIENCE", "langgraph-migration")
        ticket = sign_migration_ticket(thread_id, tenant_id, user_id, keys, audience=audience)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                res = await client.patch(
                    f"{base}/threads/{thread_id}",
                    json={"metadata": {"user_id": user_id, "tenant_id": tenant_id}},
                    headers={"Authorization": f"Bearer {ticket}"},
                )
                return res.status_code
        except httpx.HTTPError:
            # 平台不可达/超时：返回非 2xx 语义（0）计入 metadata_pending，
            # 由复跑重试；绝不回退为直接改平台私有表。
            return 0

    # ------------------------------------------------------------ 受控迁移

    async def apply(self) -> dict[str, Any]:
        # 幂等复跑优先：台账指纹命中即短路。必须早于 preflight——首轮 apply
        # 已把 json 身份落库，再跑 preflight 会把"自己造成的同名"误报为冲突。
        async with OwnerSessionLocal() as session:
            already = await session.scalar(
                text(
                    "SELECT count(*) FROM migration_ledger"
                    " WHERE manifest_fingerprint = :f AND entity_type = 'manifest'"
                    " AND status = 'applied'"
                ),
                {"f": self.fingerprint},
            )
        if already:
            return {
                "manifest_fingerprint": self.fingerprint,
                "already_applied": True,
                "message": "台账显示该清单指纹已完整应用（幂等短路，不重复写入）",
            }
        report = await self.preflight()
        if not report["clean"]:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            raise SystemExit(f"预检存在 {len(report['conflicts'])} 项硬冲突，apply 停止（不写入）")

        applied = {
            "users": 0,
            "memberships": 0,
            "roles": 0,
            "threads": 0,
            "artifacts": 0,
            "skipped": 0,
            "metadata_pending": 0,
        }
        async with OwnerSessionLocal() as session:
            tenant_ids: dict[str, uuid.UUID] = {}
            stage = await session.scalar(
                text(
                    "SELECT detail FROM migration_ledger WHERE manifest_fingerprint=:f"
                    " AND entity_type='stage' AND source_key='identity' AND status='applied'"
                ),
                {"f": self.fingerprint},
            )
            if stage is not None:
                # 仅续传平台更新，不重新授予角色/偏好或覆盖迁移后管理变更。
                for tenant in self.manifest["tenants"]:
                    tid = await session.scalar(
                        text("SELECT id FROM tenants WHERE code=:c"), {"c": tenant["code"]}
                    )
                    if tid is None:
                        raise SystemExit("已提交迁移的目标企业缺失，停止续跑")
                    tenant_ids[tenant["code"]] = tid
                user_ids = {}
                for user in self.manifest["users"]:
                    uid = await session.scalar(
                        text("SELECT id FROM users WHERE account=:a"), {"a": user["account"]}
                    )
                    if uid is None:
                        raise SystemExit("已提交迁移的目标用户缺失，停止续跑")
                    user_ids[user["account"]] = uid
                applied.update(
                    {
                        k: int(stage.get(k, 0))
                        for k in ("users", "memberships", "roles", "artifacts")
                    }
                )
            else:
                for tenant in self.manifest["tenants"]:
                    tid = uuid.uuid4()
                    existing = await session.scalar(
                        text("SELECT id FROM tenants WHERE code = :c"), {"c": tenant["code"]}
                    )
                    if existing is None:
                        await session.execute(
                            text("INSERT INTO tenants (id, code, name) VALUES (:id, :c, :n)"),
                            {"id": str(tid), "c": tenant["code"], "n": tenant["name"]},
                        )
                    else:
                        tid = uuid.UUID(str(existing))
                    await ensure_tenant_admin_role(session, tid)
                    tenant_ids[tenant["code"]] = tid

                user_ids: dict[str, uuid.UUID] = {}
                for user in self.manifest["users"]:
                    if user["source"] == "pg":
                        uid = uuid.UUID(user["pg_user_id"])
                        ok = await session.scalar(
                            text("SELECT 1 FROM users WHERE id = :i"), {"i": str(uid)}
                        )
                        if not ok:
                            raise SystemExit(f"PG 用户不存在：{user['account']}（停止）")
                    else:
                        json_user = self.json_users.get(str(user["json_id"]))
                        if json_user is None:
                            raise SystemExit(f"JSON 用户缺失：{user['account']}")
                        account = user["account"]
                        existing_id = await session.scalar(
                            text("SELECT id FROM users WHERE account = :a"), {"a": account}
                        )
                        mapped_id = await session.scalar(
                            text(
                                "SELECT target_key FROM migration_ledger"
                                " WHERE manifest_fingerprint = :f AND entity_type = 'user'"
                                " AND source_key = :s AND status = 'applied'"
                            ),
                            {"f": self.fingerprint, "s": str(user["json_id"])},
                        )
                        if existing_id is not None:
                            if mapped_id is not None and str(mapped_id) == str(existing_id):
                                # 幂等复跑：台账证明该映射由本清单建立
                                uid = uuid.UUID(str(existing_id))
                            elif user.get("canonical") == "pg" and str(
                                user.get("resolve_pg_user_id")
                            ) == str(existing_id):
                                # R06：显式 canonical=pg——复用 PG 原身份，原哈希/账号不动
                                uid = uuid.UUID(str(existing_id))
                            else:
                                # 同名不同 UUID 且无显式决议：绝不静默吞并（preflight 本应拦下）
                                raise SystemExit(
                                    f"同名身份冲突未显式 canonical 决议，拒绝吞并：{account}"
                                )
                        else:
                            # 密码哈希原样导入（bcrypt 兼容），报告不落哈希；幂等复跑不重复建号
                            inserted = await session.scalar(
                                text(
                                    "INSERT INTO users (id, account, display_name, password_hash,"
                                    " is_active) VALUES (:id, :a, :d, :h, true)"
                                    " ON CONFLICT (account) DO NOTHING RETURNING id"
                                ),
                                {
                                    "id": str(uuid.uuid4()),
                                    "a": user["account"],
                                    "d": user.get("display_name") or json_user["username"],
                                    "h": json_user["passwordHash"],
                                },
                            )
                            if inserted is None:
                                raise SystemExit("预检后账号出现冲突，停止迁移，请重新核验映射")
                            uid = uuid.UUID(str(inserted))
                        await session.execute(
                            text(
                                "INSERT INTO migration_ledger (manifest_fingerprint, entity_type,"
                                " source_key, target_key, status, detail)"
                                " VALUES (:f, 'user', :s, :t, 'applied', CAST('{}' AS jsonb))"
                                " ON CONFLICT (manifest_fingerprint, entity_type, source_key)"
                                " DO NOTHING"
                            ),
                            {
                                "f": self.fingerprint,
                                "s": str(user["json_id"]),
                                "t": str(uid),
                            },
                        )
                    user_ids[user["account"]] = uid
                    applied["users"] += 1

                for membership in self.manifest["memberships"]:
                    tid = tenant_ids[membership["tenant_code"]]
                    uid = user_ids[membership["account"]]
                    await session.execute(
                        text(
                            "INSERT INTO tenant_memberships (id, tenant_id, user_id)"
                            " VALUES (:id, :t, :u)"
                            " ON CONFLICT (tenant_id, user_id) DO NOTHING"
                        ),
                        {"id": str(uuid.uuid4()), "t": str(tid), "u": str(uid)},
                    )
                    # 复跑时取既有行 id，保证角色关联指向真实成员
                    mid = uuid.UUID(
                        str(
                            await session.scalar(
                                text(
                                    "SELECT id FROM tenant_memberships"
                                    " WHERE tenant_id = :t AND user_id = :u"
                                ),
                                {"t": str(tid), "u": str(uid)},
                            )
                        )
                    )
                    for code in dict.fromkeys(membership.get("role_codes", [])):
                        rid = await session.scalar(
                            text("SELECT id FROM roles WHERE code = :c AND tenant_id = :t"),
                            {"c": code, "t": str(tid)},
                        )
                        if rid is None:
                            raise SystemExit(
                                f"企业角色不存在：{code} @ {membership['tenant_code']}"
                            )
                        await session.execute(
                            text(
                                "INSERT INTO membership_roles (membership_id, role_id, tenant_id)"
                                " VALUES (:m, :r, :t) ON CONFLICT DO NOTHING"
                            ),
                            {"m": str(mid), "r": str(rid), "t": str(tid)},
                        )
                        applied["roles"] += 1
                    applied["memberships"] += 1

                for followed in self.manifest.get("followed_regions", []):
                    row = (
                        await session.execute(
                            text(
                                "SELECT m.id FROM tenant_memberships m"
                                " JOIN users u ON u.id = m.user_id"
                                " JOIN tenants t ON t.id = m.tenant_id"
                                " WHERE u.account = :a AND t.code = :c"
                            ),
                            {
                                "a": followed["account"],
                                "c": followed["tenant_code"],
                            },
                        )
                    ).scalar()
                    if row is None:
                        continue
                    for region_code in followed.get("region_codes", []):
                        await session.execute(
                            text(
                                "INSERT INTO membership_followed_regions"
                                " (membership_id, region_code, tenant_id)"
                                " VALUES (:m, :r, :t) ON CONFLICT DO NOTHING"
                            ),
                            {
                                "m": str(row),
                                "r": region_code,
                                "t": str(tenant_ids[followed["tenant_code"]]),
                            },
                        )

                # —— F03 阶段 A：归属与自有表回填先**可靠提交**，实体台账记
                # `ownership_committed`。平台 auth 用另一条运行角色连接，只有提交后
                # 才可见 migrated 归属行——PATCH 必须发生在提交之后。
                for thread in self.manifest["threads"]:
                    tid = tenant_ids[thread["tenant_code"]]
                    uid = user_ids[thread["account"]]
                    owned = (
                        (
                            await session.execute(
                                text(
                                    "SELECT source, tenant_id::text AS tenant_id,"
                                    " user_id::text AS user_id"
                                    " FROM thread_ownership WHERE thread_id = :t"
                                ),
                                {"t": thread["thread_id"]},
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if owned is None:
                        await session.execute(
                            text(
                                "INSERT INTO thread_ownership (thread_id, tenant_id, user_id,"
                                " source, migration_version)"
                                " VALUES (:t, :tid, :uid, 'migrated', 1)"
                            ),
                            {"t": thread["thread_id"], "tid": str(tid), "uid": str(uid)},
                        )
                    elif (
                        str(owned["source"]) != "migrated"
                        or str(owned["tenant_id"]) != str(tid)
                        or str(owned["user_id"]) != str(uid)
                    ):
                        # 归属冲突：停止且不改动他人/未知归属（不猜测合并）
                        raise SystemExit(
                            f"线程 {thread['thread_id']} 已有归属"
                            f"（source={owned['source']}），与 manifest 不一致，apply 停止"
                        )
                    await session.execute(
                        text(
                            "INSERT INTO migration_ledger (manifest_fingerprint, entity_type,"
                            " source_key, target_key, status, detail)"
                            " VALUES (:f, 'thread', :s, :t, 'pending',"
                            " CAST('{}' AS jsonb))"
                            " ON CONFLICT (manifest_fingerprint, entity_type, source_key)"
                            " DO NOTHING"
                        ),
                        {
                            "f": self.fingerprint,
                            "s": thread["thread_id"],
                            "t": f"{tid}/{uid}",
                        },
                    )

                for thread in self.manifest["threads"]:
                    json_id = thread.get("metadata_user_id")
                    if not json_id:
                        continue
                    result = await session.execute(
                        text(
                            "UPDATE tool_artifact_storage"
                            " SET tenant_id = :tid, user_id = :uid"
                            " WHERE thread_id = :t"
                            " AND (tenant_id IS NULL OR tenant_id = '')"
                        ),
                        {
                            "tid": str(tenant_ids[thread["tenant_code"]]),
                            "uid": str(user_ids[thread["account"]]),
                            "t": thread["thread_id"],
                        },
                    )
                    applied["artifacts"] += int(result.rowcount or 0)
                # 父归档完成可信回填后，保留历史计算配额和次数，补齐相同作用域。
                await session.execute(
                    text(
                        "UPDATE artifact_computations c SET tenant_id=a.tenant_id, "
                        "user_id=a.user_id"
                        " FROM tool_artifact_storage a WHERE c.artifact_id=a.artifact_id"
                        " AND a.thread_id IN :threads AND (c.tenant_id IS NULL OR c.tenant_id='')"
                    ).bindparams(bindparam("threads", expanding=True, type_=String)),
                    {"threads": [t["thread_id"] for t in self.manifest["threads"]]},
                )
                await session.execute(
                    text(
                        "INSERT INTO "
                        "migration_ledger(manifest_fingerprint,entity_type,source_key,status,detail)"
                        " VALUES (:f,'stage','identity','applied',CAST(:d AS jsonb))"
                    ),
                    {"f": self.fingerprint, "d": json.dumps(applied)},
                )
                await session.commit()  # ← 阶段 A 落定，平台侧此刻已可见归属

            # —— F03 阶段 B：逐实体平台窄更新（mt-v1 票据 + PATCH，幂等）。
            # 台账状态推进 applied；非 2xx（403/503/超时/进程中断后重试）保留
            # ownership_committed 供复跑续传；全部 applied 才登记 manifest 完成。
            for thread in self.manifest["threads"]:
                tid = tenant_ids[thread["tenant_code"]]
                uid = user_ids[thread["account"]]
                done = await session.scalar(
                    text(
                        "SELECT 1 FROM migration_ledger WHERE manifest_fingerprint = :f"
                        " AND entity_type = 'thread' AND source_key = :s AND status = 'applied'"
                    ),
                    {"f": self.fingerprint, "s": thread["thread_id"]},
                )
                if done:
                    applied["threads"] += 1
                    continue
                status_code = await self._platform_metadata_patch(
                    thread["thread_id"], str(tid), str(uid)
                )
                if 200 <= int(status_code) < 300:
                    await session.execute(
                        text(
                            "UPDATE migration_ledger SET status = 'applied',"
                            " detail = CAST(:d AS jsonb)"
                            " WHERE manifest_fingerprint = :f AND entity_type = 'thread'"
                            " AND source_key = :s"
                        ),
                        {
                            "f": self.fingerprint,
                            "s": thread["thread_id"],
                            "d": json.dumps({"platform_status": int(status_code)}),
                        },
                    )
                    await session.commit()
                    applied["threads"] += 1
                else:
                    applied["metadata_pending"] = applied.get("metadata_pending", 0) + 1

            # —— manifest 完成判定：所有线程实体 applied 才登记指纹级 applied
            outstanding = await session.scalar(
                text(
                    "SELECT count(*) FROM migration_ledger"
                    " WHERE manifest_fingerprint = :f AND entity_type = 'thread'"
                    " AND status <> 'applied'"
                ),
                {"f": self.fingerprint},
            )
            if not outstanding:
                await session.execute(
                    text(
                        "INSERT INTO migration_ledger (manifest_fingerprint, entity_type,"
                        " source_key, target_key, status, detail)"
                        " VALUES (:f, 'manifest', :f, :summary, 'applied', CAST(:d AS jsonb))"
                        " ON CONFLICT (manifest_fingerprint, entity_type, source_key)"
                        " DO UPDATE SET status = 'applied', detail = EXCLUDED.detail"
                    ),
                    {
                        "f": self.fingerprint,
                        "summary": "ok",
                        "d": json.dumps(
                            {
                                "users": applied["users"],
                                "memberships": applied["memberships"],
                                "threads": applied["threads"],
                                "artifacts": applied["artifacts"],
                            },
                            ensure_ascii=False,
                        ),
                    },
                )
                await session.commit()
            else:
                applied["incomplete_threads"] = int(outstanding)

        # 未映射 JSON 用户 / 未归属线程 → 隔离记录（资源本身保持不可访问）
        async with OwnerSessionLocal() as session:
            mapped = {str(u.get("json_id")) for u in self.manifest["users"] if u.get("json_id")}
            for json_id in sorted(set(self.json_users) - mapped):
                await self._quarantine(session, f"json:{json_id}", QUARANTINE_UNMAPPED_JSON)
            owned = {
                str(row["thread_id"])
                for row in (
                    await session.execute(text("SELECT thread_id FROM thread_ownership"))
                ).mappings()
            }
            manifest_threads = {t["thread_id"] for t in self.manifest["threads"]}
            all_threads = {
                str(row["thread_id"])
                for row in (await session.execute(text("SELECT thread_id FROM thread"))).mappings()
            }
            for thread_id in sorted(all_threads - owned - manifest_threads):
                await self._quarantine(session, f"thread:{thread_id}", QUARANTINE_UNOWNED_THREAD)
            await session.commit()
        return {
            "manifest_fingerprint": self.fingerprint,
            "completed": int(applied.get("metadata_pending", 0)) == 0
            and int(applied.get("incomplete_threads", 0)) == 0,
            "applied": applied,
            "counts": self.counts,
        }

    async def _quarantine(self, session, source_key: str, conflict: str) -> None:
        exists = await session.scalar(
            text(
                "SELECT 1 FROM migration_ledger WHERE manifest_fingerprint = :f"
                " AND entity_type = 'quarantine' AND source_key = :k"
            ),
            {"f": self.fingerprint, "k": source_key},
        )
        if exists:
            return
        await session.execute(
            text(
                "INSERT INTO migration_ledger (manifest_fingerprint, entity_type,"
                " source_key, status, conflict_code, detail)"
                " VALUES (:f, 'quarantine', :k, 'quarantined', :c, '{}'::jsonb)"
            ),
            {"f": self.fingerprint, "k": source_key, "c": conflict},
        )

    async def report(self) -> dict[str, Any]:
        async with OwnerSessionLocal() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT entity_type, status, count(*) AS n FROM migration_ledger"
                        " WHERE manifest_fingerprint = :f GROUP BY entity_type, status"
                    ),
                    {"f": self.fingerprint},
                )
            ).mappings()
            ledger = {f"{row['entity_type']}:{row['status']}": row["n"] for row in rows}
        return {"fingerprint": self.fingerprint, "ledger": ledger}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "apply", "report"])
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--json-users", default="frontend/data/users.json")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    json_users = load_json_users(args.json_users)
    runner = MigrationRunner(manifest, json_users)
    if args.command == "preflight":
        result = await runner.preflight()
    elif args.command == "apply":
        result = await runner.apply()
    else:
        result = await runner.report()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
