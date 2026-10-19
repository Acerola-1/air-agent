"""能力目录与用户选择基础模块（Change 4 核心契约与目录实现）.

包含：
- Skill 本地目录与材料指纹 (SHA-256) 计算与校验
- MCP 授权目录发现（按当前成员身份、超时隔离、部分可用与稳定 ID）
- 稳定 ID 格式与防伪造、防路径遍历门禁
- 签名游标 (Cursor) 与分页
- 统一能力目录结构 (agent.capability-catalog.v1)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import posixpath
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deepagents.backends import FilesystemBackend
from fastapi import HTTPException
from loguru import logger

from common.identity.db import fetch_active_mcp_bindings
from common.identity.runtime import RuntimeIdentity
from common.identity.ticket import parse_keys
from common.mcp_authorization import resolve_protocol_profile

VALID_GRAPHS: set[str] = {
    "basic-qa",
    "data-analysis",
    "intelligent-tracing",
}

GRAPH_SKILLS_DIRS: dict[str, Path] = {
    "basic-qa": Path(__file__).resolve().parents[1] / "basic_qa" / "skills",
    "data-analysis": Path(__file__).resolve().parents[1] / "data_analysis" / "skills",
    "intelligent-tracing": Path(__file__).resolve().parents[1]
    / "intelligent_tracing"
    / "skills",
}

_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SKILL_ID_PATTERN = re.compile(r"^skill:([a-z0-9_-]+):([a-z0-9_-]+)$")
_MCP_ID_PATTERN = re.compile(r"^mcp:([a-z0-9_-]+):([a-zA-Z0-9_-]+)$")


@dataclass(frozen=True)
class SkillCatalogItem:
    """Skill 目录条目."""

    id: str
    name: str
    description: str
    material_version: str
    status: str
    title: str = ""
    required_tool_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MCPServiceCatalogItem:
    """MCP 服务目录条目."""

    server_key: str
    display_name: str
    status: str  # available | not_configured | unverified | temporarily_unavailable


@dataclass(frozen=True)
class MCPToolCatalogItem:
    """MCP 工具目录条目."""

    id: str
    server_key: str
    name: str
    display_name: str
    description: str
    status: str  # available | temporarily_unavailable


# ---------------------------------------------------------------------------
# Skill 材料指纹与目录
# ---------------------------------------------------------------------------


def calculate_skill_material_version(skill_dir: Path) -> str:
    """计算 Skill 目录所有规则文件的稳定材料指纹（SHA-256）.

    不触发 Embedding，不调用远端模型。按文件相对路径字母序规范哈希。
    """
    if not skill_dir.is_dir():
        return "sha256:empty"
    hasher = hashlib.sha256()
    files = sorted(
        [p for p in skill_dir.rglob("*") if p.is_file() and not p.name.startswith(".")],
        key=lambda p: p.relative_to(skill_dir).as_posix(),
    )
    for file_path in files:
        rel_posix = file_path.relative_to(skill_dir).as_posix()
        try:
            content = file_path.read_bytes()
        except OSError:
            content = b""
        hasher.update(f"{rel_posix}:".encode())
        hasher.update(content)
        hasher.update(b"\n")
    return f"sha256:{hasher.hexdigest()}"


def _parse_skill_frontmatter(content: str) -> dict[str, Any]:
    """解析 SKILL.md frontmatter，只提取展示信息与声明依赖，无外部依赖."""
    if not content.startswith("---"):
        return {"name": "", "description": "", "required-tools": []}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {"name": "", "description": "", "required-tools": []}
    frontmatter_raw = parts[1]
    res: dict[str, Any] = {}
    current_key: str | None = None
    multiline_buf: list[str] = []

    for line in frontmatter_raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in line and not line.startswith(" ") and not line.startswith("\t"):
            if current_key and multiline_buf:
                res[current_key] = "\n".join(multiline_buf).strip()
                multiline_buf = []
            k, v = line.split(":", 1)
            current_key = k.strip()
            v_str = v.strip()
            if v_str == "|" or v_str == ">":
                multiline_buf = []
            else:
                res[current_key] = v_str
        elif current_key:
            multiline_buf.append(stripped)

    if current_key and multiline_buf:
        res[current_key] = "\n".join(multiline_buf).strip()

    # 处理 required-tools / required_tools
    req = res.get("required-tools") or res.get("required_tools") or ""
    if isinstance(req, str):
        req_list = [t.strip() for t in req.split() if t.strip()]
    elif isinstance(req, list):
        req_list = [str(t).strip() for t in req if str(t).strip()]
    else:
        req_list = []
    res["required-tools"] = req_list
    return res


def list_graph_skills(
    graph_id: str, *, menu_name: str | None = None
) -> list[SkillCatalogItem]:
    """读取指定图的本地 Skill 目录，过滤并返回稳定目录项."""
    if graph_id not in VALID_GRAPHS:
        raise HTTPException(status_code=422, detail=f"未知图标识: {graph_id}")

    skills_dir = GRAPH_SKILLS_DIRS.get(graph_id)
    if not skills_dir or not skills_dir.exists():
        return []

    # 如果是 data-analysis 且提供了 menu_name，只返回匹配的 Skill
    allowed_menu_skill: str | None = None
    if graph_id == "data-analysis" and menu_name:
        try:
            from data_analysis.menu_skill_mapping import DATA_ANALYSIS_SKILL_MAPPINGS

            for mapping in DATA_ANALYSIS_SKILL_MAPPINGS:
                if menu_name in mapping.menu_names:
                    allowed_menu_skill = mapping.skill_name
                    break
        except ImportError:
            pass

    items: list[SkillCatalogItem] = []
    subdirs = sorted(
        [d for d in skills_dir.iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=lambda d: d.name,
    )

    for subdir in subdirs:
        slug = subdir.name
        if not _SLUG_PATTERN.match(slug):
            continue
        if allowed_menu_skill is not None and slug != allowed_menu_skill:
            continue

        skill_file = subdir / "SKILL.md"
        if not skill_file.exists():
            continue

        try:
            text = skill_file.read_text(encoding="utf-8")
        except OSError:
            continue

        meta = _parse_skill_frontmatter(text)
        name = meta.get("name") or slug
        desc = (meta.get("description") or name).strip()
        # 清理多行描述中的换行和标记，最多 500 字符
        clean_desc = " ".join(desc.split())[:500]
        titles = {
            "air-quality-basic-query": "基础空气质量查询",
            "comparison-composition": "同比与污染物构成",
            "compliance-feasibility": "达标可行性研判",
            "ranking-assessment": "空气质量排名考核",
            "regional-benchmark": "区域空气质量对标",
            "station-extreme": "站点数据与极值",
            "trend-analysis": "空气质量趋势分析",
        }
        title = (
            meta.get("title")
            or titles.get(slug)
            or clean_desc.split("。")[0][:24]
            or name
        )
        if graph_id == "data-analysis":
            from data_analysis.menu_skill_mapping import DATA_ANALYSIS_SKILL_MAPPINGS

            title = next(
                (m.title for m in DATA_ANALYSIS_SKILL_MAPPINGS if m.skill_name == slug),
                title,
            )
        mat_version = calculate_skill_material_version(subdir)
        req_tools = meta.get("required-tools", [])

        items.append(
            SkillCatalogItem(
                id=f"skill:{graph_id}:{slug}",
                name=slug,
                description=clean_desc,
                material_version=mat_version,
                status="available",
                title=title,
                required_tool_ids=req_tools,
            )
        )
    return items


def validate_skill_selection(
    graph_id: str, skill_id: str, *, menu_name: str | None = None
) -> SkillCatalogItem:
    """严格校验选中的 Skill ID 是否属于当前图、是否存在且与菜单一致.

    防路径遍历与跨图引用：任何不匹配抛 422 或 404.
    """
    m = _SKILL_ID_PATTERN.match(skill_id)
    if not m:
        raise HTTPException(status_code=422, detail=f"非法的技能标识格式: {skill_id}")
    g_id, slug = m.group(1), m.group(2)
    if g_id != graph_id:
        raise HTTPException(
            status_code=422, detail=f"技能 {skill_id} 不属于当前图 {graph_id}"
        )
    if not _SLUG_PATTERN.match(slug) or ".." in slug:
        raise HTTPException(status_code=422, detail="非法技能目录标识")

    skills = list_graph_skills(graph_id, menu_name=menu_name)
    matched = next((s for s in skills if s.id == skill_id), None)
    if matched is None:
        # 如果是 data-analysis 且因为菜单不匹配，返回 422
        if graph_id == "data-analysis" and menu_name:
            all_skills = list_graph_skills(graph_id)
            if any(s.id == skill_id for s in all_skills):
                raise HTTPException(
                    status_code=422,
                    detail=f"所选技能 {skill_id} 与当前菜单 {menu_name} 映射不一致",
                )
        raise HTTPException(status_code=422, detail=f"技能 {skill_id} 不存在或不可用")
    return matched


# ---------------------------------------------------------------------------
# MCP 授权目录发现与超时隔离
# ---------------------------------------------------------------------------


async def discover_authorized_mcp_catalog(
    identity: RuntimeIdentity,
    *,
    timeout_per_server: float = 2.0,
    total_timeout: float = 5.0,
    max_concurrency: int = 3,
) -> tuple[list[MCPServiceCatalogItem], list[MCPToolCatalogItem]]:
    """按当前成员身份实时发现已授权 MCP 服务与工具.

    单服务超时 2 秒，总限时 5 秒，最多 3 并发；部分服务失败返回 200 并诚实标
    temporarily_unavailable；无权服务零网络发现请求且不泄露存在性。
    """
    from common.mcp_scoped import get_factory

    factory = get_factory()
    active_bindings = await fetch_active_mcp_bindings(
        identity.tenant_id, identity.membership_id
    )
    if not active_bindings:
        return [], []

    services: list[MCPServiceCatalogItem] = []
    tools: list[MCPToolCatalogItem] = []

    # 过滤出需要网络发现的服务
    to_discover: list[dict[str, Any]] = []

    for binding in active_bindings:
        server_key = binding["server_key"]
        p_version = binding.get("protocol_version", "")
        profile = resolve_protocol_profile(p_version)

        if profile is None:
            status = "unverified" if p_version == "unverified" else "unavailable"
            services.append(
                MCPServiceCatalogItem(
                    server_key=server_key,
                    display_name=server_key,
                    status=status,
                )
            )
            continue

        # 检查 server URL
        url = factory._urls.get(server_key)
        if not url:
            env_url = os.environ.get(
                f"MCP_SERVER_URL_{server_key.upper().replace('-', '_')}"
            )
            if env_url:
                factory.register_server(server_key, env_url)
                url = env_url
        if not url:
            services.append(
                MCPServiceCatalogItem(
                    server_key=server_key,
                    display_name=server_key,
                    status="not_configured",
                )
            )
            continue

        to_discover.append(binding)

    if not to_discover:
        return services, tools

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _discover_single(
        binding: dict[str, Any],
    ) -> tuple[MCPServiceCatalogItem, list[MCPToolCatalogItem]]:
        server_key = binding["server_key"]
        async with semaphore:
            try:
                discovered_tools = await asyncio.wait_for(
                    factory.tools_for(identity, server_key),
                    timeout=timeout_per_server,
                )
                svc = MCPServiceCatalogItem(
                    server_key=server_key,
                    display_name=server_key,
                    status="available",
                )
                tool_items: list[MCPToolCatalogItem] = []
                for t in discovered_tools:
                    tool_items.append(
                        MCPToolCatalogItem(
                            id=f"mcp:{server_key}:{t.name}",
                            server_key=server_key,
                            name=t.name,
                            display_name=getattr(t, "display_name", t.name) or t.name,
                            description=(getattr(t, "description", "") or t.name)[:500],
                            status="available",
                        )
                    )
                return svc, tool_items
            except Exception as exc:
                logger.warning(
                    "MCP 目录发现受阻: server={}, error={}",
                    server_key,
                    type(exc).__name__,
                )
                svc = MCPServiceCatalogItem(
                    server_key=server_key,
                    display_name=server_key,
                    status="temporarily_unavailable",
                )
                return svc, []

    # 总限时 total_timeout
    task_map = {asyncio.create_task(_discover_single(b)): b for b in to_discover}
    done, pending = await asyncio.wait(task_map.keys(), timeout=total_timeout)

    for t in pending:
        t.cancel()
        binding = task_map[t]
        server_key = binding["server_key"]
        services.append(
            MCPServiceCatalogItem(
                server_key=server_key,
                display_name=server_key,
                status="temporarily_unavailable",
            )
        )

    for t in done:
        binding = task_map[t]
        server_key = binding["server_key"]
        try:
            res = t.result()
            svc, tool_items = res
            services.append(svc)
            tools.extend(tool_items)
        except Exception:
            services.append(
                MCPServiceCatalogItem(
                    server_key=server_key,
                    display_name=server_key,
                    status="temporarily_unavailable",
                )
            )

    # 排序保持输出稳定
    services.sort(key=lambda s: s.server_key)
    tools.sort(key=lambda t: t.id)
    return services, tools


# ---------------------------------------------------------------------------
# 目录版本计算与游标签名
# ---------------------------------------------------------------------------


def compute_catalog_version(
    graph_id: str,
    identity: RuntimeIdentity,
    auth_revisions: dict[str, int],
    skills: list[SkillCatalogItem],
    mcp_services: list[MCPServiceCatalogItem],
    tools: list[MCPToolCatalogItem],
) -> str:
    """计算目录版本摘要 (SHA-256 前24位).

    包含材料指纹、授权修订、图与稳定ID，不含秘密、参数或内部路径.
    """
    hasher = hashlib.sha256()
    hasher.update(f"graph:{graph_id}\n".encode())
    hasher.update(
        f"tenant:{identity.tenant_id}:{auth_revisions.get('tenant', 1)}\n".encode()
    )
    hasher.update(f"user:{identity.user_id}:{auth_revisions.get('user', 1)}\n".encode())
    hasher.update(
        f"membership:{identity.membership_id}:{auth_revisions.get('membership', 1)}\n".encode()
    )
    for s in skills:
        hasher.update(f"s:{s.id}:{s.material_version}:{s.status}\n".encode())
    for svc in mcp_services:
        hasher.update(f"svc:{svc.server_key}:{svc.status}\n".encode())
    for t in tools:
        hasher.update(f"t:{t.id}:{t.status}\n".encode())
    return f"cat_{hasher.hexdigest()[:24]}"


def _cursor_secret_key() -> bytes:
    raw = os.environ.get("RUNTIME_TICKET_KEYS", "")
    try:
        keys = parse_keys(raw)
        if keys:
            return keys[0][1].encode("utf-8")
    except Exception:
        pass
    return b"air-agent-capabilities-cursor-fallback-key"


def encode_cursor(
    *,
    tenant_id: str,
    user_id: str,
    membership_id: str,
    graph_id: str,
    catalog_version: str,
    offset: int,
) -> str:
    """签名并生成不透明分页游标."""
    payload = {
        "t": tenant_id,
        "u": user_id,
        "m": membership_id,
        "g": graph_id,
        "v": catalog_version,
        "o": offset,
    }
    dumped = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_cursor_secret_key(), dumped, hashlib.sha256).hexdigest()[:16]
    import base64

    combined = f"{sig}:{base64.urlsafe_b64encode(dumped).decode('utf-8')}"
    return combined


def decode_cursor(
    cursor: str,
    *,
    expected_tenant_id: str,
    expected_user_id: str,
    expected_membership_id: str,
    expected_graph_id: str,
    expected_catalog_version: str,
) -> int:
    """验证并解码游标；若伪造、篡改或 scope/图/版本不匹配，抛 409 (或 422)."""
    if not cursor or ":" not in cursor:
        raise HTTPException(status_code=422, detail="游标格式无效")
    sig, raw_b64 = cursor.split(":", 1)
    import base64

    try:
        dumped = base64.urlsafe_b64decode(raw_b64.encode("utf-8"))
        payload = json.loads(dumped.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=422, detail="游标解码失败")

    expected_sig = hmac.new(_cursor_secret_key(), dumped, hashlib.sha256).hexdigest()[
        :16
    ]
    if not hmac.compare_digest(sig, expected_sig):
        raise HTTPException(status_code=409, detail="游标签名无效或已被篡改")

    if (
        payload.get("t") != expected_tenant_id
        or payload.get("u") != expected_user_id
        or payload.get("m") != expected_membership_id
        or payload.get("g") != expected_graph_id
    ):
        raise HTTPException(status_code=409, detail="游标租户/用户/成员/图作用域不匹配")

    if payload.get("v") != expected_catalog_version:
        raise HTTPException(status_code=409, detail="目录版本已过期，请重新获取")

    offset = payload.get("o")
    if not isinstance(offset, int) or offset < 0:
        raise HTTPException(status_code=422, detail="游标偏移量非法")
    return offset


# ---------------------------------------------------------------------------
# 完整能力目录编排
# ---------------------------------------------------------------------------


async def get_capabilities_catalog(
    *,
    graph_id: str,
    identity: RuntimeIdentity,
    auth_revisions: dict[str, int] | None = None,
    limit: int = 200,
    cursor: str | None = None,
    menu_name: str | None = None,
) -> dict[str, Any]:
    """生成并分页返回符合契约的能力目录响应."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit 必须在 1 到 200 之间")

    auth_revs = auth_revisions or {"tenant": 1, "user": 1, "membership": 1}

    # 1. 本地读取 Skills
    skills = list_graph_skills(graph_id, menu_name=menu_name)

    # 2. 授权发现 MCP
    services, tools = await discover_authorized_mcp_catalog(identity)

    # 3. 计算版本
    cat_ver = compute_catalog_version(
        graph_id=graph_id,
        identity=identity,
        auth_revisions=auth_revs,
        skills=skills,
        mcp_services=services,
        tools=tools,
    )

    # 4. 解析游标
    offset = 0
    if cursor:
        offset = decode_cursor(
            cursor,
            expected_tenant_id=identity.tenant_id,
            expected_user_id=identity.user_id,
            expected_membership_id=identity.membership_id,
            expected_graph_id=graph_id,
            expected_catalog_version=cat_ver,
        )

    # 分页对象：合并 items 顺序为 skills 然后 tools
    # 为保证单项完整，分别在分页窗口切片
    total_skills = len(skills)
    total_tools = len(tools)
    total_items = total_skills + total_tools

    if offset > total_items:
        offset = total_items

    sliced_skills: list[SkillCatalogItem] = []
    sliced_tools: list[MCPToolCatalogItem] = []

    remaining_limit = limit

    # Skills 窗口
    if offset < total_skills:
        skill_start = offset
        skill_end = min(total_skills, offset + remaining_limit)
        sliced_skills = skills[skill_start:skill_end]
        remaining_limit -= len(sliced_skills)

    # Tools 窗口
    tool_offset = max(0, offset - total_skills)
    if remaining_limit > 0 and tool_offset < total_tools:
        tool_start = tool_offset
        tool_end = min(total_tools, tool_offset + remaining_limit)
        sliced_tools = tools[tool_start:tool_end]

    cur_idx = offset + len(sliced_skills) + len(sliced_tools)

    next_cursor: str | None = None
    if cur_idx < total_items:
        next_cursor = encode_cursor(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            membership_id=identity.membership_id,
            graph_id=graph_id,
            catalog_version=cat_ver,
            offset=cur_idx,
        )

    response = {
        "schema_version": "agent.capability-catalog.v1",
        "graph_id": graph_id,
        "catalog_version": cat_ver,
        "generated_at": datetime.now(UTC).isoformat(),
        "skills": [asdict(s) for s in sliced_skills],
        "mcp_services": [asdict(svc) for svc in services],
        "tools": [asdict(t) for t in sliced_tools],
        "next_cursor": next_cursor,
    }

    # 检查单页大小不超过 256 KiB
    encoded_bytes = json.dumps(response, ensure_ascii=False).encode("utf-8")
    if len(encoded_bytes) > 256 * 1024:
        # 如果超出，缩减 items
        logger.warning(
            "目录单页大小超 256KiB ({} bytes)，自适应截断", len(encoded_bytes)
        )
        while len(encoded_bytes) > 256 * 1024 and (
            response["tools"] or response["skills"]
        ):
            if response["tools"]:
                response["tools"].pop()
            elif response["skills"]:
                response["skills"].pop()
            encoded_bytes = json.dumps(response, ensure_ascii=False).encode("utf-8")

        actual_items_count = len(response["skills"]) + len(response["tools"])
        new_cur_idx = offset + actual_items_count
        if new_cur_idx < total_items:
            response["next_cursor"] = encode_cursor(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                membership_id=identity.membership_id,
                graph_id=graph_id,
                catalog_version=cat_ver,
                offset=new_cur_idx,
            )
        else:
            response["next_cursor"] = None

    return response


