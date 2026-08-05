"""无第三方 SDK 的 DeepSeek 流式 HTTP 网关。

模块名和 ``AnthropicDeepSeekGateway`` 别名为旧调用方保留；实际请求使用
DeepSeek 官方 OpenAI 兼容 ``/chat/completions`` 接口，便于部署到 Android。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .branding import USER_AGENT
from .gateway import Message, StreamDelta
from .model_catalog import MODEL_CHAT, resolve_provider_model

DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TIMEOUT_SECONDS = 180


class DeepSeekHttpError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class DeepSeekHttpGateway:
    """通过 Python 标准库消费 DeepSeek SSE 流。"""

    def __init__(
        self,
        api_key: str,
        *,
        opener: Callable[..., Any] = urlopen,
        endpoint: str = DEEPSEEK_CHAT_URL,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        value = api_key.strip()
        if not value:
            raise ValueError("DeepSeek API Key 不能为空")
        self._api_key = value
        self._opener = opener
        self._endpoint = endpoint
        self._timeout = max(10, int(timeout))

    def stream_chat(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        system_prompt: str = "",
        temperature: float | None = None,
    ) -> Iterable[StreamDelta]:
        provider_model = resolve_provider_model(model)
        payload_messages: list[dict[str, str]] = []
        if system_prompt.strip():
            payload_messages.append(
                {"role": "system", "content": system_prompt.strip()}
            )
        payload_messages.extend(
            {"role": message.role, "content": message.content}
            for message in messages
        )
        payload: dict[str, Any] = {
            "model": provider_model,
            "messages": payload_messages,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "stream": True,
            "thinking": {
                "type": (
                    "disabled" if provider_model == MODEL_CHAT else "enabled"
                )
            },
        }
        if temperature is not None:
            payload["temperature"] = max(
                0.0, min(float(temperature), 2.0)
            )
        request = Request(
            self._endpoint,
            data=json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        if data == "[DONE]":
                            break
                        continue
                    delta = self._parse_delta(data)
                    if delta is None:
                        continue
                    reasoning = delta.get("reasoning_content")
                    content = delta.get("content")
                    if isinstance(reasoning, str) and reasoning:
                        yield StreamDelta(reasoning_content=reasoning)
                    if isinstance(content, str) and content:
                        yield StreamDelta(content=content)
        except HTTPError as exc:
            raise self._http_error(exc) from exc

    @staticmethod
    def _parse_delta(data: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(data)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        choice = choices[0]
        if not isinstance(choice, dict):
            return None
        delta = choice.get("delta")
        return delta if isinstance(delta, dict) else None

    @staticmethod
    def _http_error(exc: HTTPError) -> DeepSeekHttpError:
        message = f"DeepSeek HTTP {exc.code}"
        error_code = ""
        try:
            raw = exc.read(64 * 1024).decode("utf-8", errors="replace")
            payload = json.loads(raw)
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                detail = str(error.get("message", "") or "").strip()
                error_code = str(error.get("code", "") or "").strip()
                if detail:
                    message = detail[:500]
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        return DeepSeekHttpError(
            message,
            status_code=exc.code,
            error_code=error_code,
        )


# 兼容旧模块公开名称，现有 CLI、桌面入口和第三方调用无需迁移。
AnthropicDeepSeekGateway = DeepSeekHttpGateway
