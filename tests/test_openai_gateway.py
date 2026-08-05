from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from deepseek_cli.gateway import Message, StreamDelta
from deepseek_cli.openai_gateway import (
    DEEPSEEK_BASE_URL,
    OpenAIDeepSeekGateway,
)


class FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return iter(self._chunks)


def chunk(
    *, content=None, reasoning_content=None, choices=True, delta=True
):
    if not choices:
        return SimpleNamespace(choices=[])
    if not delta:
        return SimpleNamespace(choices=[SimpleNamespace(delta=None)])
    payload = SimpleNamespace(
        content=content, reasoning_content=reasoning_content
    )
    return SimpleNamespace(choices=[SimpleNamespace(delta=payload)])


def test_adapter_constructs_sdk_with_fixed_base_url():
    captured = {}
    fake_openai = ModuleType("openai")

    def fake_openai_constructor(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    fake_openai.OpenAI = fake_openai_constructor
    with patch.dict(sys.modules, {"openai": fake_openai}):
        OpenAIDeepSeekGateway("only-this-key")

    assert captured == {
        "api_key": "only-this-key",
        "base_url": DEEPSEEK_BASE_URL,
    }


def test_adapter_maps_request_and_stream_deltas():
    completions = FakeCompletions(
        [
            chunk(choices=False),
            chunk(delta=False),
            chunk(),
            chunk(reasoning_content="想"),
            chunk(content="答案"),
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    gateway = OpenAIDeepSeekGateway("unused", client=client)

    result = list(
        gateway.stream_chat(
            "deepseek-reasoner",
            [Message("user", "你好")],
        )
    )

    assert completions.kwargs == {
        "model": "deepseek-reasoner",
        "messages": [{"role": "user", "content": "你好"}],
        "stream": True,
    }
    assert result == [
        StreamDelta(reasoning_content="想"),
        StreamDelta(content="答案"),
    ]


def test_adapter_passes_system_prompt_and_temperature():
    completions = FakeCompletions([])
    gateway = OpenAIDeepSeekGateway(
        "unused",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        ),
    )

    list(
        gateway.stream_chat(
            "deepseek-chat",
            [Message("user", "你好")],
            system_prompt="扮演角色",
            temperature=1.3,
        )
    )

    assert completions.kwargs["messages"][0] == {
        "role": "system",
        "content": "扮演角色",
    }
    assert completions.kwargs["temperature"] == 1.3
