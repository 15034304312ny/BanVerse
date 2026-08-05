"""打包内置角色目录、首次写入与手动恢复。"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImageReader

from ..character_cards import CharacterCardError, dump_card, parse_card_json
from ..tts import read_tts_profile
from .assets import AvatarError, install_builtin_avatar
from .data.database import Database
from .data.repositories import CharacterRepository, SettingsRepository

BUILTIN_CHARACTER_IDS = (
    "xie_zhaoning",
    "bai_tu",
    "ruan_xingyao",
    "luo_misha",
    "zhou_jiming",
    "lin_xiaoman",
)
BUILTIN_ID_PREFIX = "builtin:"
SEED_SETTING_PREFIX = "builtin_character.seeded."
INDEXTTS2_PRESET_MIGRATION_PREFIX = (
    "builtin_character.migration.indextts2_preset.v1."
)
_MALE_VOICES = {
    "zh-CN-YunxiNeural",
    "zh-CN-YunjianNeural",
    "zh-CN-YunyangNeural",
}


class BuiltinCharacterError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BuiltinCharacterDefinition:
    builtin_id: str
    character_id: str
    card: dict
    avatar_png: bytes
    fingerprint: str


@dataclass(frozen=True, slots=True)
class BuiltinOperationResult:
    created_ids: tuple[str, ...]
    existing_ids: tuple[str, ...]


def stable_character_id(builtin_id: str) -> str:
    if not re.fullmatch(r"[a-z0-9_]+", builtin_id):
        raise BuiltinCharacterError("内置角色标识不合法。")
    return f"{BUILTIN_ID_PREFIX}{builtin_id}"


def seed_setting_key(builtin_id: str) -> str:
    stable_character_id(builtin_id)
    return f"{SEED_SETTING_PREFIX}{builtin_id}"


def index_tts2_preset_migration_key(builtin_id: str) -> str:
    stable_character_id(builtin_id)
    return f"{INDEXTTS2_PRESET_MIGRATION_PREFIX}{builtin_id}"


def load_builtin_catalog() -> tuple[BuiltinCharacterDefinition, ...]:
    """从包资源读取并完整校验内置角色目录。"""

    root = resources.files("deepseek_cli.desktop").joinpath("resources")
    cards_root = root.joinpath("builtin_characters")
    avatars_root = root.joinpath("builtin_avatars")
    definitions = []
    names: set[str] = set()
    genders: list[str] = []

    try:
        for builtin_id in BUILTIN_CHARACTER_IDS:
            card_text = cards_root.joinpath(f"{builtin_id}.json").read_text(
                encoding="utf-8"
            )
            avatar_png = avatars_root.joinpath(f"{builtin_id}.png").read_bytes()
            card = parse_card_json(card_text)
            _validate_definition(builtin_id, card, avatar_png)
            name = card["data"]["name"]
            if name in names:
                raise BuiltinCharacterError("内置角色名称重复。")
            names.add(name)
            tags = card["data"]["tags"]
            genders.append("男性" if "男性" in tags else "女性" if "女性" in tags else "")
            fingerprint = hashlib.sha256(
                dump_card(card).encode("utf-8") + avatar_png
            ).hexdigest()
            definitions.append(
                BuiltinCharacterDefinition(
                    builtin_id,
                    stable_character_id(builtin_id),
                    card,
                    avatar_png,
                    fingerprint,
                )
            )
    except (FileNotFoundError, OSError, UnicodeError, CharacterCardError, AvatarError) as exc:
        raise BuiltinCharacterError("内置角色资源缺失或损坏。") from exc

    if len(definitions) != 6 or genders.count("女性") != 5 or genders.count("男性") != 1:
        raise BuiltinCharacterError("内置角色目录必须包含五位女性和一位男性。")
    return tuple(definitions)


def _validate_definition(builtin_id: str, card: dict, avatar_png: bytes) -> None:
    data = card["data"]
    app = data.get("extensions", {}).get("deepseek_chat", {})
    if not isinstance(app, dict) or app.get("builtin_id") != builtin_id:
        raise BuiltinCharacterError("内置角色标识与资源不一致。")
    for field in (
        "name",
        "description",
        "personality",
        "scenario",
        "first_mes",
        "mes_example",
        "system_prompt",
        "post_history_instructions",
    ):
        if not str(data.get(field, "")).strip():
            raise BuiltinCharacterError(f"内置角色字段 {field} 不能为空。")
    if len(data.get("alternate_greetings", [])) < 2:
        raise BuiltinCharacterError("内置角色至少需要两个备用开场白。")
    if not data.get("character_book", {}).get("entries"):
        raise BuiltinCharacterError("内置角色缺少 Character Book。")
    profile = read_tts_profile(card)
    if "男性" in data["tags"] and profile.voice not in _MALE_VOICES:
        raise BuiltinCharacterError("男性内置角色必须使用男性音色。")

    image_data = QByteArray(avatar_png)
    buffer = QBuffer(image_data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b"PNG")
    size = reader.size()
    if not size.isValid() or size.width() != 512 or size.height() != 512:
        raise BuiltinCharacterError("内置头像必须是 512×512 PNG。")
    if reader.read().isNull():
        raise BuiltinCharacterError("内置头像无法解码。")


class BuiltinCharacterManager:
    def __init__(
        self,
        database: Database,
        characters: CharacterRepository,
        settings: SettingsRepository,
        *,
        app_data_root: str | Path,
        catalog_loader: Callable[[], tuple[BuiltinCharacterDefinition, ...]] = load_builtin_catalog,
    ) -> None:
        self._database = database
        self._characters = characters
        self._settings = settings
        self._app_data_root = Path(app_data_root)
        self._catalog_loader = catalog_loader

    def seed_on_startup(self) -> BuiltinOperationResult:
        definitions, avatar_paths = self._prepare()
        created: list[str] = []
        existing: list[str] = []
        with self._database.transaction(immediate=True) as connection:
            for definition in definitions:
                marker = seed_setting_key(definition.builtin_id)
                if self._settings.contains(marker, connection=connection):
                    current = self._characters.get(definition.character_id)
                    previous_fingerprint = self._settings.get(marker)
                    if (
                        current is not None
                        and previous_fingerprint
                        and previous_fingerprint != definition.fingerprint
                        and self._fingerprint(current.card, current.avatar_path)
                        == previous_fingerprint
                    ):
                        self._characters.update(
                            definition.character_id,
                            definition.card,
                            avatar_paths[definition.builtin_id],
                            connection=connection,
                        )
                        self._settings.set(
                            marker,
                            definition.fingerprint,
                            connection=connection,
                        )
                        current = self._characters.get(definition.character_id)
                    self._migrate_index_tts2_preset(
                        definition,
                        current,
                        connection=connection,
                    )
                    existing.append(definition.character_id)
                    continue
                if self._characters.exists(definition.character_id, connection=connection):
                    existing.append(definition.character_id)
                    current = self._characters.get(definition.character_id)
                else:
                    self._characters.create_with_id(
                        definition.character_id,
                        definition.card,
                        avatar_paths[definition.builtin_id],
                        connection=connection,
                    )
                    created.append(definition.character_id)
                    current = self._characters.get(definition.character_id)
                self._settings.set(
                    marker, definition.fingerprint, connection=connection
                )
                self._migrate_index_tts2_preset(
                    definition,
                    current,
                    connection=connection,
                )
        return BuiltinOperationResult(tuple(created), tuple(existing))

    @staticmethod
    def _fingerprint(card: dict, avatar_path: str) -> str:
        try:
            avatar_png = Path(avatar_path).read_bytes()
        except OSError:
            return ""
        return hashlib.sha256(
            dump_card(card).encode("utf-8") + avatar_png
        ).hexdigest()

    def _migrate_index_tts2_preset(
        self,
        definition: BuiltinCharacterDefinition,
        current,
        *,
        connection,
    ) -> None:
        """一次性追加克隆预设，不覆盖用户已编辑的任何角色字段。"""

        migration_marker = index_tts2_preset_migration_key(
            definition.builtin_id
        )
        if self._settings.contains(migration_marker, connection=connection):
            return

        if current is not None:
            preset = read_tts_profile(definition.card).index_tts2_preset
            card = copy.deepcopy(current.card)
            app = (
                card.get("data", {})
                .get("extensions", {})
                .get("deepseek_chat")
            )
            if isinstance(app, dict) and preset:
                tts = app.get("tts")
                if not isinstance(tts, dict):
                    tts = {}
                    app["tts"] = tts
                if "index_tts2_preset" not in tts:
                    tts["index_tts2_preset"] = preset
                    self._characters.update(
                        definition.character_id,
                        card,
                        current.avatar_path,
                        connection=connection,
                    )

        self._settings.set(migration_marker, "1", connection=connection)

    def restore_missing(self) -> BuiltinOperationResult:
        definitions, avatar_paths = self._prepare()
        created: list[str] = []
        existing: list[str] = []
        with self._database.transaction(immediate=True) as connection:
            for definition in definitions:
                if self._characters.exists(definition.character_id, connection=connection):
                    existing.append(definition.character_id)
                    self._migrate_index_tts2_preset(
                        definition,
                        self._characters.get(definition.character_id),
                        connection=connection,
                    )
                    continue
                self._characters.create_with_id(
                    definition.character_id,
                    definition.card,
                    avatar_paths[definition.builtin_id],
                    connection=connection,
                )
                self._settings.set(
                    seed_setting_key(definition.builtin_id),
                    definition.fingerprint,
                    connection=connection,
                )
                self._settings.set(
                    index_tts2_preset_migration_key(definition.builtin_id),
                    "1",
                    connection=connection,
                )
                created.append(definition.character_id)
        return BuiltinOperationResult(tuple(created), tuple(existing))

    def _prepare(self):
        try:
            definitions = self._catalog_loader()
            avatar_paths = {
                definition.builtin_id: install_builtin_avatar(
                    definition.builtin_id,
                    definition.avatar_png,
                    app_data_root=self._app_data_root,
                )
                for definition in definitions
            }
        except (BuiltinCharacterError, AvatarError):
            raise
        except Exception as exc:
            raise BuiltinCharacterError("无法准备内置角色资源。") from exc
        return definitions, avatar_paths
