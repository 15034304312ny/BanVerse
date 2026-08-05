"""会话、角色、轮次与设置的数据访问。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from ...character_cards import dump_card, empty_card, normalize_card
from ...gateway import Message
from ...model_catalog import MODEL_CHAT, resolve_model
from .database import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _title(text: str, limit: int = 24) -> str:
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else f"{normalized[:limit]}…"


@dataclass(frozen=True, slots=True)
class Conversation:
    id: str
    title: str
    model: str
    updated_at: str
    last_preview: str
    character_id: str | None = None
    avatar_override_path: str = ""
    opening_message: str = ""
    character_avatar_path: str = ""
    ai_summary: str = ""
    summary_status: str = "none"
    character_name: str = ""
    role_state_json: str = "{}"

    @property
    def effective_avatar_path(self) -> str:
        return self.avatar_override_path or self.character_avatar_path

    @property
    def display_name(self) -> str:
        return self.character_name or self.title


@dataclass(frozen=True, slots=True)
class Character:
    id: str
    name: str
    avatar_path: str
    card: dict
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Turn:
    id: str
    conversation_id: str
    user_content: str
    assistant_content: str
    reasoning_content: str
    model: str
    status: str
    error_code: str
    created_at: str
    origin: str = "user"
    user_image_path: str = ""
    user_image_description: str = ""
    assistant_image_path: str = ""
    user_sticker: str = ""
    assistant_segments_json: str = "[]"


_CONVERSATION_SELECT = """
SELECT c.id, c.title, c.model, c.updated_at, c.last_preview,
       c.character_id, c.avatar_override_path, c.opening_message,
       c.ai_summary, c.summary_status, c.role_state_json,
       COALESCE(ch.avatar_path, '') AS character_avatar_path,
       COALESCE(ch.name, '') AS character_name
