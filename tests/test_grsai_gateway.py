from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from deepseek_cli.gateway import Message, StreamDelta
from deepseek_cli.grsai_gateway import (
    DEFAULT_GRSAI_API_BASE_URL,
    DEFAULT_GRSAI_TEXT_MODEL,
    GrsAiGateway,
    GrsAiHttpError,
    normalize_grsai_base_url,
)


class FakeResponse:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return iter(self.lines)

    def __exit__(self, *_args):
        return None


def sse(payload) -> bytes:
    if payload == "[DONE]":
        return b"data: [DONE]\n"
    return (
        "data: " + json.dumps(payload, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def test_grsai_gateway_uses_configured_openai_compatible_endpoint():
    captured = {}

    def opener(request, *, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse(
            [
                sse(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "reasoning_content": "想",
                                    "content": "你好",
                                }
                            }
                        ]
                    }
                ),
                sse("[DONE]"),
            ]
        )

    result = list(
        GrsAiGateway(
            " grs-key ",
            model="gemini-test",
            opener=opener,
        ).stream_chat(
            "deepseek-v4-flash",
            [Message("user", "在吗")],
            system_prompt="扮演角色",
            temperature=1.3,
            top_p=0.9,
            frequency_penalty=0.5,
        )
    )

    assert captured["url"] == (
        f"{DEFAULT_GRSAI_API_BASE_URL}/chat/completions"
    )
    assert captured["authorization"] == "Bearer grs-key"
    assert captured["payload"] == {
        "model": "gemini-test",
        "messages": [
            {"role": "system", "content": "扮演角色"},
            {"role": "user", "content": "在吗"},
        ],
        "stream": True,
        "temperature": 1.3,
        "top_p": 0.9,
    }
    assert "frequency_penalty" not in captured["payload"]
    assert captured["timeout"] == 180
    assert result == [
        StreamDelta(reasoning_content="想"),
        StreamDelta(content="你好"),
    ]


def test_grsai_gateway_validates_base_url_and_default_model():
    captured = {}

    def opener(request, *, timeout):
        captured["payload"] = json.loads(request.data)
        return FakeResponse([sse("[DONE]")])

    list(GrsAiGateway("key", opener=opener).stream_chat("ignored", []))
    assert captured["payload"]["model"] == DEFAULT_GRSAI_TEXT_MODEL
    assert normalize_grsai_base_url(
        "https://grsaiapi.com/v1/"
    ) == "https://grsaiapi.com/v1"
    assert normalize_grsai_base_url(
        "https://grsai.dakka.com.cn/v1/chat/completions"
    ) == "https://grsai.dakka.com.cn/v1"
    assert normalize_grsai_base_url(
        "https://grsaiapi.com/v1/images/generations"
    ) == "https://grsaiapi.com/v1"
    assert normalize_grsai_base_url(
        "https://grsai.dakka.com.cn/v1/draw/nano-banana"
    ) == "https://grsai.dakka.com.cn/v1"
    assert normalize_grsai_base_url(
        "https://grsaiapi.com/v1/draw/completions"
    ) == "https://grsaiapi.com/v1"
    assert normalize_grsai_base_url(
        "https://grsaiapi.com/v1/draw/result"
    ) == "https://grsaiapi.com/v1"
    assert normalize_grsai_base_url(
        "https://grsai.dakka.com.cn/v1/api/generate"
    ) == "https://grsai.dakka.com.cn/v1"
    assert normalize_grsai_base_url(
        "https://grsai.dakka.com.cn"
    ) == "https://grsai.dakka.com.cn/v1"
    with pytest.raises(ValueError, match="HTTPS"):
        normalize_grsai_base_url("http://unsafe.example/v1")


def test_grsai_gateway_accepts_documented_full_chat_endpoint():
    captured = {}

    def opener(request, *, timeout):
        captured["url"] = request.full_url
        return FakeResponse([sse("[DONE]")])

    gateway = GrsAiGateway(
        "key",
        base_url="https://grsai.dakka.com.cn/v1/chat/completions",
        opener=opener,
    )
    list(gateway.stream_chat("ignored", []))

    assert captured["url"] == (
        "https://grsai.dakka.com.cn/v1/chat/completions"
    )


def test_grsai_gateway_exposes_http_error_details():
    body = BytesIO(
        json.dumps(
            {"error": {"message": "bad key", "code": "invalid_api_key"}}
        ).encode("utf-8")
    )

    def opener(request, *, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized", None, body)

    gateway = GrsAiGateway("key", opener=opener)
    with pytest.raises(GrsAiHttpError) as caught:
        list(gateway.stream_chat("ignored", []))

    assert caught.value.status_code == 401
    assert caught.value.error_code == "invalid_api_key"
    assert "bad key" in str(caught.value)


def test_grsai_gateway_allows_short_director_timeout_budget():
    captured = {}

    def opener(_request, *, timeout):
        captured["timeout"] = timeout
        return FakeResponse([sse("[DONE]")])

    list(
        GrsAiGateway("key", opener=opener, timeout=3).stream_chat(
            "ignored", []
        )
    )

    assert captured["timeout"] == 3
