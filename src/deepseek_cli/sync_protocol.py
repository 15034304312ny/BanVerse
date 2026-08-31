"""BanVerse 双端同步协议的共享常量与输入校验。"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

SYNC_PROTOCOL_VERSION = 2
SYNC_ENTITY_TYPES = frozenset(
    {"character", "conversation", "turn", "memory"}
)
SYNC_OPERATIONS = frozenset({"upsert", "delete"})
SYNC_MEDIA_FIELDS = {
    "character": frozenset({"avatar_path"}),
    "conversation": frozenset({"avatar_override_path"}),
    "turn": frozenset({"user_image_path", "assistant_image_path"}),
    "memory": frozenset(),
}
MAX_SYNC_EVENTS = 200
MAX_SYNC_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_SYNC_MEDIA_BYTES = 50 * 1024 * 1024
MAX_SYNC_PAIRING_CHARS = 4_096
MIN_SYNC_USERNAME_CHARS = 3
MAX_SYNC_USERNAME_CHARS = 32
MIN_SYNC_PASSWORD_CHARS = 8
MAX_SYNC_PASSWORD_CHARS = 128
DEFAULT_SYNC_URL = "https://47.102.121.29"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_ENTITY_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def normalize_sync_url(value: str) -> str:
    """规范同步服务地址；客户端只接受 HTTP(S) 且不允许内嵌凭据。"""

    candidate = value.strip().rstrip("/")
    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("同步服务地址必须是有效的 http:// 或 https:// URL。")
    if parts.username or parts.password:
        raise ValueError("同步服务地址不能包含用户名或密码。")
    if parts.query or parts.fragment:
        raise ValueError("同步服务地址不能包含查询参数或片段。")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def validate_identifier(value: str, label: str) -> str:
    candidate = value.strip()
    if not _IDENTIFIER.fullmatch(candidate):
        raise ValueError(f"{label} 格式无效。")
    return candidate


def normalize_sync_username(value: str) -> str:
    """规范登录名，允许中英文、数字以及 ``._-``，并拒绝易混淆空白。"""

    candidate = unicodedata.normalize("NFKC", str(value)).strip()
    if not MIN_SYNC_USERNAME_CHARS <= len(candidate) <= MAX_SYNC_USERNAME_CHARS:
        raise ValueError(
            f"用户名长度需为 {MIN_SYNC_USERNAME_CHARS}–{MAX_SYNC_USERNAME_CHARS} 个字符。"
        )
    if not candidate[0].isalnum() or not candidate[-1].isalnum():
        raise ValueError("用户名必须以中文、英文字母或数字开头和结尾。")
    if any(not (character.isalnum() or character in "._-") for character in candidate):
        raise ValueError("用户名只能包含中文、英文字母、数字、点、下划线和短横线。")
    return candidate


def sync_username_key(value: str) -> str:
    """返回用于唯一索引和登录匹配的大小写不敏感用户名。"""

    return normalize_sync_username(value).casefold()


def validate_sync_password(value: str) -> str:
    """限制密码长度和输入规模；密码原文不会被规范化或裁剪。"""

    password = str(value)
    if not MIN_SYNC_PASSWORD_CHARS <= len(password) <= MAX_SYNC_PASSWORD_CHARS:
        raise ValueError(
            f"密码长度需为 {MIN_SYNC_PASSWORD_CHARS}–{MAX_SYNC_PASSWORD_CHARS} 个字符。"
        )
    if not password.strip():
        raise ValueError("密码不能只包含空白字符。")
    if len(password.encode("utf-8")) > 512:
        raise ValueError("密码内容过长。")
    return password


def validate_entity_id(value: str) -> str:
    candidate = value.strip()
    if not _ENTITY_IDENTIFIER.fullmatch(candidate):
        raise ValueError("同步实体 ID 格式无效。")
    return candidate


def validate_sha256(value: str) -> str:
    candidate = value.strip().lower()
    if not _SHA256.fullmatch(candidate):
        raise ValueError("媒体 SHA-256 格式无效。")
    return candidate


def bearer_credential(account_id: str, token: str) -> str:
    account = validate_identifier(account_id, "同步账户 ID")
    secret = validate_identifier(token, "同步令牌")
    return f"{account}.{secret}"


def split_bearer_credential(value: str) -> tuple[str, str]:
    try:
        account_id, token = value.strip().split(".", 1)
    except ValueError as exc:
        raise ValueError("同步凭据格式无效。") from exc
    return (
        validate_identifier(account_id, "同步账户 ID"),
        validate_identifier(token, "同步令牌"),
    )


def parse_sync_pairing(value: str) -> dict[str, str]:
    """校验从另一台设备复制的配对 JSON，不接受额外可执行配置。"""

    raw = value.strip()
    if not raw:
        raise ValueError("剪贴板中没有同步配对信息。")
    if len(raw) > MAX_SYNC_PAIRING_CHARS:
        raise ValueError("同步配对信息过长。")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("同步配对信息不是有效的 JSON。") from exc
    if not isinstance(payload, dict):
        raise ValueError("同步配对信息必须是 JSON 对象。")
    server_url = normalize_sync_url(str(payload.get("server_url", "")))
    account_id = str(payload.get("account_id", ""))
    token = str(payload.get("token", ""))
    bearer_credential(account_id, token)
    result = {
        "server_url": server_url,
        "account_id": account_id.strip(),
        "token": token.strip(),
    }
    username = str(payload.get("username", "")).strip()
    if username:
        result["username"] = normalize_sync_username(username)
    return result


def validate_event(event: dict) -> dict:
    """返回经裁剪的协议事件；服务端和测试共用同一校验。"""

    if not isinstance(event, dict):
        raise ValueError("同步事件必须是对象。")
    entity_type = str(event.get("entity_type", "")).strip()
    operation = str(event.get("operation", "")).strip()
    if entity_type not in SYNC_ENTITY_TYPES:
        raise ValueError("同步实体类型无效。")
    if operation not in SYNC_OPERATIONS:
        raise ValueError("同步操作无效。")
    payload = event.get("payload", {})
    media = event.get("media", {})
    if not isinstance(payload, dict) or not isinstance(media, dict):
        raise ValueError("同步事件 payload/media 必须是对象。")
    entity_id = validate_entity_id(str(event.get("entity_id", "")))
    if operation == "upsert" and payload.get("id") != entity_id:
        raise ValueError("同步实体内容与实体 ID 不匹配。")
    if operation == "delete":
        payload = {}
        media = {}
    else:
        media = _validate_media(entity_type, media)
    try:
        base_revision = max(0, int(event.get("base_revision", 0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("同步事件 base_revision 无效。") from exc
    return {
        "event_id": validate_identifier(str(event.get("event_id", "")), "事件 ID"),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "operation": operation,
        "base_revision": base_revision,
        "updated_at": (
            str(event.get("updated_at", "")).strip()[:80] or utc_now()
        ),
        "payload": payload,
        "media": media,
    }


def _validate_media(entity_type: str, media: dict) -> dict:
    normalized: dict[str, dict] = {}
    allowed = SYNC_MEDIA_FIELDS[entity_type]
    if not set(media).issubset(allowed):
        raise ValueError("同步媒体字段无效。")
    for field, raw in media.items():
        if not isinstance(raw, dict):
            raise ValueError("同步媒体描述无效。")
        try:
            size = int(raw.get("bytes", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("同步媒体大小无效。") from exc
        if size <= 0 or size > MAX_SYNC_MEDIA_BYTES:
            raise ValueError("同步媒体大小无效。")
        filename = _safe_filename(str(raw.get("filename", "media.bin")))
        mime = str(raw.get("mime", "application/octet-stream"))[:128]
        normalized[field] = {
            "sha256": validate_sha256(str(raw.get("sha256", ""))),
            "bytes": size,
            "filename": filename,
            "mime": mime,
        }
    return normalized


def _safe_filename(value: str) -> str:
    """协议仅保留显示名；落盘时客户端仍按摘要重新命名。"""

    candidate = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return candidate[:180] or "media.bin"
