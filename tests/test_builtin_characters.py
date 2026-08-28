from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from importlib import resources
from pathlib import Path

from PySide6.QtGui import QImage

from deepseek_cli.character_cards import dump_card
from deepseek_cli.desktop.builtin_characters import (
    BUILTIN_CHARACTER_IDS,
    BuiltinCharacterManager,
    index_tts2_preset_migration_key,
    load_builtin_catalog,
    seed_setting_key,
    stable_character_id,
)
from deepseek_cli.desktop.data.database import Database
from deepseek_cli.desktop.data.repositories import (
    CharacterRepository,
    ChatRepository,
    SettingsRepository,
)
from deepseek_cli.tts import read_tts_profile


def manager(tmp_path):
    database = Database(tmp_path / "chat.db")
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    builtins = BuiltinCharacterManager(
        database,
        characters,
        settings,
        app_data_root=tmp_path / "appdata",
    )
    return database, characters, settings, builtins


def test_builtin_catalog_has_six_complete_cards_and_square_avatars(qapp):
    catalog = load_builtin_catalog()

    assert tuple(item.builtin_id for item in catalog) == BUILTIN_CHARACTER_IDS
    assert len({item.card["data"]["name"] for item in catalog}) == 6
    assert sum("女性" in item.card["data"]["tags"] for item in catalog) == 5
    assert sum("男性" in item.card["data"]["tags"] for item in catalog) == 1
    for item in catalog:
        data = item.card["data"]
        assert item.character_id == f"builtin:{item.builtin_id}"
        assert data["extensions"]["deepseek_chat"]["builtin_id"] == item.builtin_id
        assert len(data["alternate_greetings"]) >= 2
        blocks = [
            block
            for block in data["mes_example"].split("<START>")
            if block.strip()
        ]
        assert len(blocks) >= 4
        assert all(block.count("{{user}}:") >= 2 for block in blocks)
        assert data["character_version"] == "1.2"
        assert data["character_book"]["entries"]
        assert read_tts_profile(item.card).voice.startswith("zh-CN-")
        assert read_tts_profile(item.card).index_tts2_preset.startswith(
            "BanVerse_"
        )
        assert item.avatar_png.startswith(b"\x89PNG\r\n\x1a\n")


def test_lin_xiaoman_is_an_adult_boundary_aware_daily_sharing_character(qapp):
    definition = next(
        item for item in load_builtin_catalog() if item.builtin_id == "lin_xiaoman"
    )
    data = definition.card["data"]

    assert data["name"] == "林小满"
    assert {"现代都市", "妹妹系", "日常", "活泼"} <= set(data["tags"])
    assert "22岁" in data["description"]
    assert "主动找" in data["personality"] and "分享通勤" in data["personality"]
    assert "不催回复" in data["system_prompt"]
    assert "停止主动联系" in data["post_history_instructions"]


def test_flat_app_icon_is_packaged_and_decodable(qapp):
    icon_bytes = (
        resources.files("deepseek_cli.desktop")
        .joinpath("resources", "app_icon.png")
        .read_bytes()
    )
    image = QImage.fromData(icon_bytes, "PNG")

    assert not image.isNull()
    assert image.width() == image.height() == 512
    assert image.hasAlphaChannel()
    ico = (Path(__file__).parents[1] / "packaging" / "app_icon.ico").read_bytes()
    assert ico[:4] == b"\x00\x00\x01\x00"
    assert int.from_bytes(ico[4:6], "little") >= 6


def test_first_seed_is_idempotent_and_installs_appdata_avatars(tmp_path, qapp):
    database, characters, settings, builtins = manager(tmp_path)

    first = builtins.seed_on_startup()
    snapshots = {
        character.id: character for character in characters.list()
    }
    second = builtins.seed_on_startup()

    assert set(first.created_ids) == {
        stable_character_id(item) for item in BUILTIN_CHARACTER_IDS
    }
    assert not second.created_ids
    assert len(characters.list()) == 6
    for builtin_id in BUILTIN_CHARACTER_IDS:
        character = characters.get(stable_character_id(builtin_id))
        assert character is not None
        avatar = Path(character.avatar_path)
        assert avatar.is_file()
        assert tmp_path / "appdata" in avatar.parents
        assert settings.contains(seed_setting_key(builtin_id))
        assert character.created_at == snapshots[character.id].created_at
        assert character.updated_at == snapshots[character.id].updated_at
    database.close()


