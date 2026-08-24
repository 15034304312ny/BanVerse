from __future__ import annotations

import json
import sqlite3

from deepseek_cli.character_cards import empty_card
from deepseek_cli.desktop.data.database import Database
from deepseek_cli.desktop.data.repositories import (
    CharacterRepository,
    ChatRepository,
    SettingsRepository,
)
from deepseek_cli.gateway import Message
from deepseek_cli.model_catalog import MODEL_CHAT, MODEL_REASONER


def test_database_migrates_summary_media_and_sticker_columns(tmp_path):
    database = Database(tmp_path / "chat.db")

    conversation_columns = {
        row["name"]
        for row in database.connection.execute("PRAGMA table_info(conversations)")
    }
    turn_columns = {
        row["name"]
        for row in database.connection.execute("PRAGMA table_info(turns)")
    }

    assert database.connection.execute("PRAGMA user_version").fetchone()[0] == 8
    assert {
        "ai_summary",
        "summary_status",
        "role_state_json",
    } <= conversation_columns
    assert {
        "origin",
        "user_image_path",
        "user_image_description",
        "assistant_image_path",
        "assistant_segments_json",
        "user_sticker",
    } <= turn_columns
    sync_tables = {
        row["name"]
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    sync_triggers = {
        row["name"]
        for row in database.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
    }
    assert {
        "sync_runtime",
        "sync_outbox",
        "sync_state",
        "sync_entities",
        "sync_conflicts",
    } <= sync_tables
    assert {
        "sync_conversations_insert",
        "sync_turns_update",
        "sync_characters_delete",
    } <= sync_triggers
    database.close()


def test_existing_v2_database_is_upgraded_without_losing_conversations(tmp_path):
    path = tmp_path / "chat.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, model TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            last_preview TEXT NOT NULL DEFAULT '',
            character_id TEXT, avatar_override_path TEXT NOT NULL DEFAULT '',
            opening_message TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE turns (
            id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
            user_content TEXT NOT NULL, assistant_content TEXT NOT NULL DEFAULT '',
            reasoning_content TEXT NOT NULL DEFAULT '', model TEXT NOT NULL,
            status TEXT NOT NULL, error_code TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE characters (
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            avatar_path TEXT NOT NULL DEFAULT '', card_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO conversations
            (id, title, model, created_at, updated_at, last_preview)
        VALUES ('legacy', '旧会话', 'deepseek-v4-flash', 'now', 'now', '旧预览');
        PRAGMA user_version = 2;
        """
    )
    connection.close()

    database = Database(path)
    row = database.connection.execute(
        "SELECT title, ai_summary, summary_status FROM conversations"
    ).fetchone()

    assert tuple(row) == ("旧会话", "", "none")
    assert database.connection.execute("PRAGMA user_version").fetchone()[0] == 8
    database.close()


def test_completed_turns_are_the_only_context(tmp_path):
    database = Database(tmp_path / "chat.db")
    repository = ChatRepository(database)
    conversation = repository.create_conversation(MODEL_CHAT)
    complete = repository.create_turn(conversation.id, "成功问题", MODEL_CHAT)
    repository.complete_turn(complete.id, "成功回答", "内部推理")
    failed = repository.create_turn(conversation.id, "失败问题", MODEL_CHAT)
    repository.fail_turn(failed.id, "failed", "network")

    assert repository.completed_history(conversation.id) == [
        Message("user", "成功问题"),
        Message("assistant", "成功回答"),
    ]
    database.close()


def test_completed_history_can_limit_recent_turns_but_keep_opening(tmp_path):
    database = Database(tmp_path / "chat.db")
    repository = ChatRepository(database)
    conversation = repository.create_conversation(
        opening_message="角色开场"
    )
    for index in range(3):
        turn = repository.create_turn(
            conversation.id, f"问题{index}", MODEL_CHAT
        )
        repository.complete_turn(turn.id, f"回答{index}")

    assert repository.completed_history(
        conversation.id, max_turns=1
    ) == [
        Message("assistant", "角色开场"),
        Message("user", "问题2"),
        Message("assistant", "回答2"),
    ]
    database.close()


def test_sticker_is_persisted_and_model_receives_semantic_text(tmp_path):
    database = Database(tmp_path / "chat.db")
    repository = ChatRepository(database)
    conversation = repository.create_conversation()
    turn = repository.create_turn(
        conversation.id,
        "我发了一个“抱抱”的表情。",
        MODEL_CHAT,
        user_sticker="hug",
    )
    repository.complete_turn(turn.id, "抱住你啦。")

    saved = repository.list_turns(conversation.id)[0]
    assert saved.user_sticker == "hug"
    assert repository.completed_history(conversation.id) == [
        Message("user", "我发了一个“抱抱”的表情。"),
        Message("assistant", "抱住你啦。"),
    ]
    database.close()


def test_conversation_title_model_and_recovery(tmp_path):
    database = Database(tmp_path / "chat.db")
    repository = ChatRepository(database)
    conversation = repository.create_conversation()
    turn = repository.create_turn(
        conversation.id, "这是第一条会话标题内容", MODEL_CHAT
    )
    repository.mark_streaming(turn.id)
    repository.set_model(conversation.id, MODEL_REASONER)
    repository.recover_interrupted()

    updated = repository.get_conversation(conversation.id)
    assert updated is not None
    assert updated.title == "这是第一条会话标题内容"
    assert updated.model == MODEL_REASONER
    assert repository.list_turns(conversation.id)[0].status == "interrupted"
    database.close()


def test_legacy_model_names_are_normalized_when_read(tmp_path):
    database = Database(tmp_path / "chat.db")
    repository = ChatRepository(database)
    conversation = repository.create_conversation("deepseek-reasoner")

    loaded = repository.get_conversation(conversation.id)

    assert loaded is not None
    assert loaded.model == MODEL_REASONER
    assert repository.list_conversations()[0].model == MODEL_REASONER
    database.close()


def test_character_conversation_opening_avatar_and_delete_behavior(tmp_path):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    card = empty_card("Alice")
    card["data"]["first_mes"] = "Hello from Alice"
    character = characters.create(card, "alice.png")
    conversation = chats.create_conversation(
        title=character.name,
        character_id=character.id,
        opening_message=card["data"]["first_mes"],
    )

    loaded = chats.get_conversation(conversation.id)
    assert loaded is not None
    assert loaded.character_name == "Alice"
    assert loaded.display_name == "Alice"
    assert loaded.effective_avatar_path == "alice.png"
    assert chats.completed_history(conversation.id) == [
        Message("assistant", "Hello from Alice")
    ]
    chats.set_avatar_override(conversation.id, "custom.png")
    assert chats.get_conversation(conversation.id).effective_avatar_path == "custom.png"
    chats.rename_conversation(conversation.id, "My Alice Chat")
    characters.delete(character.id)
    detached = chats.get_conversation(conversation.id)
    assert detached is not None
    assert detached.character_id is None
    assert detached.title == "My Alice Chat"
    assert detached.opening_message == "Hello from Alice"
    database.close()


def test_settings_upsert(tmp_path):
    database = Database(tmp_path / "chat.db")
    settings = SettingsRepository(database)

    assert settings.get("theme", "system") == "system"
    settings.set("theme", "dark")
    settings.set("theme", "light")

    assert settings.get("theme") == "light"
    database.close()


def test_ai_summary_is_persisted_separately_from_full_reply(tmp_path):
    database = Database(tmp_path / "chat.db")
    repository = ChatRepository(database)
    conversation = repository.create_conversation()
    turn = repository.create_turn(conversation.id, "请分析线索", MODEL_CHAT)

    repository.complete_turn(
        turn.id,
        "这是一条需要完整保留在聊天详情中的很长回答。",
    )
    pending = repository.get_conversation(conversation.id)
    assert pending.summary_status == "pending"
    assert pending.ai_summary == ""
    assert pending.last_preview == ""

    repository.set_ai_summary(conversation.id, "双方决定继续调查关键线索")
    summarized = repository.get_conversation(conversation.id)
    assert summarized.ai_summary == "双方决定继续调查关键线索"
    assert summarized.summary_status == "ready"
    assert repository.list_turns(conversation.id)[0].assistant_content.endswith(
        "很长回答。"
    )
    database.close()


def test_role_state_is_stored_separately_from_messages(tmp_path):
    database = Database(tmp_path / "chat.db")
    repository = ChatRepository(database)
    conversation = repository.create_conversation()

    repository.set_role_state(
        conversation.id,
        {
            "scene": {"location": "天台"},
            "open_threads": ["等雨停"],
        },
    )

    loaded = repository.get_conversation(conversation.id)
    assert '"location":"天台"' in loaded.role_state_json
    assert repository.completed_history(conversation.id) == []
    database.close()


def test_proactive_turn_is_assistant_only_in_model_history(tmp_path):
    database = Database(tmp_path / "chat.db")
    repository = ChatRepository(database)
    conversation = repository.create_conversation()
    proactive = repository.create_proactive_turn(conversation.id, MODEL_CHAT)
    repository.complete_turn(proactive.id, "今天想聊聊你最近在做的事。")

    turns = repository.list_turns(conversation.id)
    assert turns[0].origin == "proactive"
    assert turns[0].user_content == ""
    assert repository.completed_history(conversation.id) == [
        Message("assistant", "今天想聊聊你最近在做的事。")
    ]
    assert repository.pending_summary_jobs() == [
        (conversation.id, "今天想聊聊你最近在做的事。")
    ]
    database.close()


def test_image_paths_and_visual_description_are_persisted_in_history(tmp_path):
    database = Database(tmp_path / "chat.db")
    repository = ChatRepository(database)
    conversation = repository.create_conversation()
    turn = repository.create_turn(
        conversation.id,
        "你看这张照片",
        MODEL_CHAT,
        user_image_path="attachments/photo.jpg",
    )
    repository.set_user_image_description(
        turn.id, "窗边放着一杯咖啡和一本打开的书。"
    )
    repository.complete_turn(turn.id, "下午的光线很温柔。")
    repository.set_assistant_image_path(
        turn.id, "generated/character-shared.png"
    )
    generated = repository.create_turn(
        conversation.id,
        "雨夜里的城市街道",
        MODEL_CHAT,
        origin="image_generation",
    )
    repository.complete_turn(
        generated.id,
        "已根据你的描述生成图片。",
        assistant_image_path="generated/city.png",
    )

    turns = repository.list_turns(conversation.id)
    assert turns[0].user_image_path == "attachments/photo.jpg"
    assert "咖啡" in turns[0].user_image_description
    assert turns[0].assistant_image_path == "generated/character-shared.png"
    assert turns[1].origin == "image_generation"
    assert turns[1].assistant_image_path == "generated/city.png"
    assert repository.completed_history(conversation.id) == [
        Message(
            "user",
            "你看这张照片\n\n"
            "[用户发送的图片内容：窗边放着一杯咖啡和一本打开的书。]",
        ),
        Message(
            "assistant",
            "下午的光线很温柔。\n\n[助手生成了一张图片。]",
        ),
        Message("user", "请生成图片：雨夜里的城市街道"),
        Message(
            "assistant",
            "已根据你的描述生成图片。\n\n[助手生成了一张图片。]",
        ),
    ]
    database.close()


def test_generated_image_path_is_written_to_the_planned_image_segment(tmp_path):
    database = Database(tmp_path / "chat.db")
    repository = ChatRepository(database)
    conversation = repository.create_conversation()
    turn = repository.create_turn(conversation.id, "分享一下", MODEL_CHAT)
    segments = [
        {"kind": "dialogue", "text": "给你看看。", "prompt": "", "image_path": ""},
        {
            "kind": "image",
            "text": "",
            "prompt": "窗边晚霞",
            "image_path": "",
        },
    ]
    repository.complete_turn(
        turn.id,
        "给你看看。",
        assistant_segments_json=json.dumps(segments, ensure_ascii=False),
    )

    repository.set_assistant_image_path(
        turn.id,
        "generated/sunset.png",
        segment_index=1,
    )

    saved = repository.list_turns(conversation.id)[0]
    restored = json.loads(saved.assistant_segments_json)
    assert saved.assistant_image_path == "generated/sunset.png"
    assert restored[1]["image_path"] == "generated/sunset.png"
    database.close()
