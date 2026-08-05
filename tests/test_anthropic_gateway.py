from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from deepseek_cli.anthropic_gateway import (
    DEEPSEEK_CHAT_URL,
    DEFAULT_MAX_TOKENS,
    DeepSeekHttpError,
    DeepSeekHttpGateway,
)
from deepseek_cli.gateway import Message, StreamDelta
from deepseek_cli.model_catalog import MODEL_CHAT, MODEL_REASONER


class FakeResponse:
    def __init__(self, lines):
        self.lines = lines
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return iter(self.lines)

    def __exit__(self, *_args):
        self.exited = True


def sse(payload) -> bytes:
    if payload == "[DONE]":
        return b"data: [DONE]\n"
    return (
        "data: " + json.dumps(payload, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def test_gateway_uses_documented_chat_completions_endpoint_and_bearer_key():
    captured = {}
    response = FakeResponse([sse("[DONE]")])

    def opener(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return response

    gateway = DeepSeekHttpGateway(" only-this-key ", opener=opener)
    assert list(gateway.stream_chat(MODEL_CHAT, [])) == []

    request = captured["request"]
    assert request.full_url == DEEPSEEK_CHAT_URL
    assert request.get_header("Authorization") == "Bearer only-this-key"
    assert request.get_method() == "POST"
    assert captured["timeout"] == 180
    assert response.entered and response.exited


def test_gateway_maps_text_and_reasoning_sse_deltas():
    response = FakeResponse(
        [
            b": keep-alive\n",
            b"data: not-json\n",
            sse({"choices": []}),
            sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "reasoning_content": "想",
                                "content": "答案",
                            }
                        }
                    ]
                }
            ),
            sse("[DONE]"),
        ]
    )
    captured = {}

    def opener(request, *, timeout):
        captured["payload"] = json.loads(request.data)
        return response

    result = list(
        DeepSeekHttpGateway("key", opener=opener).stream_chat(
            MODEL_REASONER,
            [Message("user", "你好")],
        )
    )

    assert captured["payload"] == {
        "model": MODEL_REASONER,
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": DEFAULT_MAX_TOKENS,
        "stream": True,
        "thinking": {"type": "enabled"},
    }
    assert result == [
        StreamDelta(reasoning_content="想"),
        StreamDelta(content="答案"),
    ]


def test_gateway_prepends_system_prompt_and_clamps_temperature():
    captured = {}

    def opener(request, *, timeout):
        captured["payload"] = json.loads(request.data)
        return FakeResponse([sse("[DONE]")])

    gateway = DeepSeekHttpGateway("key", opener=opener)
    list(
        gateway.stream_chat(
            MODEL_CHAT,
            [Message("user", "你好")],
            system_prompt="You are Alice.",
            temperature=2.7,
        )
    )

    payload = captured["payload"]
    assert payload["messages"][0] == {
        "role": "system",
        "content": "You are Alice.",
    }
    assert payload["temperature"] == 2.0
    assert payload["thinking"] == {"type": "disabled"}


def test_gateway_applies_claude_and_unknown_model_mapping():
    for source, expected in (
        ("claude-opus-4-1", MODEL_REASONER),
        ("claude-sonnet-4-5", MODEL_CHAT),
        ("claude-haiku-4-5", MODEL_CHAT),
        ("not-a-model", MODEL_CHAT),
    ):
        captured = {}

        def opener(request, *, timeout, _captured=captured):
            _captured["payload"] = json.loads(request.data)
            return FakeResponse([sse("[DONE]")])

        gateway = DeepSeekHttpGateway("key", opener=opener)
        list(gateway.stream_chat(source, []))
        assert captured["payload"]["model"] == expected


def test_gateway_exposes_http_status_and_api_error_code():
    body = BytesIO(
        json.dumps(
            {
                "error": {
                    "message": "Invalid API key",
                    "code": "authentication_error",
                }
            }
        ).encode("utf-8")
    )

    def opener(_request, *, timeout):
        raise HTTPError(
            DEEPSEEK_CHAT_URL,
            401,
            "Unauthorized",
            hdrs=None,
            fp=body,
        )

    gateway = DeepSeekHttpGateway("key", opener=opener)
    with pytest.raises(DeepSeekHttpError) as caught:
        list(gateway.stream_chat(MODEL_CHAT, []))

    assert caught.value.status_code == 401
    assert caught.value.error_code == "authentication_error"
    assert "Invalid API key" in str(caught.value)


def test_gateway_rejects_empty_api_key():
    with pytest.raises(ValueError):
        DeepSeekHttpGateway(" ")
