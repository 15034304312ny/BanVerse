from __future__ import annotations

from threading import Event

from deepseek_cli.chat_service import ChatEventType, ChatStreamService
from deepseek_cli.gateway import Message, StreamDelta
from deepseek_cli.model_catalog import MODEL_CHAT


class ProviderError(RuntimeError):
    def __init__(self, message, *, status_code=None, error_code=""):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class FakeGateway:
    def __init__(self, deltas):
        self.deltas = deltas
        self.calls = []

    def stream_chat(self, model, messages):
        self.calls.append((model, list(messages)))
        yield from self.deltas


def test_service_streams_and_completes_without_reasoning_in_history():
    gateway = FakeGateway(
        [StreamDelta(reasoning_content="想"), StreamDelta(content="答案")]
    )
    service = ChatStreamService(lambda: gateway)

    events = list(
        service.stream(
            MODEL_CHAT,
            [Message("user", "之前"), Message("assistant", "回复")],
            "现在",
        )
    )

    assert [event.type for event in events] == [
        ChatEventType.REASONING,
        ChatEventType.CONTENT,
        ChatEventType.COMPLETED,
    ]
    assert events[-1].text == "答案"
    assert gateway.calls[0][1][-1] == Message("user", "现在")
    assert all("想" not in message.content for message in gateway.calls[0][1])


def test_service_does_not_complete_empty_answer():
    service = ChatStreamService(
        lambda: FakeGateway([StreamDelta(reasoning_content="仅推理")])
    )

    events = list(service.stream(MODEL_CHAT, [], "问题"))

    assert events[-1].type is ChatEventType.ERROR
    assert events[-1].error_code == "empty_response"


def test_service_forwards_roleplay_options():
    captured = {}

    class ConfigurableGateway:
        def stream_chat(
            self,
            model,
            messages,
            *,
            system_prompt="",
            temperature=None,
        ):
            captured.update(
                model=model,
                messages=list(messages),
                system_prompt=system_prompt,
                temperature=temperature,
            )
            yield StreamDelta(content="角色回复")

    events = list(
        ChatStreamService(ConfigurableGateway).stream(
            MODEL_CHAT,
            [],
            "你好",
            system_prompt="角色设定",
            temperature=1.3,
        )
    )

    assert events[-1].type is ChatEventType.COMPLETED
    assert captured["system_prompt"] == "角色设定"
    assert captured["temperature"] == 1.3


def test_service_places_character_post_history_instruction_nearest_user_turn():
    captured = {}

    class ConfigurableGateway:
        def stream_chat(self, model, messages, **options):
            captured["model"] = model
            captured["messages"] = list(messages)
            captured["options"] = options
            yield StreamDelta(content="角色回复")

    events = list(
        ChatStreamService(ConfigurableGateway).stream(
            MODEL_CHAT,
            [Message("user", "最近消息"), Message("assistant", "最近回复")],
            "当前消息",
            example_messages=(
                Message("user", "示例消息"),
                Message("assistant", "示例回复"),
            ),
            post_history_prompt="临近角色指令",
        )
    )

    assert events[-1].type is ChatEventType.COMPLETED
    assert captured["messages"] == [
        Message("user", "示例消息"),
        Message("assistant", "示例回复"),
        Message("user", "最近消息"),
        Message("assistant", "最近回复"),
        Message("system", "临近角色指令"),
        Message("user", "当前消息"),
    ]


def test_service_honors_cancellation():
    cancel = Event()

    class CancellingGateway:
        def stream_chat(self, _model, _messages):
            yield StreamDelta(content="部分")
            cancel.set()
            yield StreamDelta(content="不会提交")

    events = list(
        ChatStreamService(CancellingGateway).stream(
            MODEL_CHAT, [], "问题", cancel_event=cancel
        )
    )

    assert events[-1].type is ChatEventType.CANCELLED
    assert not any(event.type is ChatEventType.COMPLETED for event in events)


def test_service_redacts_gateway_errors():
    class BrokenGateway:
        def stream_chat(self, _model, _messages):
            raise RuntimeError("secret raw SDK failure")
            yield

    events = list(
        ChatStreamService(BrokenGateway).stream(MODEL_CHAT, [], "问题")
    )

    assert events == [
        events[0]
    ]
    assert events[0].type is ChatEventType.ERROR
    assert events[0].text == ""
    assert events[0].error_code == "service_error"


def test_service_classifies_provider_http_errors_without_exposing_details():
    cases = (
        (ProviderError("forbidden", status_code=403), "authentication"),
        (ProviderError("not found", status_code=404), "text_endpoint_invalid"),
        (
            ProviderError(
                "unknown model",
                status_code=400,
                error_code="model_not_found",
            ),
            "text_model_unavailable",
        ),
        (ProviderError("invalid payload", status_code=400), "text_bad_request"),
    )
    for error, expected_code in cases:
        class BrokenGateway:
            def stream_chat(self, _model, _messages, _error=error):
                raise _error
                yield

        events = list(
            ChatStreamService(BrokenGateway).stream(
                MODEL_CHAT, [], "问题"
            )
        )
        assert events[-1].type is ChatEventType.ERROR
        assert events[-1].text == ""
        assert events[-1].error_code == expected_code
