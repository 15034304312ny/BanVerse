"""会话、角色、轮次与设置的数据访问。"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


_MEMORY_STOP_TERMS = {
    "什么",
    "怎么",
    "这个",
    "那个",
    "今天",
    "现在",
    "已经",
    "还是",
    "可以",
    "一下",
    "一个",
    "我们",
    "你们",
    "他们",
    "真的",
    "没有",
}


def _memory_terms(text: str) -> set[str]:
    """提取适合中文本地召回的受控二元词和字母数字词。"""

    source = " ".join(str(text or "").lower().split())[:4_000]
    terms = {
        item
        for item in re.findall(r"[a-z0-9_\-]{2,}", source)
        if item not in _MEMORY_STOP_TERMS
    }
    for block in re.findall(r"[\u3400-\u9fff]{2,}", source):
        if block not in _MEMORY_STOP_TERMS and len(block) <= 8:
            terms.add(block)
        for size in (2, 3):
            for index in range(max(0, len(block) - size + 1)):
                item = block[index : index + size]
                if item not in _MEMORY_STOP_TERMS:
                    terms.add(item)
                if len(terms) >= 180:
                    return terms
    return terms


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
    source_type: str = "user_created"


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


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: str
    conversation_id: str
    character_id: str | None
    category: str
    content: str
    source_type: str
    source_turn_id: str | None
    confidence: float
    salience: float
    status: str
    pinned: bool
    created_at: str
    updated_at: str
    last_used_at: str = ""
    expires_at: str = ""
    superseded_by_id: str = ""
    confirmed_at: str = ""
    deleted_at: str = ""


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

    def set_opening_message(self, conversation_id: str, opening: str) -> None:
        """更新会话开场白：AI 开场成功后清空，失败时写回角色模板兜底。"""

        with self._db:
            self._db.execute(
                """UPDATE conversations SET opening_message = ?, updated_at = ?
                   WHERE id = ?""",
                (opening, _now(), conversation_id),
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

    def create_proactive_turn(
        self,
        conversation_id: str,
        model: str,
        *,
        origin: str = "proactive",
    ) -> Turn:
        """创建仅含助手消息的主动轮次或首次开场轮次。"""

        if origin not in {"proactive", "opening"}:
            raise ValueError(origin)

        turn_id, now = str(uuid4()), _now()
        with self._db:
            self._db.execute(
                """INSERT INTO turns
                   (id, conversation_id, user_content, model, status,
                    created_at, updated_at, origin)
                   VALUES (?, ?, '', ?, 'pending', ?, ?, ?)""",
                (turn_id, conversation_id, model, now, now, origin),
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
            origin,
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
                    segments[segment_index]["status"] = "completed"
                    segments[segment_index]["error_code"] = ""
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

    def ensure_assistant_image_event(
        self,
        turn_id: str,
        prompt: str,
        event_id: str,
    ) -> int:
        """确保已完成轮次只有一个图片事件，并返回其分段索引。"""

        normalized_prompt = " ".join(str(prompt or "").split()).strip()[:1_500]
        normalized_event = " ".join(str(event_id or "").split()).strip()[:80]
        if not normalized_prompt or not normalized_event:
            raise ValueError("图片事件缺少提示词或幂等 ID")
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
                raise ValueError("只能给已完成轮次添加图片事件")
            try:
                segments = json.loads(row["assistant_segments_json"] or "[]")
            except (TypeError, ValueError):
                segments = []
            if not isinstance(segments, list):
                segments = []
            for index, segment in enumerate(segments):
                if isinstance(segment, dict) and segment.get("kind") == "image":
                    segment["prompt"] = normalized_prompt
                    segment["event_id"] = normalized_event
                    segment["status"] = "pending"
                    segment["error_code"] = ""
                    self._db.execute(
                        """UPDATE turns SET assistant_segments_json = ?, updated_at = ?
                           WHERE id = ?""",
                        (
                            json.dumps(
                                segments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            now,
                            turn_id,
                        ),
                    )
                    self._db.execute(
                        "UPDATE conversations SET updated_at = ? WHERE id = ?",
                        (now, row["conversation_id"]),
                    )
                    return index
            segments.append(
                {
                    "kind": "image",
                    "text": "",
                    "prompt": normalized_prompt,
                    "image_path": "",
                    "event_id": normalized_event,
                    "status": "pending",
                    "error_code": "",
                }
            )
            self._db.execute(
                """UPDATE turns SET assistant_segments_json = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    json.dumps(segments, ensure_ascii=False, separators=(",", ":")),
                    now,
                    turn_id,
                ),
            )
            self._db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, row["conversation_id"]),
            )
            return len(segments) - 1

    def set_assistant_image_status(
        self,
        turn_id: str,
        status: str,
        error_code: str = "",
        *,
        segment_index: int | None = None,
    ) -> None:
        """持久化图片事件状态；失败不会改变已完成文字轮次。"""

        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"pending", "failed", "cancelled"}:
            raise ValueError(status)
        with self._db:
            row = self._db.execute(
                "SELECT assistant_segments_json FROM turns WHERE id = ?",
                (turn_id,),
            ).fetchone()
            if row is None:
                raise KeyError(turn_id)
            try:
                segments = json.loads(row["assistant_segments_json"] or "[]")
            except (TypeError, ValueError):
                segments = []
            if not isinstance(segments, list):
                raise ValueError("图片分段数据无效")
            indexes = (
                (segment_index,)
                if segment_index is not None
                else tuple(range(len(segments)))
            )
            changed = False
            for index in indexes:
                if (
                    isinstance(index, int)
                    and 0 <= index < len(segments)
                    and isinstance(segments[index], dict)
                    and segments[index].get("kind") == "image"
                ):
                    segments[index]["status"] = normalized_status
                    segments[index]["error_code"] = (
                        str(error_code or "").strip()[:120]
                        if normalized_status == "failed"
                        else ""
                    )
                    changed = True
                    break
            if not changed:
                raise ValueError("图片事件不存在")
            self._db.execute(
                """UPDATE turns SET assistant_segments_json = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    json.dumps(segments, ensure_ascii=False, separators=(",", ":")),
                    _now(),
                    turn_id,
                ),
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

    def set_role_state_if_unchanged(
        self,
        conversation_id: str,
        state: dict,
        *,
        expected_json: str,
    ) -> bool:
        """乐观写入连续性状态，避免异步旧任务覆盖同步得到的新状态。"""

        value = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        if len(value) > 12_000:
            raise ValueError("角色连续性状态过大")
        with self._db:
            cursor = self._db.execute(
                """UPDATE conversations SET role_state_json = ?
                   WHERE id = ? AND role_state_json = ?""",
                (value, conversation_id, expected_json or "{}"),
            )
        return cursor.rowcount == 1

    def mark_summary_failed(self, conversation_id: str) -> None:
        with self._db:
            self._db.execute(
                """UPDATE conversations
                   SET ai_summary = '', last_preview = '',
                       summary_status = 'failed'
                   WHERE id = ?""",
                (conversation_id,),
            )

    def pending_summary_jobs(self) -> list[tuple[str, str, str]]:
        rows = self._db.execute(
            """SELECT c.id AS conversation_id, t.id AS turn_id,
                      t.assistant_content
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
            (
                row["conversation_id"],
                row["turn_id"],
                row["assistant_content"],
            )
            for row in rows
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

    def get_turn(self, conversation_id: str, turn_id: str) -> Turn | None:
        """按会话与轮次 ID 单条查询，避免全表装载后再过滤。"""

        row = self._db.execute(
            """SELECT id, conversation_id, user_content, assistant_content,
                      reasoning_content, model, status, error_code, created_at,
                      origin, user_image_path, user_image_description,
                      assistant_image_path, user_sticker,
                      assistant_segments_json
               FROM turns WHERE conversation_id = ? AND id = ?""",
            (conversation_id, turn_id),
        ).fetchone()
        return Turn(**dict(row)) if row else None

    def _completed_turns(
        self, conversation_id: str, *, max_turns: int | None = None
    ) -> list[Turn]:
        """只查询已完成的轮次，避免全表装载后在 Python 侧过滤。

        需要最近 N 条时用子查询先逆序取 N 条再正序返回，保持原有
        ``turns[-max_turns:]`` 的语义与时间顺序。
        """

        columns = (
            "id, conversation_id, user_content, assistant_content, "
            "reasoning_content, model, status, error_code, created_at, "
            "origin, user_image_path, user_image_description, "
            "assistant_image_path, user_sticker, assistant_segments_json"
        )
        base = (
            f"SELECT {columns}, rowid AS _rowid FROM turns "
            "WHERE conversation_id = ? AND status = 'completed'"
        )
        limit = max(0, int(max_turns)) if max_turns is not None else None
        if limit:
            rows = self._db.execute(
                f"SELECT {columns} FROM ({base} ORDER BY created_at DESC, "
                "_rowid DESC LIMIT ?) ORDER BY created_at, _rowid",
                (conversation_id, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                f"SELECT {columns} FROM ({base} ORDER BY created_at, _rowid)",
                (conversation_id,),
            ).fetchall()
        return [Turn(**dict(row)) for row in rows]

    def recent_window_has_assistant_image(
        self, conversation_id: str, *, window: int = 4
    ) -> bool:
        """最近 N 个已完成轮次中是否已生成过图片（自主发图冷却窗口）。"""

        rows = self._db.execute(
            """SELECT assistant_image_path FROM turns
               WHERE conversation_id = ? AND status = 'completed'
               ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            (conversation_id, max(1, int(window))),
        ).fetchall()
        return any(row["assistant_image_path"] for row in rows)

    def latest_completed_user_text(self, conversation_id: str) -> str:
        """最近一个已完成轮次的用户消息（角色连续性状态回填用）。"""

        row = self._db.execute(
            """SELECT user_content FROM turns
               WHERE conversation_id = ? AND status = 'completed'
               ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (conversation_id,),
        ).fetchone()
        return row["user_content"] if row else ""

    def latest_completed_turn(self, conversation_id: str) -> Turn | None:
        turns = self._completed_turns(conversation_id, max_turns=1)
        return turns[0] if turns else None

    def completed_history(
        self, conversation_id: str, *, max_turns: int | None = None
    ) -> list[Message]:
        conversation = self.get_conversation(conversation_id)
        history = []
        if conversation and conversation.opening_message:
            history.append(Message("assistant", conversation.opening_message))
        turns = self._completed_turns(
            conversation_id, max_turns=max_turns
        )
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

    def recalled_memories(
        self,
        conversation_id: str,
        query: str,
        *,
        exclude_recent_turns: int = 12,
        max_items: int = 4,
    ) -> list[str]:
        """从较早轮次召回与当前话题相关的共同经历。

        采用本地词项重合与时间衰减，避免在 Android 引入 embedding 依赖。
        最近原文窗口由调用方直接传给模型，因此在这里明确排除。
        """

        query_terms = _memory_terms(query)
        if not query_terms or max_items <= 0:
            return []
        governed = self._recalled_memory_records(
            conversation_id,
            query_terms,
            max_items=max_items,
        )
        remaining = max(0, min(int(max_items), 6) - len(governed))
        if remaining <= 0:
            return governed
        rows = self._db.execute(
            """SELECT user_content, assistant_content,
                      user_image_description, origin, created_at, rowid
               FROM turns
               WHERE conversation_id = ? AND status = 'completed'
               ORDER BY created_at DESC, rowid DESC LIMIT 240""",
            (conversation_id,),
        ).fetchall()
        candidates = rows[max(0, int(exclude_recent_turns)) :]
        scored: list[tuple[float, int, str]] = []
        for rank, row in enumerate(candidates):
            user_text = " ".join(str(row["user_content"] or "").split())
            assistant_text = " ".join(
                str(row["assistant_content"] or "").split()
            )
            image_text = " ".join(
                str(row["user_image_description"] or "").split()
            )
            combined = " ".join(
                item for item in (user_text, image_text, assistant_text) if item
            )
            overlap = query_terms & _memory_terms(combined)
            if not overlap:
                continue
            specificity = sum(min(len(term), 4) for term in overlap)
            recency = max(0.0, 1.5 - rank / 80)
            score = specificity + recency
            parts = []
            if user_text:
                parts.append(f"用户：{user_text[:180]}")
            if image_text:
                parts.append(f"当时图片：{image_text[:140]}")
            if assistant_text:
                parts.append(f"角色：{assistant_text[:220]}")
            if parts:
                scored.append((score, int(row["rowid"]), "；".join(parts)))
        selected = sorted(scored, key=lambda item: item[0], reverse=True)[
            :remaining
        ]
        selected.sort(key=lambda item: item[1])
        return [*governed, *(item[2] for item in selected)]

    def _recalled_memory_records(
        self,
        conversation_id: str,
        query_terms: set[str],
        *,
        max_items: int,
    ) -> list[str]:
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            return []
        now = _now()
        if conversation.character_id:
            scope_sql = "character_id = ?"
            scope_value = conversation.character_id
        else:
            scope_sql = "conversation_id = ?"
            scope_value = conversation_id
        rows = self._db.execute(
            f"""SELECT * FROM memories
                WHERE {scope_sql} AND status IN ('active', 'corrected')
                  AND content != ''
                  AND (expires_at = '' OR expires_at > ?)
                ORDER BY pinned DESC, salience DESC, updated_at DESC
                LIMIT 300""",
            (scope_value, now),
        ).fetchall()
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            overlap = query_terms & _memory_terms(row["content"])
            if not overlap and not row["pinned"]:
                continue
            score = (
                sum(min(len(term), 4) for term in overlap)
                + float(row["salience"] or 0)
                + (3.0 if row["pinned"] else 0.0)
            )
            scored.append((score, row))
        selected = sorted(scored, key=lambda item: item[0], reverse=True)[
            : max(0, min(int(max_items), 6))
        ]
        if selected:
            used_at = _now()
            with self._db:
                self._db.executemany(
                    "UPDATE memories SET last_used_at = ? WHERE id = ?",
                    [(used_at, row["id"]) for _, row in selected],
                )
        labels = {
            "user_fact": "用户已确认",
            "shared_experience": "共同经历",
            "preference_boundary": "偏好与边界",
            "open_thread": "未完话题",
            "character_commitment": "角色承诺",
        }
        return [
            f"{labels.get(row['category'], '记忆')}：{row['content']}"
            for _, row in selected
        ]

    def list_memories(
        self,
        conversation_id: str,
        *,
        query: str = "",
        include_inactive: bool = True,
    ) -> list[MemoryRecord]:
        sql = "SELECT * FROM memories WHERE conversation_id = ?"
        params: list[object] = [conversation_id]
        if not include_inactive:
            sql += " AND status IN ('active', 'corrected')"
        if query.strip():
            sql += " AND content LIKE ?"
            params.append(f"%{' '.join(query.split())[:120]}%")
        sql += " ORDER BY pinned DESC, updated_at DESC, rowid DESC"
        return [
            self._memory_record(row)
            for row in self._db.execute(sql, params).fetchall()
        ]

    def create_memory(
        self,
        conversation_id: str,
        category: str,
        content: str,
        *,
        source_type: str = "user_managed",
        source_turn_id: str = "",
        confidence: float = 1.0,
        salience: float = 0.7,
        status: str = "active",
        retention_days: int = 0,
    ) -> MemoryRecord:
        allowed_categories = {
            "user_fact",
            "shared_experience",
            "preference_boundary",
            "open_thread",
            "character_commitment",
        }
        allowed_statuses = {
            "candidate",
            "active",
            "corrected",
            "superseded",
            "deleted",
        }
        normalized = " ".join(str(content).split()).strip()[:600]
        if category not in allowed_categories or status not in allowed_statuses:
            raise ValueError("记忆类别或状态无效")
        if source_type in {
            "assistant_inferred",
            "image_analysis",
            "imported",
        } and status in {"active", "corrected"}:
            status = "candidate"
        if not normalized and status != "deleted":
            raise ValueError("记忆内容不能为空")
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            raise KeyError(conversation_id)
        memory_id = str(uuid4())
        now = _now()
        expires_at = ""
        if retention_days > 0:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(days=retention_days)
            ).isoformat(timespec="milliseconds")
        with self._db:
            self._db.execute(
                """INSERT INTO memories(
                       id, conversation_id, character_id, category, content,
                       source_type, source_turn_id, confidence, salience,
                       status, pinned, created_at, updated_at, expires_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                (
                    memory_id,
                    conversation_id,
                    conversation.character_id,
                    category,
                    normalized,
                    source_type[:80],
                    source_turn_id or None,
                    max(0.0, min(float(confidence), 1.0)),
                    max(0.0, min(float(salience), 1.0)),
                    status,
                    now,
                    now,
                    expires_at,
                ),
            )
        return self.get_memory(memory_id)  # type: ignore[return-value]

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        row = self._db.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return self._memory_record(row) if row else None

    def update_memory(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        status: str | None = None,
        pinned: bool | None = None,
    ) -> None:
        current = self.get_memory(memory_id)
        if current is None:
            raise KeyError(memory_id)
        next_content = (
            " ".join(content.split()).strip()[:600]
            if content is not None
            else current.content
        )
        next_status = status or current.status
        if (
            content is not None
            and next_content != current.content
            and status is None
            and current.status in {"candidate", "active", "corrected"}
        ):
            next_status = "corrected"
        if next_status not in {
            "candidate",
            "active",
            "corrected",
            "superseded",
            "deleted",
        }:
            raise ValueError("记忆状态无效")
        if not next_content and next_status != "deleted":
            raise ValueError("记忆内容不能为空")
        next_pinned = current.pinned if pinned is None else bool(pinned)
        deleted_at = _now() if next_status == "deleted" else ""
        confirmed_at = current.confirmed_at
        if current.status == "candidate" and next_status in {
            "active",
            "corrected",
        }:
            confirmed_at = _now()
        if next_status == "deleted":
            next_content = ""
            next_pinned = False
        with self._db:
            self._db.execute(
                """UPDATE memories
                   SET content = ?, status = ?, pinned = ?, updated_at = ?,
                       deleted_at = ?, confirmed_at = ? WHERE id = ?""",
                (
                    next_content,
                    next_status,
                    int(next_pinned),
                    _now(),
                    deleted_at,
                    confirmed_at,
                    memory_id,
                ),
            )

    def delete_memory(self, memory_id: str) -> None:
        self.update_memory(memory_id, status="deleted")

    def clear_memories(self, conversation_id: str) -> int:
        rows = self._db.execute(
            """SELECT id FROM memories
               WHERE conversation_id = ? AND status != 'deleted'""",
            (conversation_id,),
        ).fetchall()
        for row in rows:
            self.delete_memory(row["id"])
        return len(rows)

    def reset_role_continuity(self, conversation_id: str) -> int:
        count = self.clear_memories(conversation_id)
        self.set_role_state(conversation_id, {})
        return count

    def upsert_role_memories(
        self,
        conversation_id: str,
        turn_id: str,
        user_text: str,
        role_state: dict,
        *,
        retention_days: int = 0,
        max_items: int = 200,
    ) -> None:
        if not isinstance(role_state, dict):
            return
        candidates: list[tuple[str, str, str, float, float]] = []
        for content in role_state.get("user_facts", [])[:6]:
            text = " ".join(str(content).split()).strip()
            supported = self._memory_supported_by_user(text, user_text)
            candidates.append(
                (
                    "user_fact",
                    text,
                    "user_explicit" if supported else "assistant_inferred",
                    0.95 if supported else 0.45,
                    0.8,
                )
            )
        relationship = role_state.get("relationship")
        if isinstance(relationship, dict):
            for content in relationship.get("boundaries", [])[:6]:
                text = " ".join(str(content).split()).strip()
                supported = self._memory_supported_by_user(text, user_text)
                candidates.append(
                    (
                        "preference_boundary",
                        text,
                        "user_explicit" if supported else "assistant_inferred",
                        0.98 if supported else 0.45,
                        0.95,
                    )
                )
        for content in role_state.get("shared_memories", [])[:6]:
            candidates.append(
                (
                    "shared_experience",
                    " ".join(str(content).split()).strip(),
                    "role_state",
                    0.75,
                    0.7,
                )
            )
        for content in role_state.get("open_threads", [])[:6]:
            text = " ".join(str(content).split()).strip()
            category = (
                "character_commitment"
                if re.search(r"(?:角色|答应|承诺|约好|会替|会陪)", text)
                else "open_thread"
            )
            candidates.append(
                (
                    category,
                    text,
                    "role_state",
                    0.7,
                    0.65,
                )
            )
        correction = bool(
            re.search(r"(?:不是|不再|改成|纠正|记错|其实|更正)", user_text)
        )
        for category, content, source, confidence, salience in candidates:
            if not content:
                continue
            duplicate = self._db.execute(
                """SELECT id FROM memories
                   WHERE conversation_id = ? AND category = ? AND content = ?
                     AND status != 'deleted'""",
                (conversation_id, category, content[:600]),
            ).fetchone()
            if duplicate:
                continue
            status = (
                "candidate"
                if source == "assistant_inferred"
                else "active"
            )
            created = self.create_memory(
                conversation_id,
                category,
                content,
                source_type=source,
                source_turn_id=turn_id,
                confidence=confidence,
                salience=salience,
                status=status,
                retention_days=retention_days,
            )
            if correction and status == "active" and category in {
                "user_fact",
                "preference_boundary",
            }:
                self._supersede_conflicting_memories(created, user_text)
        self._prune_memories(conversation_id, max_items=max_items)

    def _supersede_conflicting_memories(
        self, replacement: MemoryRecord, user_text: str
    ) -> None:
        replacement_terms = _memory_terms(replacement.content)
        user_terms = _memory_terms(user_text)
        rows = self._db.execute(
            """SELECT * FROM memories
               WHERE conversation_id = ? AND category = ?
                 AND status = 'active' AND id != ?""",
            (
                replacement.conversation_id,
                replacement.category,
                replacement.id,
            ),
        ).fetchall()
        now = _now()
        with self._db:
            for row in rows:
                old_terms = _memory_terms(row["content"])
                if old_terms & replacement_terms & user_terms:
                    self._db.execute(
                        """UPDATE memories SET status = 'superseded',
                           superseded_by_id = ?, updated_at = ? WHERE id = ?""",
                        (replacement.id, now, row["id"]),
                    )

    def _prune_memories(self, conversation_id: str, *, max_items: int) -> None:
        limit = max(10, min(int(max_items), 2_000))
        rows = self._db.execute(
            """SELECT id FROM memories
               WHERE conversation_id = ?
                 AND status IN ('active', 'corrected', 'candidate')
                 AND pinned = 0
               ORDER BY updated_at DESC, rowid DESC""",
            (conversation_id,),
        ).fetchall()
        for row in rows[limit:]:
            self.delete_memory(row["id"])

    @staticmethod
    def _memory_supported_by_user(content: str, user_text: str) -> bool:
        memory_terms = _memory_terms(content)
        user_terms = _memory_terms(user_text)
        if not memory_terms or not user_terms:
            return False
        overlap = memory_terms & user_terms
        return len(overlap) >= min(2, max(1, len(memory_terms) // 3))

    @staticmethod
    def _memory_record(row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            conversation_id=row["conversation_id"],
            character_id=row["character_id"],
            category=row["category"],
            content=row["content"],
            source_type=row["source_type"],
            source_turn_id=row["source_turn_id"],
            confidence=float(row["confidence"]),
            salience=float(row["salience"]),
            status=row["status"],
            pinned=bool(row["pinned"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_used_at=row["last_used_at"],
            expires_at=row["expires_at"],
            superseded_by_id=row["superseded_by_id"],
            confirmed_at=row["confirmed_at"],
            deleted_at=row["deleted_at"],
        )


class CharacterRepository:
    SOURCE_TYPES = frozenset(
        {"user_created", "imported", "built_in", "ai_generated", "synced"}
    )

    def __init__(self, database: Database) -> None:
        self._db = database.connection

    def create(
        self,
        card: dict | None = None,
        avatar_path: str = "",
        *,
        source_type: str = "user_created",
    ) -> Character:
        character_id = str(uuid4())
        with self._db:
            self.create_with_id(
                character_id,
                card or empty_card(),
                avatar_path,
                source_type=source_type,
                connection=self._db,
            )
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
        source_type: str = "user_created",
        connection=None,
    ) -> None:
        """在调用方事务内用受信任的稳定 ID 创建角色。"""

        if source_type not in self.SOURCE_TYPES:
            raise ValueError(f"未知角色来源：{source_type}")
        normalized = normalize_card(card)
        now = _now()
        db = connection or self._db
        db.execute(
            """INSERT INTO characters(
                   id, name, avatar_path, card_json, created_at, updated_at,
                   source_type
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                character_id,
                normalized["data"]["name"],
                avatar_path,
                dump_card(normalized),
                now,
                now,
                source_type,
            ),
        )

    def set_source_type(
        self,
        character_id: str,
        source_type: str,
        *,
        connection=None,
    ) -> None:
        """修正受信任来源标签，不改动用户编辑过的角色卡内容。"""

        if source_type not in self.SOURCE_TYPES:
            raise ValueError(f"未知角色来源：{source_type}")
        db = connection or self._db
        statement = "UPDATE characters SET source_type = ? WHERE id = ?"
        if connection is None:
            with self._db:
                db.execute(statement, (source_type, character_id))
        else:
            db.execute(statement, (source_type, character_id))

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
            normalize_card(json.loads(row["card_json"])), row["created_at"],
            row["updated_at"], row["source_type"]
        )


class SettingsRepository:
    def __init__(self, database: Database) -> None:
        self._db = database.connection

    def get(self, key: str, default: str = "") -> str:
        row = self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """读取布尔设置；持久值按常见布尔文本解析。"""

        value = self.get(key, "true" if default else "false").lower()
        return value in {"1", "true", "yes", "on"}

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
