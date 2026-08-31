from __future__ import annotations

import hashlib
import json
from pathlib import Path

from deepseek_cli.character_cards import empty_card
from deepseek_cli.desktop.data.database import Database
from deepseek_cli.desktop.data.repositories import CharacterRepository, ChatRepository
from deepseek_cli.desktop.sync_client import SyncEngine, SyncRepository
from deepseek_cli.model_catalog import MODEL_CHAT
from deepseek_cli.sync_server import SyncServerStore


class StoreTransport:
    def __init__(self, store: SyncServerStore, account_id: str) -> None:
        self.store = store
        self.account_id = account_id

    def push(self, device_id: str, device_name: str, events: list[dict]) -> dict:
        return self.store.push(self.account_id, device_id, device_name, events)

    def pull(self, cursor: int, limit: int = 200) -> dict:
        return self.store.pull(self.account_id, cursor, limit)

    def upload_media(self, digest: str, content: bytes) -> None:
        self.store.put_media(self.account_id, digest, content)

    def download_media(self, digest: str) -> bytes:
        content = self.store.get_media(self.account_id, digest)
        if content is None:
            raise AssertionError(f"测试媒体不存在：{digest}")
        return content


def _engine(
    database: Database,
    store: SyncServerStore,
    account_id: str,
    device_id: str,
    media_root,
) -> SyncEngine:
    return SyncEngine(
        SyncRepository(database),
        StoreTransport(store, account_id),
        device_id=device_id,
        device_name=device_id,
        media_root=media_root,
    )


def test_two_devices_converge_text_character_and_media(tmp_path):
    store = SyncServerStore(tmp_path / "server.db", tmp_path / "server-media")
    account_id = store.create_account()["account_id"]
    first_db = Database(tmp_path / "first.db")
    second_db = Database(tmp_path / "second.db")
    image = tmp_path / "shared.png"
    image.write_bytes(b"original-image-bytes")

    characters = CharacterRepository(first_db)
    card = empty_card("同步角色")
    card["data"]["description"] = "会在电脑和手机之间保持一致。"
    character = characters.create(card, str(image), source_type="imported")
    chats = ChatRepository(first_db)
    conversation = chats.create_conversation(
        title="跨设备会话",
        character_id=character.id,
        opening_message="晚上好。",
    )
    turn = chats.create_turn(
        conversation.id,
        "给手机看看这张图",
        MODEL_CHAT,
        user_image_path=str(image),
    )
    segments = [
        {"kind": "dialogue", "text": "已经收到。", "image_path": ""},
        {"kind": "image", "text": "", "image_path": str(image)},
    ]
    chats.complete_turn(
        turn.id,
        "已经收到。",
        assistant_image_path=str(image),
        assistant_segments_json=json.dumps(segments, ensure_ascii=False),
    )

    first = _engine(first_db, store, account_id, "desktop-001", tmp_path / "pc")
    second = _engine(second_db, store, account_id, "android-001", tmp_path / "phone")
    first_result = first.sync_once()
    second_result = second.sync_once()

    assert first_result.pushed == 3
    assert second_result.pulled == 3
    remote_character = CharacterRepository(second_db).get(character.id)
    remote_conversation = ChatRepository(second_db).get_conversation(conversation.id)
    remote_turn = ChatRepository(second_db).list_turns(conversation.id)[0]
    assert remote_character is not None
    assert remote_character.name == "同步角色"
    assert remote_character.source_type == "imported"
    assert remote_conversation is not None
    assert remote_conversation.opening_message == "晚上好。"
    for synced_path in (
        remote_character.avatar_path,
        remote_turn.user_image_path,
        remote_turn.assistant_image_path,
    ):
        assert hashlib.sha256(Path(synced_path).read_bytes()).digest() == (
            hashlib.sha256(image.read_bytes()).digest()
        )
    restored_segments = json.loads(remote_turn.assistant_segments_json)
    assert restored_segments[1]["image_path"] == remote_turn.assistant_image_path

    first_db.close()
    second_db.close()


def test_two_devices_sync_memory_and_privacy_erasing_tombstone(tmp_path):
    store = SyncServerStore(tmp_path / "server.db", tmp_path / "server-media")
    account_id = store.create_account()["account_id"]
    first_db = Database(tmp_path / "first.db")
    second_db = Database(tmp_path / "second.db")
    first_chats = ChatRepository(first_db)
    conversation = first_chats.create_conversation(title="记忆同步")
    memory = first_chats.create_memory(
        conversation.id,
        "preference_boundary",
        "不要催促回复",
        source_type="user_explicit",
    )
    first = _engine(first_db, store, account_id, "desktop-001", tmp_path / "pc")
    second = _engine(
        second_db, store, account_id, "android-001", tmp_path / "phone"
    )

    first.sync_once()
    second.sync_once()
    remote = ChatRepository(second_db).get_memory(memory.id)
    assert remote is not None
    assert remote.content == "不要催促回复"
    assert remote.status == "active"

    first_chats.delete_memory(memory.id)
    first.sync_once()
    second.sync_once()
    deleted = ChatRepository(second_db).get_memory(memory.id)
    assert deleted is not None
    assert deleted.status == "deleted"
    assert deleted.content == ""
    first_db.close()
    second_db.close()


def test_deletion_propagates_without_generating_sync_echo(tmp_path):
    store = SyncServerStore(tmp_path / "server.db", tmp_path / "server-media")
    account_id = store.create_account()["account_id"]
    first_db = Database(tmp_path / "first.db")
    second_db = Database(tmp_path / "second.db")
    chats = ChatRepository(first_db)
    conversation = chats.create_conversation(title="待删除")
    turn = chats.create_turn(conversation.id, "稍后删除", MODEL_CHAT)
    chats.complete_turn(turn.id, "好。")
    first = _engine(first_db, store, account_id, "desktop-001", tmp_path / "pc")
    second = _engine(second_db, store, account_id, "android-001", tmp_path / "phone")
    first.sync_once()
    second.sync_once()

    chats.delete_conversation(conversation.id)
    first.sync_once()
    result = second.sync_once()

    assert result.pulled == 2
    assert ChatRepository(second_db).get_conversation(conversation.id) is None
    assert second_db.connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
    assert second_db.connection.execute(
        "SELECT COUNT(*) FROM sync_outbox"
    ).fetchone()[0] == 0
    first_db.close()
    second_db.close()


def test_concurrent_edits_keep_conflict_record_and_apply_server_version(tmp_path):
    store = SyncServerStore(tmp_path / "server.db", tmp_path / "server-media")
    account_id = store.create_account()["account_id"]
    first_db = Database(tmp_path / "first.db")
    second_db = Database(tmp_path / "second.db")
    first_chats = ChatRepository(first_db)
    conversation = first_chats.create_conversation(title="共同标题")
    first = _engine(first_db, store, account_id, "desktop-001", tmp_path / "pc")
    second = _engine(second_db, store, account_id, "android-001", tmp_path / "phone")
    first.sync_once()
    second.sync_once()

    first_chats.rename_conversation(conversation.id, "电脑端标题")
    ChatRepository(second_db).rename_conversation(conversation.id, "手机端标题")
    first.sync_once()
    result = second.sync_once()

    assert result.conflicts == 1
    assert ChatRepository(second_db).get_conversation(conversation.id).title == "电脑端标题"
    conflict = second_db.connection.execute(
        "SELECT local_event_json, server_event_json FROM sync_conflicts"
    ).fetchone()
    assert "手机端标题" in conflict["local_event_json"]
    assert "电脑端标题" in conflict["server_event_json"]
    first_db.close()
    second_db.close()