def test_existing_install_seeds_only_the_new_builtin_character(tmp_path, qapp):
    database = Database(tmp_path / "chat.db")
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    previous_catalog = load_builtin_catalog()[:-1]
    previous_manager = BuiltinCharacterManager(
        database,
        characters,
        settings,
        app_data_root=tmp_path / "appdata",
        catalog_loader=lambda: previous_catalog,
    )
    previous_manager.seed_on_startup()
    existing_ids = {character.id for character in characters.list()}

    upgraded_manager = BuiltinCharacterManager(
        database,
        characters,
        settings,
        app_data_root=tmp_path / "appdata",
    )
    result = upgraded_manager.seed_on_startup()

    assert result.created_ids == (stable_character_id("lin_xiaoman"),)
    assert set(result.existing_ids) == existing_ids
    assert len(characters.list()) == 6
    assert settings.contains(seed_setting_key("lin_xiaoman"))
    database.close()


def test_startup_upgrades_only_untouched_builtin_card(tmp_path, qapp):
    database = Database(tmp_path / "chat.db")
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    latest_catalog = load_builtin_catalog()
    target = latest_catalog[0]
    old_card = json.loads(dump_card(target.card))
    old_card["data"]["system_prompt"] = "旧版内置提示"
    old_card["data"]["character_version"] = "1.0"
    old_fingerprint = hashlib.sha256(
        dump_card(old_card).encode("utf-8") + target.avatar_png
    ).hexdigest()
    old_target = replace(
        target,
        card=old_card,
        fingerprint=old_fingerprint,
    )
    old_catalog = (old_target, *latest_catalog[1:])
    BuiltinCharacterManager(
        database,
        characters,
        settings,
        app_data_root=tmp_path / "appdata",
        catalog_loader=lambda: old_catalog,
    ).seed_on_startup()

    BuiltinCharacterManager(
        database,
        characters,
        settings,
        app_data_root=tmp_path / "appdata",
        catalog_loader=lambda: latest_catalog,
    ).seed_on_startup()

    upgraded = characters.get(target.character_id)
    assert upgraded.card["data"]["system_prompt"] != "旧版内置提示"
    assert upgraded.card["data"]["character_version"] == "1.2"
    assert settings.get(seed_setting_key(target.builtin_id)) == (
        target.fingerprint
    )
    database.close()


def test_startup_adds_index_preset_once_without_overwriting_user_edits(
    tmp_path, qapp
):
    database = Database(tmp_path / "chat.db")
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    latest_catalog = load_builtin_catalog()
    old_catalog = []
    for definition in latest_catalog:
        old_card = json.loads(dump_card(definition.card))
        tts = old_card["data"]["extensions"]["deepseek_chat"]["tts"]
        tts.pop("index_tts2_preset")
        tts["schema_version"] = 1
        old_fingerprint = hashlib.sha256(
            dump_card(old_card).encode("utf-8") + definition.avatar_png
        ).hexdigest()
        old_catalog.append(
            replace(
                definition,
                card=old_card,
                fingerprint=old_fingerprint,
            )
        )

    old_manager = BuiltinCharacterManager(
        database,
        characters,
        settings,
        app_data_root=tmp_path / "appdata",
        catalog_loader=lambda: tuple(old_catalog),
    )
    old_manager.seed_on_startup()
    target = latest_catalog[0]
    edited = characters.get(target.character_id)
    edited_card = edited.card
    edited_card["data"]["name"] = "用户自定义姓名"
    edited_card["data"]["extensions"]["deepseek_chat"]["tts"]["pitch"] = 17
    characters.update(target.character_id, edited_card, "custom-avatar.png")
    settings.set(index_tts2_preset_migration_key(target.builtin_id), "")
    database.connection.execute(
        "DELETE FROM settings WHERE key = ?",
        (index_tts2_preset_migration_key(target.builtin_id),),
    )
    database.connection.commit()

    latest_manager = BuiltinCharacterManager(
        database,
        characters,
        settings,
        app_data_root=tmp_path / "appdata",
        catalog_loader=lambda: latest_catalog,
    )
    latest_manager.seed_on_startup()

    migrated = characters.get(target.character_id)
    profile = read_tts_profile(migrated.card)
    assert migrated.name == "用户自定义姓名"
    assert migrated.avatar_path == "custom-avatar.png"
    assert profile.pitch == 17
    assert profile.index_tts2_preset == read_tts_profile(
        target.card
    ).index_tts2_preset
    assert settings.get(index_tts2_preset_migration_key(target.builtin_id)) == "1"

    migrated_card = migrated.card
    migrated_card["data"]["extensions"]["deepseek_chat"]["tts"][
        "index_tts2_preset"
    ] = ""
    characters.update(target.character_id, migrated_card, migrated.avatar_path)
    latest_manager.seed_on_startup()
    assert read_tts_profile(
        characters.get(target.character_id).card
    ).index_tts2_preset == ""
    database.close()


