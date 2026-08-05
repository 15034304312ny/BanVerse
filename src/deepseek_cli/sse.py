"""OpenAI 兼容 SSE 流解析与 HTTP 错误包装的共享实现。

``DeepSeekHttpGateway`` 与 ``GrsAiGateway`` 各自消费一个 OpenAI 兼容的
``/chat/completions`` SSE 流，解析逻辑与 HTTP 错误提取逻辑完全一致；
这里把它们收敛成单一实现，避免两份逐字相同的代码漂移。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.error import HTTPError

DONE_MARKER = "[DONE]"
MAX_ERROR_BODY_BYTES = 64 * 1024


def iter_sse_data(response: Any) -> Iterator[str]:
    """迭代一个 HTTP 响应体，产出每条 ``data:`` 事件的内容。

    忽略非 ``data:`` 的行与空事件；收到 ``[DONE]`` 后停止。
    调用方需自行关闭 ``response``（通常经由 ``with`` 语句）。
    """

    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == DONE_MARKER:
            if data == DONE_MARKER:
                break
            continue
        yield data


def parse_stream_delta(data: str) -> dict[str, Any] | None:
    """解析单条 SSE 数据，返回首个 choice 的 delta 对象；无效则返回 None。"""

    if not data:
        return None
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


def parse_http_error_body(
    exc: HTTPError,
    *,
    default_message: str,
    limit: int = MAX_ERROR_BODY_BYTES,
) -> tuple[str, str]:
    """从 HTTPError 响应体提取 (message, error_code)。

    优先读取 ``{"error": {"message": ..., "code": ...}}``；兼容顶层
    ``message``/``code`` 以及非 JSON 明文响应体。解析失败时返回默认消息。
    """

    try:
        detail = exc.read(limit).decode("utf-8", errors="replace")
    except (AttributeError, OSError):
        detail = ""
    if not detail.strip():
        return default_message, ""
    try:
        payload = json.loads(detail)
    except (TypeError, ValueError):
        return detail.strip()[:500], ""
    if not isinstance(payload, dict):
        return default_message, ""
    error = payload.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or default_message).strip()[:500]
        error_code = str(error.get("code") or "").strip()
        return message, error_code
    message = str(payload.get("message") or default_message).strip()[:500]
    error_code = str(payload.get("code") or "").strip()
    return message, error_code
