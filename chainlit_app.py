"""Chainlit 多图聊天界面.

通过页面上的按钮切换 langgraph.json 中声明的多个图, 并调用本机
langgraph-api 后端(langgraph dev, 默认 :2024)执行对话.

启动方式::

    chainlit run chainlit_app.py --port 8000

环境变量: LANGGRAPH_API_URL 指向 langgraph-api 地址.
"""

import os
from typing import Any, Mapping

import chainlit as cl
from chainlit.input_widget import Select
from langgraph_sdk import get_client

# langgraph-api 后端地址, 与 run-local.sh 中的 langgraph dev 端口一致.
LANGGRAPH_API_URL = os.getenv("LANGGRAPH_API_URL", "http://localhost:2024")

# 与 langgraph.json 中 graphs 声明一致的图 id -> 中文展示名.
GRAPHS: dict[str, str] = {
    "basic-qa": "基础问答",
    "intelligent-analysis": "智能分析",
    "data-analysis": "数据分析助手",
    "intelligent-report": "报告生成",
    "deep-research": "深度研究",
    "intelligent-tracing": "溯源追踪",
}

# 会话默认使用的图 id.
DEFAULT_GRAPH_ID = next(iter(GRAPHS))

# 当前选中图在 user_session 中的键.
_SESSION_GRAPH_ID = "graph_id"

# 当前会话绑定的 langgraph 线程 id 在 user_session 中的键.
_SESSION_THREAD_ID = "lg_thread_id"


def _get_graph_id() -> str:
    """获取当前会话选中的图 id, 未选择时返回默认图."""
    return str(cl.user_session.get(_SESSION_GRAPH_ID) or DEFAULT_GRAPH_ID)


def _ensure_client() -> Any:
    """获取 langgraph-sdk 客户端, 按会话缓存复用."""
    client = cl.user_session.get("lg_client")
    if client is None:
        client = get_client(url=LANGGRAPH_API_URL)
        cl.user_session.set("lg_client", client)
    return client


def _first_message(data: Any) -> Mapping[str, Any] | None:
    """从流式事件的 data 中取出消息字典.

    stream_mode="messages" 时 data 为 [message, metadata] 二元组列表,
    兼容部分版本返回 {"message": ...} 字典的形态.
    """
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, dict):
            return message
    return None