def test_edit_and_delete_survive_startup_and_restore_only_missing(tmp_path, qapp):
    database, characters, _settings, builtins = manager(tmp_path)
    builtins.seed_on_startup()
    edited_id = stable_character_id("xie_zhaoning")
    deleted_id = stable_character_id("bai_tu")
    edited = characters.get(edited_id)
    card = edited.card
    card["data"]["name"] = "用户改名"
    characters.update(edited_id, card, "custom.png")
    characters.delete(deleted_id)

    builtins.seed_on_startup()
    assert characters.get(edited_id).name == "用户改名"
    assert characters.get(edited_id).avatar_path == "custom.png"
    assert characters.get(deleted_id) is None

    result = builtins.restore_missing()
    assert result.created_ids == (deleted_id,)
    assert characters.get(deleted_id).name == "白荼"
    assert characters.get(edited_id).name == "用户改名"
    assert not builtins.restore_missing().created_ids
    database.close()


def test_restore_does_not_rebind_detached_conversation(tmp_path, qapp):
    database, characters, _settings, builtins = manager(tmp_path)
    chats = ChatRepository(database)
    builtins.seed_on_startup()
    character_id = stable_character_id("zhou_jiming")
    conversation = chats.create_conversation(
        title="救援",
        character_id=character_id,
        opening_message="无线电开场",
    )

    characters.delete(character_id)
    assert chats.get_conversation(conversation.id).character_id is None
    builtins.restore_missing()

    restored = chats.get_conversation(conversation.id)
    assert restored.character_id is None
    assert restored.opening_message == "无线电开场"
    database.close()


def test_duplicate_clears_builtin_identity_but_keeps_tts(tmp_path, qapp):
    database, characters, _settings, builtins = manager(tmp_path)
    builtins.seed_on_startup()
    source = characters.get(stable_character_id("ruan_xingyao"))

    duplicate = characters.duplicate(source.id)

    app = duplicate.card["data"]["extensions"]["deepseek_chat"]
    assert duplicate.id != source.id and not duplicate.id.startswith("builtin:")
    assert "builtin_id" not in app
    assert app["tts"] == source.card["data"]["extensions"]["deepseek_chat"]["tts"]
    database.close()


def test_transaction_rolls_back_all_database_changes(tmp_path, qapp):
    database, characters, settings, builtins = manager(tmp_path)
    original = characters.create_with_id
    calls = 0

    def fail_on_third(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("forced failure")
        return original(*args, **kwargs)

    characters.create_with_id = fail_on_third
    try:
        try:
            builtins.seed_on_startup()
        except RuntimeError as exc:
            assert str(exc) == "forced failure"
        else:
            raise AssertionError("seed should fail")
    finally:
        characters.create_with_id = original

    assert characters.list() == []
    assert not any(settings.contains(seed_setting_key(item)) for item in BUILTIN_CHARACTER_IDS)
    database.close()
