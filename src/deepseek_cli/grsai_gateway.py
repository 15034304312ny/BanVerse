"""GRS AI OpenAI-compatible streaming text gateway."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .branding import USER_AGENT
from .gateway import Message, StreamDelta
from .sse import iter_sse_data, parse_http_error_body, parse_stream_delta

DEFAULT_GRSAI_API_BASE_URL = "https://grsai.dakka.com.cn/v1"
DEFAULT_GRSAI_TEXT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_TIMEOUT_SECONDS = 180


class GrsAiHttpError(RuntimeError):
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


def normalize_grsai_base_url(value: str) -> str:
    base_url = (value or DEFAULT_GRSAI_API_BASE_URL).strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("GRS AI API 地址必须是有效的 HTTPS 地址")
    if parsed.username or parsed.password:
        raise ValueError("GRS AI API 地址不能包含用户名或密码")

    # The settings field historically accepted both an API base and a full
    # endpoint.  Keep both forms working, otherwise a pasted documented URL
    # such as ``.../v1/chat/completions`` becomes
    # ``.../v1/chat/completions/chat/completions``.
    path = parsed.path.rstrip("/")
    for endpoint_suffix in (
        "/chat/completions",
        "/images/generations",
        "/draw/nano-banana",
        "/draw/completions",
        "/draw/result",
        "/api/generate",
    ):
        if path.endswith(endpoint_suffix):
            path = path[: -len(endpoint_suffix)].rstrip("/")
            break
    if not path:
        path = "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class GrsAiGateway:
    """Consume GRS AI's documented OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_GRSAI_API_BASE_URL,
        model: str = DEFAULT_GRSAI_TEXT_MODEL,
        opener: Callable[..., Any] = urlopen,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key.strip()
        if not self._api_key:
            raise ValueError("GRS AI 文本 API Key 不能为空")
        self._endpoint = f"{normalize_grsai_base_url(base_url)}/chat/completions"
        self._model = model.strip() or DEFAULT_GRSAI_TEXT_MODEL
        self._opener = opener
        self._timeout = max(10, int(timeout))

    def stream_chat(
        self,
        _model: str,
        messages: Sequence[Message],
        *,
        system_prompt: str = "",
        temperature: float | None = None,
    ) -> Iterable[StreamDelta]:
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
            "model": self._model,
            "messages": payload_messages,
            "stream": True,
        }
        if temperature is not None:
            payload["temperature"] = max(0.0, min(float(temperature), 2.0))
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
                for data in iter_sse_data(response):
                    delta = parse_stream_delta(data)
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
        except URLError as exc:
            raise GrsAiHttpError(f"GRS AI 网络错误: {exc.reason}") from exc

    @staticmethod
    def _http_error(exc: HTTPError) -> GrsAiHttpError:
        message, error_code = parse_http_error_body(
            exc, default_message=f"GRS AI HTTP {exc.code}"
        )
        return GrsAiHttpError(
            message,
            status_code=exc.code,
            error_code=error_code,
        )
