"""图内 Skill 语义路由辅助工具."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import openai
import yaml
from loguru import logger
from pydantic import ConfigDict, PrivateAttr
from semantic_router.encoders.base import DenseEncoder

from common.question_normalizer import QuestionNormalizer

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(?P<body>.*?)\r?\n---\r?\n", re.DOTALL)


@dataclass(frozen=True)
class SkillRouteCandidate:
    """匹配到的 Skill 路由候选."""

    name: str
    description: str
    path: str
    allowed_tools: tuple[str, ...]
    similarity_score: float | None


@dataclass(frozen=True)
class SkillMetadata:
    """从 SKILL.md frontmatter 解析出的 Skill 元数据."""

    name: str
    description: str
    path: Path
    allowed_tools: tuple[str, ...]
    utterances: tuple[str, ...]


def normalize_routing_text(text: str) -> str:
    """语义路由前归一化用户文本."""
    return QuestionNormalizer.normalize(text)


def _parse_frontmatter(body: str, skill_file: Path) -> dict[str, Any] | None:
    """解析 SKILL.md 的 YAML frontmatter."""
    try:
        metadata = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        logger.warning(
            "技能语义路由跳过文件：frontmatter YAML 解析失败，file={}，错误={}",
            skill_file,
            exc,
        )
        return None

    if metadata is None:
        return {}
    if not isinstance(metadata, Mapping):
        logger.warning(
            "技能语义路由跳过文件：frontmatter 不是 YAML mapping，file={}",
            skill_file,
        )
        return None
    return {str(key): value for key, value in metadata.items()}


def _parse_string_field(raw_value: Any) -> str:
    """将 YAML 标量字段归一化为字符串."""
    return raw_value.strip() if isinstance(raw_value, str) else ""


def _parse_allowed_tools(raw_value: Any) -> tuple[str, ...]:
    """解析 Skill frontmatter 中的 allowed-tools 字段."""
    if not raw_value:
        return ()

    if isinstance(raw_value, str):
        raw_tools = raw_value.split()
    elif isinstance(raw_value, Sequence) and not isinstance(raw_value, str):
        raw_tools = [item for item in raw_value if isinstance(item, str)]
    else:
        return ()

    tools = [item.strip() for item in raw_tools if item.strip()]
    return tuple(dict.fromkeys(tools))


def _description_to_utterances(name: str, description: str) -> tuple[str, ...]:
    """根据 Skill 描述生成 semantic-router utterances."""
    utterances: list[str] = [name]
    for line in description.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.endswith(("：", ":")):
            continue
        cleaned = cleaned.removeprefix("-").strip()
        cleaned = cleaned.strip("\"'")
        if cleaned:
            utterances.append(normalize_routing_text(cleaned))

    deduped = list(dict.fromkeys(item for item in utterances if item))
    return tuple(deduped)


def load_skill_metadata(skills_dir: str | Path) -> list[SkillMetadata]:
    """从 skills 目录加载当前业务图的 Skill 元数据."""
    root = Path(skills_dir)
    if not root.exists():
        logger.warning("技能语义路由加载失败：skills 目录不存在，skills_dir={}", root)
        return []

    skills: list[SkillMetadata] = []
    for skill_file in sorted(root.glob("*/SKILL.md")):
        text = skill_file.read_text(encoding="utf-8")
        match = _FRONTMATTER_RE.match(text)
        if not match:
            logger.warning(
                "技能语义路由跳过文件：缺少 frontmatter，file={}", skill_file
            )
            continue

        metadata = _parse_frontmatter(match.group("body"), skill_file)
        if metadata is None:
            continue

        name = _parse_string_field(metadata.get("name"))
        description = _parse_string_field(metadata.get("description"))
        allowed_tools = _parse_allowed_tools(metadata.get("allowed-tools"))
        if not name or not description:
            logger.warning("技能语义路由跳过文件：metadata 不完整，file={}", skill_file)
            continue

        skills.append(
            SkillMetadata(
                name=name,
                description=description,
                path=skill_file,
                allowed_tools=allowed_tools,
                utterances=_description_to_utterances(name, description),
            )
        )
    return skills


class SiliconFlowEmbeddingEncoder(DenseEncoder):
    """兼容 semantic-router DenseEncoder 的 SiliconFlow embedding 编码器."""

    openai_base_url: str
    openai_api_key: str
    type: str = "siliconflow"
    model_config = ConfigDict(arbitrary_types_allowed=True)
    _client: openai.Client = PrivateAttr()
    _aclient: openai.AsyncClient = PrivateAttr()

    def model_post_init(self, __context: Any) -> None:
        """初始化兼容 OpenAI 协议的 embedding 客户端."""
        self._client = openai.Client(
            base_url=self.openai_base_url,
            api_key=self.openai_api_key,
        )
        self._aclient = openai.AsyncClient(
            base_url=self.openai_base_url,
            api_key=self.openai_api_key,
        )

    def __call__(self, docs: list[str]) -> list[list[float]]:
        """为 semantic-router 生成 embedding（同步，仅用于启动时构建索引）."""
        resp = self._client.embeddings.create(model=self.name, input=docs)
        return [item.embedding for item in resp.data]

    async def acall(self, docs: list[str]) -> list[list[float]]:
        """为 semantic-router 生成 embedding（异步，用于运行时查询，不阻塞事件循环）."""
        resp = await self._aclient.embeddings.create(model=self.name, input=docs)
        return [item.embedding for item in resp.data]


class SkillSemanticRouter:
    """当前业务图内的 Skill 语义路由器."""

    _MATCH_CACHE_MAX_ENTRIES = 256

    def __init__(
        self,
        skills_dir: str | Path,
        *,
        score_threshold: float = 0.55,
        max_candidates: int = 3,
        exposed_skills_root: str = "/skills",
    ) -> None:
        """使用当前业务图的 Skill 元数据配置初始化路由器."""
        self.skills_dir = Path(skills_dir)
        self.exposed_skills_root = exposed_skills_root.rstrip("/") or "/skills"
        self.score_threshold = score_threshold
        self.max_candidates = max_candidates
        self._lock = Lock()
        self._router: Any | None = None
        self._skills_by_name: dict[str, SkillMetadata] = {}
        self._match_cache: dict[str, tuple[SkillRouteCandidate, ...]] = {}
        self._build_failed = False

    def _build_router(self) -> Any | None:
        """延迟构建 semantic-router."""
        if self._router is not None or self._build_failed:
            logger.info(
                "技能语义路由构建跳过：skills_dir={}，已构建={}，曾构建失败={}",
                self.skills_dir,
                self._router is not None,
                self._build_failed,
            )
            return self._router

        with self._lock:
            if self._router is not None or self._build_failed:
                logger.info(
                    "技能语义路由构建跳过（加锁后复查）：skills_dir={}，已构建={}，曾构建失败={}",
                    self.skills_dir,
                    self._router is not None,
                    self._build_failed,
                )
                return self._router

            try:
                from semantic_router import Route, SemanticRouter
                from semantic_router.index import LocalIndex
            except ImportError as exc:
                logger.warning(
                    "技能语义路由已禁用：未安装 semantic-router，错误={}", exc
                )
                self._build_failed = True
                return None

            try:
                skills = load_skill_metadata(self.skills_dir)
                if not skills:
                    logger.info(
                        "技能语义路由已禁用：未找到 skill，skills_dir={}",
                        self.skills_dir,
                    )
                    self._build_failed = True
                    return None

                logger.info(
                    "技能语义路由开始构建索引：skills_dir={}，skill数量={}，skill列表={}",
                    self.skills_dir,
                    len(skills),
                    [skill.name for skill in skills],
                )
                routes = [
                    Route(name=skill.name, utterances=list(skill.utterances))
                    for skill in skills
                ]

                from common.config import config

                encoder = SiliconFlowEmbeddingEncoder(
                    name=config.SILICONFLOW_EMBEDDING_MODEL,
                    openai_base_url=config.SILICONFLOW_BASE_URL,
                    openai_api_key=config.SILICONFLOW_API_KEY,
                )
                router = SemanticRouter(
                    encoder=encoder,
                    routes=routes,
                    index=LocalIndex(),
                    aggregation="max",
                    top_k=10,
                )
                router.add(routes)
            except Exception as exc:
                logger.warning("技能语义路由已禁用：构建失败，错误={}", exc)
                self._build_failed = True
                return None

            self._router = router
            self._skills_by_name = {skill.name: skill for skill in skills}
            logger.info(
                "技能语义路由初始化完成：skills_dir={}，skill数量={}",
                self.skills_dir,
                len(skills),
            )
            return self._router

    def prewarm(self) -> None:
        """预构建 embedding 索引，供服务启动时后台线程调用.

        避免首个用户请求承担冷启动的批量 embedding 构建延迟。
        构建失败时内部已记录日志并标记 _build_failed，不抛出异常。
        """
        try:
            self._build_router()
        except Exception as exc:
            logger.warning("技能语义路由预热失败: {}", exc)

    def _cache_match(
        self,
        normalized_question: str,
        candidates: tuple[SkillRouteCandidate, ...],
    ) -> None:
        """写入匹配缓存，超出上限时按插入顺序淘汰最老条目."""
        if len(self._match_cache) >= self._MATCH_CACHE_MAX_ENTRIES:
            self._match_cache.pop(next(iter(self._match_cache)), None)
        self._match_cache[normalized_question] = candidates

    async def amatch(self, question: str) -> list[SkillRouteCandidate]:
        """返回用户问题匹配到的 Skill 候选（异步，不阻塞事件循环）."""
        normalized_question = normalize_routing_text(question)
        if not normalized_question:
            logger.info(
                "技能语义匹配跳过：skills_dir={}，原因=问题为空",
                self.skills_dir,
            )
            return []
        if normalized_question in self._match_cache:
            cached = self._match_cache[normalized_question]
            logger.info(
                "技能语义匹配命中缓存：skills_dir={}，归一化问题={}，候选数量={}",
                self.skills_dir,
                normalized_question[:120],
                len(cached),
            )
            return list(self._match_cache[normalized_question])

        router = self._build_router()
        if router is None:
            logger.info(
                "技能语义匹配跳过：skills_dir={}，原因=路由器不可用",
                self.skills_dir,
            )
            return []

        logger.info(
            "技能语义匹配开始：skills_dir={}，归一化问题={}",
            self.skills_dir,
            normalized_question[:120],
        )
        try:
            matches = await router.acall(
                normalized_question, limit=self.max_candidates
            )
        except Exception as exc:
            logger.warning("技能语义匹配失败：错误={}", exc)
            return []

        # semantic-router 的 limit>1 返回类型是联合类型：
        #   - 0 命中 → 单个空 RouteChoice（name=None）
        #   - 1 命中 → 单个 RouteChoice（向后兼容，不是 list）
        #   - ≥2 命中 → list[RouteChoice]
        raw_matches = matches if isinstance(matches, list) else [matches]
        # 过滤掉空 RouteChoice（name=None，0 命中时的占位符）
        raw_matches = [
            m for m in raw_matches if getattr(m, "name", None) is not None
        ]

        if not raw_matches:
            logger.info(
                "技能语义匹配无原始命中：skills_dir={}，归一化问题={}",
                self.skills_dir,
                normalized_question[:120],
            )
            self._cache_match(normalized_question, ())
            return []
        logger.info(
            "技能语义匹配原始结果：skills_dir={}，结果={}",
            self.skills_dir,
            [
                {
                    "name": getattr(match, "name", ""),
                    "score": getattr(match, "similarity_score", None),
                }
                for match in raw_matches
            ],
        )
        candidates: list[SkillRouteCandidate] = []
        for match in raw_matches:
            name = getattr(match, "name", "")
            skill = self._skills_by_name.get(name)
            if skill is None:
                logger.info(
                    "技能语义匹配忽略未知路由：skills_dir={}，route_name={}",
                    self.skills_dir,
                    name,
                )
                continue

            score = getattr(match, "similarity_score", None)
            if score is not None and score < self.score_threshold:
                logger.info(
                    "技能语义匹配拒绝低分候选：skill={}，分数={}，阈值={}",
                    name,
                    score,
                    self.score_threshold,
                )
                continue

            candidates.append(
                SkillRouteCandidate(
                    name=skill.name,
                    description=skill.description,
                    path=self._exposed_skill_path(skill.path),
                    allowed_tools=skill.allowed_tools,
                    similarity_score=score,
                )
            )
            if len(candidates) >= self.max_candidates:
                break

        self._cache_match(normalized_question, tuple(candidates))
        logger.info(
            "技能语义匹配完成：skills_dir={}，候选数量={}，候选={}",
            self.skills_dir,
            len(candidates),
            [
                {"name": candidate.name, "score": candidate.similarity_score}
                for candidate in candidates
            ],
        )
        return candidates

    def resolve_skill_file(self, name: str) -> Path | None:
        """返回已加载 Skill 的真实 SKILL.md 路径，用于命中后内联规则内容."""
        skill = self._skills_by_name.get(name)
        return skill.path if skill is not None else None

    def _exposed_skill_path(self, skill_file: Path) -> str:
        """返回本地 Skill 文件在后端可见的虚拟路径."""
        try:
            relative_path = skill_file.relative_to(self.skills_dir)
        except ValueError:
            return skill_file.as_posix()
        return f"{self.exposed_skills_root}/{relative_path.as_posix()}"
