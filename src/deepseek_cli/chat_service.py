"""与界面无关的流式聊天用例。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
import logging
from threading import Event

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
            Message(role="user", content=text),
        ]
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
                ChatEventType.ERROR, error_code=self._classify_error(exc)
            )
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        status_code = getattr(exc, "status_code", None)
        api_error_code = str(
            getattr(exc, "error_code", "") or ""
        ).lower()
        if (
            status_code in {401, 403}
            or "auth" in name
            or "auth" in api_error_code
            or "401" in message
            or "api key" in message
        ):
            return "authentication"
        if "timeout" in name or "timeout" in message:
            return "timeout"
        if (
            "connect" in name
            or "urlerror" in name
            or "network" in message
        ):
            return "network"
        if status_code == 429 or "rate" in name or "429" in message:
            return "rate_limit"
        if status_code == 404:
            return "text_endpoint_invalid"
        if status_code == 400 and (
            "model" in api_error_code or "model" in message
        ):
            return "text_model_unavailable"
        if status_code == 400:
            return "text_bad_request"
        return "service_error"
