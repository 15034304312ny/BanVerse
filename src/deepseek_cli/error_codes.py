"""网关与服务异常的共享错误分类。

文本侧（``chat_service``）与图片/媒体侧（``desktop.workers``）各维护过
一套语义相同、前缀不同的分类规则。这里收敛为单一实现：

- ``classify_error`` 返回不带前缀的基础码；
- ``text_error_code`` / ``image_error_code`` 负责各自的前缀与 404/400 的
  语义映射（文本侧 404 表示端点无效，图片侧 404 表示模型不存在）。

错误码表见 ``desktop/ui/pages/chat_page.py`` 的 ``_ERROR_MESSAGES``。
"""

from __future__ import annotations

from typing import Any


def classify_error(exc: BaseException) -> str:
    """按异常内容返回基础语义码（authentication/timeout/.../service_error）。"""

    name = type(exc).__name__.lower()
    message = str(exc).lower()
    status_code = getattr(exc, "status_code", None)
    api_error_code = str(getattr(exc, "error_code", "") or "").lower()
    if (
        "insufficient_quota" in api_error_code
        or "billing" in api_error_code
        or "quota" in message
        or "billing" in message
        or (
            api_error_code == "failed_precondition"
            and "free tier" in message
        )
    ):
        return "quota"
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
        # 文本侧解释为端点无效，图片侧解释为模型不存在。
        return "not_found"
    if (
        "model_not_found" in api_error_code
        or "does not exist" in message
        or (status_code == 400 and "model" in api_error_code)
    ):
        return "model_unavailable"
    if status_code == 400 and "model" in message:
        return "model_unavailable"
    if status_code == 400:
        return "bad_request"
    return "service_error"


def text_error_code(exc: BaseException) -> str:
    """文本会话的错误码；保留历史名称（authentication/service_error 等）。"""

    base = classify_error(exc)
    return {
        "not_found": "text_endpoint_invalid",
        "model_unavailable": "text_model_unavailable",
        "bad_request": "text_bad_request",
    }.get(base, base)


def image_error_code(exc: BaseException) -> str:
    """图片/媒体能力的错误码；统一加 ``image_`` 前缀。"""

    base = classify_error(exc)
    if base == "not_found":
        return "image_model_unavailable"
    return f"image_{base}"


def describe_error(exc: BaseException) -> tuple[str, str, Any]:
    """返回异常的 (类型名, 消息, status_code)，供日志记录使用。"""

    return type(exc).__name__, str(exc), getattr(exc, "status_code", None)
