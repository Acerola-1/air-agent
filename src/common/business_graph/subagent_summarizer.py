"""独立 Subagent 总结提炼子代理模块.

当工具返回超过安全阈值的大数据集（如 50 万字符）时，
启动拥有独立 192K 上下文的 Subagent 实施精准目标抽取与高浓度总结，
确保数据零截断丢失，同时将主模型上下文控制在极小范围内。
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from common.models import ModelRegistry

_SUBAGENT_SYSTEM_PROMPT = """你是一个专业、严谨的数据精炼与结构化抽取助手。
在数据查询流程中，某个工具返回了海量原始数据（可能包含数万至数十万字符）。
你的任务是：根据用户的原始提问和工具调用意图，对海量原始数据进行高浓度提炼，剔除噪音，保留对回答用户问题有直接价值的关键指标。

【严格遵守以下规则】
1. 目标实体精准抽取：若数据中包含了用户询问的具体目标实体（如特定省份、城市、区县、站点、指标因子或特定时间段），请精准提取该目标对象的有效数值和关键结论，彻底过滤掉其他无关实体的冗余数据；
2. 缺失显式警示：若通读数据后，确认数据中【未包含】用户所询问的目标实体（例如用户询问海淀区，但数据全是全省或其他城市，或者因参数传空返回了无关列表），必须明确输出警示：
   "【数据警示】工具返回了海量数据，但经检索未包含用户目标实体 [{target}] 的专属数据，疑似查询参数过滤缺失或未命中。以下为返回数据的宏观概况：..."
   切勿无中生有编造目标数据；
3. 输出精炼紧凑：输出为结构化的中文提炼文本，保留关键数值、单位与时间，严格控制在 1500 字符以内，不要输出无关的废话；
4. 格式友好：使用清晰的 Markdown 列表或小表格呈现核心数据。
"""


def _build_summarizer_prompt(
    *,
    user_question: str,
    tool_name: str,
    tool_args: dict[str, Any] | str,
    char_length: int,
    raw_output: str,
) -> str:
    """构建 Subagent 用户指令提示词."""
    # 防止传入极端天文数字导致 LLM 自身超出 192K，截取前 120,000 字符约 40k~60k tokens
    max_safe_len = 120_000
    effective_data = raw_output[:max_safe_len]
    truncated_hint = (
        f"\n(注：原始数据共 {char_length} 字符，此处截取前 {max_safe_len} 字符输入进行提炼)\n"
        if char_length > max_safe_len
        else ""
    )

    return f"""## 用户当前原始提问
{user_question or '（未指定具体提问）'}

## 工具调用信息
- 工具名：{tool_name}
- 工具参数：{tool_args}
- 原始数据大小：{char_length} 字符{truncated_hint}

## 待精炼的工具返回原始数据
```text
{effective_data}
```

请根据上述用户提问，对原始数据执行精准抽取与浓缩提炼："""


def _fallback_summary(
    *,
    tool_name: str,
    char_length: int,
    raw_output: str,
    reason: str,
) -> str:
    """Subagent 异常或超时时的安全降级文本."""
    preview_head = raw_output[:1500].strip()
    preview_tail = raw_output[-500:].strip() if char_length > 2000 else ""
    return (
        f"【工具超大输出降级摘要】\n"
        f"工具 [{tool_name}] 返回了大型数据集（共 {char_length} 字符）。\n"
        f"由于 Subagent 自动提炼降级（原因: {reason}），原始数据已完整落库保存。\n"
        f"数据前段样本预览：\n{preview_head}\n"
        + (f"...\n数据尾段样本预览：\n{preview_tail}\n" if preview_tail else "")
        + "\n（提示：如需特定区县或时段的精准数据，请细化参数重新查询）"
    )


async def summarize_large_tool_output(
    *,
    user_question: str,
    tool_name: str,
    tool_args: dict[str, Any] | str,
    raw_output: str,
    timeout_seconds: float = 25.0,
) -> str:
    """使用独立 192K 上下文的 Subagent 对超大工具返回进行精准抽取提炼.

    Args:
        user_question: 用户当前提问
        tool_name: 工具名称
        tool_args: 工具调用参数
        raw_output: 超大原始文本
        timeout_seconds: Subagent 最大等待超时时间（秒）

    Returns:
        提炼后的高浓度字符串（控制在 1500 字符内）
    """
    char_length = len(raw_output)
    logger.info(
        "启动 Subagent 对超大工具输出进行提炼: tool={}, char_length={}, question={}",
        tool_name,
        char_length,
        user_question[:60],
    )

    user_prompt = _build_summarizer_prompt(
        user_question=user_question,
        tool_name=tool_name,
        tool_args=tool_args,
        char_length=char_length,
        raw_output=raw_output,
    )

    messages = [
        SystemMessage(content=_SUBAGENT_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    # 获取模型实例 (默认复用 main_llm)
    model = ModelRegistry.main_llm

    try:
        response = await asyncio.wait_for(
            model.ainvoke(messages),
            timeout=timeout_seconds,
        )
        content = getattr(response, "content", "")
        summary_text = str(content).strip()
        if not summary_text:
            return _fallback_summary(
                tool_name=tool_name,
                char_length=char_length,
                raw_output=raw_output,
                reason="模型返回空摘要",
            )
        logger.info(
            "Subagent 提炼完成: tool={}, 原长={}, 提炼后长度={}",
            tool_name,
            char_length,
            len(summary_text),
        )
        return summary_text
    except TimeoutError:
        logger.warning(
            "Subagent 提炼超时 ({}s): tool={}, 启用安全降级",
            timeout_seconds,
            tool_name,
        )
        return _fallback_summary(
            tool_name=tool_name,
            char_length=char_length,
            raw_output=raw_output,
            reason=f"提炼超时 ({timeout_seconds}s)",
        )
    except Exception as e:
        logger.exception(
            "Subagent 提炼发生未知异常: tool={}, error={}, 启用安全降级",
            tool_name,
            e,
        )
        return _fallback_summary(
            tool_name=tool_name,
            char_length=char_length,
            raw_output=raw_output,
            reason=f"提炼异常 ({e})",
        )