def _extract_text(content: Any) -> str:
    """从 langgraph 消息 content 中提取纯文本.

    content 可能是字符串, 也可能是多模态块列表(文本块/工具调用块等),
    仅拼接其中的文本块, 忽略工具调用等非文本内容.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


async def _render_switcher(active_graph_id: str) -> None:
    """发送一条带图切换按钮的消息, 按钮点击后切换当前图."""
    active_label = GRAPHS.get(active_graph_id, active_graph_id)
    message = cl.Message(
        content=f"当前图：**{active_label}**\n\n点击下方按钮切换图，切换后下一轮对话将开启新会话。"
    )
    await message.send()
    for graph_id, label in GRAPHS.items():
        action = cl.Action(
            name="switch_graph",
            payload={"graph_id": graph_id, "label": label},
            label=label,
            tooltip=f"切换到{label}",
        )
        await action.send(for_id=message.id)


async def _render_tool_step(message_dict: Mapping[str, Any]) -> None:
    """把工具调用结果渲染成可折叠的代码步骤, 避免原始 JSON 直接铺在对话流中."""
    name = str(message_dict.get("name") or "工具调用")
    output = _extract_text(message_dict.get("content"))
    if len(output) > 1500:
        output = output[:1500] + "\n…(内容过长已截断)"
    async with cl.Step(name=name, type="tool", language="json") as step:
        step.output = output


async def _get_or_create_thread(client: Any, graph_id: str) -> str:
    """按图获取或创建 langgraph 线程.

    线程元数据记录 graph_id, 与现有前端会话列表的隔离约定保持一致;
    首次调用时创建线程, 后续复用同一线程以保留上下文.
    """
    thread_id = cl.user_session.get(_SESSION_THREAD_ID)
    if not isinstance(thread_id, str):
        thread = await client.threads.create(metadata={"graph_id": graph_id})
        thread_id = thread["thread_id"]
        cl.user_session.set(_SESSION_THREAD_ID, thread_id)
    return thread_id


async def _render_chat_settings() -> None:
    """渲染快速/专家模式的常驻设置项(页面头部设置入口)."""
    widgets: list[Any] = [
        Select(
            id="chat_mode",
            label="回答模式",
            values=["fast", "expert"],
            initial_value="fast",
            description="快速：简洁高效回答 / 专家：深度专业分析",
        )
    ]
    settings = cl.ChatSettings(inputs=widgets)
    await settings.send()


def _get_chat_mode() -> str:
    """读取当前会话选中的回答模式, 默认 fast."""
    chat_settings = getattr(cl.context.session, "chat_settings", None) or {}
    return str(chat_settings.get("chat_mode") or "fast")


@cl.on_chat_start
async def on_chat_start() -> None:
    """会话启动: 初始化默认图、渲染设置项与图切换按钮."""
    cl.user_session.set(_SESSION_GRAPH_ID, DEFAULT_GRAPH_ID)
    cl.user_session.set(_SESSION_THREAD_ID, None)
    await _render_chat_settings()
    await _render_switcher(DEFAULT_GRAPH_ID)


@cl.action_callback("switch_graph")
async def on_switch_graph(action: cl.Action) -> None:
    """图切换按钮回调: 更新当前图并重置线程, 下次提问开启新会话."""
    graph_id = str(action.payload.get("graph_id") or DEFAULT_GRAPH_ID)
    cl.user_session.set(_SESSION_GRAPH_ID, graph_id)
    cl.user_session.set(_SESSION_THREAD_ID, None)
    label = GRAPHS.get(graph_id, graph_id)
    await cl.Message(content=f"已切换到 **{label}**，请输入你的问题。").send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """处理用户消息: 按当前图调用 langgraph-api 并流式展示回复."""
    graph_id = _get_graph_id()
    client = _ensure_client()
    try:
        thread_id = await _get_or_create_thread(client, graph_id)
    except Exception as exc:
        await cl.Message(content=f"❌ 连接 LangGraph 后端失败：{exc}").send()
        return

    answer = cl.Message(content="")
    await answer.send()

    chat_mode = _get_chat_mode()
    seen: dict[str, int] = {}
    shown_tool_steps: set[str] = set()
    try:
        async for part in client.runs.stream(
            thread_id,
            assistant_id=graph_id,
            input={"messages": [{"role": "user", "content": message.content}]},
            config={"configurable": {"chat_mode": chat_mode}},
            stream_mode="messages",
        ):
            # 新版 langgraph-api 的消息事件名为 messages/partial(增量)与 messages/complete(最终),
            # 兼容旧版 SDK 的 messages 命名.
            if part.event not in ("messages", "messages/partial", "messages/complete"):
                continue
            message_dict = _first_message(part.data)
            if message_dict is None:
                continue
            message_id = str(message_dict.get("id") or "default")
            # 工具调用结果(tool 消息)不直接铺在对话流里, 折叠成代码步骤展示.
            if message_dict.get("type") == "tool":
                if message_id not in shown_tool_steps:
                    shown_tool_steps.add(message_id)
                    await _render_tool_step(message_dict)
                continue
            text = _extract_text(message_dict.get("content"))
            if not text:
                continue
            previous = seen.get(message_id, 0)
            if len(text) > previous:
                await answer.stream_token(text[previous:])
                seen[message_id] = len(text)
    except Exception as exc:
        await answer.stream_token(f"\n\n⚠️ 请求出错：{exc}")
    finally:
        await answer.update()
