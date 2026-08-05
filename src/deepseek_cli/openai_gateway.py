"""基于 OpenAI Python SDK 的 DeepSeek 网关适配器。"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .gateway import Message, StreamDelta

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class OpenAIDeepSeekGateway:
    """将 OpenAI 兼容 SDK 响应转换成应用内部的流式增量。"""

    def __init__(self, api_key: str, client: Any | None = None) -> None:
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        self._client = client

    def stream_chat(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        system_prompt: str = "",
        temperature: float | None = None,
    ) -> Iterable[StreamDelta]:
        payload = [
            {"role": message.role, "content": message.content}
            for message in messages
        ]
        if system_prompt.strip():
            payload.insert(0, {"role": "system", "content": system_prompt})
        request = dict(
            model=model,
            messages=payload,
            stream=True,
        )
        if temperature is not None:
            request["temperature"] = max(0.0, min(float(temperature), 2.0))
        stream = self._client.chat.completions.create(**request)
        for chunk in stream:
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None) or ""
            reasoning_content = (
                getattr(delta, "reasoning_content", None) or ""
            )
            if content or reasoning_content:
                yield StreamDelta(
                    content=content,
                    reasoning_content=reasoning_content,
                )
