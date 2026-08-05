"""DeepAgent 中间件包.

包含自定义中间件：
- PermissionClassifyMiddleware: 权限需求规则预分类中间件
- ExpandQuestionMiddleware: 问题扩展后置中间件
- TimeContextMiddleware: 当前时间上下文注入中间件
- mode_routing_prompt: 模式路由动态提示中间件
- RichOutputMiddleware: 富输出拦截中间件
- GlobalExceptionMiddleware: 全局异常友好兜底中间件
- MCPResilienceMiddleware: MCP 工具异常降级中间件
- SkillToolRegistryMiddleware: 业务工具预注册中间件
- SkillToolFilterMiddleware: Skill allowed-tools 动态披露中间件
- ToolProgressMiddleware: 工具调用前端进度中间件
- FinalOutputCleanupMiddleware: 最终答案清理中间件
"""

from __future__ import annotations

from common.middleware.expand_question_middleware import ExpandQuestionMiddleware
from common.middleware.final_output_cleanup_middleware import (
    FinalOutputCleanupMiddleware,
)
from common.middleware.global_exception_middleware import GlobalExceptionMiddleware
from common.middleware.mcp_resilience_middleware import MCPResilienceMiddleware
from common.middleware.mode_routing_middleware import mode_routing_prompt
from common.middleware.permission_classify_middleware import (
    PermissionClassifyMiddleware,
)
from common.middleware.rich_output_middleware import RichOutputMiddleware
from common.middleware.skill_tool_disclosure_middleware import (
    SkillToolFilterMiddleware,
    SkillToolRegistryMiddleware,
)
from common.middleware.time_context_middleware import TimeContextMiddleware
from common.middleware.tool_progress_middleware import ToolProgressMiddleware

__all__ = [
    "PermissionClassifyMiddleware",
    "ExpandQuestionMiddleware",
    "TimeContextMiddleware",
    "mode_routing_prompt",
    "RichOutputMiddleware",
    "GlobalExceptionMiddleware",
    "MCPResilienceMiddleware",
    "SkillToolRegistryMiddleware",
    "SkillToolFilterMiddleware",
    "ToolProgressMiddleware",
    "FinalOutputCleanupMiddleware",
]
