"""Small controlled messages that still use the answer lifecycle.

Short failures, clarifications and policy-bound fallbacks are user-visible
answers but are explicitly *not* proof of token streaming.  They use the same
``final_output_*`` lifecycle with ``delivery_mode=single_shot`` so clients have
one consumer path and measurements can label the source honestly.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from common.output_streaming.answer import chunk_text
from common.output_streaming.contract import DELIVERY_MODE_SINGLE_SHOT
from common.output_streaming.publisher import OutputAborted, OutputPublisher


async def publish_control_message(
    config: dict[str, Any],
    *,
    graph_id: str,
    text: str,
    writer: Any = None,
    authorization_probe: Any = None,
    control_code: str = "control",
) -> AIMessage:
    """Publish a short server-authored message as one completed output."""
    publisher = OutputPublisher(
        config,
        graph_id=graph_id,
        delivery_mode=DELIVERY_MODE_SINGLE_SHOT,
        writer=writer,
        authorization_probe=authorization_probe,
    )
    publisher.start(control_code=control_code)
    try:
        await publisher.finish(text)
    except OutputAborted:
        return AIMessage(
            content=text,
            id=publisher.message_id,
            additional_kwargs={
                "ui_visible": False,
                "output_protocol_version": 1,
                "output_id": publisher.output_id,
                "output_aborted": "permission_revoked",
            },
        )
    return AIMessage(
        content=text,
        id=publisher.message_id,
        additional_kwargs={
            "ui_visible": True,
            "output_protocol_version": 1,
            "output_id": publisher.output_id,
            "delivery_mode": DELIVERY_MODE_SINGLE_SHOT,
            "control_code": control_code,
        },
    )


def response_text(response: Any) -> str:
    """Best-effort text of a model response for legacy call sites."""
    if isinstance(response, AIMessage) and isinstance(response.content, str):
        return response.content
    return chunk_text(response)
