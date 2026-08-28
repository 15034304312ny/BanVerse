"""与界面无关的流式聊天用例。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from threading import Event

from .error_codes import text_error_code
from .gateway import ChatGateway, Message
from .model_catalog import resolve_model

LOGGER = logging.getLogger("banverse.startup")


class ChatEventType(str, Enum):
    REASONING = "reasoning"
    CONTENT = "content"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ChatEvent:
    type: ChatEventType
    text: str = ""
    error_code: str = ""


class ChatStreamService:
    """执行一次请求，并将网关增量转换成稳定的领域事件。"""

    def __init__(self, gateway_factory: Callable[[], ChatGateway]) -> None:
        self._gateway_factory = gateway_factory

    def stream(
        self,
        model: str,
        history: Sequence[Message],
        user_text: str,
        *,
        cancel_event: Event | None = None,
        system_prompt: str = "",
        example_messages: Sequence[Message] = (),
        post_history_prompt: str = "",
        temperature: float | None = None,
    ) -> Iterable[ChatEvent]:
        resolved_model = resolve_model(model)
        if resolved_model is None:
            yield ChatEvent(ChatEventType.ERROR, error_code="invalid_model")
            return
        text = user_text.strip()
        if not text:
            yield ChatEvent(ChatEventType.ERROR, error_code="empty_message")
            return

        request_messages = [
            *example_messages,
            *history,
        ]
        if post_history_prompt.strip():
            request_messages.append(
                Message(role="system", content=post_history_prompt.strip())
            )
        request_messages.append(Message(role="user", content=text))
        answer_parts: list[str] = []
        stream = None
        try:
            gateway = self._gateway_factory()
            options = {}
            if system_prompt.strip():
                options["system_prompt"] = system_prompt
            if temperature is not None:
                options["temperature"] = max(
                    0.0, min(float(temperature), 2.0)
                )
            stream = gateway.stream_chat(
                resolved_model, request_messages, **options
            )
            for delta in stream:
                if cancel_event is not None and cancel_event.is_set():
                    yield ChatEvent(ChatEventType.CANCELLED)
                    return
                if delta.reasoning_content:
                    yield ChatEvent(
                        ChatEventType.REASONING, text=delta.reasoning_content
                    )
                if delta.content:
                    answer_parts.append(delta.content)
                    yield ChatEvent(ChatEventType.CONTENT, text=delta.content)

            if cancel_event is not None and cancel_event.is_set():
                yield ChatEvent(ChatEventType.CANCELLED)
                return
            answer = "".join(answer_parts)
            if not answer.strip():
                yield ChatEvent(
                    ChatEventType.ERROR, error_code="empty_response"
                )
                return
            yield ChatEvent(ChatEventType.COMPLETED, text=answer)
        except KeyboardInterrupt:
            yield ChatEvent(ChatEventType.CANCELLED)
        except Exception as exc:
            LOGGER.warning(
                "Chat request failed; type=%s error=%s",
                type(exc).__name__,
                str(exc)[:500],
            )
            yield ChatEvent(
                ChatEventType.ERROR, error_code=text_error_code(exc)
            )
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                with suppress(Exception):
                    close()
