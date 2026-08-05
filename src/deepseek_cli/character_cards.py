"""Character Card V2 JSON 的校验、规范化与导入导出。"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SPEC = "chara_card_v2"
SPEC_VERSION = "2.0"
MAX_CARD_BYTES = 2 * 1024 * 1024
MAX_TEXT_LENGTH = 200_000
MAX_LIST_ITEMS = 1_000

_TEXT_FIELDS = (
    "name",
    "description",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "creator_notes",
    "system_prompt",
    "post_history_instructions",
    "creator",
    "character_version",
)
_LIST_FIELDS = ("alternate_greetings", "tags")


class CharacterCardError(ValueError):
    """角色卡格式不合法。"""


def empty_card(name: str = "新角色") -> dict[str, Any]:
    return {
        "spec": SPEC,
        "spec_version": SPEC_VERSION,
        "data": {
            "name": name,
            "description": "",
            "personality": "",
            "scenario": "",
            "first_mes": "",
            "mes_example": "",
            "creator_notes": "",
            "system_prompt": "",
            "post_history_instructions": "",
            "alternate_greetings": [],
            "tags": [],
            "creator": "",
            "character_version": "",
            "extensions": {},
        },
    }


def normalize_card(raw: Mapping[str, Any]) -> dict[str, Any]:
    """补齐 V2 必需字段，同时保留未知字段和扩展。"""

    if not isinstance(raw, Mapping):
        raise CharacterCardError("角色卡顶层必须是 JSON 对象。")
    spec = raw.get("spec")
    if spec not in {None, SPEC}:
        raise CharacterCardError("仅支持 Character Card V2。")
    version = str(raw.get("spec_version", SPEC_VERSION))
    if not version.startswith("2"):
        raise CharacterCardError("仅支持 Character Card V2。")
    source_data = raw.get("data")
    if not isinstance(source_data, Mapping):
        raise CharacterCardError("角色卡缺少 data 对象。")

    card = copy.deepcopy(dict(raw))
    card["spec"] = SPEC
    card["spec_version"] = SPEC_VERSION
    data = copy.deepcopy(dict(source_data))
    for field in _TEXT_FIELDS:
        value = data.get(field, "")
        if not isinstance(value, str):
            raise CharacterCardError(f"字段 {field} 必须是文本。")
        if len(value) > MAX_TEXT_LENGTH:
            raise CharacterCardError(f"字段 {field} 过长。")
        data[field] = value
    if not data["name"].strip():
        raise CharacterCardError("角色名称不能为空。")

    for field in _LIST_FIELDS:
        value = data.get(field, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise CharacterCardError(f"字段 {field} 必须是文本数组。")
        if len(value) > MAX_LIST_ITEMS:
            raise CharacterCardError(f"字段 {field} 项目过多。")
        data[field] = value

    extensions = data.get("extensions", {})
    if not isinstance(extensions, Mapping):
        raise CharacterCardError("字段 extensions 必须是对象。")
    data["extensions"] = copy.deepcopy(dict(extensions))
    if "character_book" in data:
        _validate_character_book(data["character_book"])
    card["data"] = data
    _check_depth(card)
    return card


def parse_card_json(text: str) -> dict[str, Any]:
    if len(text.encode("utf-8")) > MAX_CARD_BYTES:
        raise CharacterCardError("角色卡文件超过 2 MB。")
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CharacterCardError("角色卡不是有效的 UTF-8 JSON。") from exc
    return normalize_card(raw)


def load_card(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if source.suffix.lower() != ".json":
        raise CharacterCardError("首版仅支持 Character Card V2 JSON 文件。")
    if source.stat().st_size > MAX_CARD_BYTES:
        raise CharacterCardError("角色卡文件超过 2 MB。")
    try:
        return parse_card_json(source.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise CharacterCardError("角色卡必须使用 UTF-8 编码。") from exc


def dump_card(card: Mapping[str, Any]) -> str:
    normalized = normalize_card(card)
    return json.dumps(normalized, ensure_ascii=False, indent=2)


def save_card(path: str | Path, card: Mapping[str, Any]) -> None:
    Path(path).write_text(dump_card(card), encoding="utf-8")


def _validate_character_book(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise CharacterCardError("character_book 必须是对象。")
    entries = value.get("entries", [])
    if not isinstance(entries, list) or len(entries) > MAX_LIST_ITEMS:
        raise CharacterCardError("character_book.entries 必须是数组。")
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise CharacterCardError("世界书条目必须是对象。")
        keys = entry.get("keys", [])
        content = entry.get("content", "")
        if not isinstance(keys, list) or not all(
            isinstance(key, str) for key in keys
        ):
            raise CharacterCardError("世界书条目的 keys 必须是文本数组。")
        if not isinstance(content, str) or len(content) > MAX_TEXT_LENGTH:
            raise CharacterCardError("世界书条目的 content 不合法。")


def _check_depth(value: Any, depth: int = 0) -> None:
    if depth > 30:
        raise CharacterCardError("角色卡 JSON 嵌套过深。")
    if isinstance(value, Mapping):
        for child in value.values():
            _check_depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _check_depth(child, depth + 1)