# ---------------------------------------------------------------------------
# 执行侧收窄与边界防护（任务 4.3 / 4.4）
# ---------------------------------------------------------------------------


async def refresh_authorized_mcp_tools(config: Any) -> None:
    """只以可信成员身份刷新目录，并把本次获准的稳定 ID 留在运行上下文."""
    from common.identity.runtime import identity_from_config

    identity = identity_from_config(config)
    ids: list[str] = []
    if identity is not None:
        _, tools = await discover_authorized_mcp_catalog(identity)
        ids = [tool.id for tool in tools if tool.status == "available"]
    config.setdefault("configurable", {})["capability_authorized_tool_ids"] = ids


class BoundedSkillsBackend(FilesystemBackend):
    """带显式技能访问边界约束与路径安全检查的技能文件系统后端（任务 4.4）."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """初始化边界约束文件系统后端."""
        super().__init__(*args, **kwargs)
        self._explicit_skill_override: str | None = None

    def set_explicit_skill(self, skill_name: str | None) -> None:
        """测试或手动设置显式技能范围."""
        self._explicit_skill_override = skill_name

    def _get_allowed_slug(self) -> str | None:
        if self._explicit_skill_override:
            return self._explicit_skill_override
        try:
            from langgraph.config import get_config

            cfg = dict(get_config() or {})
        except Exception:
            cfg = {}
        configurable = cfg.get("configurable") or {}
        metadata = cfg.get("metadata") or {}
        skill_mode = (
            configurable.get("capability_skill_mode")
            or metadata.get("capability_skill_mode")
            or "auto"
        )
        if skill_mode == "explicit":
            skill_id = (
                configurable.get("capability_skill_id")
                or metadata.get("capability_skill_id")
                or ""
            )
            if skill_id:
                return skill_id.split(":")[-1]
        return None

    def _is_path_in_allowed_slug(self, file_path: str, allowed_slug: str) -> bool:
        clean = str(file_path or "").replace("\\", "/").strip().lstrip("/")
        parts = [p for p in clean.split("/") if p and p != "."]
        if ".." in parts:
            return False
        if parts and parts[0] == "skills":
            target = parts[1] if len(parts) > 1 else None
        elif parts:
            target = parts[0]
        else:
            target = None
        return target == allowed_slug

    def _check_allowed(self, path: str) -> None:
        # 1. 规范化路径并防路径穿越
        clean_path = str(path or "").replace("\\", "/").strip()
        parts = [p for p in clean_path.lstrip("/").split("/") if p and p != "."]
        if ".." in parts:
            raise PermissionError("非法路径穿越访问")

        # 2. 检查显式技能限制
        allowed_slug = self._get_allowed_slug()
        if not allowed_slug:
            return

        if not parts:
            return

        if parts[0] == "skills":
            target_slug = parts[1] if len(parts) > 1 else None
        else:
            target_slug = parts[0]

        if target_slug and target_slug != allowed_slug:
            raise PermissionError(
                f"未授权访问技能文件: {target_slug} (当前显式选定限制: {allowed_slug})"
            )

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        """同步读取文件内容（受边界约束防护）."""
        self._check_allowed(file_path)
        return super().read(file_path, offset, limit)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> Any:
        """异步读取文件内容（受边界约束防护）."""
        self._check_allowed(file_path)
        return await super().aread(file_path, offset, limit)

    def ls(self, path: str = "") -> Any:
        """同步列出目录条目（受边界约束防护）."""
        self._check_allowed(path)
        res = super().ls(path)
        allowed_slug = self._get_allowed_slug()
        if allowed_slug and getattr(res, "entries", None) is not None:
            clean_p = str(path or "").replace("\\", "/").strip().lstrip("/")
            if clean_p in ("", "skills"):
                filtered_entries = [
                    e
                    for e in res.entries
                    if (
                        isinstance(e, dict)
                        and e.get("path", "")
                        .replace("\\", "/")
                        .strip("/")
                        .split("/")[0]
                        == allowed_slug
                    )
                    or (
                        hasattr(e, "path")
                        and str(getattr(e, "path", ""))
                        .replace("\\", "/")
                        .strip("/")
                        .split("/")[0]
                        == allowed_slug
                    )
                ]
                return type(res)(error=res.error, entries=filtered_entries)
        return res

    async def als(self, path: str = "") -> Any:
        """异步列出目录条目（受边界约束防护）."""
        self._check_allowed(path)
        res = await super().als(path)
        allowed_slug = self._get_allowed_slug()
        if allowed_slug and getattr(res, "entries", None) is not None:
            clean_p = str(path or "").replace("\\", "/").strip().lstrip("/")
            if clean_p in ("", "skills"):
                filtered_entries = [
                    e
                    for e in res.entries
                    if (
                        isinstance(e, dict)
                        and e.get("path", "")
                        .replace("\\", "/")
                        .strip("/")
                        .split("/")[0]
                        == allowed_slug
                    )
                    or (
                        hasattr(e, "path")
                        and str(getattr(e, "path", ""))
                        .replace("\\", "/")
                        .strip("/")
                        .split("/")[0]
                        == allowed_slug
                    )
                ]
                return type(res)(error=res.error, entries=filtered_entries)
        return res

    def glob(self, pattern: str, path: str | None = None) -> Any:
        """受边界约束防护的同步通配文件，支持可选路径限制."""
        if path is not None:
            combined = posixpath.normpath(f"{path}/{pattern}")
            self._check_allowed(combined)
            return super().glob(pattern, path=path)
        self._check_allowed(pattern)
        return super().glob(pattern)

    async def aglob(self, pattern: str, path: str | None = None) -> Any:
        """受边界约束防护的异步通配文件，支持可选路径限制."""
        if path is not None:
            combined = posixpath.normpath(f"{path}/{pattern}")
            self._check_allowed(combined)
            return await super().aglob(pattern, path=path)
        self._check_allowed(pattern)
        return await super().aglob(pattern)

    def write(self, file_path: str, content: str) -> Any:
        """同步写入文件（受边界约束防护）."""
        self._check_allowed(file_path)
        return super().write(file_path, content)

    async def awrite(self, file_path: str, content: str) -> Any:
        """异步写入文件（受边界约束防护）."""
        self._check_allowed(file_path)
        return await super().awrite(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str) -> Any:
        """同步编辑文件（受边界约束防护）."""
        self._check_allowed(file_path)
        return super().edit(file_path, old_string, new_string)

    async def aedit(self, file_path: str, old_string: str, new_string: str) -> Any:
        """异步编辑文件（受边界约束防护）."""
        self._check_allowed(file_path)
        return await super().aedit(file_path, old_string, new_string)

    def delete(self, file_path: str) -> Any:
        """同步删除文件（受边界约束防护）."""
        self._check_allowed(file_path)
        return super().delete(file_path)

    async def adelete(self, file_path: str) -> Any:
        """异步删除文件（受边界约束防护）."""
        self._check_allowed(file_path)
        return await super().adelete(file_path)

    def download_files(self, paths: list[str]) -> Any:
        """下载多个文件，未授权访问返回 permission_denied."""
        from deepagents.backends.protocol import FileDownloadResponse

        results: list[Any] = []
        for p in paths:
            try:
                self._check_allowed(p)
                res = super().download_files([p])
                results.extend(res)
            except PermissionError:
                results.append(
                    FileDownloadResponse(
                        path=p, content=None, error="permission_denied"
                    )
                )
        return results

    async def adownload_files(self, paths: list[str]) -> Any:
        """异步下载多个文件，未授权访问返回 permission_denied."""
        from deepagents.backends.protocol import FileDownloadResponse

        results: list[Any] = []
        for p in paths:
            try:
                self._check_allowed(p)
                res = await super().adownload_files([p])
                results.extend(res)
            except PermissionError:
                results.append(
                    FileDownloadResponse(
                        path=p, content=None, error="permission_denied"
                    )
                )
        return results

    def upload_files(self, files: list[tuple[str, bytes]]) -> Any:
        """上传文件，未授权访问返回 permission_denied."""
        from deepagents.backends.protocol import FileUploadResponse

        results: list[Any] = []
        for p, data in files:
            try:
                self._check_allowed(p)
                res = super().upload_files([(p, data)])
                results.extend(res)
            except PermissionError:
                results.append(FileUploadResponse(path=p, error="permission_denied"))
        return results

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> Any:
        """异步上传文件，未授权访问返回 permission_denied."""
        from deepagents.backends.protocol import FileUploadResponse

        results: list[Any] = []
        for p, data in files:
            try:
                self._check_allowed(p)
                res = await super().aupload_files([(p, data)])
                results.extend(res)
            except PermissionError:
                results.append(FileUploadResponse(path=p, error="permission_denied"))
        return results

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
        context_lines: int = 0,
    ) -> Any:
        """同步正则匹配文件内容（受边界约束防护，过滤非选定技能内容）."""
        if path:
            self._check_allowed(path)
        res = super().grep(
            pattern, path, glob, max_count=max_count, context_lines=context_lines
        )
        allowed_slug = self._get_allowed_slug()
        if allowed_slug and getattr(res, "matches", None):
            filtered = [
                m
                for m in res.matches
                if self._is_path_in_allowed_slug(
                    m.get("path") if isinstance(m, dict) else getattr(m, "path", ""),
                    allowed_slug,
                )
            ]
            from deepagents.backends.protocol import GrepResult

            return GrepResult(
                error=res.error, matches=filtered, truncated=res.truncated
            )
        return res

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
        context_lines: int = 0,
    ) -> Any:
        """异步正则匹配文件内容（受边界约束防护，过滤非选定技能内容）."""
        if path:
            self._check_allowed(path)
        res = await super().agrep(
            pattern, path, glob, max_count=max_count, context_lines=context_lines
        )
        allowed_slug = self._get_allowed_slug()
        if allowed_slug and getattr(res, "matches", None):
            filtered = [
                m
                for m in res.matches
                if self._is_path_in_allowed_slug(
                    m.get("path") if isinstance(m, dict) else getattr(m, "path", ""),
                    allowed_slug,
                )
            ]
            from deepagents.backends.protocol import GrepResult

            return GrepResult(
                error=res.error, matches=filtered, truncated=res.truncated
            )
        return res


def filter_capability_mcp_tools(
    tools: list[Any],
    config: Any = None,
) -> list[Any]:
    """按已受理 capability_selection 过滤披露给模型的 MCP 工具（任务 4.3 披露层）.

    - auto: 保持全量已授权 MCP 工具披露
    - allowlist 且 tool_ids 为空: 移除全部 MCP 工具（仅保留本地工具）
    - allowlist 且包含特定 ID: 仅保留在 allowlist 中的 MCP 工具
    - 本地工具永远不受影响
    """
    if not config:
        try:
            from langgraph.config import get_config

            cfg = dict(get_config() or {})
        except Exception:
            cfg = {}
    else:
        cfg = config if isinstance(config, dict) else {}

    configurable = cfg.get("configurable") or {}
    metadata = cfg.get("metadata") or {}
    mcp_mode = (
        configurable.get("capability_mcp_mode")
        or metadata.get("capability_mcp_mode")
        or "auto"
    )
    from common.mcp_scoped import get_factory, server_clean_key
    from common.runtime_tools import get_mcp_tool_names, tool_name

    factory = get_factory()
    mcp_tool_names = set(get_mcp_tool_names())

    authorized_ids = configurable.get("capability_authorized_tool_ids")
    if authorized_ids is not None:
        authorized_set = set(authorized_ids)
        tools = [
            tool
            for tool in tools
            if not (getattr(tool, "metadata", None) or {}).get("is_mcp")
            or (getattr(tool, "metadata", None) or {}).get("stable_id")
            in authorized_set
        ]

    if mcp_mode != "allowlist":
        auth_servers = configurable.get(
            "capability_authorized_servers"
        ) or metadata.get("capability_authorized_servers")
        if auth_servers is None:
            auth_user = configurable.get("langgraph_auth_user") or metadata.get(
                "langgraph_auth_user"
            )
            if auth_user:
                tenant_id = (
                    auth_user.get("tenant_id")
                    if isinstance(auth_user, dict)
                    else getattr(auth_user, "tenant_id", None)
                )
                membership_id = (
                    auth_user.get("membership_id")
                    if isinstance(auth_user, dict)
                    else getattr(auth_user, "membership_id", None)
                )
                if factory is not None and tenant_id:
                    auth_servers = factory.authorized_servers_for_scope(
                        str(tenant_id),
                        str(membership_id) if membership_id else None,
                    )
                else:
                    auth_servers = set()

        if auth_servers is not None:
            allowed_servers = set(auth_servers)
            filtered_auto: list[Any] = []
            for t in tools:
                t_name = tool_name(t)
                if not t_name:
                    filtered_auto.append(t)
                    continue
                s_key = (
                    getattr(t, "server_key", None)
                    or (getattr(t, "metadata", None) or {}).get("server_key")
                    or (factory.server_of(t_name) if factory else None)
                    or ""
                )
                is_mcp = (
                    t_name in mcp_tool_names
                    or bool(s_key)
                    or getattr(t, "is_mcp", False)
                    or getattr(t, "_is_mcp", False)
                    or (getattr(t, "metadata", None) or {}).get("is_mcp", False)
                    or t_name.startswith("mcp_")
                    or t_name.startswith("mcp:")
                )
                if not is_mcp or s_key in allowed_servers:
                    filtered_auto.append(t)
            return filtered_auto
        # 无可信作用域时不披露进程内其它成员曾发现的工具。
        return [
            t
            for t in tools
            if not (
                (getattr(t, "metadata", None) or {}).get("is_mcp")
                or factory.server_of(tool_name(t) or "")
                or (tool_name(t) or "").startswith(("mcp_", "mcp:"))
            )
        ]

    allowed_ids = configurable.get("capability_tool_ids")
    if allowed_ids is None:
        allowed_ids = metadata.get("capability_tool_ids") or []
    allowed_set = set(allowed_ids)

    filtered: list[Any] = []
    for t in tools:
        t_name = tool_name(t)
        if not t_name:
            filtered.append(t)
            continue
        # 判断是否为 MCP 工具
        server_key = (
            getattr(t, "server_key", None)
            or (getattr(t, "metadata", None) or {}).get("server_key")
            or (factory.server_of(t_name) if factory else None)
            or ""
        )
        stable_id = (
            getattr(t, "stable_id", None)
            or (getattr(t, "metadata", None) or {}).get("stable_id")
            or f"mcp:{server_key}:{t_name}"
        )
        is_mcp = (
            t_name in mcp_tool_names
            or bool(server_key)
            or getattr(t, "is_mcp", False)
            or getattr(t, "_is_mcp", False)
            or (getattr(t, "metadata", None) or {}).get("is_mcp", False)
            or t_name.startswith("mcp_")
            or t_name.startswith("mcp:")
        )
        if not is_mcp:
            filtered.append(t)
            continue
        clean_key = server_clean_key(server_key)
        alias = f"mcp_{clean_key}__{t_name}"

        if stable_id in allowed_set or alias in allowed_set or t_name in allowed_set:
            filtered.append(t)

    return filtered