FROM conversations c LEFT JOIN characters ch ON ch.id = c.character_id
"""


class ChatRepository:
    def __init__(self, database: Database) -> None:
        self._db = database.connection

    def recover_interrupted(self) -> None:
        with self._db:
            self._db.execute(
                """UPDATE turns SET status = 'interrupted', updated_at = ?
                   WHERE status IN ('pending', 'streaming')""",
                (_now(),),
            )

    def create_conversation(
        self,
        model: str = MODEL_CHAT,
        *,
        title: str = "新对话",
        character_id: str | None = None,
        opening_message: str = "",
    ) -> Conversation:
        conversation_id = str(uuid4())
        now = _now()
        with self._db:
            self._db.execute(
                """INSERT INTO conversations
                   (id, title, model, created_at, updated_at, last_preview,
                    character_id, avatar_override_path, opening_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '', ?)""",
                (
                    conversation_id,
                    title.strip() or "新对话",
                    model,
                    now,
                    now,
                    "",
                    character_id,
                    opening_message,
                ),
            )
        return self.get_conversation(conversation_id)  # type: ignore[return-value]

    def list_conversations(self, query: str = "") -> list[Conversation]:
        sql = _CONVERSATION_SELECT
        params: tuple[str, ...] = ()
        if query.strip():
            sql += (
                " WHERE c.title LIKE ? OR c.ai_summary LIKE ?"
                " OR COALESCE(ch.name, '') LIKE ?"
            )
            value = f"%{query.strip()}%"
            params = (value, value, value)
        sql += " ORDER BY c.updated_at DESC"
        return [self._conversation(row) for row in self._db.execute(sql, params)]

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        row = self._db.execute(
            _CONVERSATION_SELECT + " WHERE c.id = ?", (conversation_id,)
        ).fetchone()
        return self._conversation(row) if row else None

    def _conversation(self, row) -> Conversation:
        data = dict(row)
        data["model"] = resolve_model(data["model"]) or MODEL_CHAT
        return Conversation(**data)

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        value = " ".join(title.split())
        if not value:
            raise ValueError("会话名称不能为空")
        with self._db:
            self._db.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (value[:80], _now(), conversation_id),
            )

    def set_avatar_override(self, conversation_id: str, path: str) -> None:
        with self._db:
            self._db.execute(
                """UPDATE conversations SET avatar_override_path = ?,
                   updated_at = ? WHERE id = ?""",
                (path, _now(), conversation_id),
            )

    def bind_character(self, conversation_id: str, character_id: str | None) -> None:
        with self._db:
            self._db.execute(
                """UPDATE conversations SET character_id = ?, updated_at = ?
                   WHERE id = ?""",
                (character_id, _now(), conversation_id),
            )

    def set_model(self, conversation_id: str, model: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE conversations SET model = ?, updated_at = ? WHERE id = ?",
                (model, _now(), conversation_id),
            )

    def delete_conversation(self, conversation_id: str) -> None:
        with self._db:
            self._db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

    def clear_all(self) -> None:
        with self._db:
            self._db.execute("DELETE FROM conversations")

    def create_turn(
        self,
        conversation_id: str,
        user_content: str,
        model: str,
        *,
        user_image_path: str = "",
        user_sticker: str = "",
        origin: str = "user",
    ) -> Turn:
        if origin not in {"user", "image_generation"}:
            raise ValueError(origin)
        turn_id, now = str(uuid4()), _now()
        with self._db:
            count = self._db.execute(
                "SELECT COUNT(*) FROM turns WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()[0]
            self._db.execute(
                """INSERT INTO turns
                   (id, conversation_id, user_content, model, status,
                    created_at, updated_at, origin, user_image_path,
                    user_sticker)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
                (
                    turn_id,
                    conversation_id,
                    user_content,
                    model,
                    now,
                    now,
                    origin,
                    user_image_path,
                    user_sticker,
                ),
            )
            current = self.get_conversation(conversation_id)
            auto_title = count == 0 and current and current.title == "新对话"
            if auto_title:
                self._db.execute(
                    "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                    (_title(user_content), now, conversation_id),
                )
            else:
                self._db.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (now, conversation_id),
                )
        return Turn(
            turn_id,
            conversation_id,
            user_content,
            "",
            "",
            model,
            "pending",
            "",
            now,
            origin,
            user_image_path,
            user_sticker=user_sticker,
        )

    def create_proactive_turn(self, conversation_id: str, model: str) -> Turn:
        """创建仅含助手消息的主动会话轮次。"""

        turn_id, now = str(uuid4()), _now()
        with self._db:
            self._db.execute(
                """INSERT INTO turns
                   (id, conversation_id, user_content, model, status,
                    created_at, updated_at, origin)
                   VALUES (?, ?, '', ?, 'pending', ?, ?, 'proactive')""",
                (turn_id, conversation_id, model, now, now),
            )
            self._db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
        return Turn(
            turn_id,
            conversation_id,
            "",
            "",
            "",
            model,
            "pending",
            "",
            now,
            "proactive",
        )

    def mark_streaming(self, turn_id: str) -> None:
        self._update_status(turn_id, "streaming")

    def complete_turn(
        self,
        turn_id: str,
        answer: str,
        reasoning: str = "",
        *,
        assistant_image_path: str = "",
        assistant_segments_json: str = "[]",
    ) -> None:
        now = _now()
        with self._db:
            row = self._db.execute(
                "SELECT conversation_id FROM turns WHERE id = ?", (turn_id,)
            ).fetchone()
            if row is None:
                raise KeyError(turn_id)
            self._db.execute(
                """UPDATE turns SET assistant_content = ?, reasoning_content = ?,
                   assistant_image_path = ?, assistant_segments_json = ?,
                   status = 'completed',
                   error_code = '', updated_at = ? WHERE id = ?""",
                (
                    answer,
                    reasoning,
                    assistant_image_path,
                    assistant_segments_json,
                    now,
                    turn_id,
                ),
            )
            self._db.execute(
                """UPDATE conversations
                   SET last_preview = '', ai_summary = '',
                       summary_status = 'pending', updated_at = ?
                   WHERE id = ?""",
                (now, row["conversation_id"]),
            )

    def set_user_image_description(self, turn_id: str, description: str) -> None:
        value = " ".join(description.split()).strip()
        if not value:
            raise ValueError("图片描述不能为空")
        with self._db:
            self._db.execute(
                """UPDATE turns SET user_image_description = ?, updated_at = ?
                   WHERE id = ?""",
                (value[:2_000], _now(), turn_id),
            )

    def set_assistant_image_path(
        self,
        turn_id: str,
        image_path: str,
        *,
        segment_index: int | None = None,
    ) -> None:
        """给已完成的角色消息追加自主生成的本地图片。"""

        value = image_path.strip()
        if not value:
            raise ValueError("角色图片路径不能为空")
        now = _now()
        with self._db:
            row = self._db.execute(
                """SELECT conversation_id, status, assistant_segments_json
                   FROM turns WHERE id = ?""",
                (turn_id,),
            ).fetchone()
            if row is None:
                raise KeyError(turn_id)
            if row["status"] != "completed":
                raise ValueError("只能给已完成的角色消息追加图片")
            segments_json = row["assistant_segments_json"]
            if segment_index is not None:
                try:
                    segments = json.loads(segments_json or "[]")
                except (TypeError, ValueError):
                    segments = []
                if (
                    isinstance(segments, list)
                    and 0 <= segment_index < len(segments)
                    and isinstance(segments[segment_index], dict)
                    and segments[segment_index].get("kind") == "image"
                ):
                    segments[segment_index]["image_path"] = value
                    segments_json = json.dumps(
                        segments,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
            self._db.execute(
                """UPDATE turns SET assistant_image_path = ?,
                   assistant_segments_json = ?, updated_at = ?
                   WHERE id = ?""",
                (value, segments_json, now, turn_id),
            )
            self._db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, row["conversation_id"]),
            )

    def set_ai_summary(self, conversation_id: str, summary: str) -> None:
        value = " ".join(summary.split()).strip()
        if not value:
            raise ValueError("AI 摘要不能为空")
        with self._db:
            self._db.execute(
                """UPDATE conversations
                   SET ai_summary = ?, last_preview = ?,
                       summary_status = 'ready'
                   WHERE id = ?""",
                (value[:120], value[:120], conversation_id),
            )

    def set_role_state(self, conversation_id: str, state: dict) -> None:
        value = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        if len(value) > 12_000:
            raise ValueError("角色连续性状态过大")
        with self._db:
            self._db.execute(
                "UPDATE conversations SET role_state_json = ? WHERE id = ?",
                (value, conversation_id),
            )

    def mark_summary_failed(self, conversation_id: str) -> None:
        with self._db:
            self._db.execute(
                """UPDATE conversations
                   SET ai_summary = '', last_preview = '',
                       summary_status = 'failed'
                   WHERE id = ?""",
                (conversation_id,),
            )

    def pending_summary_jobs(self) -> list[tuple[str, str]]:
        rows = self._db.execute(
            """SELECT c.id AS conversation_id, t.assistant_content
               FROM conversations c
               JOIN turns t ON t.id = (
                   SELECT latest.id FROM turns latest
                   WHERE latest.conversation_id = c.id
                     AND latest.status = 'completed'
                   ORDER BY latest.created_at DESC, latest.rowid DESC
                   LIMIT 1
               )
               WHERE c.summary_status = 'pending'
                 AND t.assistant_content != ''
               ORDER BY c.updated_at"""
        )
        return [
            (row["conversation_id"], row["assistant_content"]) for row in rows
        ]

    def delete_turn(self, turn_id: str) -> None:
        with self._db:
            self._db.execute("DELETE FROM turns WHERE id = ?", (turn_id,))

    def fail_turn(self, turn_id: str, status: str, error_code: str = "") -> None:
        if status not in {"failed", "cancelled", "interrupted"}:
            raise ValueError(status)
        with self._db:
            self._db.execute(
                """UPDATE turns SET status = ?, error_code = ?, updated_at = ? WHERE id = ?""",
                (status, error_code, _now(), turn_id),
            )

    def _update_status(self, turn_id: str, status: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE turns SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), turn_id),
            )

    def list_turns(self, conversation_id: str) -> list[Turn]:
        rows = self._db.execute(
            """SELECT id, conversation_id, user_content, assistant_content,
                      reasoning_content, model, status, error_code, created_at,
                      origin, user_image_path, user_image_description,
                      assistant_image_path, user_sticker,
                      assistant_segments_json
               FROM turns WHERE conversation_id = ? ORDER BY created_at, rowid""",
            (conversation_id,),
        )
        return [Turn(**dict(row)) for row in rows]

    def completed_history(
        self, conversation_id: str, *, max_turns: int | None = None
    ) -> list[Message]:
        conversation = self.get_conversation(conversation_id)
        history = []
        if conversation and conversation.opening_message:
            history.append(Message("assistant", conversation.opening_message))
        turns = [
            turn
            for turn in self.list_turns(conversation_id)
            if turn.status == "completed"
        ]
        if max_turns is not None:
            limit = max(0, int(max_turns))
            turns = turns[-limit:] if limit else []
        for turn in turns:
            if turn.origin in {"user", "image_generation"}:
                user_parts = []
                if turn.origin == "image_generation":
                    user_parts.append(f"请生成图片：{turn.user_content}")
                elif turn.user_content:
                    user_parts.append(turn.user_content)
                if turn.user_image_description:
                    user_parts.append(
                        f"[用户发送的图片内容：{turn.user_image_description}]"
                    )
                elif turn.user_image_path:
                    user_parts.append("[用户发送了一张图片，画面内容未识别。]")
                if user_parts:
                    history.append(Message("user", "\n\n".join(user_parts)))
            assistant_parts = (
                [turn.assistant_content] if turn.assistant_content else []
            )
            if turn.assistant_image_path:
                assistant_parts.append("[助手生成了一张图片。]")
            if assistant_parts:
                history.append(Message("assistant", "\n\n".join(assistant_parts)))
        return history


