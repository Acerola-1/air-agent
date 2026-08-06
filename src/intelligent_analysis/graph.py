"""智能分析业务图入口（LangGraph 显式节点流实现）.

原 deepagents 中间件链实现已由 common.business_graph 节点流替代：
权限审查、技能路由、提示词组装为确定性前置节点，
最终输出由 finalize_output 节点统一推送 final_output_delta/done 事件。
"""

from __future__ import annotations

from pathlib import Path

from common.business_graph import build_business_graph

SKILLS_DIR = Path(__file__).parent / "skills"

graph = build_business_graph(
    skills_dir=SKILLS_DIR,
    name="intelligent-analysis",
    checkpointer=None,
)
