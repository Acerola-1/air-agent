"""权限审查模块.

采用"事实抽取层 + 可信上下文层 + 确定性规则引擎层"的三层设计：
1. LLM 只输出 PermissionExtractedSlots（信息抽取）
2. MCP 工具提供可信事实（用户画像、行政区解析）
3. 代码规则引擎做判定（check_permission -> PermissionResult）
"""

from __future__ import annotations

from common.permission.engine import check_permission
from common.permission.facts import (
    PermissionFacts,
    StationPermissionCandidate,
    build_permission_facts,
)
from common.permission.result import (
    AllowedRegion,
    PermissionResult,
    RegionPermissionResult,
    StationOverride,
)
from common.permission.rules import classify_permission_need
from common.permission.slots import (
    PERMISSION_SLOT_EXTRACTION_PROMPT,
    PermissionExtractedSlots,
    PermissionRegionRequest,
    normalize_target_level,
    normalize_time_granularity,
)

__all__ = [
    "check_permission",
    "PermissionFacts",
    "StationPermissionCandidate",
    "build_permission_facts",
    "AllowedRegion",
    "PermissionResult",
    "RegionPermissionResult",
    "StationOverride",
    "classify_permission_need",
    "PERMISSION_SLOT_EXTRACTION_PROMPT",
    "PermissionExtractedSlots",
    "PermissionRegionRequest",
    "normalize_target_level",
    "normalize_time_granularity",
]