class CharacterRepository:
    def __init__(self, database: Database) -> None:
        self._db = database.connection

    def create(self, card: dict | None = None, avatar_path: str = "") -> Character:
        character_id = str(uuid4())
        with self._db:
            self.create_with_id(character_id, card or empty_card(), avatar_path, connection=self._db)
        return self.get(character_id)  # type: ignore[return-value]

    def exists(self, character_id: str, *, connection=None) -> bool:
        db = connection or self._db
        return db.execute(
            "SELECT 1 FROM characters WHERE id = ?", (character_id,)
        ).fetchone() is not None

    def create_with_id(
        self,
        character_id: str,
        card: dict,
        avatar_path: str = "",
        *,
        connection=None,
    ) -> None:
        """在调用方事务内用受信任的稳定 ID 创建角色。"""

        normalized = normalize_card(card)
        now = _now()
        db = connection or self._db
        db.execute(
            """INSERT INTO characters(id, name, avatar_path, card_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                character_id,
                normalized["data"]["name"],
                avatar_path,
                dump_card(normalized),
                now,
                now,
            ),
        )

    def update(
        self,
        character_id: str,
        card: dict,
        avatar_path: str | None = None,
        *,
        connection=None,
    ) -> None:
        normalized = normalize_card(card)
        db = connection or self._db
        if avatar_path is None:
            statement = (
                """UPDATE characters SET name = ?, card_json = ?,
                   updated_at = ? WHERE id = ?"""
            )
            params = (
                normalized["data"]["name"],
                dump_card(normalized),
                _now(),
                character_id,
            )
        else:
            statement = (
                """UPDATE characters SET name = ?, card_json = ?,
                   avatar_path = ?, updated_at = ? WHERE id = ?"""
            )
            params = (
                normalized["data"]["name"],
                dump_card(normalized),
                avatar_path,
                _now(),
                character_id,
            )
        if connection is None:
            with self._db:
                db.execute(statement, params)
        else:
            db.execute(statement, params)

    def get(self, character_id: str) -> Character | None:
        row = self._db.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
        return self._character(row) if row else None

    def list(self, query: str = "") -> list[Character]:
        if query.strip():
            rows = self._db.execute(
                "SELECT * FROM characters WHERE name LIKE ? ORDER BY updated_at DESC",
                (f"%{query.strip()}%",),
            )
        else:
            rows = self._db.execute("SELECT * FROM characters ORDER BY updated_at DESC")
        return [self._character(row) for row in rows]

    def duplicate(self, character_id: str) -> Character:
        source = self.get(character_id)
        if source is None:
            raise KeyError(character_id)
        card = json.loads(dump_card(source.card))
        card["data"]["name"] += "（副本）"
        app_extension = card["data"].get("extensions", {}).get("deepseek_chat")
        if isinstance(app_extension, dict):
            app_extension.pop("builtin_id", None)
        return self.create(card, source.avatar_path)

    def delete(self, character_id: str) -> None:
        with self._db:
            self._db.execute("DELETE FROM characters WHERE id = ?", (character_id,))

    @staticmethod
    def _character(row) -> Character:
        return Character(
            row["id"], row["name"], row["avatar_path"],
            normalize_card(json.loads(row["card_json"])), row["created_at"], row["updated_at"]
        )


class SettingsRepository:
    def __init__(self, database: Database) -> None:
        self._db = database.connection

    def get(self, key: str, default: str = "") -> str:
        row = self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def contains(self, key: str, *, connection=None) -> bool:
        db = connection or self._db
        return db.execute(
            "SELECT 1 FROM settings WHERE key = ?", (key,)
        ).fetchone() is not None

    def set(self, key: str, value: str, *, connection=None) -> None:
        db = connection or self._db
        if connection is None:
            with self._db:
                self._set(db, key, value)
        else:
            self._set(db, key, value)

    @staticmethod
    def _set(db, key: str, value: str) -> None:
        db.execute(
            """INSERT INTO settings(key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )
